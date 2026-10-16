"""全像素容差 + 全局/重叠局部计数 + 导航证据保护。无抽样、无新依赖。

16 位整数通道槽带 0x8000 保护位，并行比较 abs(a-b)>T；每槽中间值始终
为正且小于 65536，不发生跨槽借位。分块限制 GIL 占用和临时内存。
"""

import hashlib
import math

from .stability import masked_rgb


def changed_flags(a: bytes, b: bytes, tolerance: int) -> bytes:
    if len(a) != len(b) or len(a) % 3 or type(tolerance) is not int or not 0 <= tolerance <= 254:
        raise ValueError("invalid RGB comparison input")
    result = bytearray()
    for start in range(0, len(a), 16384 * 3):
        left, right = a[start : start + 16384 * 3], b[start : start + 16384 * 3]
        count = len(left) // 3
        lanes = bytearray(count * 2)
        unit = int.from_bytes(b"\x01\x00" * count, "little")
        guard, threshold = unit << 15, unit * (tolerance + 1)
        changed = 0
        for channel in range(3):
            lanes[::2] = left[channel::3]
            x = int.from_bytes(lanes, "little")
            lanes[::2] = right[channel::3]
            y = int.from_bytes(lanes, "little")
            changed |= (((x | guard) - y - threshold) | ((y | guard) - x - threshold)) & guard
        result.extend(changed.to_bytes(count * 2, "little")[1::2])
    return bytes(result)


def protected_rectangles(entities, policy):
    width, height = policy["frame_size"]
    margin = policy["protected_margin"]
    return sorted(
        {
            (
                max(0, math.floor(e.bbox[0] * width) - margin),
                max(0, math.floor(e.bbox[1] * height) - margin),
                min(width, math.ceil(e.bbox[2] * width) + margin),
                min(height, math.ceil(e.bbox[3] * height) + margin),
            )
            for e in entities
        }
    )


def verdict(metrics, policy):
    """由报告中的计数复验判据，不声称能用摘要重算像素。"""
    width, height = policy["frame_size"]
    retained = width * height - sum(
        (right - left) * (bottom - top)
        for left, top, right, bottom in policy["excluded_rectangles"]
    )
    if metrics.get("retained_pixels") != retained:
        raise ValueError("invalid retained pixel count")
    changed = metrics.get("changed_pixels")
    if type(changed) is not int or not 0 <= changed <= retained:
        raise ValueError("invalid changed pixel count")
    if type(metrics.get("black")) is not bool:
        raise ValueError("invalid black verdict")
    global_ok = changed * 1_000_000 <= retained * policy["max_changed_ppm"]
    if not global_ok:
        if (
            metrics.get("max_tile_changed") is not None
            or metrics.get("protected_changed") is not None
        ):
            raise ValueError("unexpected partial comparison details")
        return False
    for name in ("max_tile_changed", "protected_changed"):
        value = metrics.get(name)
        if type(value) is not int or not 0 <= value <= changed:
            raise ValueError("invalid local change count")
    if metrics["max_tile_changed"] > policy["tile_size"] ** 2:
        raise ValueError("invalid tile capacity")
    return (
        not metrics["black"]
        and metrics["max_tile_changed"] <= policy["max_tile_changed"]
        and metrics["protected_changed"] == 0
    )


def compare_v2(reference, current, policy, entities=()):
    a, b = masked_rgb(reference, policy), masked_rgb(current, policy)
    flags = changed_flags(a, b, policy["pixel_tolerance"])
    width, height = policy["frame_size"]
    retained = width * height - sum(
        (right - left) * (bottom - top)
        for left, top, right, bottom in policy["excluded_rectangles"]
    )
    changed = flags.count(b"\x80")
    rectangles = protected_rectangles(entities, policy)
    black = any(
        all(max(rgb[i : i + 65536]) <= policy["black_threshold"] for i in range(0, len(rgb), 65536))
        for rgb in (a, b)
    )
    maximum = protected = None
    if changed * 1_000_000 <= retained * policy["max_changed_ppm"]:
        maximum = protected = 0
        grids = ({}, {}, {}, {})
        size, offset = policy["tile_size"], policy["tile_offset"]
        index = flags.find(b"\x80")
        while index >= 0:
            y, x = divmod(index, width)
            protected += any(
                left <= x < right and top <= y < bottom for left, top, right, bottom in rectangles
            )
            for grid, (ox, oy) in zip(
                grids, ((0, 0), (offset, 0), (0, offset), (offset, offset)), strict=True
            ):
                key = ((x + ox) // size, (y + oy) // size)
                grid[key] = grid.get(key, 0) + 1
                maximum = max(maximum, grid[key])
            index = flags.find(b"\x80", index + 1)
    result = dict(
        reference_digest=reference.digest(),
        current_digest=current.digest(),
        reference_rgb_digest=hashlib.sha256(a).hexdigest(),
        current_rgb_digest=hashlib.sha256(b).hexdigest(),
        black=black,
        changed_pixels=changed,
        retained_pixels=retained,
        max_tile_changed=maximum,
        protected_changed=protected,
        protected_rectangles=[list(r) for r in rectangles],
    )
    result["stable"] = verdict(result, policy)
    return result
