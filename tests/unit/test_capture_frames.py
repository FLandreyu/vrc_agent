"""内存帧与帧表的单元测试（T04）。"""

from __future__ import annotations

import hashlib

import pytest

from vrc_agent.capture.frames import (
    BYTES_PER_PIXEL,
    Frame,
    FrameStore,
    is_black_frame,
    max_channel_value,
)
from vrc_agent.contracts import MAX_IDENTIFIER_LENGTH


def bgra(*pixels: tuple[int, int, int, int]) -> bytes:
    return bytes(channel for pixel in pixels for channel in pixel)


def test_frame_accepts_valid_geometry() -> None:
    frame = Frame(
        ref="frame-1", data=bytes(2 * 3 * BYTES_PER_PIXEL), width=2, height=3, captured_at_ns=7
    )
    assert frame.stride == 2 * BYTES_PER_PIXEL
    assert frame.size == (2, 3)
    assert frame.captured_at_ns == 7


@pytest.mark.parametrize(
    "ref",
    [
        "a/b",
        "a\\b",
        r"C:\shots\frame.png",
        "frame 1",
        "",
        "x" * (MAX_IDENTIFIER_LENGTH + 1),
        "帧-1",
    ],
)
def test_frame_ref_cannot_be_a_file_path(ref: str) -> None:
    """frame_ref 受 Identifier 正则约束：结构上不可能是路径，也不可能是空串。"""
    with pytest.raises(ValueError):
        Frame(ref=ref, data=bytes(BYTES_PER_PIXEL), width=1, height=1, captured_at_ns=0)


@pytest.mark.parametrize("width, height", [(0, 1), (1, 0), (-1, 4), (4, -1)])
def test_frame_rejects_non_positive_size(width: int, height: int) -> None:
    with pytest.raises(ValueError):
        Frame(ref="frame-1", data=b"", width=width, height=height, captured_at_ns=0)


def test_frame_rejects_wrong_buffer_length() -> None:
    with pytest.raises(ValueError):
        Frame(ref="frame-1", data=bytes(4), width=2, height=2, captured_at_ns=0)


def test_frame_pixel_reads_bgra_in_row_major_order() -> None:
    frame = Frame(
        ref="frame-1",
        data=bgra((1, 2, 3, 255), (4, 5, 6, 255), (7, 8, 9, 0), (10, 11, 12, 0)),
        width=2,
        height=2,
        captured_at_ns=0,
    )
    assert frame.pixel(0, 0) == (1, 2, 3, 255)
    assert frame.pixel(1, 0) == (4, 5, 6, 255)
    assert frame.pixel(0, 1) == (7, 8, 9, 0)
    assert frame.pixel(1, 1) == (10, 11, 12, 0)
    with pytest.raises(IndexError):
        frame.pixel(2, 0)
    with pytest.raises(IndexError):
        frame.pixel(0, -1)


def test_frame_digest_matches_sha256_of_payload() -> None:
    data = bgra((1, 2, 3, 4))
    frame = Frame(ref="frame-1", data=data, width=1, height=1, captured_at_ns=0)
    assert frame.digest() == hashlib.sha256(data).hexdigest()


def test_max_channel_value_ignores_alpha_byte() -> None:
    """GDI 的 32bpp BI_RGB 不定义 alpha，判定黑帧时必须忽略第 4 字节。"""
    assert max_channel_value(b"") == 0
    assert max_channel_value(bgra((0, 0, 0, 255))) == 0
    assert max_channel_value(bgra((0, 0, 0, 255), (9, 0, 0, 0))) == 9


def test_black_frame_detection_boundary() -> None:
    assert is_black_frame(b"") is True
    assert is_black_frame(bgra((0, 0, 0, 255))) is True
    assert is_black_frame(bgra((8, 8, 8, 255))) is True  # 默认阈值 8 视为黑
    assert is_black_frame(bgra((9, 0, 0, 0))) is False
    assert is_black_frame(bgra((0, 0, 0, 255), (0, 40, 0, 0))) is False
    assert is_black_frame(bgra((8, 8, 8, 255)), max_channel=7) is False
    assert is_black_frame(bgra((0, 0, 30, 0)), max_channel=40) is True


def test_frame_store_rejects_invalid_capacity_and_prefix() -> None:
    with pytest.raises(ValueError):
        FrameStore(0)
    with pytest.raises(ValueError):
        FrameStore(-1)
    with pytest.raises(ValueError):
        FrameStore(2, prefix="has space")
    with pytest.raises(ValueError):
        FrameStore(2, prefix="has-dash")


def test_frame_store_put_get_drop_clear() -> None:
    store = FrameStore(4, prefix="shot")
    frame = store.put(bytes(4), 1, 1, captured_at_ns=5)
    assert frame.ref.startswith("shot-")
    assert store.get(frame.ref) is frame
    assert store.refs() == (frame.ref,)
    assert len(store) == 1
    assert store.drop(frame.ref) is True
    assert store.drop(frame.ref) is False
    assert store.get(frame.ref) is None
    store.put(bytes(4), 1, 1, captured_at_ns=6)
    store.clear()
    assert len(store) == 0


def test_frame_store_assigns_a_fresh_ref_for_every_put() -> None:
    """每次采集都是新的 ref：调用方无法用旧 ref 读回后来那一帧。"""
    store = FrameStore(4)
    refs = {store.put(bytes(4), 1, 1, captured_at_ns=n).ref for n in range(20)}
    assert len(refs) == 20


def test_frame_store_evicts_oldest_when_over_capacity() -> None:
    store = FrameStore(2)
    first = store.put(bytes(4), 1, 1, captured_at_ns=0)
    second = store.put(bytes(4), 1, 1, captured_at_ns=1)
    third = store.put(bytes(4), 1, 1, captured_at_ns=2)
    assert store.evicted == 1
    assert store.refs() == (second.ref, third.ref)
    assert store.get(first.ref) is None


def test_frame_store_ref_factory_is_deterministic() -> None:
    """回放用确定性 ref 工厂；工厂只被调用一次 per put。"""
    calls: list[int] = []

    def factory(index: int) -> str:
        calls.append(index)
        return f"replay.t04.{index + 1:04d}"

    store = FrameStore(2, ref_factory=factory)
    assert store.put(bytes(4), 1, 1, captured_at_ns=0).ref == "replay.t04.0001"
    assert store.put(bytes(4), 1, 1, captured_at_ns=1).ref == "replay.t04.0002"
    assert calls == [0, 1]


def test_frame_store_explicit_ref_does_not_consume_factory_counter() -> None:
    store = FrameStore(4, ref_factory=lambda index: f"gen-{index}")
    store.put(bytes(4), 1, 1, captured_at_ns=0, ref="pinned")
    assert store.put(bytes(4), 1, 1, captured_at_ns=1).ref == "gen-0"


def test_frame_store_copies_input_buffer() -> None:
    """登记后外部缓冲被改写，不应影响已登记的帧。"""
    buffer = bytearray(bgra((1, 2, 3, 255)))
    store = FrameStore(1)
    frame = store.put(buffer, 1, 1, captured_at_ns=0)
    buffer[0] = 200
    assert frame.pixel(0, 0) == (1, 2, 3, 255)
    assert isinstance(frame.data, bytes)
