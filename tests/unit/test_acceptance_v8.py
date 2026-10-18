"""v8 候选语义、保护文件与原阈值反例；无任何实机能力。"""

import asyncio
import hashlib
from pathlib import Path

import pytest

from vrc_agent.contracts import (
    ActionProposal,
    ActionRequest,
    ActionResult,
    Entity,
    Observation,
    SceneState,
)
from vrc_agent.live_navigation.acceptance_v8 import DECLARATION, AcceptanceSkillV8, scope_scene
from vrc_agent.live_navigation.stability import load_policy
from vrc_agent.live_navigation.world import load_live_world
from vrc_agent.planner import target


def inputs(*, dirty=False, near=False, box=None):
    policy, _ = load_policy("v8")
    skill = AcceptanceSkillV8(load_live_world("v2").landmark("G2"), policy, profile="field")

    def observed(index):
        obs = Observation(
            session_id="test",
            environment_epoch=1,
            observation_id=f"obs-{index}",
            captured_at_ns=index * 1000,
            window_state="visible",
            frame_ref=f"frame-{index}",
            frame_size=(2160, 1440),
        )
        skill.register(obs)
        entities = (
            Entity(
                track_id="goal",
                label="home_raccoon",
                bbox=box or ((0.1, 0.1, 0.9, 0.9) if near else (0.41, 0.47, 0.59, 0.65)),
                confidence=0.9,
                evidence_observation_id=obs.observation_id,
                last_seen_ns=obs.captured_at_ns,
            ),
            Entity(
                track_id="floor",
                label="forward_clearance",
                bbox=(0.1, 0.6, 0.9, 0.98),
                confidence=0.9,
                evidence_observation_id=obs.observation_id,
                last_seen_ns=obs.captured_at_ns,
            ),
            Entity(
                track_id="other",
                label="unrelated",
                bbox=(0.1, 0.1, 0.2, 0.2),
                confidence=0.6,
                evidence_observation_id=obs.observation_id,
                last_seen_ns=obs.captured_at_ns,
            ),
        )
        scene = SceneState(
            session_id="test",
            environment_epoch=1,
            observation_ids=(obs.observation_id,),
            generated_at_ns=obs.captured_at_ns,
            entities=entities,
            confidence=0.6,
            hazards=("obstacle",) if dirty else (),
            uncertainties=("unknown",) if dirty else (),
        )
        return obs, scope_scene(scene, "home_raccoon")

    return skill, observed


def test_v8_exact_v6_base_and_candidate_digest():
    v6, h6 = load_policy("v6")
    v8, h8 = load_policy("v8")
    assert h6 == "e46e0c2816c6f512fbd4fd5e5c60ff1ef3fc4d0560e0e8b29d3b6a6315caa513"
    assert v8 == {
        **v6,
        "version": "local-rgb-stability-v8-candidate",
        "status": "FROZEN",
        **DECLARATION,
    }
    root = Path("src/vrc_agent/live_navigation")
    assert hashlib.sha256((root / "stability_v8.json").read_bytes()).hexdigest() == h8
    assert (root / "stability_v8.sha256").read_bytes() == (h8 + "\r\n").encode()
    for version in range(1, 9):
        policy, _ = load_policy(f"v{version}")
        assert policy["status"] == (
            "FROZEN" if version in (5, 6, 8) else "REQUIRES_FIELD_CALIBRATION"
        )


def test_dirty_scene_recognized_and_motion_uses_clearance_without_unknown_zero_substitution():
    skill, observed = inputs(dirty=True)
    _, scene = observed(1)
    assert scene.confidence == 0.9 and scene.source_confidence == 0.6
    assert all(skill.recognition_predicates(scene).values())
    assert skill.select_target(scene) is not None and target(scene, skill.goal) is None
    for _ in range(3):
        assert asyncio.run(skill.decide(scene, ())).kind == "motion"
    assert skill.halt_reason is None
    missing = scene.model_copy(update={"entities": scene.entities[1:]})
    # 构造新作用域时，未知目标为 None，不能记成完整0或沿用无关实体最小值。
    scoped = scope_scene(
        SceneState(
            **{
                k: v
                for k, v in missing.model_dump().items()
                if k not in {"confidence_scope", "target_label", "source_confidence"}
            }
        ),
        "home_raccoon",
    )
    assert scoped.confidence is None and skill.select_target(scoped) is None


