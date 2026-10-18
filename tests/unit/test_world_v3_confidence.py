"""world v3：显式目标门槛、旧世界解释与保存数值证据的内存重放。"""

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from vrc_agent.contracts import Entity, Observation, SceneState
from vrc_agent.live_navigation.acceptance_v8 import AcceptanceSkillV8, scope_scene
from vrc_agent.live_navigation.skill import CalibratedSkill, calibrated_prompt
from vrc_agent.live_navigation.stability import load_policy
from vrc_agent.live_navigation.world import (
    LiveLandmarkV3,
    LiveWorldV3,
    load_live_world,
)

FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures/world_v3_confidence.json").read_text())
WORLD_DIGEST = "3e7b4566cfabcb6d2527c5d221b4e9d0427b542adb8d6183bc13cf90d3d9885f"


def observed(group="G2", confidence=0.7, *, version="v3", box=(0.4, 0.4, 0.6, 0.6)):
    landmark = load_live_world(version).landmark(group)
    skill = AcceptanceSkillV8(landmark, load_policy("v9")[0], profile="field")
    observation = Observation(
        session_id="memory",
        environment_epoch=1,
        observation_id="obs",
        captured_at_ns=100,
        frame_ref="memory-frame",
        frame_size=(2160, 1440),
        window_state="visible",
    )
    skill.register(observation)
    entity = Entity(
        track_id="target",
        label=landmark.label,
        bbox=box,
        confidence=confidence,
        evidence_observation_id="obs",
        last_seen_ns=100,
    )
    scene = scope_scene(
        SceneState(
            session_id="memory",
            environment_epoch=1,
            observation_ids=("obs",),
            generated_at_ns=100,
            entities=(entity,),
            confidence=0.6,
            hazards=("present",),
            uncertainties=("present",),
        ),
        landmark.label,
    )
    return skill, observation, scene


def test_candidate_exact_v2_plus_explicit_thresholds_and_original_digests():
    v1, v2, v3 = (load_live_world(v) for v in ("v1", "v2", "v3"))
    assert v1.digest() == "dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335"
    assert v2.digest() == "b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0"
    assert v3.digest() == WORLD_DIGEST
    assert v3.status == "REQUIRES_FIELD_CALIBRATION"
    assert v3.source_world_v2_digest == v2.digest()
    data = v3.model_dump(mode="json")
    for name in ("status", "source_world_v2_digest"):
        data.pop(name)
    data.update(version=v2.version, schema_version=2)
    for item in data["landmarks"]:
        assert item.pop("min_confidence") == (0.7 if item["group"] in {"G2", "G4"} else 0.85)
    assert data == v2.model_dump(mode="json")
    root = Path("src/vrc_agent/skills/worlds")
    assert (root / "vrchat_home_live_v3.sha256").read_text().strip() == WORLD_DIGEST
    assert (
        hashlib.sha256((root / "vrchat_home_live_v2.json").read_bytes()).hexdigest()
        == "98798198c6dffce329444a7be4c808e8e0046d9ca7b4270ee4897675db565276"
    )
    for version in ("v1", "v2"):
        for group in ("G1", "G2", "G3", "G4", "G5"):
            assert (
                CalibratedSkill(load_live_world(version).landmark(group)).goal.min_confidence
                == 0.85
            )
    for group in ("G1", "G2", "G3", "G4", "G5"):
        assert calibrated_prompt(v3.landmark(group)) == calibrated_prompt(v2.landmark(group))


@pytest.mark.parametrize("value", [True, False, "0.7", 1.5, -0.1, None, float("nan"), float("inf")])
def test_landmark_threshold_strict_type_and_range(value):
    data = load_live_world("v3").landmark("G2").model_dump(mode="json")
    data["min_confidence"] = value
    with pytest.raises(ValueError):
        LiveLandmarkV3.model_validate_json(json.dumps(data))
    world = load_live_world("v3").model_dump(mode="json")
    world["landmarks"][1]["min_confidence"] = value
    with pytest.raises(ValueError):
        LiveWorldV3.model_validate_json(json.dumps(world))


def test_missing_field_never_becomes_a_skill_default():
    world = load_live_world("v3").model_dump(mode="json")
    data = world["landmarks"][1]
    del data["min_confidence"]
    with pytest.raises(ValueError):
        LiveWorldV3.model_validate_json(json.dumps(world))
    with pytest.raises(ValueError):
        LiveLandmarkV3.model_validate_json(json.dumps(data))
    # 即使绕过模型校验构造坏对象，skill也不按缺字段回落到旧值。
    with pytest.raises(AttributeError):
        CalibratedSkill(LiveLandmarkV3.model_construct(**data))


@pytest.mark.parametrize("fault", ["threshold", "arrival", "prompt", "lineage", "status"])
def test_v3_contract_rejects_changes_outside_authorized_candidate(fault):
    data = load_live_world("v3").model_dump(mode="json")
    if fault == "threshold":
        data["landmarks"][0]["min_confidence"] = 0.7
    elif fault == "arrival":
        data["landmarks"][1]["arrival"]["min_width"] = 0.1
    elif fault == "prompt":
        data["landmarks"][1]["description"] = "changed"
    elif fault == "lineage":
        data["source_world_v2_digest"] = "0" * 64
    else:
        data["status"] = "APPROVED"
    with pytest.raises(ValueError):
        LiveWorldV3.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("group", ["G2", "G4"])
