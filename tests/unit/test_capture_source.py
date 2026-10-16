"""采集状态机单元测试（T04）。

这份文件承载 T04 的第一条验收条件：**采集失败绝不复用旧帧冒充新帧**。
它用一个可控的假后端把全部失败分支逐一走一遍，而不是只测 happy path。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from vrc_agent.capture import (
    CAPTURE_FAILURES,
    CaptureError,
    FrameStore,
    ObservationSource,
    WindowCaptureSource,
    WindowInfo,
)
from vrc_agent.capture.backend import OcclusionReport
from vrc_agent.capture.synthetic import render_bgra
from vrc_agent.clock import FakeClock
from vrc_agent.config import CaptureConfig
from vrc_agent.telemetry import ALLOWED_FIELDS

TITLE = "VRChat"
HWND = 0x1234
FILL = (30, 40, 50)


@dataclass
class StubBackend:
    """可控的采集后端：改一个属性就能把状态机推到某条失败分支。"""

    awareness_level: str = "per_monitor_v2"
    found: bool = True
    width: int = 8
    height: int = 4
    visible: bool = True
    iconic: bool = False
    occlusion_ratio: float = 1.0
    fill: tuple[int, int, int] = FILL
    grab_override: bytes | None = None
    info_error: CaptureError | None = None
    grab_error: BaseException | None = None
    calls: list[str] = field(default_factory=list)
    match_mode: str | None = None
    class_name_seen: str | None = None
    occlusion_samples_seen: int = 0

    # ---- CaptureBackend ----
    def awareness(self) -> str:
        self.calls.append("awareness")
        return self.awareness_level

    def find_window(self, title: str, class_name: str | None, match: str) -> int | None:
        self.calls.append("find_window")
        self.match_mode = match
        self.class_name_seen = class_name
        return HWND if self.found else None

    def window_info(self, hwnd: int) -> WindowInfo:
        self.calls.append("window_info")
        assert hwnd == HWND
        if self.info_error is not None:
            raise self.info_error
        return WindowInfo(
            hwnd=HWND,
            title=TITLE,
            class_name="UnityWndClass",
            pid=4242,
            rect=(0, 0, self.width, self.height),
            client_origin=(0, 0),
            client_size=(self.width, self.height),
            dpi=144,
            visible=self.visible,
            iconic=self.iconic,
            foreground=True,
        )

    def occlusion(self, info: WindowInfo, samples: int) -> OcclusionReport:
        self.calls.append("occlusion")
        self.occlusion_samples_seen = samples
        return OcclusionReport(samples=samples, matching=round(self.occlusion_ratio * samples))

    def grab_bgra(self, info: WindowInfo) -> bytes:
        self.calls.append("grab_bgra")
        if self.grab_error is not None:
            raise self.grab_error
        if self.grab_override is not None:
            return self.grab_override
        return render_bgra(info.width, info.height, {"type": "solid", "bgr": list(self.fill)})


@dataclass
class RecordingSink:
    events: list[tuple[str, dict]] = field(default_factory=list)

    def emit(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))


def make_source(
    backend: StubBackend | None = None,
    *,
    tweak: dict | None = None,
    config: CaptureConfig | None = None,
    clock: FakeClock | None = None,
    frames: FrameStore | None = None,
    sink: RecordingSink | None = None,
) -> tuple[WindowCaptureSource, StubBackend, FakeClock]:
    backend = backend or StubBackend()
    for name, value in (tweak or {}).items():
        # DPI 感知级别在构造时读一次，所以场景必须在构造前就位。
        setattr(backend, name, value)
    clock = clock or FakeClock()
    source = WindowCaptureSource(
        session_id="s",
        window_title=TITLE,
        backend=backend,
        clock=clock,
        config=config,
        frames=frames,
        sink=sink,
    )
    return source, backend, clock


def test_source_satisfies_the_observation_source_protocol() -> None:
    source, _, _ = make_source()
    assert isinstance(source, ObservationSource)


def test_successful_capture_yields_a_visible_observation_with_a_frame() -> None:
    source, backend, clock = make_source()
    observation = source.next_observation()

    assert observation.window_state == "visible"
    assert observation.session_id == "s"
    assert observation.captured_at_ns == clock.now_ns()
    assert observation.frame_size == (8, 4)
    assert observation.frame_ref is not None

    frame = source.frame_for(observation)
    assert frame is not None
    assert frame.size == (8, 4)
    assert frame.pixel(0, 0) == (*FILL, 255)
    assert frame.data == render_bgra(8, 4, {"type": "solid", "bgr": list(FILL)})
    assert frame.captured_at_ns == observation.captured_at_ns
    assert backend.calls.count("grab_bgra") == 1


def test_every_capture_gets_a_brand_new_frame_ref() -> None:
    source, _, _ = make_source()
    refs = [source.next_observation().frame_ref for _ in range(5)]
    assert len(set(refs)) == 5
    assert None not in refs


# --------------------------------------------------------------------------
# 验收条件 1：采集失败不复用旧帧冒充新帧
# --------------------------------------------------------------------------


FAILURE_SCENARIOS: dict[str, dict] = {
    "DPI_UNAWARE": {"awareness_level": "unaware"},
    "WINDOW_NOT_FOUND": {"found": False},
    "WINDOW_MINIMIZED": {"iconic": True},
    "WINDOW_HIDDEN": {"visible": False},
    "WINDOW_EMPTY_SIZE": {"width": 0},
    "WINDOW_TOO_LARGE": {"width": 5000},
    "WINDOW_OCCLUDED": {"occlusion_ratio": 0.0},
    "FRAME_BLACK": {"fill": (0, 0, 0)},
    "CAPTURE_FAILED": {"grab_override": b""},
}

#: DPI 场景在构造时就已生效，做不了「先成功再失败」，单独用一个测试覆盖。
NO_REUSE_SCENARIOS = {k: v for k, v in FAILURE_SCENARIOS.items() if k != "DPI_UNAWARE"}

EXPECTED_STATE: dict[str, str] = {
    "DPI_UNAWARE": "unknown",
    "WINDOW_NOT_FOUND": "lost",
    "WINDOW_MINIMIZED": "minimized",
    "WINDOW_HIDDEN": "lost",
    "WINDOW_EMPTY_SIZE": "unknown",
    "WINDOW_TOO_LARGE": "unknown",
    "WINDOW_OCCLUDED": "unknown",
    "FRAME_BLACK": "unknown",
    "CAPTURE_FAILED": "unknown",
}


def test_scenarios_cover_every_declared_failure() -> None:
    """素材或后端换实现时，跑不到的分支必须在这里暴露出来。"""
    assert set(FAILURE_SCENARIOS) == set(CAPTURE_FAILURES)
    assert set(EXPECTED_STATE) == set(CAPTURE_FAILURES)


@pytest.mark.parametrize("failure, tweak", sorted(FAILURE_SCENARIOS.items()))
def test_failure_is_reported_explicitly(failure: str, tweak: dict) -> None:
    source, _, _ = make_source(tweak=tweak)

    observation = source.next_observation()

    assert observation.window_state == EXPECTED_STATE[failure]
    assert observation.frame_ref is None
    assert observation.frame_size is None
    assert source.frame_for(observation) is None
    assert source.stats().failures == ((failure, 1),)
    assert len(source.frames) == 0


@pytest.mark.parametrize("failure, tweak", sorted(NO_REUSE_SCENARIOS.items()))
def test_failure_never_reuses_the_previous_frame(failure: str, tweak: dict) -> None:
    """先成功抓一帧，再让采集失败：失败的那条观测不得指向任何已有帧。"""
    source, backend, _ = make_source()
    good = source.next_observation()
    good_ref = good.frame_ref
    assert good_ref is not None
    held_before = source.frames.refs()

    defaults = StubBackend()
    for name, value in tweak.items():
        setattr(backend, name, value)
    bad = source.next_observation()

    assert bad.frame_ref is None
    assert bad.frame_size is None
    assert source.frames.refs() == held_before  # 帧表没被动过
    assert source.frame_for(bad) is None
    assert bad.frame_ref != good_ref  # 旧 ref 没有回流到新观测里
    # 失败之后恢复，必须是一个**新** ref，而不是复活旧 ref。
    for name in tweak:
        setattr(backend, name, getattr(defaults, name))
    recovered = source.next_observation()
    assert recovered.frame_ref not in (None, good_ref)
    assert source.frame_for(recovered) is not None


def test_unaware_dpi_capture_does_not_reuse_a_preloaded_frame() -> None:
    """帧表里已经有旧帧，采不了时必须如实报告失败，而不是把旧帧端出去。"""
    frames = FrameStore(4, prefix="stale")
    stale = frames.put(bytes(8 * 4 * 4), 8, 4, captured_at_ns=1)
    source, _, _ = make_source(tweak={"awareness_level": "unaware"}, frames=frames)

    observation = source.next_observation()

    assert observation.window_state == "unknown"
    assert observation.frame_ref is None
    assert source.stats().failures == (("DPI_UNAWARE", 1),)
    assert frames.refs() == (stale.ref,)


def test_failures_do_not_advance_the_frame_counter_or_evict_frames() -> None:
    source, backend, _ = make_source(config=CaptureConfig(frames_in_memory=2))
    refs = [source.next_observation().frame_ref for _ in range(2)]
    backend.visible = False
    for _ in range(5):
        assert source.next_observation().frame_ref is None
    stats = source.stats()
    assert stats.ok == 2
    assert stats.frames_held == 2
    assert stats.evicted_frames == 0
    assert source.frames.refs() == tuple(refs)


# --------------------------------------------------------------------------
# 判定顺序、配置开关与统计
# --------------------------------------------------------------------------


def test_dpi_awareness_is_read_once_at_construction() -> None:
    """构造时读一次是刻意设计：采集期间进程感知级别不会变。"""
    backend = StubBackend(awareness_level="unaware")
    source, _, _ = make_source(backend)
    backend.awareness_level = "per_monitor_v2"
    assert source.awareness == "unaware"
    assert source.next_observation().window_state == "unknown"
    assert source.stats().failures == (("DPI_UNAWARE", 1),)


def test_dpi_requirement_can_be_relaxed() -> None:
    backend = StubBackend(awareness_level="system")
    source, _, _ = make_source(backend, config=CaptureConfig(require_dpi_awareness=False))
    assert source.next_observation().window_state == "visible"


def test_window_is_looked_up_before_dpi_is_checked() -> None:
    """DPI 不感知时不会去枚举窗口（省一次系统调用，也不产生误导性的 not found）。"""
    backend = StubBackend(awareness_level="unaware", found=False)
    source, _, _ = make_source(backend)
    source.next_observation()
    assert "find_window" not in backend.calls


def test_minimized_wins_over_hidden() -> None:
    backend = StubBackend(iconic=True, visible=False)
    source, _, _ = make_source(backend)
    assert source.next_observation().window_state == "minimized"


def test_occlusion_can_be_disabled() -> None:
    backend = StubBackend(occlusion_ratio=0.0)
    source, _, _ = make_source(backend, config=CaptureConfig(require_unoccluded=False))
    assert source.next_observation().window_state == "visible"
    assert "occlusion" not in backend.calls


def test_occlusion_threshold_accepts_partial_matches_when_configured() -> None:
    strict, _, _ = make_source(
        tweak={"occlusion_ratio": 0.96}, config=CaptureConfig(occlusion_samples=50)
    )
    assert strict.next_observation().window_state == "unknown"  # 48/50 = 0.96 < 1.0

    relaxed, _, _ = make_source(
        tweak={"occlusion_ratio": 0.96},
        config=CaptureConfig(occlusion_samples=50, occlusion_min_ratio=0.9),
    )
    assert relaxed.next_observation().window_state == "visible"


def test_occlusion_sample_count_comes_from_config() -> None:
    backend = StubBackend()
    source, _, _ = make_source(backend, config=CaptureConfig(occlusion_samples=3))
    source.next_observation()
    assert backend.occlusion_samples_seen == 3
    assert source.stats().last_occlusion == OcclusionReport(samples=3, matching=3)


def test_window_class_is_passed_through_when_configured() -> None:
    backend = StubBackend()
    source, _, _ = make_source(
        backend, config=CaptureConfig(window_class="UnityWndClass", window_match="contains")
    )
    source.next_observation()
    assert backend.class_name_seen == "UnityWndClass"
    assert backend.match_mode == "contains"


def test_black_frame_detection_can_be_disabled() -> None:
    backend = StubBackend(fill=(0, 0, 0))
    source, _, _ = make_source(backend, config=CaptureConfig(detect_black_frames=False))
    assert source.next_observation().window_state == "visible"


def test_black_threshold_is_configurable() -> None:
    backend = StubBackend(fill=(20, 20, 20))
    strict, _, _ = make_source(backend, config=CaptureConfig(black_max_channel=30))
    assert strict.next_observation().window_state == "unknown"
    relaxed, _, _ = make_source(backend, config=CaptureConfig(black_max_channel=8))
    assert relaxed.next_observation().window_state == "visible"


def test_truncated_buffer_is_a_capture_failure() -> None:
    backend = StubBackend(grab_override=b"\x00" * 10)
    source, _, _ = make_source(backend)
    assert source.next_observation().window_state == "unknown"
    assert source.stats().failures == (("CAPTURE_FAILED", 1),)


def test_backend_can_raise_capture_error_directly() -> None:
    backend = StubBackend(info_error=CaptureError("WINDOW_NOT_FOUND", "vanished"))
    source, _, _ = make_source(backend)
    observation = source.next_observation()
    assert observation.window_state == "lost"
    assert source.stats().failures == (("WINDOW_NOT_FOUND", 1),)


def test_unexpected_backend_errors_are_not_swallowed() -> None:
    """后端抛非 CaptureError 时，异常原样逃逸，且不留任何副作用。

    CaptureError 是后台协议里约定的唯一失败通道；其他异常要么是编程错误，要么是
    环境已经不可恢复（例如 GDI 的动态库加载失败）。把它悄悄降级成「又一次采集失败」
    会让 bug 混进本来就充满失败的观测流里，所以这里刻意不吞。但「不复用旧帧」这条
    仍然成立：帧表不增长、成功计数不变、连一条半成品观测都不会产生。
    """
    backend = StubBackend(grab_error=OSError(6, "device error"))
    source, _, _ = make_source(backend)
    before = source.stats()
    with pytest.raises(OSError):
        source.next_observation()
    after = source.stats()
    assert after.attempts == before.attempts + 1
    assert after.ok == before.ok == 0
    assert after.frames_held == before.frames_held == 0
    assert dict(after.failures) == dict(before.failures) == {}
    assert source.frames.refs() == ()


def test_retain_frames_can_be_turned_off() -> None:
    """不回存原始画面时仍然如实报告尺寸，但没有任何帧引用。"""
    source, _, _ = make_source(config=CaptureConfig(retain_frames=False))
    observation = source.next_observation()
    assert observation.window_state == "visible"
    assert observation.frame_ref is None
    assert observation.frame_size is None
    assert len(source.frames) == 0


def test_frame_store_capacity_comes_from_config() -> None:
    source, _, _ = make_source(config=CaptureConfig(frames_in_memory=2))
    for _ in range(3):
        source.next_observation()
    stats = source.stats()
    assert stats.frames_held == 2
    assert stats.evicted_frames == 1


def test_resolution_change_is_counted() -> None:
    source, backend, _ = make_source()
    source.next_observation()
    assert source.stats().resolution_changes == 0
    backend.width, backend.height = 16, 8
    observation = source.next_observation()
    assert observation.frame_size == (16, 8)
    assert source.stats().resolution_changes == 1


def test_captured_at_ns_tracks_the_clock_and_never_goes_backwards() -> None:
    clock = FakeClock()
    source, _, _ = make_source(clock=clock)
    stamps = []
    for index in range(4):
        clock.advance_ms(25 * (index + 1))
        stamps.append(source.next_observation().captured_at_ns)
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == 4
    assert stamps[-1] == clock.now_ns()
    assert source.stats().last_captured_at_ns == stamps[-1]


def test_observation_ids_are_unique_and_ordered() -> None:
    source, _, _ = make_source()
    ids = [source.next_observation().observation_id for _ in range(4)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 4


def test_stats_aggregate_by_reason() -> None:
    source, backend, _ = make_source()
    source.next_observation()
    backend.visible = False
    source.next_observation()
    source.next_observation()
    backend.visible = True
    source.next_observation()
    stats = source.stats()
    assert (stats.attempts, stats.ok) == (4, 2)
    assert stats.failures == (("WINDOW_HIDDEN", 2),)
    assert stats.last_size == (8, 4)
    assert stats.as_dict()["failures"] == {"WINDOW_HIDDEN": 2}


def test_sink_only_receives_whitelisted_telemetry_fields() -> None:
    """采集失败要能写进日志，而日志只接受白名单字段。"""
    sink = RecordingSink()
    source, backend, _ = make_source(sink=sink)
    source.next_observation()  # 成功不写日志
    assert sink.events == []
    backend.iconic = True
    source.next_observation()

    assert len(sink.events) == 1
    event, fields = sink.events[0]
    assert event == "capture"
    assert set(fields) <= ALLOWED_FIELDS
    assert fields["reason_code"] == "WINDOW_MINIMIZED"
    assert fields["state"] == "minimized"
    assert fields["status"] == "failed"


def test_close_clears_frames_and_blocks_further_capture() -> None:
    source, _, _ = make_source()
    source.next_observation()
    assert len(source.frames) == 1
    source.close()
    assert len(source.frames) == 0
    with pytest.raises(RuntimeError):
        source.next_observation()


def test_context_manager_closes_the_source() -> None:
    with make_source()[0] as source:
        source.next_observation()
    assert len(source.frames) == 0
    with pytest.raises(RuntimeError):
        source.next_observation()


def test_frames_live_in_memory_only() -> None:
    """验收条件 3 的一部分：原始画面只留在内存里，没有任何落盘 API。"""
    source, _, _ = make_source()
    observations = [source.next_observation() for _ in range(3)]
    for observation in observations:
        frame = source.frame_for(observation)
        assert frame is not None
        assert len(frame.data) == 8 * 4 * 4
    assert source.frames.refs() == tuple(o.frame_ref for o in observations)
    # 采集源不提供任何「保存/导出」入口。
    public = {name for name in dir(source) if not name.startswith("_")}
    assert not {
        name for name in public if any(w in name for w in ("save", "write", "dump", "export"))
    }
