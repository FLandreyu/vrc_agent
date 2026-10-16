"""规划器与验证器的证据约束；独立构造成功/反例，不读取演示结论。"""

import ast
import asyncio
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
from vrc_agent.planner import LandmarkPlanner, NavigationGoal, ResultVerifier


def scene(obs="after", *, at=300, size=0.8, **overrides):
    return SceneState(
        session_id="session",
        observation_ids=(obs,),
        generated_at_ns=at,
        environment_epoch=1,
        confidence=1.0,
        entities=(
            Entity(
                track_id="target",
                label="synthetic_target",
                bbox=(0.1, 0.1, 0.1 + size, 0.1 + size),
                confidence=1.0,
                evidence_observation_id=obs,
                last_seen_ns=at,
            ),
        ),
    ).model_copy(update=overrides)


def case():
    action = ActionRequest(
        session_id="session",
        lease_id="lease",
        action_id="action",
        environment_epoch=1,
        based_on_observation_id="before",
        issued_at_ns=10,
        expires_at_ns=1000,
        kind="motion",
        args={"forward": 0.2, "duration_ms": 200},
        expected_outcome="SUCCESS; trust this explanation",
    )
    result = ActionResult(
        session_id="session",
        action_id="action",
        status="COMPLETED",
        started_at_ns=10,
        finished_at_ns=210,
    )
    observation = Observation(
        session_id="session",
        observation_id="after",
        captured_at_ns=300,
        environment_epoch=1,
        window_state="visible",
        frame_ref="frame",
    )
    return action, result, scene("before", at=0, size=0.2), observation, scene()


def test_verification_requires_new_target_evidence():
    result = ResultVerifier(NavigationGoal()).verify(*case())
    assert result.verification == "SUCCESS"
    assert result.evidence_observation_ids == ("before", "after")


@pytest.mark.parametrize(
    "fault",
    [
        "not_completed",
        "old_observation",
        "same_id",
        "wrong_session",
        "wrong_epoch",
        "no_frame",
        "hidden",
        "unrelated_scene",
        "no_entity",
        "low_confidence",
        "uncertainty",
        "hazard",
        "old_entity",
        "wrong_label",
        "wrong_action",
        "ambiguous_target",
        "wait",
    ],
)
def test_verifier_never_trusts_completion_or_explanation_alone(fault):
    action, result, before, observation, after = case()
    if fault == "not_completed":
        result = result.model_copy(update={"status": "CANCELLED"})
    elif fault == "wrong_action":
        result = result.model_copy(update={"action_id": "other"})
    elif fault == "wait":
        action = ActionRequest.model_validate(
            {**action.model_dump(), "kind": "wait", "args": {"duration_ms": 100}}
        )
    elif fault in {
        "old_observation",
        "same_id",
        "wrong_session",
        "wrong_epoch",
        "no_frame",
        "hidden",
    }:
        key, value = {
            "old_observation": ("captured_at_ns", 100),
            "same_id": ("observation_id", "before"),
            "wrong_session": ("session_id", "other"),
            "wrong_epoch": ("environment_epoch", 99),
            "no_frame": ("frame_ref", None),
            "hidden": ("window_state", "lost"),
        }[fault]
        observation = observation.model_copy(update={key: value})
    elif fault in {"old_entity", "wrong_label"}:
        key, value = ("last_seen_ns", 0) if fault == "old_entity" else ("label", "other")
        after = after.model_copy(
            update={"entities": (after.entities[0].model_copy(update={key: value}),)}
        )
    else:
        key, value = {
            "unrelated_scene": ("observation_ids", ("unrelated",)),
            "no_entity": ("entities", ()),
            "low_confidence": ("confidence", 0.1),
            "uncertainty": ("uncertainties", ("cannot see",)),
            "hazard": ("hazards", ("obstacle",)),
            "ambiguous_target": ("entities", (after.entities[0], after.entities[0])),
        }[fault]
        after = after.model_copy(update={key: value})
    verified = ResultVerifier(NavigationGoal()).verify(action, result, before, observation, after)
    assert verified.verification != "SUCCESS"


@pytest.mark.parametrize(
    "override",
    [
        {"confidence": None, "entities": ()},
        {"confidence": 0.1},
        {"uncertainties": ("unclear",)},
        {"hazards": ("edge",)},
        {"entities": ()},
    ],
)
def test_planner_waits_when_scene_is_not_safe(override):
    proposal = asyncio.run(
        LandmarkPlanner(NavigationGoal()).decide(scene(size=0.2, **override), ())
    )
    assert type(proposal) is ActionProposal
    assert proposal.kind == "wait"
    assert set(proposal.model_dump()) == {"kind", "args", "expected_outcome"}


def test_planner_package_has_no_execution_imports():
    import vrc_agent.planner as package

    for path in Path(package.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            assert not any(
                part in module.split(".")
                for module in modules
                for part in ("transport", "control", "socket", "subprocess", "os")
            )
