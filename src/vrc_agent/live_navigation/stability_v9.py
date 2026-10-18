"""v9候选：各可见保护框独立ppm预算；变化位置见证供报告重新计数。"""

import hashlib
import json

from .stability import masked_rgb
from .stability_v2 import changed_flags
from .stability_v3 import black_frame
from .stability_v5 import audit_entities, audit_regions, masked_area, union_area, verify_audit
from .stability_v7 import count_rect, count_union, region_shapes, validate_runs

TOLERANCE = {
    "policy": "per_visible_region_ppm_clamped_v1",
    "max_changed_ppm": 100,
    "min_allowed_px": 1,
    "max_allowed_px": 128,
    "rounding": "ceil",
    "regions": "independent",
}
SHAPE_KEYS = {"track_id", "label", "entity_rectangle", "protected_rectangle"}
REGION_KEYS = SHAPE_KEYS | {
    "protected_visible_px",
    "protected_changed_px",
    "protected_allowed_px",
    "protected_changed_ppm",
}


def allowed_pixels(visible, policy):
    declaration = policy.get("protected_tolerance")
    if (
        policy.get("version") != "local-rgb-stability-v9-candidate"
        or json.dumps(declaration, sort_keys=True, allow_nan=False)
        != json.dumps(TOLERANCE, sort_keys=True)
        or policy.get("protected_mask_policy") != "exclude_masked"
        or type(visible) is not int
        or visible <= 0
    ):
        raise ValueError("invalid v9 tolerance declaration or visible area")
    return min(
        visible,
        declaration["max_allowed_px"],
        max(
            declaration["min_allowed_px"],
            (visible * declaration["max_changed_ppm"] + 999_999) // 1_000_000,
        ),
    )


def region_counts(shape, runs, policy):
    raw, box = shape["entity_rectangle"], shape["protected_rectangle"]
    audit_regions([raw], [box], policy)
    visible = (box[2] - box[0]) * (box[3] - box[1]) - masked_area([box], policy)
    count = count_rect(runs, box, policy["frame_size"][0])
    return {
        "protected_visible_px": visible,
        "protected_changed_px": count,
        "protected_allowed_px": allowed_pixels(visible, policy),
        "protected_changed_ppm": count * 1_000_000 / visible,
    }


def verdict(metrics, policy):
    allowed_pixels(1, policy)  # 即使预热无保护框，也必须校验显式声明。
    if metrics.get("tolerance_policy") != TOLERANCE["policy"]:
        raise ValueError("invalid v9 tolerance policy")
    runs, regions = metrics.get("changed_runs"), metrics.get("protected_regions")
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
        or any(k in metrics for k in ("max_tile_changed", "evidence_regions"))
    ):
        raise ValueError("invalid v9 global details")
    shapes, safe = [], True
    for row in regions:
        if not isinstance(row, dict) or set(row) != REGION_KEYS:
            raise ValueError("invalid v9 protected region")
        if any(
            type(row[k]) is not str or not 1 <= len(row[k]) <= 128 for k in ("track_id", "label")
        ):
            raise ValueError("invalid v9 region identity")
        shape = {k: row[k] for k in SHAPE_KEYS}
        expected = region_counts(shape, runs, policy)
        for key, value in expected.items():
            if type(row[key]) is not type(value) or row[key] != value:
                raise ValueError("v9 per-region count or budget mismatch")
        safe &= expected["protected_changed_px"] <= expected["protected_allowed_px"]
        shapes.append(shape)
    if shapes != sorted(shapes, key=lambda r: (r["track_id"], r["label"], r["entity_rectangle"])):
        raise ValueError("unordered v9 regions")
    raw = sorted({tuple(s["entity_rectangle"]) for s in shapes})
    boxes = sorted({tuple(s["protected_rectangle"]) for s in shapes})
    verify_audit(metrics, audit_regions([list(r) for r in raw], [list(r) for r in boxes], policy))
    if type(metrics.get("protected_changed")) is not int or metrics[
        "protected_changed"
    ] != count_union(runs, boxes, width):
        raise ValueError("v9 protected union count mismatch")
    return (
        not metrics["black"]
        and changed * 1_000_000 <= retained * policy["max_changed_ppm"]
        and safe
    )


def compare_v9(reference, current, policy, entities=()):
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
    rows = [{**s, **region_counts(s, runs, policy)} for s in region_shapes(entities, policy)]
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
        "protected_regions": rows,
        "tolerance_policy": TOLERANCE["policy"],
        **audit,
    }
    result["stable"] = verdict(result, policy)
    return result


def verify_regions(metrics, entities, policy):
    rows = metrics.get("protected_regions")
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("missing v9 regions")
    if [{k: r.get(k) for k in SHAPE_KEYS} for r in rows] != region_shapes(entities, policy):
        raise ValueError("v9 source labels or geometry mismatch")


def verify_prechecks(report, sources, observations, policy):
    from vrc_agent.contracts import Entity

    for proof in report["stability_prechecks"]:
        earlier = [
            obs
            for obs in observations.values()
            if obs["captured_at_ns"] < proof["reference_captured_at_ns"]
        ]
        latest = max(earlier, key=lambda obs: obs["captured_at_ns"], default=None)
        source = sources.get(latest["derived_from"]) if latest else None
        entities = (
            [Entity.model_validate({**e, "bbox": tuple(e["bbox"])}) for e in source["entities"]]
            if source
            else []
        )
        verify_regions(proof, entities, policy)
