"""逐框ppm边界、真实变化、掩码与严格重算；仅合成内存帧。"""

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from test_stability_v3 import entity, pair

from vrc_agent.capture.frames import Frame
from vrc_agent.live_navigation import stability
from vrc_agent.live_navigation.stability import compare, load_policy
from vrc_agent.live_navigation.stability_v9 import (
    TOLERANCE,
    allowed_pixels,
    verdict,
    verify_regions,
)

DIGEST = "990a1d7de902318f4ed04715adaa7bfd71c9c806c30fb90e82ad37a142ceef0e"


def target(box=(0.4, 0.4, 0.6, 0.6), track="target"):
    return entity(box).model_copy(update={"label": "home_raccoon", "track_id": track})


def changes(count, x=1000, y=700):
    return [(x + i, y, 80) for i in range(count)]


def test_candidate_has_only_new_protection_semantics_and_old_statuses_unchanged():
    old, old_digest = load_policy("v8")
    policy, digest = load_policy("v9")
    assert old_digest == "11b98adff4003ee62d40f3a6c96960f50d0d5c2d743a264f2f9dd14af2680cae"
    assert policy == {
        **old,
        "version": "local-rgb-stability-v9-candidate",
        "status": "REQUIRES_FIELD_CALIBRATION",
        "protected_tolerance": TOLERANCE,
    }
    path = Path("src/vrc_agent/live_navigation/stability_v9.json")
    assert digest == DIGEST == hashlib.sha256(path.read_bytes()).hexdigest()
    assert path.with_suffix(".sha256").read_bytes() == (digest + "\r\n").encode()
    for i in range(1, 9):
        previous, h = load_policy(f"v{i}")
        raw = path.with_name(f"stability_v{i}.json").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == h
        assert previous["status"] == ("FROZEN" if i in (5, 6, 8) else "REQUIRES_FIELD_CALIBRATION")


@pytest.mark.parametrize(
    "status,ok",
    [
        ("FROZEN", True),
        ("REQUIRES_FIELD_CALIBRATION", True),
        ("other", False),
        (None, False),
        (1, False),
        (True, False),
        (["FROZEN"], False),
    ],
)
def test_status_closed_set_and_strict_candidate_declaration(monkeypatch, tmp_path, status, ok):
    root = Path("src/vrc_agent/live_navigation")
    for i in range(1, 10):
        for suffix in ("json", "sha256"):
            shutil.copyfile(
                root / f"stability_v{i}.{suffix}", tmp_path / f"stability_v{i}.{suffix}"
            )
    policy, _ = load_policy("v9")
    policy["status"] = status

    def write():
        raw = json.dumps(policy).encode()
        (tmp_path / "stability_v9.json").write_bytes(raw)
        (tmp_path / "stability_v9.sha256").write_text(
            hashlib.sha256(raw).hexdigest(), encoding="ascii"
        )

    monkeypatch.setattr(stability, "files", lambda _: tmp_path)
    write()
    if ok:
        assert load_policy("v9")[0]["status"] == status
        policy["protected_tolerance"]["max_changed_ppm"] = 100.0
        write()
    with pytest.raises(ValueError, match="invalid v9"):
        load_policy("v9")


@pytest.mark.parametrize(
    "area,allowed",
    [
        (1, 1),
        (100, 1),
        (20000, 2),
        (29999, 3),
        (30000, 3),
        (60000, 6),
        (57525, 6),
        (126036, 13),
        (1280000, 128),
        (3000000, 128),
    ],
)
def test_explicit_ceil_minimum_and_maximum(area, allowed):
    assert allowed_pixels(area, load_policy("v9")[0]) == allowed


@pytest.mark.parametrize("delta", [0, 1])
def test_each_region_allowed_and_allowed_plus_one(delta):
    policy, _ = load_policy("v9")
    clean = compare(*pair(), policy, (target(),))
    budget = clean["protected_regions"][0]["protected_allowed_px"]
    proof = compare(*pair(changes=changes(budget + delta)), policy, (target(),))
    assert proof["protected_regions"][0]["protected_changed_px"] == budget + delta
    assert proof["changed_pixels"] < proof["retained_pixels"] * 0.05
    assert proof["stable"] is (delta == 0)
    assert verdict(proof, policy) is (delta == 0)


@pytest.mark.parametrize("count", [0, 1, 2])
def test_target_alone_zero_and_one_two_pixel_noise(count):
    policy, _ = load_policy("v9")
    proof = compare(*pair(changes=changes(count)), policy, (target(),))
    assert proof["stable"] and proof["protected_changed"] == count
    assert proof["protected_regions"][0]["protected_changed_px"] == count
    assert (
        proof["protected_regions"][0]["protected_changed_ppm"]
        == count * 1e6 / proof["protected_visible_px"]
    )


def test_two_regions_cannot_pool_or_hide_small_target_in_large_floor():
    policy, _ = load_policy("v9")
    small = target()
    large = target((0.02, 0.75, 0.98, 0.98), track="large")
    rows = compare(*pair(), policy, (small, large))["protected_regions"]
    allowed = next(r["protected_allowed_px"] for r in rows if r["track_id"] == "target")
    proof = compare(*pair(changes=changes(allowed + 1)), policy, (small, large))
    assert (
        sum(r["protected_allowed_px"] for r in proof["protected_regions"])
        > proof["protected_changed"]
    )
    assert not proof["stable"]
    verify_regions(proof, (small, large), policy)
    with pytest.raises(ValueError, match="source labels or geometry"):
        verify_regions(proof, (large,), policy)


