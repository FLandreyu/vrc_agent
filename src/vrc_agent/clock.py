"""可注入时钟，单元测试不依赖真实等待。"""

import time
from typing import Protocol


class Clock(Protocol):
    def now_ns(self) -> int: ...


class SystemClock:
    def now_ns(self) -> int:
        return time.monotonic_ns()


class FakeClock:
    def __init__(self, now_ns: int = 1_000_000_000):
        self.value = now_ns

    def now_ns(self) -> int:
        return self.value

    def advance_ms(self, milliseconds: int) -> None:
        if milliseconds < 0:
            raise ValueError("clock cannot move backwards")
        self.value += milliseconds * 1_000_000
