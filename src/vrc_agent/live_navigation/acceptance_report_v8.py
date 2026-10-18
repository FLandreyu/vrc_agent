"""重算 v8 目标/路径采信与到达结果；结构一致性验证，不是现场签名。"""

import json

from vrc_agent.contracts import ActionProposal, ActionRequest, ActionResult, Entity, Observation

from .acceptance_v8 import SEMANTICS, AcceptanceSkillV8, ScopedSceneV8, scoped_confidence
from .stability import load_policy
from .world import load_live_world


def same(actual, expected):
    if json.dumps(actual, sort_keys=True, allow_nan=False) != json.dumps(
        expected, sort_keys=True, allow_nan=False
    ):
        raise ValueError("v8 acceptance evidence mismatch")


def validate_acceptance(report, *, policy_version="v8", schema_version=11):
    # v9仅换像素判据，采信语义仍为target-path-v8；schema/策略配对不可任意指定。
    if {"v8": 11, "v9": 12}.get(policy_version) != schema_version:
        raise ValueError("invalid acceptance policy/schema pairing")
    try:
        _validate_acceptance(report, policy_version, schema_version)
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError("missing or invalid v8 acceptance evidence") from exc


def _validate_acceptance(report, policy_version, schema_version):
    if (
        type(report.get("schema_version")) is not int
        or report["schema_version"] != schema_version
        or report.get("acceptance_semantics") != SEMANTICS
    ):
        raise ValueError("v8 acceptance requires schema11 and semantics")
    policy, _ = load_policy(policy_version)
    version = {"vrchat-home-live-v1": "v1", "vrchat-home-live-v2": "v2"}[report["world_version"]]
    world = load_live_world(version)
    same(report["suite_digest"], world.digest())
    skill = AcceptanceSkillV8(
        world.landmark(world.task(report["task_id"]).group),
        policy,
        profile=report["run_parameters"]["motion_profile"],
    )
    scenes, actions, results = {}, report["actions"], report["results"]
    if len(actions) != len(results) or len(actions) > policy["max_actions"]:
        raise ValueError("v8 action count mismatch")
    action_index = {a["action_id"]: i for i, a in enumerate(actions)}
    if len(action_index) != len(actions):
        raise ValueError("duplicate v8 action")
    decisions, feedback = {}, set()
    last_capture = -1
    for metadata in report["acceptance_observations"]:
        same(
            set_keys(metadata),
            [
                "captured_at_ns",
                "environment_epoch",
                "frame_ref",
                "frame_size",
                "observation_id",
                "session_id",
                "window_state",
            ],
        )
        observation = Observation.model_validate_json(json.dumps(metadata))
        if (
            observation.session_id != report["session_id"]
            or observation.frame_ref is None
            or observation.window_state != "visible"
            or list(observation.frame_size or ()) != policy["frame_size"]
        ):
            raise ValueError("invalid v8 capture metadata")
        if (
            observation.observation_id in skill.observations
            or observation.captured_at_ns <= last_capture
        ):
            raise ValueError("duplicate or unordered v8 capture")
        skill.register(observation)
        last_capture = observation.captured_at_ns
    for event in report["events"]:
        kind = event["event"]
        if kind in {"inference_source", "observation"}:
            if (
                type(event["has_hazards"]) is not bool
                or type(event["has_uncertainties"]) is not bool
            ):
                raise ValueError("v8 scene flags must be bool")
            metadata = event["observation_metadata"]
            same(
                set_keys(metadata),
                [
                    "captured_at_ns",
                    "environment_epoch",
                    "frame_ref",
                    "frame_size",
                    "observation_id",
                    "session_id",
                    "window_state",
                ],
            )
            observation = Observation.model_validate_json(json.dumps(metadata))
            recorded = skill.observations[observation.observation_id]
            same(recorded.model_dump(mode="json", include=set(metadata)), metadata)
            generated = event["scene_generated_at_ns"]
            if (
                type(generated) is not int
                or not observation.captured_at_ns <= generated <= event["at_ns"]
            ):
                raise ValueError("v8 scene generation order mismatch")
            if (
                observation.observation_id != event["observation_id"]
                or observation.session_id != report["session_id"]
                or observation.captured_at_ns != event["captured_at_ns"]
                or observation.window_state != "visible"
                or observation.frame_ref is None
                or list(observation.frame_size or ()) != policy["frame_size"]
            ):
                raise ValueError("v8 frame binding mismatch")
            entities = tuple(Entity.model_validate_json(json.dumps(e)) for e in event["entities"])
            same(event["confidence_scope"], "target_entity")
            same(event["confidence"], scoped_confidence(entities, skill.goal.label))
            scene = ScopedSceneV8(
                session_id=observation.session_id,
                environment_epoch=observation.environment_epoch,
                observation_ids=(observation.observation_id,),
                generated_at_ns=event["scene_generated_at_ns"],
                entities=entities,
                confidence=event["confidence"],
                confidence_scope="target_entity",
                target_label=skill.goal.label,
                source_confidence=event["source_confidence"],
                hazards=("present",) if event["has_hazards"] else (),
                uncertainties=("present",) if event["has_uncertainties"] else (),
            )
            skill.register(observation)
            if kind == "observation":
                if observation.observation_id in scenes:
                    raise ValueError("duplicate v8 observation")
                scenes[observation.observation_id] = scene
        elif kind == "acceptance_decision":
            index = event["decision_index"]
            if (
                type(index) is not int
                or index != len(decisions) + 1
                or index > policy["max_actions"]
            ):
                raise ValueError("invalid v8 decision count")
            scene = scenes[event["observation_id"]]
            requested = ActionProposal.model_validate_json(json.dumps(event["requested"]))
            dispatched = ActionProposal.model_validate_json(json.dumps(event["dispatched"]))
            audit = skill.audit(scene, requested, index)
            same(event["audit"], audit)
            expected = (
                requested
                if audit["proposal_permitted"]
                else ActionProposal(kind="wait", args={"duration_ms": 100})
            )
            same(dispatched.model_dump(mode="json"), expected.model_dump(mode="json"))
            decisions[index] = event
            if index <= len(actions):
                action = actions[index - 1]
                same(action["based_on_observation_id"], event["observation_id"])
                same(
                    {"kind": action["kind"], "args": action["args"]},
                    {"kind": dispatched.kind, "args": dispatched.args.model_dump(mode="json")},
                )
        elif kind == "navigation_feedback":
            index = action_index[event["action_id"]]
            if index in feedback:
                raise ValueError("duplicate v8 feedback")
            if index + 1 not in decisions:
                raise ValueError("missing v8 decision before feedback")
            action = ActionRequest.model_validate_json(
                json.dumps({**actions[index], "lease_id": "report-only"})
            )
            result = ActionResult.model_validate_json(json.dumps(results[index]))
            before = scenes[action.based_on_observation_id]
            after_id = event["evidence"]["observation_id"]
            after = scenes[after_id]
            observation = skill.observations[after_id]
            skill.decision_count = index + 1
            skill.last_skill = (
                "approach"
                if action.kind == "motion" and action.args.forward
                else "turn"
                if action.kind == "motion" and action.args.yaw
                else "wait"
            )
            verified = skill.verify(action, result, before, observation, after)
            same(event["evidence"], skill.last_evidence)
            same(result.verification, verified.verification)
            same(list(result.evidence_observation_ids), list(verified.evidence_observation_ids))
            if verified.verification == "SUCCESS" and not skill.arrival_permitted(after):
                raise ValueError("v8 dirty scene cannot claim success")
            feedback.add(index)
    if len(decisions) < len(actions):
        raise ValueError("missing v8 decisions")
    for index, result in enumerate(results):
        if (
            len(result["evidence_observation_ids"]) > 1 or result["verification"] == "SUCCESS"
        ) and index not in feedback:
            raise ValueError("missing v8 feedback")
    for proof in (
        report["stability_proofs"] + report["warmup_checks"] + report["stability_prechecks"]
    ):
        for side in ("reference", "current"):
            obs = skill.observations[proof[side + "_observation_id"]]
            if (
                obs.session_id != proof["session_id"]
                or obs.environment_epoch != proof["environment_epoch"]
                or obs.captured_at_ns != proof[side + "_captured_at_ns"]
            ):
                raise ValueError("v8 proof metadata mismatch")
    if report["outcome"] == "SUCCESS" and (not results or results[-1]["verification"] != "SUCCESS"):
        raise ValueError("v8 success without arrival evidence")


def set_keys(mapping):
    return sorted(mapping.keys())