def test_41811_outside_target_is_preexisting_global_tolerance_and_1202965_rejected():
    policy, _ = load_policy("v9")
    original = bytes([20, 20, 20, 255]) * (2160 * 1440)
    before = Frame("a", original, 2160, 1440, 1)
    for count, expected in ((41811, True), (1202965, False)):
        data = bytearray(original)
        remaining = count - 1
        for y in range(1440):
            for left, right in ((0, 800), (1400, 1500)):
                n = min(remaining, right - left)
                at = (y * 2160 + left) * 4
                data[at : at + n * 4] = bytes([80, 80, 80, 255]) * n
                remaining -= n
            if remaining == 0:
                break
        assert remaining == 0
        at = (700 * 2160 + 1000) * 4
        data[at : at + 4] = bytes([80, 80, 80, 255])
        proof = compare(
            before,
            Frame("b", bytes(data), 2160, 1440, 2),
            policy,
            (target((0.455, 0.375, 0.515, 0.535)),),
        )
        assert proof["protected_visible_px"] == 57525
        assert proof["changed_pixels"] == count and proof["protected_changed"] == 1
        assert proof["stable"] is expected


def test_exact_ppm_boundary_from_witness():
    policy, _ = load_policy("v9")
    from vrc_agent.live_navigation.stability_v5 import audit_regions

    box = [400, 600, 600, 1100]  # 100000可见像素，100ppm恰好10px。
    for count, expected in ((10, True), (11, False)):
        runs = [[700 * 2160 + 450, 700 * 2160 + 450 + count]]
        proof = {
            **audit_regions([box], [box], policy),
            "black": False,
            "changed_pixels": count,
            "retained_pixels": 2848256,
            "changed_runs": runs,
            "protected_changed": count,
            "tolerance_policy": TOLERANCE["policy"],
            "protected_regions": [
                {
                    "track_id": "x",
                    "label": "home_raccoon",
                    "entity_rectangle": box,
                    "protected_rectangle": box,
                    "protected_visible_px": 100000,
                    "protected_changed_px": count,
                    "protected_allowed_px": 10,
                    "protected_changed_ppm": float(count * 10),
                }
            ],
        }
        assert verdict(proof, policy) is expected


@pytest.mark.parametrize(
    "field",
    [
        "protected_visible_px",
        "protected_changed_px",
        "protected_allowed_px",
        "protected_changed_ppm",
    ],
)
def test_witness_recomputes_every_region_field(field):
    policy, _ = load_policy("v9")
    proof = compare(*pair(changes=changes(2)), policy, (target(),))
    bad = copy.deepcopy(proof)
    bad["protected_regions"][0][field] += 1
    with pytest.raises(ValueError):
        verdict(bad, policy)
    del bad["protected_regions"][0][field]
    with pytest.raises(ValueError):
        verdict(bad, policy)


def test_mask_visibility_black_dimension_and_delta_unchanged():
    policy, _ = load_policy("v9")
    for box in ((0.72, 0.01, 0.9, 0.2),):
        with pytest.raises(ValueError, match="fully masked"):
            compare(*pair(), policy, (target(box),))
    # 冻结exclude_masked语义：部分交叠仍排除遮罩像素；不改成v4的reject_on_overlap。
    partial = target((0.65, 0.05, 0.73, 0.2))
    proof = compare(*pair(changes=[(1540, 150, 80)]), policy, (partial,))
    assert proof["stable"] and proof["protected_masked_px"] > 0
    assert proof["protected_regions"][0]["protected_visible_px"] == proof["protected_visible_px"]
    assert not compare(*pair(value=8), policy)["stable"]
    assert compare(*pair(uniform=22), policy, (target(),))["stable"]
    assert not compare(*pair(uniform=23), policy, (target(),))["stable"]
    with pytest.raises(ValueError, match="frame mismatch"):
        compare(Frame("a", b"1234", 1, 1, 1), pair()[1], policy)


def test_partial_mask_uses_this_regions_visible_denominator_and_hidden_region_cannot_borrow():
    policy, _ = load_policy("v9")
    partial = target((0.65, 0.05, 0.73, 0.2))
    proof = compare(*pair(), policy, (partial,))
    row = proof["protected_regions"][0]
    box = row["protected_rectangle"]
    whole = (box[2] - box[0]) * (box[3] - box[1])
    assert row["protected_visible_px"] == whole - proof["protected_masked_px"]
    assert row["protected_allowed_px"] < (whole * 100 + 999999) // 1000000
    count = row["protected_allowed_px"] + 1
    changed = compare(*pair(changes=changes(count, x=1400, y=150)), policy, (partial,))
    assert not changed["stable"]
    assert changed["protected_regions"][0]["protected_changed_px"] == count
    fully_masked = target((0.75, 0.05, 0.85, 0.2), track="hidden")
    with pytest.raises(ValueError, match="fully masked evidence"):
        compare(*pair(), policy, (target(), fully_masked))
