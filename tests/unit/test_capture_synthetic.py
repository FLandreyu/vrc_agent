"""确定性合成素材的单元测试（T04）。"""

from __future__ import annotations

import pytest

from vrc_agent.capture.synthetic import DEFAULT_FIXTURE, default_fixture, render_bgra

ALPHA = 255


def pixel(data: bytes, width: int, x: int, y: int) -> tuple[int, int, int, int]:
    offset = (y * width + x) * 4
    return (data[offset], data[offset + 1], data[offset + 2], data[offset + 3])


def test_solid_fills_every_pixel() -> None:
    data = render_bgra(3, 2, {"type": "solid", "bgr": [10, 20, 30]})
    assert len(data) == 3 * 2 * 4
    for y in range(2):
        for x in range(3):
            assert pixel(data, 3, x, y) == (10, 20, 30, ALPHA)


def test_gradient_x_runs_left_to_right_and_is_row_identical() -> None:
    data = render_bgra(4, 2, {"type": "gradient", "bgr": [0, 0, 0], "to_bgr": [255, 255, 255]})
    assert pixel(data, 4, 0, 0)[:3] == (0, 0, 0)
    assert pixel(data, 4, 3, 0)[:3] == (255, 255, 255)
    assert data[: 4 * 4] == data[4 * 4 :]


def test_gradient_y_differs_per_row() -> None:
    data = render_bgra(
        2, 3, {"type": "gradient", "bgr": [0, 0, 0], "to_bgr": [255, 0, 0], "axis": "y"}
    )
    assert pixel(data, 2, 0, 0)[:3] == (0, 0, 0)
    assert pixel(data, 2, 0, 2)[:3] == (255, 0, 0)
    assert pixel(data, 2, 1, 1) == pixel(data, 2, 0, 1)


def test_checker_alternates_by_tile() -> None:
    spec = {"type": "checker", "tile": 2, "bgr": [1, 1, 1], "alt_bgr": [2, 2, 2]}
    data = render_bgra(4, 4, spec)
    assert pixel(data, 4, 0, 0)[:3] == (1, 1, 1)
    assert pixel(data, 4, 2, 0)[:3] == (2, 2, 2)
    assert pixel(data, 4, 0, 2)[:3] == (2, 2, 2)
    assert pixel(data, 4, 2, 2)[:3] == (1, 1, 1)


def test_scene_paints_background_and_normalized_boxes() -> None:
    spec = {
        "type": "scene",
        "background_bgr": [5, 5, 5],
        "boxes": [{"bbox": [0.25, 0.0, 0.75, 1.0], "bgr": [9, 9, 9]}],
    }
    data = render_bgra(4, 2, spec)
    # bbox 归一化 -> 列 1..2 被填色，列 0 与 3 保持背景。
    assert [pixel(data, 4, x, 0)[:3] for x in range(4)] == [
        (5, 5, 5),
        (9, 9, 9),
        (9, 9, 9),
        (5, 5, 5),
    ]
    assert [pixel(data, 4, x, 1)[:3] for x in range(4)] == [
        (5, 5, 5),
        (9, 9, 9),
        (9, 9, 9),
        (5, 5, 5),
    ]


def test_scene_without_boxes_is_plain_background() -> None:
    data = render_bgra(2, 2, {"type": "scene", "background_bgr": [7, 7, 7]})
    assert set(data) == {7, ALPHA}


def test_rendering_is_deterministic() -> None:
    for spec in [
        {"type": "solid", "bgr": [1, 2, 3]},
        {"type": "gradient", "bgr": [1, 2, 3], "to_bgr": [200, 100, 50]},
        {"type": "checker", "tile": 3, "bgr": [1, 2, 3], "alt_bgr": [4, 5, 6]},
        {
            "type": "scene",
            "background_bgr": [1, 2, 3],
            "boxes": [{"bbox": [0.1, 0.1, 0.9, 0.9], "bgr": [8, 8, 8]}],
        },
    ]:
        assert render_bgra(9, 7, spec) == render_bgra(9, 7, spec)


def test_alpha_byte_is_always_255() -> None:
    data = render_bgra(
        3, 3, {"type": "checker", "tile": 1, "bgr": [0, 0, 0], "alt_bgr": [255, 255, 255]}
    )
    assert data[3::4] == bytes([ALPHA]) * 9


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "sphere"},
        {"type": "solid", "bgr": [1, 2]},
        {"type": "solid", "bgr": [1, 2, 300]},
        {"type": "solid", "bgr": [-1, 2, 3]},
        {"type": "solid", "bgr": [1, 2, True]},
        {"type": "solid", "bgr": [1.0, 2, 3]},
        {"type": "gradient", "bgr": [0, 0, 0], "axis": "z"},
        {"type": "checker", "tile": 0, "bgr": [1, 1, 1]},
        {"type": "scene", "background_bgr": [0, 0, 0], "boxes": [1, 2]},
        {"type": "scene", "background_bgr": [0, 0, 0], "boxes": [{"bbox": [0, 0, 1]}]},
        {"type": "scene", "background_bgr": [0, 0, 0], "boxes": [{"bbox": [0.8, 0, 0.2, 1]}]},
        {"type": "scene", "background_bgr": [0, 0, 0], "boxes": [{"bbox": [-0.1, 0, 0.5, 1]}]},
        {"type": "scene", "background_bgr": [0, 0, 0], "boxes": [{"bbox": [0, 0, 1.5, 1]}]},
    ],
)
def test_invalid_specs_raise_value_error(spec: dict) -> None:
    with pytest.raises(ValueError):
        render_bgra(4, 4, spec)


@pytest.mark.parametrize("width, height", [(0, 1), (1, 0), (-2, 2), (True, 2)])
def test_invalid_geometry_raises_value_error(width: int, height: int) -> None:
    with pytest.raises(ValueError):
        render_bgra(width, height, {"type": "solid", "bgr": [0, 0, 0]})


def test_default_fixture_is_a_deep_copy() -> None:
    fixture = default_fixture()
    fixture["steps"][0]["size"][0] = 4096
    fixture["steps"].clear()
    assert DEFAULT_FIXTURE["steps"][0]["size"] == [64, 48]
    assert len(DEFAULT_FIXTURE["steps"]) == 13
