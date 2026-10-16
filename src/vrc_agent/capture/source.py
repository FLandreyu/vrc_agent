"""窗口采集状态机。

live 与 replay 共用这一份实现：失败判定（DPI 不感知 / 窗口消失 / 隐藏 / 最小化 /
被遮挡 / 尺寸为空 / 尺寸超限 / 全黑 / GDI 失败）只写一次，回放素材驱动的是真实
逻辑，而不是另写一套简化版。

关键不变量：只有成功抓到并通过校验的画面才会登记进 FrameStore；任何失败路径都
不碰 FrameStore，返回的 Observation.frame_ref 一律为 None。也就是说「复用旧截图
冒充新帧」在这一层是结构上做不到的，不依赖调用方自觉。
"""

from __future__ import annotations

from dataclasses import dataclass

from vrc_agent.capture.backend import AWARE_LEVELS, CaptureBackend, OcclusionReport
from vrc_agent.capture.errors import FAILURE_WINDOW_STATE, CaptureError, CaptureFailure
from vrc_agent.capture.frames import Frame, FrameStore, is_black_frame
from vrc_agent.capture.protocol import EventSink
from vrc_agent.clock import Clock
from vrc_agent.config import CaptureConfig
from vrc_agent.contracts import Observation


@dataclass(frozen=True, slots=True)
class CaptureStats:
    attempts: int
    ok: int
    failures: tuple[tuple[str, int], ...]
    last_size: tuple[int, int] | None
    last_captured_at_ns: int | None
    last_occlusion: OcclusionReport | None
    resolution_changes: int
    evicted_frames: int
    frames_held: int

    def as_dict(self) -> dict[str, object]:
        return {
            "attempts": self.attempts,
            "ok": self.ok,
            "failures": dict(self.failures),
            "last_size": list(self.last_size) if self.last_size else None,
            "last_captured_at_ns": self.last_captured_at_ns,
            "last_occlusion": (
                None
                if self.last_occlusion is None
                else {
                    "samples": self.last_occlusion.samples,
                    "matching": self.last_occlusion.matching,
                    "ratio": round(self.last_occlusion.ratio, 4),
                }
            ),
            "resolution_changes": self.resolution_changes,
            "evicted_frames": self.evicted_frames,
            "frames_held": self.frames_held,
        }


