"""v5 的部分交叠是声明放行、不是漏判；可见区域仍严格保护。"""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from test_stability_v3 import entity, pair

from vrc_agent.live_navigation.stability import compare, load_policy
from vrc_agent.live_navigation.stability_v5 import audit_entities, union_area, verdict

MASKED_MARGIN = (1400 / 2160, 100 / 1440, 1520 / 2160, 200 / 1440)


def test_v5_version_hash_thresholds_and_old_behavior():
    policy, digest = load_policy("v5")
    assert digest == "1cc4287a4c944ae898012e016ee21b591041f1eb7000041844312c3ec02de21c"
    v4, _ = load_policy("v4")
    assert {k for k in set(v4) | set(policy) if v4.get(k) != policy.get(k)} == {
        "version",
        "metric",
        "protected_mask_policy",
    }
    a, b = pair(changes=[(1540, 150, 90)])
    # 原 F1 反例：v5 声明放行；v4 仍拒绝，v3 保持原结果。
    proof = compare(a, b, policy, (entity(MASKED_MARGIN),))
    assert proof["stable"] and proof["changed_pixels"] == 0
    assert proof["protected_masked_px"] == 16 * 164
    assert proof["protected_visible_px"] == 184 * 164 - 16 * 164
    with pytest.raises(ValueError, match="protected region masked"):
        compare(a, b, v4, (entity(MASKED_MARGIN),))
    assert compare(a, b, load_policy("v3")[0], (entity(MASKED_MARGIN),))["stable"]


def test_partial_overlap_visible_change_still_rejected():
    policy, _ = load_policy("v5")
    proof = compare(
        *pair(changes=[(1450, 150, 90), (1540, 150, 90)]), policy, (entity(MASKED_MARGIN),)
    )
    assert not proof["stable"] and proof["protected_changed"] == proof["changed_pixels"] == 1


@pytest.mark.parametrize(
    "box",
    [
        (1600 / 2160, 100 / 1440, 1700 / 2160, 200 / 1440),
        # 原 bbox 完全遮罩，即使外扩后有可见部分也拒绝。
        (1536 / 2160, 100 / 1440, 1540 / 2160, 200 / 1440),
    ],
)
def test_fully_masked_raw_entity_rejected(box):
    with pytest.raises(ValueError, match="fully masked evidence"):
        compare(*pair(), load_policy("v5")[0], (entity(box),))


def test_union_audit_deduplicates_overlapping_boxes():
    policy, _ = load_policy("v5")
    first, second = (
        entity(MASKED_MARGIN),
        entity((1410 / 2160, 110 / 1440, 1520 / 2160, 200 / 1440)),
    )
    audit = audit_entities((first, second, first), policy)
    assert audit["protected_masked_px"] == 2624
    assert audit["protected_visible_px"] == 27552
    assert union_area([[0, 0, 10, 10], [5, 5, 15, 15]]) == 175
    assert union_area([]) == 0
    # 不能用一个干净实体的面积补偿另一个完全遮罩实体。
    with pytest.raises(ValueError, match="fully masked"):
        audit_entities((first, entity((0.75, 0.1, 0.8, 0.2))), policy)


@pytest.mark.parametrize(
    "key,value",
    [
        ("protected_masked_px", 0),
        ("protected_masked_px", 2624.0),
        ("entity_masked_px", True),
        ("protected_visible_px", -1),
        ("entity_rectangles", [[10, 10, 5, 5]]),
        ("protected_rectangles", [[-1, 0, 1, 1]]),
    ],
)
def test_v5_audit_tampering_rejected(key, value):
    policy, _ = load_policy("v5")
    metrics = compare(*pair(), policy, (entity(MASKED_MARGIN),))
    with pytest.raises(ValueError):
        verdict({**metrics, key: value}, policy)


def test_field_five_frame_box_table_replay():
    fixture = Path(__file__).parents[1] / "fixtures/t08_v5_field_boxes.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    assert data["replay_scope"] == "model_boxes_only_not_image_replay"
    policy, _ = load_policy("v5")
    assert len(data["cases"]) == 7
    for case in data["cases"]:
        if not case["goal_found"]:
            assert case["group"] == "G3" and case["expected"] == "NO_TARGET"
            from vrc_agent.contracts import Observation
            from vrc_agent.perception.scene import build_scene
            from vrc_agent.planner import NavigationGoal, target

            scene = build_scene(
                {"entities": [], "uncertainties": ["wooden frame not visible"]},
                Observation(session_id="field-box-replay", captured_at_ns=1, environment_epoch=0),
                generated_at_ns=2,
            ).scene
            assert scene.confidence is None
            assert target(scene, NavigationGoal(label="home_avatar_mirror")) is None
            continue  # 缺目标仍缺目标；不得创造一个框来宣称到达。
        result = compare(*pair(), policy, (entity(tuple(case["bbox"])),))
        assert result["stable"] and case["expected"] == "GEOMETRY_ADMISSIBLE_ONLY"
    # 数据来源可追溯；素材可移机，未要求 .runtime 工件存在于其他执行者机器。
    source = Path(data["source"])
    if source.exists():
        assert hashlib.sha256(source.read_bytes()).hexdigest() == data["source_sha256"]


def test_v5_verdict_global_short_circuit_and_empty_warmup():
    policy, _ = load_policy("v5")
    proof = compare(*pair(), policy)
    assert proof["protected_masked_px"] == proof["protected_visible_px"] == 0
    proof.update(changed_pixels=142413, protected_changed=None)
    assert not verdict(proof, policy)
    bad = copy.deepcopy(proof)
    bad["protected_changed"] = 0
    with pytest.raises(ValueError):
        verdict(bad, policy)
