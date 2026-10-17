"""v3 独立复核第 7 节（F1/F2/N2）修正的回归。

F1：外扩保护矩形与遮罩相交时，遮罩会把交集内的变化清零，不能再宣称保护区零变化。
F2：版本选择只能由实现类或统一工厂决定，直接给基类传新版本必须立即拒绝。
N2：报告复验对计数类型与已移除的块字段按声明严格校验。
"""

import copy
import hashlib
from pathlib import Path

import pytest
from test_stability_v3 import entity, pair, sparse

from vrc_agent.live_navigation.stability import (
    compare,
    load_policy,
    masked_protection_overlap,
    rejects_masked_protection,
)
from vrc_agent.live_navigation.stability_v2 import protected_rectangles
from vrc_agent.live_navigation.stability_v3 import verdict
from vrc_agent.live_navigation.stable_runtime import (
    STABLE_NAVIGATION_CLASSES,
    StableNavigation,
    StableNavigationV2,
    StableNavigationV3,
    StableNavigationV4,
    StableNavigationV5,
    StableNavigationV6,
    StableNavigationV7,
    stable_navigation_class,
)

REPOSITORY = Path(__file__).resolve().parents[2]
# 复核 F1 的最小反例：目标 bbox 像素 [1400,100,1520,200]，外扩后右侧进入遮罩。
MASKED_BOX = (1400 / 2160, 100 / 1440, 1520 / 2160, 200 / 1440)
MASKED_RECTANGLE = [[1368, 68, 1552, 232]]


def test_v4_is_the_declared_successor_of_v3():
    policy, digest = load_policy("v4")
    assert digest == "e4dcb100c6da68e360199593e90408806aa7753558650e4403e9497b2aefefea"
    recorded = (
        (REPOSITORY / "src/vrc_agent/live_navigation/stability_v4.sha256")
        .read_text(encoding="utf-8")
        .strip()
    )
    raw = (REPOSITORY / "src/vrc_agent/live_navigation/stability_v4.json").read_bytes()
    assert recorded == digest == hashlib.sha256(raw).hexdigest()
    assert policy["version"] == "local-rgb-stability-v4-candidate"
    assert policy["status"] == "REQUIRES_FIELD_CALIBRATION"
    assert policy["protected_mask_policy"] == "reject_on_overlap"
    # 与 v3 的差异必须只有版本名与这条保护语义，判据值一律不动。
    v3, _ = load_policy("v3")
    differences = {key for key in set(v3) | set(policy) if v3.get(key) != policy.get(key)}
    assert differences == {"version", "protected_mask_policy"}
    assert rejects_masked_protection(policy)
    assert not rejects_masked_protection(v3)


def test_masked_protection_overlap_is_rejected_instead_of_stable():
    policy, _ = load_policy("v4")
    rectangles = protected_rectangles((entity(MASKED_BOX),), policy)
    assert rectangles == [tuple(MASKED_RECTANGLE[0])]
    assert masked_protection_overlap(rectangles, policy)
    frames = pair(changes=[(1540, 150, 200)])
    with pytest.raises(ValueError, match="protected region masked"):
        compare(*frames, policy, (entity(MASKED_BOX),))
    # v3 未声明该语义，保持既有已复核行为：这正是 v3 被 v4 取代的原因（第 51 节）。
    legacy, _ = load_policy("v3")
    accepted = compare(*frames, legacy, (entity(MASKED_BOX),))
    assert accepted["stable"] is True and accepted["protected_changed"] == 0


def test_v4_verdict_rejects_masked_protection_evidence():
    policy, _ = load_policy("v4")
    proof = compare(*pair(changes=sparse(10)), policy)
    masked = {
        **proof,
        "protected_changed": 0,
        "protected_rectangles": copy.deepcopy(MASKED_RECTANGLE),
    }
    with pytest.raises(ValueError, match="protected region masked"):
        verdict(masked, policy)
    clean = {**proof, "protected_rectangles": []}
    assert verdict(clean, policy) is True
    for bad in ([[1, 2, 3]], [[1, 2, 3, "4"]], None, "x"):
        with pytest.raises(ValueError, match="invalid protected evidence regions"):
            verdict({**proof, "protected_rectangles": bad}, policy)


def test_base_class_only_accepts_v1_and_factory_owns_version_choice():
    assert StableNavigation.POLICY_VERSION == "v1"
    for version in ("v2", "v3", "v4", "v5", "v6", "v7"):
        with pytest.raises(ValueError, match="must match the navigation class"):
            StableNavigation(None, None, None, None, None, policy_version=version)
    assert STABLE_NAVIGATION_CLASSES == {
        "v1": StableNavigation,
        "v2": StableNavigationV2,
        "v3": StableNavigationV3,
        "v4": StableNavigationV4,
        "v5": StableNavigationV5,
        "v6": StableNavigationV6,
        "v7": StableNavigationV7,
    }
    for version, runtime_class in STABLE_NAVIGATION_CLASSES.items():
        assert stable_navigation_class(version) is runtime_class
        assert runtime_class.POLICY_VERSION == version
    with pytest.raises(ValueError, match="unknown stability policy version"):
        stable_navigation_class("v8")


def test_verdict_strictness_matches_the_v3_contract():
    policy, _ = load_policy("v3")
    proof = compare(*pair(changes=sparse(10)), policy)
    with pytest.raises(ValueError, match="invalid retained pixel count"):
        verdict({**proof, "retained_pixels": float(proof["retained_pixels"])}, policy)
    with pytest.raises(ValueError, match="invalid retained pixel count"):
        verdict({**proof, "retained_pixels": True}, policy)
    with pytest.raises(ValueError, match="unexpected tile comparison details"):
        verdict({**proof, "max_tile_changed": None}, policy)
    assert verdict(proof, policy) is True
