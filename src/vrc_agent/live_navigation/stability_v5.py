"""v5：仅保护可见像素；遮罩交集是显式不保证区，不代表已验证稳定。"""

import hashlib

from .stability import masked_rgb
from .stability_v2 import changed_flags, protected_rectangles
from .stability_v3 import black_frame

AUDIT_FIELDS = (
    "entity_rectangles",
    "protected_rectangles",
    "entity_masked_px",
    "protected_masked_px",
    "protected_visible_px",
)


def validate_rectangles(rectangles, policy):
    width, height = policy["frame_size"]
    if not isinstance(rectangles, list):
        raise ValueError("invalid visible evidence regions")
    for rect in rectangles:
        if not isinstance(rect, list) or len(rect) != 4 or any(type(v) is not int for v in rect):
            raise ValueError("invalid visible evidence regions")
        left, top, right, bottom = rect
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError("invalid visible evidence geometry")


def union_area(rectangles):
    """整数半开矩形并集面积；重叠实体、遮罩均不重复计数。"""
    xs = sorted({v for left, _, right, _ in rectangles for v in (left, right)})
    area = 0
    for x1, x2 in zip(xs, xs[1:]):
        intervals = sorted(
            (top, bottom) for left, top, right, bottom in rectangles if left < x2 and right > x1
        )
        end, height = -1, 0
        for top, bottom in intervals:
            height += max(0, bottom - max(top, end))
            end = max(end, bottom)
        area += (x2 - x1) * height
    return area


def masked_area(rectangles, policy):
    intersections = []
    for left, top, right, bottom in rectangles:
        for ml, mt, mr, mb in policy["excluded_rectangles"]:
            box = (max(left, ml), max(top, mt), min(right, mr), min(bottom, mb))
            if box[0] < box[2] and box[1] < box[3]:
                intersections.append(box)
    return union_area(intersections)


def require_visible(rectangles, policy):
    validate_rectangles(rectangles, policy)
    for left, top, right, bottom in rectangles:
        if masked_area([(left, top, right, bottom)], policy) == (right - left) * (bottom - top):
            raise ValueError("fully masked evidence")


def audit_regions(entity_rectangles, protection, policy):
    # 各矩形逐一要求有可见部分；不能用另一个实体的可见面积掩盖完全遮罩实体。
    require_visible(entity_rectangles, policy)
    require_visible(protection, policy)
    masked = masked_area(protection, policy)
    return {
        "entity_rectangles": entity_rectangles,
        "protected_rectangles": protection,
        "entity_masked_px": masked_area(entity_rectangles, policy),
        "protected_masked_px": masked,
        "protected_visible_px": union_area(protection) - masked,
    }


def audit_entities(entities, policy):
    entities = tuple(entities)
    raw = [list(r) for r in protected_rectangles(entities, {**policy, "protected_margin": 0})]
    protection = [list(r) for r in protected_rectangles(entities, policy)]
    return audit_regions(raw, protection, policy)


def verify_audit(metrics, expected):
    for key in AUDIT_FIELDS:
        value = metrics.get(key)
        if key.endswith("_px") and type(value) is not int:
            raise ValueError("invalid visible evidence count")
        if key.endswith("rectangles") and (
            not isinstance(value, list)
            or any(
                not isinstance(rect, list)
                or len(rect) != 4
                or any(type(v) is not int for v in rect)
                for rect in value
            )
        ):
            raise ValueError("invalid visible evidence regions")
        if value != expected[key]:
            raise ValueError("visible evidence audit mismatch")


def verdict(metrics, policy):
    if policy.get("protected_mask_policy") != "exclude_masked":
        raise ValueError("unknown visible protection policy")
    width, height = policy["frame_size"]
    retained = width * height - union_area(policy["excluded_rectangles"])
    if type(metrics.get("retained_pixels")) is not int or metrics["retained_pixels"] != retained:
        raise ValueError("invalid retained pixel count")
    changed = metrics.get("changed_pixels")
    if type(changed) is not int or not 0 <= changed <= retained:
        raise ValueError("invalid changed pixel count")
    if type(metrics.get("black")) is not bool or "max_tile_changed" in metrics:
        raise ValueError("invalid visible stability details")
    expected = audit_regions(
        metrics.get("entity_rectangles"), metrics.get("protected_rectangles"), policy
    )
    verify_audit(metrics, expected)
    if changed * 1_000_000 > retained * policy["max_changed_ppm"]:
        if metrics.get("protected_changed") is not None:
            raise ValueError("unexpected partial comparison details")
        return False
    protected = metrics.get("protected_changed")
    if type(protected) is not int or not 0 <= protected <= min(
        changed, expected["protected_visible_px"]
    ):
        raise ValueError("invalid protected change count")
    return not metrics["black"] and protected == 0


def compare_v5(reference, current, policy, entities=()):
    audit = audit_entities(entities, policy)
    a, b = masked_rgb(reference, policy), masked_rgb(current, policy)
    flags = changed_flags(a, b, policy["pixel_tolerance"])
    width, height = policy["frame_size"]
    retained = width * height - union_area(policy["excluded_rectangles"])
    changed = flags.count(b"\x80")
    protected = None
    if changed * 1_000_000 <= retained * policy["max_changed_ppm"]:
        protected = 0
        if audit["protected_rectangles"]:
            index = flags.find(b"\x80")
            while index >= 0:
                y, x = divmod(index, width)
                protected += any(
                    left <= x < right and top <= y < bottom
                    for left, top, right, bottom in audit["protected_rectangles"]
                )
                index = flags.find(b"\x80", index + 1)
    result = dict(
        reference_digest=reference.digest(),
        current_digest=current.digest(),
        reference_rgb_digest=hashlib.sha256(a).hexdigest(),
        current_rgb_digest=hashlib.sha256(b).hexdigest(),
        black=black_frame(a, policy["black_threshold"])
        or black_frame(b, policy["black_threshold"]),
        changed_pixels=changed,
        retained_pixels=retained,
        protected_changed=protected,
        **audit,
    )
    result["stable"] = verdict(result, policy)
    return result
