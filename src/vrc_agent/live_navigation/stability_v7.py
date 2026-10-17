"""v7候选：目标零容差，其他可见证据框显式面积预算，逐框差分可重算。"""

import hashlib

from .stability import masked_rgb
from .stability_v2 import changed_flags, protected_rectangles
from .stability_v3 import black_frame
from .stability_v5 import audit_entities, audit_regions, masked_area, union_area, verify_audit

REGION_KEYS = {
    "track_id",
    "label",
    "entity_rectangle",
    "protected_rectangle",
    "visible_px",
    "changed",
    "zero_tolerance",
    "budget_ppm",
}


def count_rect(runs, rect, width):
    left, top, right, bottom = rect

    def prefix(pos):
        row, col = divmod(pos, width)
        total = min(max(row - top, 0), bottom - top) * (right - left)
        if top <= row < bottom:
            total += min(max(col - left, 0), right - left)
        return total

    return sum(prefix(end) - prefix(start) for start, end in runs)


def count_union(runs, rectangles, width):
    xs = sorted({x for left, _, right, _ in rectangles for x in (left, right)})
    count = 0
    for x1, x2 in zip(xs, xs[1:]):
        intervals = sorted(
            (top, bottom) for left, top, right, bottom in rectangles if left < x2 and right > x1
        )
        end = -1
        for top, bottom in intervals:
            if bottom > max(top, end):
                count += count_rect(runs, (x1, max(top, end), x2, bottom), width)
            end = max(end, bottom)
    return count


def region_shapes(entities, policy):
    result = []
    for entity in entities:
        raw = list(protected_rectangles((entity,), {**policy, "protected_margin": 0})[0])
        protected = list(protected_rectangles((entity,), policy)[0])
        result.append(
            {
                "track_id": entity.track_id,
                "label": entity.label,
                "entity_rectangle": raw,
                "protected_rectangle": protected,
            }
        )
    return sorted(result, key=lambda r: (r["track_id"], r["label"], r["entity_rectangle"]))


def validate_runs(runs, policy):
    if not isinstance(runs, list):
        raise ValueError("missing v7 change witness")
    width, height = policy["frame_size"]
    previous = -1
    for run in runs:
        if (
            not isinstance(run, list)
            or len(run) != 2
            or any(type(v) is not int for v in run)
            or not 0 <= run[0] < run[1] <= width * height
            or run[0] <= previous
        ):
            raise ValueError("invalid v7 change runs")
        previous = run[1]
    if any(count_rect(runs, rect, width) for rect in policy["excluded_rectangles"]):
        raise ValueError("masked pixels in v7 witness")
    return sum(end - start for start, end in runs)