@pytest.mark.parametrize("signals", [(True, False), (False, True), (True, True)])
@pytest.mark.parametrize("axis", ["forward", "strafe", "yaw"])
def test_signals_are_record_only_for_all_axes(signals, axis):
    skill, observed = inputs()
    _, scene = observed(1)
    recovery = scene.entities[1].model_copy(
        update={"label": "recovery_clearance", "track_id": "recovery"}
    )
    scene = scene.model_copy(
        update={
            "hazards": ("hazard",) if signals[0] else (),
            "uncertainties": ("uncertain",) if signals[1] else (),
            "entities": scene.entities + (recovery,),
        }
    )
    proposal = ActionProposal(
        kind="motion",
        args={
            axis: getattr(skill.motion_envelope, axis),
            "duration_ms": getattr(skill.motion_envelope, axis + "_ms"),
        },
    )
    audit = skill.audit(scene, proposal, 1)
    assert audit["proposal_permitted"]
    assert audit["path_safety"]["has_hazards"] is signals[0]
    assert audit["path_safety"]["has_uncertainties"] is signals[1]
    assert audit["scope"]["motion_effect"] == "record_only"
    assert not skill.arrival_permitted(scene)
    assert skill.recovery_allowed(scene)
    if axis == "strafe":
        skill._recovery.append("recover_strafe")
    elif axis == "yaw":
        target_entity = scene.entities[0].model_copy(update={"bbox": (0.6, 0.47, 0.9, 0.65)})
        scene = scene.model_copy(update={"entities": (target_entity,) + scene.entities[1:]})
    assert asyncio.run(skill.decide(scene, ())).kind == "motion"


def test_signals_without_clearance_still_wait_then_pause():
    skill, observed = inputs(dirty=True)
    _, scene = observed(1)
    scene = scene.model_copy(update={"entities": (scene.entities[0],)})
    for _ in range(3):
        assert asyncio.run(skill.decide(scene, ())).kind == "wait"
    assert skill.halt_reason == "PATH_UNSAFE"


@pytest.mark.parametrize(
    "fault", ["duplicate", "low", "unbound", "bbox", "hidden", "frame", "masked"]
)
def test_target_recognition_all_predicates_fail_closed(fault):
    skill, observed = inputs()
    obs, scene = observed(1)
    entity = scene.entities[0]
    expected = {
        "duplicate": "unique_label",
        "low": "entity_confident",
        "unbound": "evidence_bound",
        "bbox": "bbox_legal",
        "hidden": "frame_visible",
        "frame": "target_frame_bound",
        "masked": "target_not_fully_masked",
    }[fault]
    if fault == "duplicate":
        scene = scene.model_copy(
            update={"entities": scene.entities + (entity.model_copy(update={"confidence": 0.1}),)}
        )
    elif fault in {"hidden", "frame"}:
        skill.register(
            obs.model_copy(
                update={"window_state": "lost"} if fault == "hidden" else {"frame_ref": None}
            )
        )
    else:
        change = {
            "low": {"confidence": 0.84},
            "unbound": {"evidence_observation_id": "other"},
            "bbox": {"bbox": (-0.1, 0.2, 0.3, 0.4)},
            "masked": {"bbox": (1600 / 2160, 100 / 1440, 1700 / 2160, 200 / 1440)},
        }[fault]
        scene = scene.model_copy(
            update={"entities": (entity.model_copy(update=change),) + scene.entities[1:]}
        )
    assert not skill.recognition_predicates(scene)[expected]
    assert skill.select_target(scene) is None


