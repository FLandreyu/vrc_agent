import pytest

from vrc_agent.capture.frames import Frame
from vrc_agent.contracts import Entity
from vrc_agent.live_navigation.stability import compare, intersects_mask, load_policy
from vrc_agent.live_navigation.stability_v2 import changed_flags, verdict


@pytest.mark.parametrize("tolerance", [0, 1, 2, 8, 254])
def test_integer_lanes_against_exhaustive_scalar_oracle(tolerance):
    pairs = [(x, y) for x in range(256) for y in range(256)]
    a = bytes(c for x, _ in pairs for c in (x, (x + 39) % 256, 255 - x))
    b = bytes(c for _, y in pairs for c in (y, (y + 39) % 256, 255 - y))
    expected = bytes(
        128 if max(abs(a[i + c] - b[i + c]) for c in range(3)) > tolerance else 0
        for i in range(0, len(a), 3)
    )
    assert changed_flags(a, b, tolerance) == expected


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


@pytest.mark.parametrize(
    "new,stable", [(18, True), (19, True), (20, True), (21, True), (22, True), (23, False)]
)
def test_full_frame_noise_and_cumulative_drift(new, stable):
    policy, _ = load_policy("v2")
    result = compare(*pair(uniform=new), policy)
    assert result["stable"] is stable
    assert verdict(result, policy) is stable


@pytest.mark.parametrize("xy", [(300, 300), (31, 31), (1279, 639)])
def test_small_object_and_mirror_cluster_even_across_tile_boundary(xy):
    x, y = xy
    policy, _ = load_policy("v2")
    result = compare(
        *pair(changes=[(x + i, y + j, 80) for i in range(4) for j in range(4)]), policy
    )
    assert result["changed_pixels"] == 16
    assert result["max_tile_changed"] == 16
    assert result["stable"] is False


def test_navigation_region_protects_one_changed_pixel():
    policy, _ = load_policy("v2")
    frames = pair(changes=[(1000, 700, 80)])
    assert compare(*frames, policy)["stable"] is True  # 明确局部阈值以下的剩余边界。
    proof = compare(*frames, policy, (entity(),))
    assert proof["protected_changed"] == 1 and not proof["stable"]


def test_distributed_half_percent_noise_and_global_excess():
    policy, _ = load_policy("v2")
    sparse = [(x, y, 80) for x in range(0, 2160, 16) for y in range(0, 1440, 16)]
    result = compare(*pair(changes=sparse), policy)
    assert result["stable"] and result["changed_pixels"] > 10000
    dense = [(x, y, 80) for x in range(0, 2160, 8) for y in range(0, 1440, 8)]
    result = compare(*pair(changes=dense), policy)
    assert not result["stable"] and result["max_tile_changed"] is None


def test_new_ui_region_and_old_mask_no_longer_excluded():
    policy, _ = load_policy("v2")
    proof = compare(*pair(changes=[(1700, 100, 200)]), policy)
    assert proof["changed_pixels"] == 0 and proof["stable"]
    assert intersects_mask(entity((0.7, 0.0, 0.9, 0.5)), policy)
    proof = compare(
        *pair(changes=[(10 + i, 1400 + j, 80) for i in range(4) for j in range(4)]), policy
    )
    assert not proof["stable"]


@pytest.mark.parametrize("value,stable", [(0, False), (8, False), (9, True)])
def test_black_boundary(value, stable):
    policy, _ = load_policy("v2")
    assert compare(*pair(value=value), policy)["stable"] is stable
