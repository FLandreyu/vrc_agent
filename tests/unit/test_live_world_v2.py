"""世界 v2：新参考/尺寸/提示与旧世界报告隔离；不推断现场成功。"""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from test_live_navigation import scene_for

from vrc_agent.cli import main
from vrc_agent.live_navigation.skill import CalibratedSkill, calibrated_prompt
from vrc_agent.live_navigation.world import LiveWorld, LiveWorldV2, load_live_world

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = "b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0"


def test_versioned_world_and_exact_allowed_changes():
    v1, v2 = load_live_world(), load_live_world("v2")
    assert type(v1) is LiveWorld and type(v2) is LiveWorldV2
    assert v1.digest() == "dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335"
    assert v2.digest() == EXPECTED
    assert v2.source_world_digest == v1.digest()
    assert v1.tasks == v2.tasks and len(v2.tasks) == 20
    before, after = v1.model_dump(mode="json"), v2.model_dump(mode="json")
    assert {k for k in set(before) | set(after) if before.get(k) != after.get(k)} == {
        "schema_version",
        "version",
        "source_world_digest",
        "calibration_source",
        "calibration_source_sha256",
        "landmarks",
    }
    for group in ("G1", "G2", "G3", "G4", "G5"):
        old = v1.landmark(group).model_dump(mode="json")
        new = v2.landmark(group).model_dump(mode="json")
        addendum = new.pop("prompt_addendum")
        if group == "G3":
            assert {k for k in old if old[k] != new[k]} == {
                "arrival",
                "arrival_reference",
                "arrival_reference_sha256",
            }
            old_rule, new_rule = old["arrival"], new["arrival"]
            assert {k for k in old_rule if old_rule[k] != new_rule[k]} == {
                "min_width",
                "min_height",
            }
            assert (
                new_rule["kind"] == "box"
                and new_rule["min_width"] == 0.5
                and new_rule["min_height"] == 0.52
            )
        elif group == "G5":
            assert {k for k in old if old[k] != new[k]} == {
                "arrival_reference",
                "arrival_reference_sha256",
            }
            assert old["arrival"] == new["arrival"]
        else:
            assert new == old and not addendum


def test_world_v2_digest_sidecar_and_source_slice():
    world = load_live_world("v2")
    root = ROOT / "src/vrc_agent/skills/worlds"
    assert (root / "vrchat_home_live_v2.sha256").read_text(encoding="ascii").strip() == EXPECTED
    assert (
        hashlib.sha256((root / "vrchat_home_live_v2.json").read_bytes()).hexdigest()
        == "98798198c6dffce329444a7be4c808e8e0046d9ca7b4270ee4897675db565276"
    )
    raw = (ROOT / "docs/validation/T08_stability_v5_field.md").read_bytes()
    section = raw[raw.index(b"## 2.") : raw.index(b"## 4.")]
    assert hashlib.sha256(section).hexdigest() == world.calibration_source_sha256


@pytest.mark.parametrize(
    "group,name,digest",
    [
        (
            "G3",
            "clean_g3_preactivation2_20261017_115715.png",
            "41b2d90236fa8fb8b79ba2cf699275539626e6ecf5bfb5b4296ce7cba0c09a6b",
        ),
        (
            "G5",
            "clean_g5_pattern_20261017_115903.png",
            "51a7858833c66280a2fae457f4af4220ef5246c2ffbbcf0d17efafd36d27145c",
        ),
    ],
)
def test_new_reference_hashes(group, name, digest):
    landmark = load_live_world("v2").landmark(group)
    assert landmark.arrival_reference == name and landmark.arrival_reference_sha256 == digest
    path = ROOT / ".runtime/tmp/probe_frames" / name
    if path.exists():  # 原始照片按现场隐私约定不入库，换机器可无照片。
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("fault", ["missing_task", "predecessor", "bad_schema", "bad_version"])
def test_v2_contract_rejects_wrong_lineage_or_schema(fault):
    data = load_live_world("v2").model_dump(mode="json")
    if fault == "missing_task":
        data["tasks"].pop()
    elif fault == "predecessor":
        data["source_world_digest"] = "0" * 64
    elif fault == "bad_schema":
        data["schema_version"] = 1
    else:
        data["version"] = "vrchat-home-live-v1"
    with pytest.raises(ValueError):
        LiveWorldV2.model_validate_json(json.dumps(data))