class WindowCaptureSource:
    """实现 ObservationSource：每次调用产出一条新的 Observation。"""

    def __init__(
        self,
        *,
        session_id: str,
        window_title: str,
        backend: CaptureBackend,
        clock: Clock,
        config: CaptureConfig | None = None,
        environment_epoch: int = 0,
        frames: FrameStore | None = None,
        sink: EventSink | None = None,
    ) -> None:
        self._config = config or CaptureConfig()
        self._session_id = session_id
        self._window_title = window_title
        self._backend = backend
        self._clock = clock
        self._environment_epoch = environment_epoch
        self._sink = sink
        # 注意用 is None 判断：FrameStore 定义了 __len__，空表是 falsy 的。
        self._frames = frames if frames is not None else FrameStore(self._config.frames_in_memory)
        self._sequence = 0
        self._attempts = 0
        self._ok = 0
        self._failures: dict[str, int] = {}
        self._last_size: tuple[int, int] | None = None
        self._last_captured_at_ns: int | None = None
        self._last_occlusion: OcclusionReport | None = None
        self._resolution_changes = 0
        self._closed = False
        self._awareness = backend.awareness()

    # ---- 对外只读视图 -------------------------------------------------

    @property
    def frames(self) -> FrameStore:
        return self._frames

    @property
    def awareness(self) -> str:
        return self._awareness

    @property
    def window_title(self) -> str:
        return self._window_title

    def stats(self) -> CaptureStats:
        return CaptureStats(
            attempts=self._attempts,
            ok=self._ok,
            failures=tuple(sorted(self._failures.items())),
            last_size=self._last_size,
            last_captured_at_ns=self._last_captured_at_ns,
            last_occlusion=self._last_occlusion,
            resolution_changes=self._resolution_changes,
            evicted_frames=self._frames.evicted,
            frames_held=len(self._frames),
        )

    def frame_for(self, observation: Observation) -> Frame | None:
        """取回某条观测引用的内存帧；失败观测返回 None。"""
        if observation.frame_ref is None:
            return None
        return self._frames.get(observation.frame_ref)

    # ---- ObservationSource -------------------------------------------

    def next_observation(self) -> Observation:
        if self._closed:
            raise RuntimeError("capture source is closed")
        self._attempts += 1
        try:
            return self._capture()
        except CaptureError as exc:
            return self._fail(exc.failure)

    def close(self) -> None:
        """关闭后不再采集，并丢弃内存里的原始画面。"""
        self._closed = True
        self._frames.clear()

    def __enter__(self) -> WindowCaptureSource:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---- 内部 ---------------------------------------------------------

    def _capture(self) -> Observation:
        config = self._config

        if config.require_dpi_awareness and self._awareness not in AWARE_LEVELS:
            raise CaptureError("DPI_UNAWARE", self._awareness)

        hwnd = self._backend.find_window(
            self._window_title, config.window_class, config.window_match
        )
        if hwnd is None:
            raise CaptureError("WINDOW_NOT_FOUND", self._window_title)

        info = self._backend.window_info(hwnd)
        if info.iconic:
            raise CaptureError("WINDOW_MINIMIZED", "window is minimized")
        if not info.visible:
            raise CaptureError("WINDOW_HIDDEN", "window is not visible")
        if info.width <= 0 or info.height <= 0:
            raise CaptureError("WINDOW_EMPTY_SIZE", f"{info.width}x{info.height}")
        if (
            info.width > config.max_width
            or info.height > config.max_height
            or info.area > config.max_pixels
        ):
            raise CaptureError("WINDOW_TOO_LARGE", f"{info.width}x{info.height}")

        if config.require_unoccluded:
            report = self._backend.occlusion(info, config.occlusion_samples)
            self._last_occlusion = report
            if not report.unoccluded(config.occlusion_min_ratio):
                raise CaptureError(
                    "WINDOW_OCCLUDED", f"{report.matching}/{report.samples} samples still on target"
                )

        # 时间戳在抓取**之前**读取：画面内容对应抓取期间的状态，按更早的时刻标注只会让观测显得更旧，
        # 控制层因此更保守；反过来会用抓取耗时把旧证据算成新的。
        now = self._clock.now_ns()
        data = self._backend.grab_bgra(info)
        expected = info.width * info.height * 4
        if len(data) != expected:
            raise CaptureError("CAPTURE_FAILED", f"expected {expected} bytes, got {len(data)}")
        if config.detect_black_frames and is_black_frame(data, config.black_max_channel):
            raise CaptureError("FRAME_BLACK", f"max channel <= {config.black_max_channel}")

        size = (info.width, info.height)
        if self._last_size is not None and self._last_size != size:
            self._resolution_changes += 1
        self._last_size = size
        self._last_captured_at_ns = now
        self._ok += 1

        frame_ref: str | None = None
        frame_size: tuple[int, int] | None = None
        if config.retain_frames:
            frame = self._frames.put(data, size[0], size[1], now)
            frame_ref = frame.ref
            frame_size = size

        return self._observation(
            captured_at_ns=now,
            window_state="visible",
            frame_ref=frame_ref,
            frame_size=frame_size,
        )

    def _fail(self, failure: CaptureFailure) -> Observation:
        state = FAILURE_WINDOW_STATE[failure]
        count = self._failures.get(failure, 0) + 1
        self._failures[failure] = count
        if self._sink is not None:
            self._sink.emit(
                "capture",
                operation="capture",
                status="failed",
                state=state,
                reason_code=failure,
                count=count,
            )
        return self._observation(
            captured_at_ns=self._clock.now_ns(),
            window_state=state,
            frame_ref=None,
            frame_size=None,
        )

    def _observation(
        self,
        *,
        captured_at_ns: int,
        window_state: str,
        frame_ref: str | None,
        frame_size: tuple[int, int] | None,
    ) -> Observation:
        self._sequence += 1
        return Observation(
            session_id=self._session_id,
            observation_id=f"obs-{self._sequence:06d}",
            captured_at_ns=captured_at_ns,
            environment_epoch=self._environment_epoch,
            frame_ref=frame_ref,
            frame_size=frame_size,
            window_state=window_state,
        )
