"""v7显式候选、逐框预算/差分见证及旧版隔离。"""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_stability_v3 import entity, pair

from vrc_agent.capture.frames import Frame
from vrc_agent.live_navigation.stability import compare, load_policy
from vrc_agent.live_navigation.stability_v7 import count_rect, count_union, verdict


def evidence():
    return (
        entity().model_copy(update={"label": "home_raccoon"}),
        entity((0.2, 0.6, 0.9, 0.98)).model_copy(
            update={"label": "forward_clearance", "track_id": "floor"}
        ),
    )


def test_v7_policy_only_declared_differences():
    assert (
        load_policy("v7")[1] == "fbebd9cbfd6d11988dd783ea83b7a4e8ef38c1b3ce236e435dcd20617cb2caf7"
    )
    v6, old = load_policy("v6")
    v7, digest = load_policy("v7")
    assert old == "e46e0c2816c6f512fbd4fd5e5c60ff1ef3fc4d0560e0e8b29d3b6a6315caa513"
    assert {k for k in v6.keys() | v7.keys() if v6.get(k) != v7.get(k)} == {
        "version",
        "status",
        "zero_tolerance_labels",
        "evidence_changed_ppm",
    }
    assert v7["status"] == "REQUIRES_FIELD_CALIBRATION"
    assert v7["zero_tolerance_labels"] == ["home_raccoon"] and v7["evidence_changed_ppm"] == 1000
    assert (
        digest
        == hashlib.sha256(
            Path("src/vrc_agent/live_navigation/stability_v7.json").read_bytes()
        ).hexdigest()
    )


@pytest.mark.parametrize("case", ["floor", "target", "overlap", "masked", "delta2", "black"])
def test_per_label_pixel_rules(case):
    policy = load_policy("v7")[0]
    changes = {
        "floor": [(1000, 1000, 90)],
        "target": [(1000, 700, 90)],
        "overlap": [(1000, 850, 90)],
        "masked": [(1600, 100, 90)],
        "delta2": [(1000, 700, 22)],
        "black": [],
    }[case]
    frames = pair(value=0 if case == "black" else 20, changes=changes)
    result = compare(*frames, policy, evidence())
    assert result["stable"] == (case in {"floor", "masked", "delta2"})
    if case == "floor":
        assert not compare(*frames, load_policy("v6")[0], evidence())["stable"]
        rows = {r["label"]: r for r in result["evidence_regions"]}
        assert rows["forward_clearance"]["changed"] == 1 and rows["home_raccoon"]["changed"] == 0


@pytest.mark.parametrize("extra", [0, 1])
def test_evidence_budget_exact_integer_boundary(extra):
    policy = load_policy("v7")[0]
    empty = compare(*pair(), policy, evidence())
    floor = next(r for r in empty["evidence_regions"] if r["label"] == "forward_clearance")
    budget = floor["visible_px"] * 1000 // 1_000_000
    result = compare(
        *pair(changes=[(600 + i, 1000, 90) for i in range(budget + extra)]), policy, evidence()
    )
    assert result["stable"] == (extra == 0)


@pytest.mark.parametrize("count,stable", [(142412, True), (142413, False)])
def test_global_limit_unchanged(count, stable):
    a, _ = pair()
    data = bytearray(a.data)
    start = 600 * 2160
    data[start * 4 : (start + count) * 4] = b"\x70\x70\x70\xff" * count
    b = Frame("b", bytes(data), 2160, 1440, 2)
    result = compare(a, b, load_policy("v7")[0])
    assert result["stable"] == stable


@pytest.mark.parametrize(
    "fault", ["changed", "visible", "label", "budget", "missing", "runs", "masked_runs", "global"]
)
def test_per_box_and_witness_tampering(fault):
    policy = load_policy("v7")[0]
    result = compare(*pair(changes=[(1000, 1000, 90)]), policy, evidence())
    bad = copy.deepcopy(result)
    row = next(r for r in bad["evidence_regions"] if r["label"] == "forward_clearance")
    if fault == "changed":
        row["changed"] = 0
    elif fault == "visible":
        row["visible_px"] += 1
    elif fault == "label":
        row["label"] = "home_raccoon"
    elif fault == "budget":
        row["budget_ppm"] = 50000
    elif fault == "missing":
        del row["changed"]
    elif fault == "runs":
        bad["changed_runs"] = [[1, 2], [1, 2]]
    elif fault == "masked_runs":
        bad["changed_runs"] = [[1600, 1601]]
    else:
        bad["changed_pixels"] = 0
    with pytest.raises(ValueError):
        verdict(bad, policy)


def test_fully_masked_evidence_refused():
    with pytest.raises(ValueError, match="fully masked"):
        compare(*pair(), load_policy("v7")[0], (entity((0.75, 0.1, 0.8, 0.2)),))


@pytest.mark.parametrize("fault", ["missing", "float", "mask", "status", "unknown"])
def test_loader_rejects_rehashed_undeclared_policy(monkeypatch, fault):
    from vrc_agent.live_navigation import stability

    policy = copy.deepcopy(load_policy("v7")[0])
    if fault == "missing":
        del policy["zero_tolerance_labels"]
    elif fault == "float":
        policy["evidence_changed_ppm"] = 1000.0
    elif fault == "mask":
        policy["excluded_rectangles"] = []
    elif fault == "status":
        policy["status"] = "FROZEN"
    else:
        policy["implicit_budget"] = 0
    raw = json.dumps(policy).encode()
    root = stability.files("vrc_agent.live_navigation")

    class Resources:
        def joinpath(self, name):
            if name == "stability_v7.json":
                return SimpleNamespace(read_bytes=lambda: raw)
            if name == "stability_v7.sha256":
                return SimpleNamespace(read_text=lambda **_: hashlib.sha256(raw).hexdigest())
            return root.joinpath(name)

    monkeypatch.setattr(stability, "files", lambda _: Resources())
    with pytest.raises(ValueError, match="v7 candidate"):
        load_policy("v7")


def test_run_rectangle_counts_against_enumerated_pixels():
    runs = [[0, 2], [8, 14], [20, 29], [32, 70]]
    rectangles = [(1, 1, 7, 7), (3, 2, 9, 5)]
    pixels = {i for start, end in runs for i in range(start, end)}

    def inside(i, rect):
        y, x = divmod(i, 10)
        return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]

    for rect in rectangles:
        assert count_rect(runs, rect, 10) == sum(inside(i, rect) for i in pixels)
    assert count_union(runs, rectangles, 10) == sum(
        any(inside(i, r) for r in rectangles) for i in pixels
    )
