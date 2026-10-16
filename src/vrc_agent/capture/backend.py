"""采集后端接口。

`CaptureBackend` 把「找到窗口 → 看它现在什么样 → 抓一帧 BGRA」这三件事抽出来：
live 用 Win32/GDI 实现，replay 用脚本化素材实现。**同一个 WindowCaptureSource
状态机跑在两种后端上**，所以离线回放覆盖的是真实的失败判定逻辑，不是另写一套。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

WindowMatch = Literal["exact", "prefix", "contains"]

#: 进程 DPI 感知级别。"unaware"/"system" 不够用：窗口坐标会被系统虚拟化，
#: 抓到的画面是被缩放过的错误分辨率（本机 2160x1440 会被看成 1440x960）。
DpiAwareness = Literal[
    "per_monitor_v2", "per_monitor", "system", "unaware", "unknown", "unavailable"
]

#: 采集要求至少达到这一档。
AWARE_LEVELS = frozenset({"per_monitor_v2", "per_monitor"})


def matches(title: str, pattern: str, mode: WindowMatch) -> bool:
    """窗口标题匹配。大小写敏感：配置里的标题必须如实对上。"""
    if mode == "exact":
        return title == pattern
    if mode == "prefix":
        return title.startswith(pattern)
    return pattern in title


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """一次窗口状态查询的完整结果。"""

    hwnd: int
    title: str
    class_name: str
    pid: int
    #: 窗口矩形（屏幕坐标，含非客户区）。
    rect: tuple[int, int, int, int]
    #: 客户区左上角（屏幕坐标）。
    client_origin: tuple[int, int]
    #: 客户区宽高，就是采集出来的画面尺寸。
    client_size: tuple[int, int]
    dpi: int
    visible: bool
    iconic: bool
    foreground: bool

    @property
    def width(self) -> int:
        return self.client_size[0]

    @property
    def height(self) -> int:
        return self.client_size[1]

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def client_rect(self) -> tuple[int, int, int, int]:
        """客户区矩形（屏幕坐标）。遮挡采样用这个，而不是整窗矩形。"""
        left, top = self.client_origin
        return (left, top, left + self.width, top + self.height)


@dataclass(frozen=True, slots=True)
class OcclusionReport:
    """遮挡采样结果：网格点里有多少个仍然命中目标窗口。"""

    samples: int
    matching: int

    @property
    def ratio(self) -> float:
        return self.matching / self.samples if self.samples else 0.0

    def unoccluded(self, min_ratio: float = 1.0) -> bool:
        if self.samples <= 0:
            return False
        return self.ratio >= min_ratio


class CaptureBackend(Protocol):
    def awareness(self) -> str:
        """当前进程的 DPI 感知级别，取值见 win32.awareness()。"""
        ...

    def find_window(self, title: str, class_name: str | None, match: WindowMatch) -> int | None:
        """按标题（和可选的窗口类）找目标窗口；找不到返回 None。"""
        ...

    def window_info(self, hwnd: int) -> WindowInfo:
        """读取窗口当前状态。窗口已消失时抛 CaptureError(WINDOW_NOT_FOUND)。"""
        ...

    def occlusion(self, info: WindowInfo, samples: int) -> OcclusionReport:
        """按 samples x samples 网格检查客户区是否仍被目标窗口占住。"""
        ...

    def grab_bgra(self, info: WindowInfo) -> bytes:
        """抓取客户区画面。失败抛 CaptureError。"""
        ...
