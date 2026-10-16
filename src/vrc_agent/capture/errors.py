"""采集失败原因。

刻意与 `contracts.Reason` 分开：后者描述的是“动作请求为什么被拒绝”，
这里的取值只描述“这一次采集为什么拿不到可信画面”。
"""

from typing import Literal

CaptureFailure = Literal[
    "WINDOW_NOT_FOUND",
    "WINDOW_HIDDEN",
    "WINDOW_MINIMIZED",
    "WINDOW_OCCLUDED",
    "WINDOW_EMPTY_SIZE",
    "WINDOW_TOO_LARGE",
    "FRAME_BLACK",
    "CAPTURE_FAILED",
    "DPI_UNAWARE",
]

#: 顺序稳定，供测试与文档枚举；新增取值时必须同步 ARCHITECTURE。
CAPTURE_FAILURES: tuple[CaptureFailure, ...] = (
    "WINDOW_NOT_FOUND",
    "WINDOW_HIDDEN",
    "WINDOW_MINIMIZED",
    "WINDOW_OCCLUDED",
    "WINDOW_EMPTY_SIZE",
    "WINDOW_TOO_LARGE",
    "FRAME_BLACK",
    "CAPTURE_FAILED",
    "DPI_UNAWARE",
)

#: 采集失败时写入 Observation.window_state 的取值。除 visible 外都会让控制器暂停。
FAILURE_WINDOW_STATE: dict[str, Literal["visible", "lost", "minimized", "unknown"]] = {
    "WINDOW_NOT_FOUND": "lost",
    "WINDOW_HIDDEN": "lost",
    "WINDOW_MINIMIZED": "minimized",
    "WINDOW_OCCLUDED": "unknown",
    "WINDOW_EMPTY_SIZE": "unknown",
    "WINDOW_TOO_LARGE": "unknown",
    "FRAME_BLACK": "unknown",
    "CAPTURE_FAILED": "unknown",
    "DPI_UNAWARE": "unknown",
}


class CaptureError(RuntimeError):
    """底层采集失败。backends 抛出，由 WindowCaptureSource 转成显式失败观测。"""

    def __init__(self, failure: CaptureFailure, detail: str = "") -> None:
        if failure not in FAILURE_WINDOW_STATE:
            raise ValueError(f"unknown capture failure: {failure}")
        super().__init__(f"{failure}: {detail}" if detail else failure)
        self.failure = failure
        self.detail = detail


class ReplayExhausted(RuntimeError):
    """回放素材已放完。显式失败，不循环、不填充重复帧。"""
