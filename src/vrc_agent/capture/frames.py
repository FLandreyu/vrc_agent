"""内存帧存储。

原始画面**只存在于内存**：`frame_ref` 是受限引用，受 `contracts.Identifier` 正则约束，
因此不可能是一个文件路径。capture/ 下任何模块都不写文件。
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from vrc_agent.contracts import IDENTIFIER_PATTERN, MAX_IDENTIFIER_LENGTH

#: 每像素字节数：BGRA（GDI 32bpp BI_RGB，行序自上而下，stride = width * 4）。
BYTES_PER_PIXEL = 4

_FRAME_REF_RE = re.compile(IDENTIFIER_PATTERN)


@dataclass(frozen=True, slots=True)
class Frame:
    """一帧 BGRA 画面。不可变，只存在于内存。"""

    ref: str
    data: bytes
    width: int
    height: int
    captured_at_ns: int

    def __post_init__(self) -> None:
        if not _FRAME_REF_RE.match(self.ref) or len(self.ref) > MAX_IDENTIFIER_LENGTH:
            # 与 contracts.Identifier 同一条约束：ref 必须能原样放进 Observation。
            raise ValueError(
                f"frame_ref must match {IDENTIFIER_PATTERN} and be <= {MAX_IDENTIFIER_LENGTH} chars: {self.ref!r}"
            )
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"frame size must be positive: {self.width}x{self.height}")
        expected = self.width * self.height * BYTES_PER_PIXEL
        if len(self.data) != expected:
            raise ValueError(f"frame data must be {expected} bytes, got {len(self.data)}")

    @property
    def stride(self) -> int:
        return self.width * BYTES_PER_PIXEL

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def pixel(self, x: int, y: int) -> tuple[int, int, int, int]:
        """返回 (b, g, r, a)。GDI 不定义 alpha，第 4 字节仅原样带出。"""
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError(f"pixel out of range: ({x}, {y}) for {self.width}x{self.height}")
        offset = (y * self.width + x) * BYTES_PER_PIXEL
        chunk = self.data[offset : offset + BYTES_PER_PIXEL]
        return (chunk[0], chunk[1], chunk[2], chunk[3])

    def digest(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def max_channel_value(data: bytes) -> int:
    """BGRA 缓冲里 B/G/R 三个通道的最大值。

    分三个步长切片再取 max，是 C 速度的整块扫描；第 4 字节（alpha）被刻意忽略，
    因为 GDI 的 32bpp BI_RGB 不定义 alpha，可能是任意值。
    """
    if not data:
        return 0
    return max(max(data[0::4]), max(data[1::4]), max(data[2::4]))


def is_black_frame(data: bytes, max_channel: int = 8) -> bool:
    """整幅画面三个颜色通道都不超过 max_channel 即判定为黑帧。"""
    if not data:
        return True
    return max_channel_value(data) <= max_channel


class FrameStore:
    """固定容量的内存帧表。超出容量按插入顺序淘汰最旧的一帧。"""

    def __init__(
        self,
        capacity: int = 8,
        *,
        prefix: str = "frame",
        ref_factory: Callable[[int], str] | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("frame store capacity must be >= 1")
        if not prefix or not re.match(r"^[A-Za-z0-9_]+$", prefix):
            raise ValueError(f"frame ref prefix must be alphanumeric: {prefix!r}")
        self._capacity = capacity
        self._prefix = prefix
        self._ref_factory = ref_factory
        self._created = 0
        self._frames: OrderedDict[str, Frame] = OrderedDict()
        self._lock = threading.Lock()
        self.evicted = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)

    def refs(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._frames)

    def put(
        self,
        data: bytes,
        width: int,
        height: int,
        captured_at_ns: int,
        ref: str | None = None,
    ) -> Frame:
        """登记一帧并返回它。每次都分配新的 ref，调用方不能靠旧 ref 拿到新画面。"""
        if ref is None:
            ref = (
                self._ref_factory(self._created)
                if self._ref_factory is not None
                else f"{self._prefix}-{uuid4().hex}"
            )
            self._created += 1
        frame = Frame(
            ref=ref,
            data=bytes(data),
            width=width,
            height=height,
            captured_at_ns=captured_at_ns,
        )
        with self._lock:
            self._frames[frame.ref] = frame
            self._frames.move_to_end(frame.ref)
            while len(self._frames) > self._capacity:
                self._frames.popitem(last=False)
                self.evicted += 1
        return frame

    def get(self, ref: str) -> Frame | None:
        with self._lock:
            return self._frames.get(ref)

    def drop(self, ref: str) -> bool:
        with self._lock:
            return self._frames.pop(ref, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()