@pytest.mark.parametrize(
    "box,confidence,allowed",
    [
        ((0.35, 0.7, 0.65, 0.9), 0.9, True),
        ((0.350001, 0.7, 0.65, 0.9), 0.9, False),
        ((0.35, 0.700001, 0.65, 0.9), 0.9, False),
        ((0.35, 0.7, 0.649999, 0.9), 0.9, False),
        ((0.35, 0.7, 0.65, 0.899999), 0.9, False),
        ((0.35, 0.7, 0.65, 0.9), 0.899999, False),
    ],
)
def test_clearance_boundaries_unchanged(box, confidence, allowed):
    skill, observed = inputs()
    _, scene = observed(1)
    floor = scene.entities[1].model_copy(update={"bbox": box, "confidence": confidence})
    scene = scene.model_copy(update={"entities": (scene.entities[0], floor)})
    assert skill.clearance(scene, "forward_clearance") is allowed
    assert not skill.clearance(scene, "recovery_clearance")


def test_path_envelope_budget_and_recovery_independent():
    skill, observed = inputs()
    _, scene = observed(1)
    assert asyncio.run(skill.decide(scene, ())).kind == "motion"
    assert not skill.audit(
        scene, ActionProposal(kind="motion", args={"forward": 0.5, "duration_ms": 500}), 1
    )["proposal_permitted"]
    assert not skill.audit(
        scene, ActionProposal(kind="motion", args={"forward": 0.4, "duration_ms": 500}), 41
    )["proposal_permitted"]
    assert not skill.audit(
        scene, ActionProposal(kind="motion", args={"strafe": 0.2, "duration_ms": 200}), 1
    )["proposal_permitted"]


@pytest.mark.parametrize("dirty", [True, False])
def test_arrival_two_new_observations_and_signals_are_explicit(dirty):
    skill, observed = inputs(dirty=dirty, near=True)
    _, before = observed(1)
    for index in (2, 3):
        obs, after = observed(index)
        action = ActionRequest(
            kind="wait",
            args={"duration_ms": 100},
            session_id="test",
            lease_id="synthetic",
            environment_epoch=1,
            based_on_observation_id=before.observation_ids[0],
            issued_at_ns=before.generated_at_ns + 1,
            expires_at_ns=before.generated_at_ns + 100,
        )
        result = ActionResult(
            session_id="test",
            action_id=action.action_id,
            status="COMPLETED",
            reason_code="OK",
            finished_at_ns=before.generated_at_ns + 99,
        )
        skill.decision_count = index - 1
        verified = skill.verify(action, result, before, obs, after)
        assert skill.last_evidence["valid_target"]
        assert skill.last_evidence["acceptance"]["arrival_review_required"] is dirty
        assert verified.verification == ("SUCCESS" if index == 3 and not dirty else "UNKNOWN")
        before = after


@pytest.mark.parametrize("fault", ["window", "frame", "time", "epoch", "session", "binding"])
def test_original_verification_safety_is_not_relaxed(fault):
    skill, observed = inputs(near=True)
    _, before = observed(1)
    obs, after = observed(2)
    action = ActionRequest(
        kind="wait",
        args={"duration_ms": 100},
        session_id="test",
        lease_id="synthetic",
        environment_epoch=1,
        based_on_observation_id=before.observation_ids[0],
        issued_at_ns=1001,
        expires_at_ns=1101,
    )
    result = ActionResult(
        session_id="test",
        action_id=action.action_id,
        status="COMPLETED",
        reason_code="OK",
        finished_at_ns=1100,
    )
    if fault == "window":
        obs = obs.model_copy(update={"window_state": "lost"})
    elif fault == "frame":
        obs = obs.model_copy(update={"frame_ref": None})
    elif fault == "time":
        obs = obs.model_copy(update={"captured_at_ns": 1100})
    elif fault == "epoch":
        after = after.model_copy(update={"environment_epoch": 2})
    elif fault == "session":
        after = after.model_copy(update={"session_id": "other"})
    else:
        after = after.model_copy(update={"observation_ids": ("other",)})
    skill.decision_count = 1
    assert skill.verify(action, result, before, obs, after).verification == "UNKNOWN"
    assert not skill.last_evidence["valid_target"]
