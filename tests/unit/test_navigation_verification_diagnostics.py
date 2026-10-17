"""pilot5 的脱敏元数据 + 合成内存帧；不访问现场源/供应商/控制器。"""

import copy
import json
from pathlib import Path

import pytest

from vrc_agent.capture.frames import Frame
from vrc_agent.contracts import ActionRequest, ActionResult, Entity, Observation, SceneState
from vrc_agent.live_navigation.skill import CalibratedSkill
from vrc_agent.live_navigation.stability_report import validate_feedback_diagnostics
from vrc_agent.live_navigation.world import load_live_world
from vrc_agent.skills.navigation import VERIFICATION_PREDICATES


def pilot_sequence():
    data = json.loads(
        (Path(__file__).parents[1] / "fixtures/pilot5_verification.json").read_text("utf-8")
    )
    skill = CalibratedSkill(load_live_world("v2").landmark("G2"))
    observations = {}
    for event in data["observations"]:
        key = event["observation_id"]
        frame = Frame(
            "synthetic-" + key,
            bytes((24, 24, 24, 255)) * 2160 * 1440,
            2160,
            1440,
            event["captured_at_ns"],
        )
        observation = Observation(
            session_id=data["actions"][0]["session_id"],
            environment_epoch=1,
            observation_id=key,
            captured_at_ns=frame.captured_at_ns,
            frame_ref=frame.ref,
            frame_size=frame.size,
            window_state="visible",
        )
        scene = SceneState(
            session_id=observation.session_id,
            environment_epoch=1,
            observation_ids=(key,),
            generated_at_ns=event["at_ns"],
            confidence=event["confidence"],
            hazards=("recorded_present",) if event["has_hazards"] else (),
            uncertainties=("recorded_present",) if event["has_uncertainties"] else (),
            entities=tuple(
                Entity.model_validate({**e, "bbox": tuple(e["bbox"])}) for e in event["entities"]
            ),
        )
        observations[key] = observation, scene
    sequence = []
    for action, result in zip(data["actions"], data["results"], strict=True):
        action = ActionRequest.model_validate_json(
            json.dumps({**action, "lease_id": "synthetic-lease"})
        )
        result = ActionResult.model_validate_json(json.dumps(result))
        _, before = observations[action.based_on_observation_id]
        observation, after = observations[result.evidence_observation_ids[-1]]
        sequence.append((action, result, before, observation, after))
    return skill, sequence


def test_pilot5_print_every_predicate_and_reproduce_unknowns():
    skill, sequence = pilot_sequence()
    for index, args in enumerate(sequence, 1):
        verified = skill.verify(*args)
        evidence = skill.last_evidence
        print(f"\n{args[0].based_on_observation_id} -> {args[3].observation_id}")
        for name, passed in evidence["verification_predicates"].items():
            print(f"{name}={passed}")
        assert set(evidence["verification_predicates"]) == VERIFICATION_PREDICATES
        assert evidence["failed_predicates"] == [
            "scene_confidence_sufficient",
            "scene_no_hazards",
            "scene_no_uncertainties",
        ]
        assert not evidence["valid_target"]
        assert skill.unknowns == index and verified.verification == "UNKNOWN"
        # 两个观测均入 evidence，反证基础 valid 为 false 的假设。
        assert verified.evidence_observation_ids == (
            args[0].based_on_observation_id,
            args[3].observation_id,
        )
    assert skill.halt_reason == "TARGET_UNOBSERVED"


def clean_inputs():
    skill, sequence = pilot_sequence()
    action, result, before, observation, after = sequence[0]
    before = before.model_copy(update={"confidence": 0.99, "hazards": (), "uncertainties": ()})
    after = after.model_copy(update={"confidence": 0.99, "hazards": (), "uncertainties": ()})
    return skill, [action, result, before, observation, after]