@pytest.mark.parametrize(
    "confidence,accepted", [(0.7, True), (0.699, False), (0.55, False), (0.72, True), (0.85, True)]
)
def test_measured_groups_threshold_boundary(group, confidence, accepted):
    skill, _, scene = observed(group, confidence)
    predicates = skill.recognition_predicates(scene)
    assert predicates["entity_confident"] is accepted
    assert all(v for k, v in predicates.items() if k != "entity_confident")
    assert (skill.select_target(scene) is not None) is accepted


@pytest.mark.parametrize(
    "version,group", [("v1", "G2"), ("v2", "G2"), ("v3", "G1"), ("v3", "G3"), ("v3", "G5")]
)
def test_legacy_and_unmeasured_groups_keep_085(version, group):
    for value, accepted in ((0.7, False), (0.82, False), (0.849, False), (0.85, True)):
        skill, _, scene = observed(group, value, version=version)
        assert skill.goal.min_confidence == 0.85
        assert (skill.select_target(scene) is not None) is accepted


@pytest.mark.parametrize(
    "fault", ["duplicate", "bbox", "hidden", "evidence", "frame", "session", "epoch", "masked"]
)
def test_other_recognition_predicates_still_fail_closed(fault):
    skill, obs, scene = observed()
    entity = scene.entities[0]
    if fault == "duplicate":
        scene = scene.model_copy(
            update={"entities": (entity, entity.model_copy(update={"track_id": "other"}))}
        )
    elif fault in {"bbox", "evidence", "masked"}:
        update = (
            {"bbox": (-0.1, 0.4, 0.6, 0.6)}
            if fault == "bbox"
            else {"evidence_observation_id": "old"}
            if fault == "evidence"
            else {"bbox": (0.75, 0.02, 0.85, 0.2)}
        )
        scene = scene.model_copy(update={"entities": (entity.model_copy(update=update),)})
    else:
        update = (
            {"window_state": "hidden"}
            if fault == "hidden"
            else {"frame_ref": None}
            if fault == "frame"
            else {"session_id": "other"}
            if fault == "session"
            else {"environment_epoch": 2}
        )
        skill.register(obs.model_copy(update=update))
    assert skill.select_target(scene) is None


def test_42_saved_observations_and_three_farther_055_are_memory_recognition_only():
    assert len(FIXTURE["observations"]) == 42
    assert Counter(x["confidence"] for x in FIXTURE["observations"]) == {0.72: 34, 0.82: 3, 0.9: 5}
    legacy_passes = Counter()
    for row in FIXTURE["observations"]:
        skill, _, scene = observed(row["group"], row["confidence"], box=tuple(row["bbox"]))
        assert all(skill.recognition_predicates(scene).values())
        assert not skill.arrival_permitted(scene)  # 有信号仍不得自动成功。
        old, _, old_scene = observed(
            row["group"], row["confidence"], version="v2", box=tuple(row["bbox"])
        )
        legacy_passes[row["group"]] += old.select_target(old_scene) is not None
    assert legacy_passes == {"G2": 5, "G4": 0}
    assert len(FIXTURE["farther_field_samples"]) == 3
    for row in FIXTURE["farther_field_samples"]:
        assert row["confidence"] == 0.55
        skill, _, scene = observed("G4", 0.55, box=tuple(row["bbox"]))
        assert not skill.recognition_predicates(scene)["entity_confident"]


def test_target_07_does_not_reduce_clearance_09_or_geometry():
    skill, _, scene = observed()
    for value, allowed in ((0.7, False), (0.899, False), (0.9, True)):
        floor = scene.entities[0].model_copy(
            update={
                "label": "forward_clearance",
                "track_id": "floor",
                "confidence": value,
                "bbox": (0.1, 0.6, 0.9, 0.98),
            }
        )
        extended = scene.model_copy(update={"entities": scene.entities + (floor,)})
        assert skill.clearance(extended, "forward_clearance") is allowed
    unsafe = floor.model_copy(update={"bbox": (0.36, 0.6, 0.9, 0.98)})
    assert not skill.clearance(
        scene.model_copy(update={"entities": scene.entities + (unsafe,)}), "forward_clearance"
    )


def test_loader_rejects_tampered_file_and_missing_v3_field(monkeypatch, tmp_path):
    from vrc_agent.live_navigation import world as module

    folder = tmp_path / "worlds"
    folder.mkdir()
    for path in Path("src/vrc_agent/skills/worlds").glob("vrchat_home_live_v*.*"):
        (folder / path.name).write_bytes(path.read_bytes())
    data = copy.deepcopy(load_live_world("v3").model_dump(mode="json"))
    monkeypatch.setattr(module, "files", lambda _: tmp_path)
    (folder / "vrchat_home_live_v3.sha256").write_text("0" * 64)
    with pytest.raises(ValueError, match="digest mismatch"):
        module.load_live_world("v3")
    del data["landmarks"][1]["min_confidence"]
    (folder / "vrchat_home_live_v3.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="min_confidence"):
        module.load_live_world("v3")
