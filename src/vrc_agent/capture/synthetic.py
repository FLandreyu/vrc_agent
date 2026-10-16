"""确定性合成画面。

回放素材不是真实会话的录屏（那涉及隐私与授权），而是按规格当场渲染：同一份规格
在任何机器、任何次数下渲染出的字节完全一致，所以回放可以逐帧复现。

渲染出来的第 4 字节固定为 255；真实 GDI 帧的 alpha 是未定义值，两者都不参与
黑帧判定（判定只看 B/G/R 三个通道）。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

Bgr = tuple[int, int, int]
Pattern = Mapping[str, Any]

_ALPHA = 255


def _color(spec: Mapping[str, Any], key: str, default: Bgr = (0, 0, 0)) -> Bgr:
    raw = spec.get(key, default)
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ValueError(f"{key} must be a 3-element BGR list, got {raw!r}")
    values: list[int] = []
    for component in raw:
        if not isinstance(component, int) or isinstance(component, bool):
            raise ValueError(f"{key} components must be integers, got {raw!r}")
        if not 0 <= component <= 255:
            raise ValueError(f"{key} components must be within 0..255, got {raw!r}")
        values.append(component)
    return (values[0], values[1], values[2])


def _pixel(color: Bgr) -> bytes:
    return bytes((color[0], color[1], color[2], _ALPHA))


def _mix(start: Bgr, end: Bgr, t: float) -> bytes:
    return bytes(
        (
            round(start[0] + (end[0] - start[0]) * t),
            round(start[1] + (end[1] - start[1]) * t),
            round(start[2] + (end[2] - start[2]) * t),
            _ALPHA,
        )
    )


def _positive(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return value


def _box(spec: Mapping[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    raw = spec.get("bbox")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ValueError(f"bbox must be 4 normalized numbers, got {raw!r}")
    x1, y1, x2, y2 = (float(value) for value in raw)
    if not (0.0 <= x1 < x2 <= 1.0 and 0.0 <= y1 < y2 <= 1.0):
        raise ValueError(f"bbox must satisfy 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1: {raw!r}")
    return (
        min(width, int(x1 * width)),
        min(height, int(y1 * height)),
        min(width, max(int(x1 * width) + 1, int(x2 * width + 0.5))),
        min(height, max(int(y1 * height) + 1, int(y2 * height + 0.5))),
    )


def render_bgra(width: int, height: int, spec: Pattern) -> bytes:
    """把一份图案规格渲染成 BGRA 字节（行序自上而下，stride = width * 4）。"""
    width = _positive(width, "width")
    height = _positive(height, "height")
    kind = str(spec.get("type", "solid"))

    if kind == "solid":
        return _pixel(_color(spec, "bgr")) * (width * height)

    if kind == "gradient":
        start = _color(spec, "bgr")
        end = _color(spec, "to_bgr", start)
        axis = str(spec.get("axis", "x"))
        if axis == "x":
            row = bytearray()
            for x in range(width):
                row += _mix(start, end, x / max(1, width - 1))
            return bytes(row) * height
        if axis == "y":
            return b"".join(_mix(start, end, y / max(1, height - 1)) * width for y in range(height))
        raise ValueError(f"gradient axis must be 'x' or 'y', got {axis!r}")

    if kind == "checker":
        tile = _positive(spec.get("tile", 8), "tile")
        first = _color(spec, "bgr")
        second = _color(spec, "alt_bgr", first)
        rows = bytearray()
        for y in range(height):
            for x in range(width):
                rows += _pixel(first if ((x // tile) + (y // tile)) % 2 == 0 else second)
        return bytes(rows)

    if kind == "scene":
        background = _color(spec, "background_bgr")
        boxes: Sequence[Any] = spec.get("boxes", ())
        if not isinstance(boxes, (list, tuple)):
            raise ValueError("scene boxes must be a list")
        buffer = bytearray(_pixel(background) * (width * height))
        for entry in boxes:
            if not isinstance(entry, Mapping):
                raise ValueError(f"scene box must be a mapping, got {entry!r}")
            left, top, right, bottom = _box(entry, width, height)
            fill = _pixel(_color(entry, "bgr"))
            for y in range(top, bottom):
                offset = (y * width + left) * 4
                buffer[offset : offset + (right - left) * 4] = fill * (right - left)
        return bytes(buffer)

    raise ValueError(f"unknown pattern type: {kind!r}")


#: 内置回放素材：一条会话里依次出现正常帧、分辨率变化、黑屏、最小化、窗口消失、
#: 遮挡、GDI 失败、隐藏、空尺寸、尺寸超限，最后恢复。它同时是
#: `vrc-agent replay` 的默认输入，并被 tests/replay/ 下的存档素材逐字对齐。
#:
#: environment_epoch 取 1：控制器每次 arm() 都会把 epoch 加一（DISARMED 时是 0），
#: 所以「一次已启用的会话」对应 epoch 1。采集源必须用当时的 epoch 标注观测，否则
#: Controller.observe() 会直接拒收——回放素材要和真实会话对齐，不能自带一个假 epoch。
DEFAULT_FIXTURE: dict[str, Any] = {
    "name": "t04_smoke",
    "session_id": "replay-t04",
    "environment_epoch": 1,
    "start_ns": 1_000_000_000,
    "occlusion_samples": 5,
    "window": {
        "title": "VRChat",
        "class_name": "UnityWndClass",
        "pid": 4242,
        "dpi": 144,
    },
    "steps": [
        {
            "at_ms": 0,
            "window": "visible",
            "size": [64, 48],
            "pattern": {
                "type": "gradient",
                "bgr": [10, 20, 40],
                "to_bgr": [200, 180, 120],
                "axis": "x",
            },
        },
        {
            "at_ms": 150,
            "window": "visible",
            "size": [64, 48],
            "pattern": {
                "type": "scene",
                "background_bgr": [30, 30, 30],
                "boxes": [{"bbox": [0.25, 0.25, 0.75, 0.75], "bgr": [60, 200, 90]}],
            },
        },
        {
            "at_ms": 300,
            "window": "visible",
            "size": [96, 72],
            "pattern": {
                "type": "checker",
                "tile": 8,
                "bgr": [50, 50, 200],
                "alt_bgr": [200, 50, 50],
            },
        },
        {
            "at_ms": 450,
            "window": "visible",
            "size": [96, 72],
            "pattern": {"type": "solid", "bgr": [0, 0, 0]},
        },
        {"at_ms": 600, "window": "minimized"},
        {"at_ms": 750, "window": "gone"},
        {
            "at_ms": 900,
            "window": "visible",
            "size": [96, 72],
            "pattern": {
                "type": "checker",
                "tile": 4,
                "bgr": [10, 10, 10],
                "alt_bgr": [240, 240, 240],
            },
        },
        {
            "at_ms": 1050,
            "window": "visible",
            "size": [96, 72],
            "pattern": {"type": "solid", "bgr": [90, 90, 90]},
            "occlusion_ratio": 0.6,
        },
        {
            "at_ms": 1200,
            "window": "visible",
            "size": [96, 72],
            "pattern": {"type": "solid", "bgr": [90, 90, 90]},
            "failure": "CAPTURE_FAILED",
        },
        {"at_ms": 1350, "window": "hidden"},
        {"at_ms": 1500, "window": "empty"},
        {"at_ms": 1650, "window": "oversized"},
        {
            "at_ms": 1800,
            "window": "visible",
            "size": [64, 48],
            "pattern": {
                "type": "gradient",
                "bgr": [200, 120, 40],
                "to_bgr": [20, 60, 200],
                "axis": "y",
            },
        },
    ],
}


def default_fixture() -> dict[str, Any]:
    """返回内置素材的深拷贝，避免调用方改到模块级常量。"""
    import copy

    return copy.deepcopy(DEFAULT_FIXTURE)