def test_unknown_world_never_becomes_a_path():
    for name in ("v4", "../v1", ""):
        with pytest.raises(ValueError, match="unknown live world"):
            load_live_world(name)


def test_tampered_world_file_rejected_by_digest(monkeypatch, tmp_path):
    import vrc_agent.live_navigation.world as module

    folder = tmp_path / "worlds"
    folder.mkdir()
    source = ROOT / "src/vrc_agent/skills/worlds"
    for name in (
        "vrchat_home_live_v1.json",
        "vrchat_home_live_v1.sha256",
        "vrchat_home_live_v2.json",
        "vrchat_home_live_v2.sha256",
    ):
        (folder / name).write_bytes((source / name).read_bytes())
    data = json.loads((folder / "vrchat_home_live_v2.json").read_text(encoding="utf-8"))
    data["landmarks"][2]["arrival"]["min_width"] = 0.1
    (folder / "vrchat_home_live_v2.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(module, "files", lambda _: tmp_path)
    with pytest.raises(ValueError, match="digest mismatch"):
        module.load_live_world("v2")


def test_g3_boundaries_and_field_boxes_are_size_only_evidence():
    old = CalibratedSkill(load_live_world().landmark("G3"))
    new = CalibratedSkill(load_live_world("v2").landmark("G3"))
    assert new.arrived(scene_for(new, (0.215, 0.205, 0.755, 0.775)).entities[0])
    assert not old.arrived(scene_for(old, (0.215, 0.205, 0.755, 0.775)).entities[0])
    for box in (
        (0.25, 0.24, 0.75, 0.76),
        (0.251, 0.24, 0.749, 0.76),
        (0.25, 0.241, 0.75, 0.759),
        (0.45, 0.24, 0.95, 0.76),
    ):
        result = new.arrived(scene_for(new, box).entities[0])
        assert result == (box == (0.25, 0.24, 0.75, 0.76))


def test_g5_lower_half_formula_unchanged_and_uncertainty_blocks_motion():
    skill = CalibratedSkill(load_live_world("v2").landmark("G5"))
    scene = scene_for(skill, (0.09, 0.42, 0.93, 0.89))
    assert skill.arrived(scene.entities[0])
    # 产品按画面下半部面积归一化，现场记录的0.328是全画面面积（四舍五入）。
    assert (0.93 - 0.09) * (0.89 - 0.5) / 0.5 == pytest.approx(0.6552)
    uncertain = scene.model_copy(update={"uncertainties": ("pattern may be water",)})
    assert asyncio.run(skill.decide(uncertain, ())).kind == "wait"


def test_prompt_addenda_only_affect_v2_g3_g5():
    old, new = load_live_world(), load_live_world("v2")
    for group in ("G1", "G2", "G4"):
        assert calibrated_prompt(old.landmark(group)) == calibrated_prompt(new.landmark(group))
    g3 = calibrated_prompt(new.landmark("G3"))
    assert "Before activation" in g3 and "omit the target" in g3
    assert "After activation" in g3 and "Do not infer an occluded frame boundary" in g3
    assert "Before activation" not in calibrated_prompt(old.landmark("G3"))
    g5 = calibrated_prompt(new.landmark("G5"))
    assert "white-outlined nested diamond motif itself" in g5
    assert "Exclude the pool surface" in g5 and "Never claim arrival" in g5
    assert "white-outlined" not in calibrated_prompt(old.landmark("G5"))


def test_cli_explicit_world_plan_and_summary(capsys, tmp_path):
    assert main(["navigate-live", "--task", "G3-1", "--world-version", "v2"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["mode"] == "plan-only" and plan["sends_live_input"] is False
    assert plan["suite_digest"] == EXPECTED and plan["world"]["version"] == "vrchat-home-live-v2"
    assert main(["navigation-report", "--world-version", "v2", "--report-dir", str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["suite_digest"] == EXPECTED and report["successes"] == 0
