"""T04 窗口采集与回放。

- `WindowCaptureSource`：唯一的采集状态机，live 与 replay 共用。
- `Win32CaptureBackend`（capture.win32）：真实窗口/GDI 采集，仅 Windows。
- `ReplaySource`：用确定性合成素材驱动同一个状态机。

隐私与安全：本模块只在内存里持有原始画面，**不写任何文件**；`frame_ref` 受
`contracts.Identifier` 约束，不可能是文件路径；任何采集失败都不会产出可用的帧引用。
"""

from vrc_agent.capture.backend import (
    AWARE_LEVELS,
    CaptureBackend,
    DpiAwareness,
    OcclusionReport,
    WindowInfo,
    WindowMatch,
    matches,
)
from vrc_agent.capture.errors import CAPTURE_FAILURES, CaptureError, CaptureFailure, ReplayExhausted
from vrc_agent.capture.frames import Frame, FrameStore, is_black_frame, max_channel_value
from vrc_agent.capture.protocol import EventSink, ObservationSource
from vrc_agent.capture.replay import (
    ReplayFixture,
    ReplaySource,
    ReplayStep,
    collect,
    default_replay_source,
    load_fixture,
    observation_signature,
    parse_fixture,
    sequence_digest,
)
from vrc_agent.capture.source import CaptureStats, WindowCaptureSource
from vrc_agent.capture.synthetic import DEFAULT_FIXTURE, default_fixture, render_bgra

__all__ = [
    "AWARE_LEVELS",
    "CAPTURE_FAILURES",
    "CaptureBackend",
    "CaptureError",
    "CaptureFailure",
    "CaptureStats",
    "DEFAULT_FIXTURE",
    "DpiAwareness",
    "EventSink",
    "Frame",
    "FrameStore",
    "ObservationSource",
    "OcclusionReport",
    "ReplayExhausted",
    "ReplayFixture",
    "ReplaySource",
    "ReplayStep",
    "WindowCaptureSource",
    "WindowInfo",
    "WindowMatch",
    "collect",
    "default_fixture",
    "default_replay_source",
    "is_black_frame",
    "load_fixture",
    "matches",
    "max_channel_value",
    "observation_signature",
    "parse_fixture",
    "render_bgra",
    "sequence_digest",
]
