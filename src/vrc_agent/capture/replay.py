"""回放源：用合成素材驱动与 live 完全相同的采集状态机。

素材描述的是**窗口当时的状态**（可见/隐藏/最小化/消失/空尺寸/超限/遮挡/抓取失败），
不是预先写好的失败结论——失败判定仍然由 WindowCaptureSource 真实执行。所以回放
覆盖的是产品代码，而不是一份平行的简化实现。

回放不循环、不填充：`next_observation()` 走完素材后抛 ReplayExhausted，宁可让调用方
看到「素材放完了」，也不重复吐旧帧。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from vrc_agent.capture.backend import OcclusionReport, WindowInfo, WindowMatch, matches
from vrc_agent.capture.errors import CaptureError, ReplayExhausted
from vrc_agent.capture.frames import Frame, FrameStore
from vrc_agent.capture.source import CaptureStats, WindowCaptureSource
from vrc_agent.capture.synthetic import default_fixture, render_bgra
from vrc_agent.clock import FakeClock
from vrc_agent.config import CaptureConfig
from vrc_agent.contracts import Observation

WINDOW_CONDITIONS = ("visible", "hidden", "minimized", "gone", "empty", "oversized")
#: 只能靠底层注入复现的失败（GDI 出错没法用「数据」表达，其余全部由状态推导）。
INJECTABLE_FAILURES = ("CAPTURE_FAILED",)
FAKE_HWND = 0x0004_2000
DEFAULT_SIZE = (64, 48)
OVERSIZED = (5000, 5000)
_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")


@dataclass(frozen=True, slots=True)
class ReplayStep:
    at_ms: int
    window: str
    size: tuple[int, int] | None
    pattern: Mapping[str, Any] | None
    occlusion_ratio: float
    failure: str | None


@dataclass(frozen=True, slots=True)
class ReplayFixture:
    name: str
    session_id: str
    window_title: str
    window_class: str
    pid: int
    dpi: int
    start_ns: int
    environment_epoch: int
    occlusion_samples: int
    steps: tuple[ReplayStep, ...]


def _require_int(value: Any, name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}, got {value!r}")
    return value


def _parse_step(raw: Any, index: int) -> ReplayStep:
    if not isinstance(raw, Mapping):
        raise ValueError(f"step {index} must be a mapping, got {raw!r}")
    at_ms = _require_int(raw.get("at_ms"), f"step {index} at_ms")
    window = str(raw.get("window", "visible"))
    if window not in WINDOW_CONDITIONS:
        raise ValueError(f"step {index} has unknown window condition {window!r}")
    failure = raw.get("failure")
    if failure is not None:
        if failure not in INJECTABLE_FAILURES:
            raise ValueError(f"step {index} cannot inject failure {failure!r}")
        failure = str(failure)
    ratio = raw.get("occlusion_ratio", 1.0)
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
        raise ValueError(f"step {index} occlusion_ratio must be a number")
    ratio = float(ratio)
    if not 0.0 <= ratio <= 1.0:
        raise ValueError(f"step {index} occlusion_ratio must be within 0..1, got {ratio}")

    size_raw = raw.get("size")
    size: tuple[int, int] | None = None
    if size_raw is not None:
        if not isinstance(size_raw, (list, tuple)) or len(size_raw) != 2:
            raise ValueError(f"step {index} size must be [width, height]")
        size = (
            _require_int(size_raw[0], f"step {index} size[0]", minimum=1),
            _require_int(size_raw[1], f"step {index} size[1]", minimum=1),
        )

    pattern_raw = raw.get("pattern")
    if pattern_raw is not None and not isinstance(pattern_raw, Mapping):
        raise ValueError(f"step {index} pattern must be a mapping")
    if window == "visible" and failure is None and pattern_raw is None:
        raise ValueError(f"step {index} is a visible step but carries no pattern")

    return ReplayStep(
        at_ms=at_ms,
        window=window,
        size=size,
        pattern=pattern_raw,
        occlusion_ratio=ratio,
        failure=failure,
    )


def parse_fixture(data: Any) -> ReplayFixture:
    if not isinstance(data, Mapping):
        raise ValueError("replay fixture must be a JSON object")
    name = str(data.get("name", "fixture"))
    if not name or any(character not in _NAME_CHARS for character in name):
        raise ValueError(f"fixture name must be a simple slug, got {name!r}")
    window = data.get("window", {})
    if not isinstance(window, Mapping):
        raise ValueError("fixture window must be a mapping")
    steps_raw = data.get("steps")
    if not isinstance(steps_raw, (list, tuple)) or not steps_raw:
        raise ValueError("fixture needs a non-empty steps list")

    steps: list[ReplayStep] = []
    previous_at_ms: int | None = None
    for index, raw in enumerate(steps_raw):
        step = _parse_step(raw, index)
        if previous_at_ms is not None and step.at_ms < previous_at_ms:
            raise ValueError(
                f"step {index} moves backwards: {step.at_ms}ms after {previous_at_ms}ms"
            )
        previous_at_ms = step.at_ms
        steps.append(step)

    samples = _require_int(data.get("occlusion_samples", 5), "occlusion_samples", minimum=1)

    return ReplayFixture(
        name=name,
        session_id=str(data.get("session_id", f"replay-{name}")),
        window_title=str(window.get("title", "VRChat")),
        window_class=str(window.get("class_name", "UnityWndClass")),
        pid=_require_int(window.get("pid", 0), "window.pid"),
        dpi=_require_int(window.get("dpi", 96), "window.dpi", minimum=1),
        start_ns=_require_int(data.get("start_ns", 1_000_000_000), "start_ns"),
        environment_epoch=_require_int(data.get("environment_epoch", 0), "environment_epoch"),
        occlusion_samples=samples,
        steps=tuple(steps),
    )


def load_fixture(path: str | Path) -> ReplayFixture:
    """从磁盘读取存档素材（tests/replay/ 下的小型 JSON）。"""
    text = Path(path).read_text(encoding="utf-8")
    return parse_fixture(json.loads(text))


class ScriptedBackend:
    """按素材逐步改变窗口状态；不碰任何真实窗口。"""

    name = "scripted"

    def __init__(self, fixture: ReplayFixture) -> None:
        self._fixture = fixture
        self._step = fixture.steps[0]

    def select(self, index: int) -> None:
        self._step = self._fixture.steps[index]

    def awareness(self) -> str:
        # 回放环境固定为「已具备 DPI 感知」，DPI 相关的失败由单独的单测覆盖。
        return "per_monitor_v2"

    def find_window(self, title: str, class_name: str | None, match: WindowMatch) -> int | None:
        if self._step.window == "gone":
            return None
        if not matches(self._fixture.window_title, title, match):
            return None
        if class_name is not None and class_name != self._fixture.window_class:
            return None
        return FAKE_HWND

    def window_info(self, hwnd: int) -> WindowInfo:
        step = self._step
        width, height = step.size or DEFAULT_SIZE
        if step.window == "empty":
            width = height = 0
        elif step.window == "oversized":
            width, height = OVERSIZED
        return WindowInfo(
            hwnd=hwnd,
            title=self._fixture.window_title,
            class_name=self._fixture.window_class,
            pid=self._fixture.pid,
            rect=(0, 0, width, height),
            client_origin=(0, 0),
            client_size=(width, height),
            dpi=self._fixture.dpi,
            visible=step.window not in ("hidden", "gone"),
            iconic=step.window == "minimized",
            foreground=step.window == "visible",
        )

    def occlusion(self, info: WindowInfo, samples: int) -> OcclusionReport:
        total = max(1, samples) ** 2
        matching = int(round(self._step.occlusion_ratio * total))
        return OcclusionReport(samples=total, matching=min(total, matching))

    def grab_bgra(self, info: WindowInfo) -> bytes:
        if self._step.failure is not None:
            raise CaptureError(self._step.failure, "scripted backend failure")  # type: ignore[arg-type]
        pattern = self._step.pattern
        if pattern is None:
            raise CaptureError("CAPTURE_FAILED", "scripted step has no pattern")
        return render_bgra(info.width, info.height, pattern)


class ReplaySource:
    """实现 ObservationSource：按素材逐步产观测，耗尽即抛 ReplayExhausted。"""

    def __init__(
        self,
        fixture: ReplayFixture,
        *,
        clock: FakeClock | None = None,
        config: CaptureConfig | None = None,
        sink: Any | None = None,
    ) -> None:
        self._fixture = fixture
        self._clock = clock or FakeClock(fixture.start_ns)
        self._config = config or CaptureConfig(occlusion_samples=fixture.occlusion_samples)
        self._backend = ScriptedBackend(fixture)
        self._source = WindowCaptureSource(
            session_id=fixture.session_id,
            window_title=fixture.window_title,
            backend=self._backend,
            clock=self._clock,
            config=self._config,
            environment_epoch=fixture.environment_epoch,
            frames=FrameStore(
                self._config.frames_in_memory,
                ref_factory=lambda index: f"replay.{fixture.name}.{index + 1:04d}",
            ),
            sink=sink,
        )
        self._index = 0
        self._previous_at_ms = 0

    # ---- ObservationSource -------------------------------------------

    def next_observation(self) -> Observation:
        if self._index >= len(self._fixture.steps):
            raise ReplayExhausted(
                f"fixture {self._fixture.name!r} is exhausted after {self._index} steps"
            )
        step = self._fixture.steps[self._index]
        self._clock.advance_ms(step.at_ms - self._previous_at_ms)
        self._previous_at_ms = step.at_ms
        self._backend.select(self._index)
        self._index += 1
        return self._source.next_observation()

    # ---- 只读视图 -----------------------------------------------------

    @property
    def fixture(self) -> ReplayFixture:
        return self._fixture

    @property
    def frames(self) -> FrameStore:
        return self._source.frames

    @property
    def remaining(self) -> int:
        return len(self._fixture.steps) - self._index

    def stats(self) -> CaptureStats:
        return self._source.stats()

    def frame_for(self, observation: Observation) -> Frame | None:
        return self._source.frame_for(observation)

    def close(self) -> None:
        self._source.close()


def observation_signature(observation: Observation, frame: Frame | None) -> dict[str, object]:
    """回放的可复现部分：去掉每轮唯一的 event_id，其余字段都应逐轮一致。"""
    return {
        "observation_id": observation.observation_id,
        "captured_at_ns": observation.captured_at_ns,
        "window_state": observation.window_state,
        "frame_ref": observation.frame_ref,
        "frame_size": list(observation.frame_size) if observation.frame_size else None,
        "frame_sha256": frame.digest() if frame is not None else None,
        "environment_epoch": observation.environment_epoch,
    }


def collect(
    source: ReplaySource, limit: int | None = None
) -> list[tuple[Observation, Frame | None]]:
    """跑完（或跑到 limit 条）素材，返回 (观测, 内存帧)。失败观测对应的帧是 None。"""
    records: list[tuple[Observation, Frame | None]] = []
    while limit is None or len(records) < limit:
        try:
            observation = source.next_observation()
        except ReplayExhausted:
            break
        records.append((observation, source.frame_for(observation)))
    return records


def sequence_digest(records: list[tuple[Observation, Frame | None]]) -> str:
    payload = [observation_signature(observation, frame) for observation, frame in records]
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def default_replay_source(**kwargs: Any) -> ReplaySource:
    return ReplaySource(parse_fixture(default_fixture()), **kwargs)
