"""live 采集源工厂：把 Win32 后端接到共用的采集状态机上。"""

from __future__ import annotations

from vrc_agent.capture.protocol import EventSink
from vrc_agent.capture.source import WindowCaptureSource
from vrc_agent.capture.win32 import Win32CaptureBackend, ensure_dpi_awareness
from vrc_agent.clock import Clock, SystemClock
from vrc_agent.config import AppConfig


def live_capture(
    *,
    session_id: str,
    config: AppConfig,
    clock: Clock | None = None,
    environment_epoch: int = 0,
    sink: EventSink | None = None,
) -> WindowCaptureSource:
    """构造真实窗口采集源。窗口标题默认继承 control.window_title。"""
    # 必须先提升 DPI 感知再构造后端：状态机会在构造时读一次感知级别。
    ensure_dpi_awareness()
    return WindowCaptureSource(
        session_id=session_id,
        window_title=config.capture_window_title(),
        backend=Win32CaptureBackend(),
        clock=clock or SystemClock(),
        config=config.capture,
        environment_epoch=environment_epoch,
        sink=sink,
    )
