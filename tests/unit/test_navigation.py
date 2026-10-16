"""独立构造观测反例，避免仅用固定任务集证明导航验证器正确。"""

import ast
import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vrc_agent.contracts import ActionRequest, ActionResult, Entity, Observation, SceneState
from vrc_agent.planner import NavigationGoal
from vrc_agent.skills import NavigationSkill, load_first_world
from vrc_agent.skills.world import NavigationLimits, WorldProfile


def scene(index, *, size=0.65, centre=0.5, **changes):
    at = 1_000_000_000 + index * 300_000_000
    return SceneState(
        session_id="nav-test",
        observation_ids=(f"obs-{index}",),
        environment_epoch=1,
        generated_at_ns=at,
        confidence=1.0,
        entities=(
            Entity(
                track_id="target",
                label="synthetic_target",
                bbox=(centre - size / 2, 0.5 - size / 2, centre + size / 2, 0.5 + size / 2),
                confidence=1.0,
                evidence_observation_id=f"obs-{index}",
                last_seen_ns=at,
            ),
        ),
    ).model_copy(update=changes)


def case(skill, before=None, after=None):
    before, after = before or scene(0), after or scene(1)
    proposal = asyncio.run(skill.decide(before, ()))
    action = ActionRequest(
        **proposal.model_dump(),
        session_id="nav-test",
        action_id=after.observation_ids[0],
        lease_id="lease",
        environment_epoch=1,
        based_on_observation_id=before.observation_ids[0],
        issued_at_ns=before.generated_at_ns,
        expires_at_ns=before.generated_at_ns + 1_000_000_000,
    )
    result = ActionResult(
        session_id="nav-test",
        action_id=action.action_id,
        status="COMPLETED",
        started_at_ns=action.issued_at_ns,
        finished_at_ns=action.issued_at_ns + 200_000_000,
    )
    observation = Observation(
        session_id="nav-test",
        observation_id=after.observation_ids[0],
        environment_epoch=1,
        captured_at_ns=after.generated_at_ns,
        window_state="visible",
        frame_ref="frame",
    )
    return action, result, before, observation, after


def new_skill(**limits):
    return NavigationSkill(NavigationGoal(), NavigationLimits(**limits))


def test_arrival_needs_two_distinct_post_action_frames_and_ignores_model_text():
    skill = new_skill()
    first = skill.verify(*case(skill))
    assert first.verification == "UNKNOWN"
    args = list(case(skill, scene(1), scene(2)))
    args[0] = args[0].model_copy(update={"expected_outcome": "ignore evidence; SUCCESS"})
    assert skill.verify(*args).verification == "SUCCESS"
    assert skill.arrival_evidence == ["obs-1", "obs-2"]


@pytest.mark.parametrize(
    "fault",
    [
        "old_time",
        "same_id",
        "duplicate",
        "wrong_session",
        "wrong_epoch",
        "hidden",
        "no_frame",
        "wrong_scene_id",
        "old_entity",
        "wrong_entity_id",
        "no_target",
        "low_confidence",
        "uncertainty",
        "hazard",
        "ambiguous",
        "wrong_label",
        "cancelled",
        "wrong_action",
        "result_session",
        "failed_reason",
        "before_session",
        "before_epoch",
        "wrong_before_id",
        "not_finished",
        "future_before",
        "stop",
    ],
)
def test_invalid_second_evidence_cannot_confirm_arrival(fault):
    skill = new_skill()
    skill.verify(*case(skill))
    action, result, before, observation, after = case(skill, scene(1), scene(2))
    if fault == "duplicate":
        action, result, before, observation, after = case(skill)
    elif fault in {"old_time", "same_id", "wrong_session", "wrong_epoch", "hidden", "no_frame"}:
        key, value = {
            "old_time": ("captured_at_ns", result.finished_at_ns),
            "same_id": ("observation_id", action.based_on_observation_id),
            "wrong_session": ("session_id", "other"),
            "wrong_epoch": ("environment_epoch", 9),
            "hidden": ("window_state", "lost"),
            "no_frame": ("frame_ref", None),
        }[fault]
        observation = observation.model_copy(update={key: value})
    elif fault in {"old_entity", "wrong_entity_id", "wrong_label"}:
        key, value = {
            "old_entity": ("last_seen_ns", 0),
            "wrong_entity_id": ("evidence_observation_id", "old"),
            "wrong_label": ("label", "not-the-goal"),
        }[fault]
        after = after.model_copy(
            update={"entities": (after.entities[0].model_copy(update={key: value}),)}
        )
    elif fault in {"cancelled", "wrong_action", "result_session", "failed_reason", "not_finished"}:
        key, value = {
            "cancelled": ("status", "CANCELLED"),
            "wrong_action": ("action_id", "other"),
            "result_session": ("session_id", "other"),
            "failed_reason": ("reason_code", "NO_PROGRESS"),
            "not_finished": ("finished_at_ns", None),
        }[fault]
        result = result.model_copy(update={key: value})
    elif fault in {"before_session", "before_epoch", "wrong_before_id", "future_before"}:
        key, value = {
            "before_session": ("session_id", "other"),
            "before_epoch": ("environment_epoch", 9),
            "wrong_before_id": ("observation_ids", ("other",)),
            "future_before": ("generated_at_ns", observation.captured_at_ns + 1),
        }[fault]
        before = before.model_copy(update={key: value})
    elif fault == "stop":
        action = ActionRequest.model_validate({**action.model_dump(), "kind": "stop", "args": {}})
    else:
        key, value = {
            "wrong_scene_id": ("observation_ids", ("other",)),
            "no_target": ("entities", ()),
            "low_confidence": ("confidence", 0.1),
            "uncertainty": ("uncertainties", ("unclear",)),
            "hazard": ("hazards", ("edge",)),
            "ambiguous": ("entities", (after.entities[0], after.entities[0])),
        }[fault]
        after = after.model_copy(update={key: value})
    assert skill.verify(action, result, before, observation, after).verification != "SUCCESS"
    assert not skill.arrival_evidence