def verdict(metrics, policy):
    # 两个新键均为显式声明；禁止缺省或把语义作用到旧版。
    if (
        policy.get("version") != "local-rgb-stability-v7-candidate"
        or policy.get("zero_tolerance_labels") != ["home_raccoon"]
        or type(policy.get("evidence_changed_ppm")) is not int
        or policy["evidence_changed_ppm"] != 1000
    ):
        raise ValueError("invalid v7 protection declaration")
    runs, regions = metrics.get("changed_runs"), metrics.get("evidence_regions")
    changed = validate_runs(runs, policy)
    width, height = policy["frame_size"]
    retained = width * height - union_area(policy["excluded_rectangles"])
    if (
        type(metrics.get("changed_pixels")) is not int
        or metrics["changed_pixels"] != changed
        or type(metrics.get("retained_pixels")) is not int
        or metrics["retained_pixels"] != retained
        or type(metrics.get("black")) is not bool
        or not isinstance(regions, list)
        or "max_tile_changed" in metrics
    ):
        raise ValueError("invalid v7 global details")
    shapes = []
    evidence_ok = True
    for region in regions:
        if not isinstance(region, dict) or set(region) != REGION_KEYS:
            raise ValueError("invalid v7 evidence region")
        if any(
            not isinstance(region[k], str) or not 1 <= len(region[k]) <= 128
            for k in ("track_id", "label")
        ):
            raise ValueError("invalid v7 region identity")
        raw, box = region["entity_rectangle"], region["protected_rectangle"]
        audit_regions([raw], [box], policy)
        visible = (box[2] - box[0]) * (box[3] - box[1]) - masked_area([box], policy)
        count = count_rect(runs, box, width)
        zero = region["label"] in policy["zero_tolerance_labels"]
        budget = 0 if zero else policy["evidence_changed_ppm"]
        if (
            type(region["visible_px"]) is not int
            or region["visible_px"] != visible
            or type(region["changed"]) is not int
            or region["changed"] != count
            or type(region["zero_tolerance"]) is not bool
            or region["zero_tolerance"] != zero
            or type(region["budget_ppm"]) is not int
            or region["budget_ppm"] != budget
        ):
            raise ValueError("v7 per-region count or budget mismatch")
        evidence_ok &= count * 1_000_000 <= visible * budget
        shapes.append(
            {k: region[k] for k in ("track_id", "label", "entity_rectangle", "protected_rectangle")}
        )
    if shapes != sorted(shapes, key=lambda r: (r["track_id"], r["label"], r["entity_rectangle"])):
        raise ValueError("unordered v7 regions")
    raw = sorted({tuple(s["entity_rectangle"]) for s in shapes})
    boxes = sorted({tuple(s["protected_rectangle"]) for s in shapes})
    expected = audit_regions([list(r) for r in raw], [list(r) for r in boxes], policy)
    verify_audit(metrics, expected)
    protected = count_union(runs, boxes, width)
    if (
        type(metrics.get("protected_changed")) is not int
        or metrics["protected_changed"] != protected
    ):
        raise ValueError("v7 protected count mismatch")
    return (
        not metrics["black"]
        and changed * 1_000_000 <= retained * policy["max_changed_ppm"]
        and evidence_ok
    )


def compare_v7(reference, current, policy, entities=()):
    entities = tuple(entities)
    audit = audit_entities(entities, policy)
    a, b = masked_rgb(reference, policy), masked_rgb(current, policy)
    flags = changed_flags(a, b, policy["pixel_tolerance"])
    runs = []
    start = flags.find(b"\x80")
    while start >= 0:
        end = flags.find(b"\0", start)
        if end < 0:
            end = len(flags)
        runs.append([start, end])
        start = flags.find(b"\x80", end)
    regions = region_shapes(entities, policy)
    for region in regions:
        box = region["protected_rectangle"]
        zero = region["label"] in policy["zero_tolerance_labels"]
        region.update(
            visible_px=(box[2] - box[0]) * (box[3] - box[1]) - masked_area([box], policy),
            changed=count_rect(runs, box, reference.width),
            zero_tolerance=zero,
            budget_ppm=0 if zero else policy["evidence_changed_ppm"],
        )
    result = {
        "reference_digest": reference.digest(),
        "current_digest": current.digest(),
        "reference_rgb_digest": hashlib.sha256(a).hexdigest(),
        "current_rgb_digest": hashlib.sha256(b).hexdigest(),
        "black": black_frame(a, policy["black_threshold"])
        or black_frame(b, policy["black_threshold"]),
        "changed_pixels": sum(e - s for s, e in runs),
        "retained_pixels": reference.width * reference.height
        - union_area(policy["excluded_rectangles"]),
        "protected_changed": count_union(runs, audit["protected_rectangles"], reference.width),
        "changed_runs": runs,
        "evidence_regions": regions,
        **audit,
    }
    result["stable"] = verdict(result, policy)
    return result


def verify_regions(metrics, entities, policy):
    rows = metrics.get("evidence_regions")
    if not isinstance(rows, list):
        raise ValueError("missing v7 regions")
    actual = [
        {k: r.get(k) for k in ("track_id", "label", "entity_rectangle", "protected_rectangle")}
        for r in rows
    ]
    if actual != region_shapes(entities, policy):
        raise ValueError("v7 source labels or geometry mismatch")
