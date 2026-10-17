"""运行时提示明确观察契约，但不放宽或替代模型证据校验。"""

import asyncio

import pytest
from test_live_navigation import scene_for

from vrc_agent.live_navigation.skill import (
    CLEARANCE_GUIDANCE,
    CLEARANCE_GUIDANCE_VERSION,
    CalibratedSkill,
    calibrated_prompt,
)
from vrc_agent.live_navigation.world import load_live_world


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("group", ["G1", "G2", "G3", "G4", "G5"])
def test_all_effective_prompts_explain_clearance_without_fabrication(version, group):
    landmark = load_live_world(version).landmark(group)
    prompt = calibrated_prompt(landmark)
    assert prompt.endswith(CLEARANCE_GUIDANCE) and CLEARANCE_GUIDANCE_VERSION in prompt
    for text in (
        "x1<=0.35",
        "x2>=0.65",
        "y1<=0.7",
        "y2>=0.9",
        "confidence>=0.9",
        "actual current evidence_observation_id",
        "Positive example",
        "Negative examples",
        "omit forward_clearance",
        "not evidence of floor",
        "No clearance is a valid observation",
        "Do not stretch",
        "BOTH lateral short steps",
    ):
        assert text in prompt
    if getattr(landmark, "prompt_addendum", ""):
        assert "World-specific observation guidance: " + landmark.prompt_addendum in prompt


@pytest.mark.parametrize(
    "box,allowed",
    [
        ((0.1, 0.55, 0.9, 0.98), True),
        ((0.35, 0.7, 0.65, 0.9), True),
        ((0.3501, 0.55, 0.9, 0.98), False),
        ((0.1, 0.55, 0.6499, 0.98), False),
        ((0.1, 0.7001, 0.9, 0.98), False),
        ((0.1, 0.55, 0.9, 0.8999), False),
        ((0.4, 0.55, 0.6, 0.98), False),
        ((0.1, 0.2, 0.9, 0.6), False),
        ((0.1, 0.75, 0.9, 0.98), False),
    ],
)
@pytest.mark.parametrize("label", ["forward_clearance", "recovery_clearance"])
def test_prompt_examples_and_each_geometry_boundary_match_unchanged_gate(box, allowed, label):
    skill = CalibratedSkill(load_live_world("v2").landmark("G2"))
    scene = scene_for(skill, (0.41, 0.44, 0.57, 0.6))
    scene = scene.model_copy(
        update={
            "entities": tuple(
                e.model_copy(update={"bbox": box}) if e.label == label else e
                for e in scene.entities
            )
        }
    )
    assert skill.clearance(scene, label) == allowed
    if label == "forward_clearance":
        proposal = asyncio.run(skill.decide(scene, ()))
        assert (proposal.kind == "motion" and proposal.args.forward > 0) == allowed


@pytest.mark.parametrize("fault", ["missing", "low", "old", "hazard", "uncertain"])
def test_prompt_does_not_make_missing_or_untrusted_clearance_actionable(fault):
    skill = CalibratedSkill(load_live_world("v2").landmark("G2"))
    scene = scene_for(skill, (0.41, 0.44, 0.57, 0.6), floor=fault != "missing")
    if fault in {"hazard", "uncertain"}:
        scene = scene.model_copy(
            update={"hazards" if fault == "hazard" else "uncertainties": ("unsafe",)}
        )
    elif fault in {"low", "old"}:
        update = {"confidence": 0.8999} if fault == "low" else {"evidence_observation_id": "old"}
        scene = scene.model_copy(
            update={
                "entities": tuple(
                    e.model_copy(update=update) if e.label.endswith("clearance") else e
                    for e in scene.entities
                )
            }
        )
    assert not skill.clearance(scene, "forward_clearance")
    assert not skill.recovery_allowed(scene)
    proposal = asyncio.run(skill.decide(scene, ()))
    assert proposal.kind != "motion"