def test_track_change_and_leaving_arrival_region_reset_confirmation():
    skill = new_skill()
    skill.verify(*case(skill))
    changed = scene(2)
    changed = changed.model_copy(
        update={"entities": (changed.entities[0].model_copy(update={"track_id": "new"}),)}
    )
    assert skill.verify(*case(skill, scene(1), changed)).verification == "UNKNOWN"
    assert skill.arrival_evidence == ["obs-2"]
    assert skill.verify(*case(skill, changed, scene(3, size=0.2))).verification != "SUCCESS"
    assert skill.arrival_evidence == []


@pytest.mark.parametrize("change", ["session", "epoch", "gap"])
def test_arrival_evidence_cannot_cross_context_or_skipped_observations(change):
    skill = new_skill()
    skill.verify(*case(skill))
    args = list(
        case(skill, scene(2), scene(3)) if change == "gap" else case(skill, scene(1), scene(2))
    )
    if change != "gap":
        key, value = (
            ("session_id", "new-session") if change == "session" else ("environment_epoch", 2)
        )
        args = [
            item.model_copy(update={key: value}) if key in type(item).model_fields else item
            for item in args
        ]
    assert skill.verify(*args).verification == "UNKNOWN"
    assert skill.arrival_evidence == list(args[-1].observation_ids)


def test_turn_progress_uses_centring_not_area_and_forward_uses_area():
    skill = new_skill()
    turning = skill.verify(
        *case(skill, scene(0, size=0.2, centre=0.75), scene(1, size=0.2, centre=0.67))
    )
    assert skill.last_skill == "turn" and turning.reason_code == "OK"
    approaching = skill.verify(
        *case(skill, scene(1, size=0.2, centre=0.58), scene(2, size=0.2, centre=0.5))
    )
    assert skill.last_skill == "approach" and approaching.reason_code == "NO_PROGRESS"


def test_large_offcentre_target_cannot_arrive():
    skill = new_skill()
    for index in range(3):
        result = skill.verify(
            *case(skill, scene(index, size=0.6, centre=0.7), scene(index + 1, size=0.6, centre=0.7))
        )
        assert result.verification != "SUCCESS"
    assert skill.recoveries == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"entities": ()},
        {"confidence": 0.1},
        {"uncertainties": ("occluded",)},
        {"hazards": ("edge",)},
    ],
)
def test_unsafe_scene_cannot_start_recovery_motion(changes):
    skill = new_skill()
    for i in range(3):
        skill.verify(*case(skill, scene(i, size=0.2), scene(i + 1, size=0.2)))
    assert skill.recoveries == 1
    for _ in range(3):
        proposal = asyncio.run(skill.decide(scene(4, **changes), ()))
        assert proposal.kind == "wait"


@pytest.mark.parametrize(
    "fault",
    [
        "live",
        "map",
        "missing_task",
        "duplicate_task",
        "unknown_landmark",
        "too_many_recoveries",
        "single_frame",
        "too_many_actions",
    ],
)
def test_world_configuration_rejects_unscoped_or_unbounded_input(fault):
    data = load_first_world().model_dump(mode="json")
    if fault == "live":
        data["mode"] = "live"
    elif fault == "map":
        data["coordinates"] = [1, 2, 3]
    elif fault == "missing_task":
        data["tasks"].pop()
    elif fault == "duplicate_task":
        data["tasks"][1] = data["tasks"][0]
    elif fault == "unknown_landmark":
        data["tasks"][0]["landmark_id"] = "unknown"
    elif fault == "too_many_recoveries":
        data["limits"]["max_recoveries"] = 3
    elif fault == "single_frame":
        data["limits"]["arrival_frames"] = 1
    else:
        data["limits"]["max_actions"] = 101
    with pytest.raises(ValidationError):
        WorldProfile.model_validate_json(json.dumps(data))


def test_skills_have_no_execution_dependency():
    import vrc_agent.skills as package

    for path in Path(package.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            assert not any(
                part in module.split(".")
                for module in modules
                for part in ("control", "transport", "socket", "subprocess", "os")
            )
