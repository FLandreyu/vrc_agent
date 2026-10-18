"""v3 判据：全局面积上限 + 证据保护区；逐块局部判据已按现场证据移除。"""

import hashlib
from pathlib import Path

import pytest

from vrc_agent.capture.frames import Frame
from vrc_agent.contracts import Entity
from vrc_agent.live_navigation.stability import compare, intersects_mask, load_policy
from vrc_agent.live_navigation.stability_v3 import verdict

REPOSITORY = Path(__file__).resolve().parents[2]


def pair(*, value=20, changes=(), uniform=None):
    original = bytes([value, value, value, 255]) * (2160 * 1440)
    altered = bytearray(original if uniform is None else bytes([uniform] * 3 + [0]) * (2160 * 1440))
    for x, y, new in changes:
        at = (y * 2160 + x) * 4
        altered[at : at + 3] = bytes([new] * 3)
    return Frame("a", original, 2160, 1440, 1), Frame("b", bytes(altered), 2160, 1440, 2)


def entity(box=(0.4, 0.4, 0.6, 0.6)):
    return Entity(
        track_id="target",
        label="target",
        bbox=box,
        confidence=0.99,
        evidence_observation_id="obs",
        last_seen_ns=1,
    )


def sparse(count):
    """在保留区域（遮罩外）生成 count 个互不相同的像素位置。"""
    positions, x, y = [], 0, 600
    for _ in range(count):
        positions.append((x, y, 80))
        x += 3
        if x >= 1500:
            x, y = 0, y + 1
    return positions


def test_policy_integrity_and_no_local_criterion():
    policy, digest = load_policy("v3")
    assert digest == "333739e324ccc30f63e0c1ec533290dbd572090c03ef0d2be85711239d4adb5a"
    recorded = (
        (REPOSITORY / "src/vrc_agent/live_navigation/stability_v3.sha256")
        .read_text(encoding="utf-8")
        .strip()
    )
    raw = (REPOSITORY / "src/vrc_agent/live_navigation/stability_v3.json").read_bytes()
    assert recorded == digest == hashlib.sha256(raw).hexdigest()
    assert policy["version"] == "local-rgb-stability-v3-candidate"
    assert policy["metric"] == "rgb_tolerance_area_guard_v3"
    assert policy["status"] == "REQUIRES_FIELD_CALIBRATION"
    assert policy["max_changed_ppm"] == 50_000
    assert policy["excluded_rectangles"] == [[1536, 0, 2048, 512]]
    assert not {"tile_size", "tile_offset", "max_tile_changed"} & set(policy)


def test_report_version_mapping_and_config_literal():
    from vrc_agent.config import AppConfig
    from vrc_agent.live_navigation.stability_report import SCHEMAS, WARMUP_VERSIONS

    assert SCHEMAS == {"v1": 2, "v2": 3, "v3": 4, "v4": 5, "v5": 6, "v6": 9, "v7": 10, "v8": 11}
    assert WARMUP_VERSIONS == {"v2", "v3", "v4", "v5", "v6", "v7", "v8"}
    config = AppConfig(navigation_design="stable-inference-v3")
    assert config.navigation_design == "stable-inference-v3"
    assert AppConfig(navigation_design="stable-inference-v4").navigation_design == (
        "stable-inference-v4"
    )
    assert AppConfig().navigation_design == "legacy"


@pytest.mark.parametrize(
    "new,stable", [(18, True), (19, True), (20, True), (21, True), (22, True), (23, False)]
)
def test_full_frame_noise_and_cumulative_drift(new, stable):
    policy, _ = load_policy("v3")
    result = compare(*pair(uniform=new), policy)
    assert result["stable"] is stable
    assert verdict(result, policy) is stable


def test_global_area_boundary_and_unknown_partial_details():
    policy, _ = load_policy("v3")
    # 保留像素 2160×1440−512×512；5% 恰好是 142412.8 像素。
    under = compare(*pair(changes=sparse(142_412)), policy)
    assert under["retained_pixels"] == 2_848_256 and under["stable"] is True
    assert under["protected_changed"] == 0
    over = compare(*pair(changes=sparse(142_413)), policy)
    assert over["stable"] is False and over["protected_changed"] is None


def test_navigation_region_protects_one_changed_pixel():
    policy, _ = load_policy("v3")
    frames = pair(changes=[(1000, 700, 80)])
    proof = compare(*frames, policy)
    assert proof["stable"] is True and proof["protected_changed"] == 0
    guarded = compare(*frames, policy, (entity((0.4, 0.4, 0.6, 0.6)),))
    assert guarded["protected_changed"] == 1 and not guarded["stable"]


def test_protected_margin_expands_evidence_rectangle():
    policy, _ = load_policy("v3")
    box = (0.06, 0.09, 0.08, 0.12)
    inside = compare(*pair(changes=[(150, 150, 80)]), policy, (entity(box),))
    assert inside["protected_rectangles"] == [[97, 97, 205, 205]]
    assert inside["protected_changed"] == 1 and not inside["stable"]
    just_outside = compare(*pair(changes=[(95, 95, 80)]), policy, (entity(box),))
    assert just_outside["protected_changed"] == 0 and just_outside["stable"]


def test_local_block_criterion_removed_and_protection_takes_over():
    policy, _ = load_policy("v3")
    # v2 下同一处 4×4 连续变化会被单块阈值拒绝；v3 只按面积计，靠证据保护区拦截。
    frames = pair(changes=[(300 + i, 300 + j, 80) for i in range(4) for j in range(4)])
    loose = compare(*frames, policy)
    assert loose["changed_pixels"] == 16 and loose["stable"] is True
    guarded = compare(*frames, policy, (entity((0.135, 0.2, 0.145, 0.215)),))
    assert guarded["protected_changed"] == 16 and not guarded["stable"]


def test_ui_mask_covers_declared_region_only():
    policy, _ = load_policy("v3")
    inside = compare(*pair(changes=[(1600, 384, 200), (1536, 511, 200)]), policy)
    assert inside["changed_pixels"] == 0 and inside["stable"]
    boundary = compare(*pair(changes=[(1535, 100, 200)]), policy)
    assert boundary["changed_pixels"] == 1
    assert intersects_mask(entity((0.72, 0.0, 0.9, 0.4)), policy)
    assert not intersects_mask(entity((0.5, 0.4, 0.6, 0.6)), policy)


@pytest.mark.parametrize("value,stable", [(0, False), (8, False), (9, True)])
def test_black_boundary(value, stable):
    policy, _ = load_policy("v3")
    assert compare(*pair(value=value), policy)["stable"] is stable


def test_verdict_rejects_tampered_counts():
    policy, _ = load_policy("v3")
    proof = compare(*pair(changes=sparse(10)), policy)
    for field, value, message in (
        ("retained_pixels", 1, "invalid retained pixel count"),
        ("changed_pixels", -1, "invalid changed pixel count"),
        ("changed_pixels", 1.0, "invalid changed pixel count"),
        ("black", 1, "invalid black verdict"),
        ("max_tile_changed", 0, "unexpected tile comparison details"),
        ("protected_changed", 11, "invalid protected change count"),
    ):
        with pytest.raises(ValueError, match=message):
            verdict({**proof, field: value}, policy)
    rejected = compare(*pair(changes=sparse(142_413)), policy)
    with pytest.raises(ValueError, match="unexpected partial comparison details"):
        verdict({**rejected, "protected_changed": 0}, policy)
    assert verdict({**proof, "protected_changed": 1}, policy) is False