@pytest.mark.parametrize("predicate", sorted(VERIFICATION_PREDICATES))
def test_every_rejected_predicate_remains_fail_closed(predicate):
    skill, args = clean_inputs()
    action, result, before, observation, after = args
    changes = {
        "result_completed": (1, {"status": "FAILED"}),
        "result_ok": (1, {"reason_code": "TRANSPORT_ERROR"}),
        "result_action_matches": (1, {"action_id": "other"}),
        "result_session_matches": (1, {"session_id": "other"}),
        "action_verifiable": (0, {"kind": "stop"}),
        "result_finished": (1, {"finished_at_ns": None}),
        "observation_after_finish": (3, {"captured_at_ns": result.finished_at_ns}),
        "observation_new": (0, {"based_on_observation_id": observation.observation_id}),
        "window_visible": (3, {"window_state": "unknown"}),
        "frame_bound": (3, {"frame_ref": None}),
        "session_consistent": (4, {"session_id": "other"}),
        "epoch_consistent": (4, {"environment_epoch": 2}),
        "scene_observation_bound": (4, {"observation_ids": ("other",)}),
        "scene_generated_after_capture": (4, {"generated_at_ns": observation.captured_at_ns - 1}),
        "action_before_bound": (2, {"observation_ids": ("other",)}),
        "scene_before_issue": (2, {"generated_at_ns": action.issued_at_ns + 1}),
        "scene_confidence_known": (4, {"confidence": None, "entities": ()}),
        "scene_confidence_sufficient": (4, {"confidence": 0.84}),
        "scene_no_hazards": (4, {"hazards": ("hazard",)}),
        "scene_no_uncertainties": (4, {"uncertainties": ("uncertain",)}),
        "unique_bound_confident_target": (4, {"entities": ()}),
        "target_timestamp_current": (
            4,
            {
                "entities": tuple(
                    e.model_copy(update={"last_seen_ns": observation.captured_at_ns - 1})
                    for e in after.entities
                )
            },
        ),
    }
    if predicate == "capture_monotonic":
        skill._last_capture = observation.captured_at_ns
    elif predicate == "observation_unseen":
        skill._seen.add(observation.observation_id)
    else:
        index, update = changes[predicate]
        args[index] = args[index].model_copy(update=update)
    verified = skill.verify(*args)
    assert predicate in skill.last_evidence["failed_predicates"]
    assert not skill.last_evidence["valid_target"]
    assert verified.verification != "SUCCESS"


def test_valid_target_does_not_claim_unobserved_arrival():
    skill, args = clean_inputs()
    verified = skill.verify(*args)
    assert skill.last_evidence["valid_target"]
    assert skill.last_evidence["failed_predicates"] == []
    assert verified.verification == "UNKNOWN"  # G2 小框，未到达。
    assert skill.arrival_evidence == []


def test_success_still_needs_two_new_bound_arrival_observations():
    skill, sequence = pilot_sequence()
    for index, args in enumerate(sequence[:2]):
        action, result, before, observation, after = args
        scenes = []
        for scene in (before, after):
            entity = scene.entities[0].model_copy(
                update={"track_id": "same-target", "bbox": (0.05, 0.1, 0.95, 0.95)}
            )
            scenes.append(
                scene.model_copy(
                    update={
                        "confidence": 0.99,
                        "hazards": (),
                        "uncertainties": (),
                        "entities": (entity,),
                    }
                )
            )
        verified = skill.verify(action, result, scenes[0], observation, scenes[1])
        assert skill.last_evidence["failed_predicates"] == []
        assert verified.verification == ("UNKNOWN" if index == 0 else "SUCCESS")
        assert len(skill.arrival_evidence) == index + 1


@pytest.mark.parametrize("fault", ["name", "bool", "failed", "valid", "version", "missing"])
def test_report_diagnostics_reject_tampering(fault):
    skill, args = clean_inputs()
    skill.verify(*args)
    evidence = copy.deepcopy(skill.last_evidence)
    if fault == "name":
        evidence["verification_predicates"]["untrusted-name"] = True
    elif fault == "bool":
        evidence["verification_predicates"]["window_visible"] = 1
    elif fault == "failed":
        evidence["failed_predicates"] = ["window_visible"]
    elif fault == "valid":
        evidence["valid_target"] = False
    elif fault == "version":
        evidence["diagnostics_version"] = True
    else:
        del evidence["verification_predicates"]
    with pytest.raises(ValueError, match="verification diagnostics"):
        validate_feedback_diagnostics(
            {"events": [{"event": "navigation_feedback", "evidence": evidence}]}
        )


def test_legacy_feedback_and_new_feedback_both_validate():
    skill, args = clean_inputs()
    skill.verify(*args)
    validate_feedback_diagnostics(
        {
            "events": [
                {"event": "navigation_feedback", "evidence": {"valid_target": False}},
                {"event": "navigation_feedback", "evidence": skill.last_evidence},
            ]
        }
    )
