"""T04 契约交叉验证：capture/replay 产出的观测必须能被严格契约与控制层直接接收。

这份文件刻意**不复用** capture 包内部的判定表，而是把期望值独立写死一遍（哪一步该
是什么 window_state、该记哪条失败原因），再拿产品代码的真实输出对照。回放素材一旦
被改动，这里会先炸——这正是契约测试该有的样子。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vrc_agent.capture.protocol import ObservationSource
from vrc_agent.capture.replay import ReplaySource, collect, default_replay_source, parse_fixture
from vrc_agent.capture.synthetic import default_fixture
from vrc_agent.clock import FakeClock
from vrc_agent.config import ControlConfig
from vrc_agent.contracts import ActionRequest, Observation, new_id
from vrc_agent.control.core import Controller
from vrc_agent.transport.osc import RecordingTransport

#: 每一步的期望结果：(window_state, 该步应记入统计的失败原因或 None)。
#: 与 src/vrc_agent/capture/synthetic.py 的 DEFAULT_FIXTURE 一一对应。
EXPECTED_STEPS: tuple[tuple[str, str | None], ...] = (
    ("visible", None),  # 0 正常渐变帧
    ("visible", None),  # 1 正常场景帧
    ("visible", None),  # 2 分辨率变化后仍是正常帧
    ("unknown", "FRAME_BLACK"),  # 3 纯黑帧 → 拒绝，不复用上一帧
    ("minimized", "WINDOW_MINIMIZED"),  # 4 最小化
    ("lost", "WINDOW_NOT_FOUND"),  # 5 窗口消失
    ("visible", None),  # 6 恢复
    ("unknown", "WINDOW_OCCLUDED"),  # 7 被遮挡
    ("unknown", "CAPTURE_FAILED"),  # 8 GDI 抓取失败
    ("lost", "WINDOW_HIDDEN"),  # 9 隐藏
    ("unknown", "WINDOW_EMPTY_SIZE"),  # 10 空尺寸
    ("unknown", "WINDOW_TOO_LARGE"),  # 11 尺寸超限
    ("visible", None),  # 12 再次恢复
)

EXPECTED_FAILURES = {
    "FRAME_BLACK": 1,
    "WINDOW_MINIMIZED": 1,
    "WINDOW_NOT_FOUND": 1,
    "WINDOW_OCCLUDED": 1,
    "CAPTURE_FAILED": 1,
    "WINDOW_HIDDEN": 1,
    "WINDOW_EMPTY_SIZE": 1,
    "WINDOW_TOO_LARGE": 1,
}

#: 观测里允许出现的键：没有一个是图像字节，也没有一个是文件路径。
OBSERVATION_KEYS = {
    "schema_version",
    "event_id",
    "session_id",
    "observation_id",
    "captured_at_ns",
    "frame_ref",
    "frame_size",
    "transcript",
    "avatar_parameters",
    "window_state",
    "environment_epoch",
}


@pytest.fixture
def replay() -> ReplaySource:
    return default_replay_source()


def _sequence(source: ReplaySource) -> list[Observation]:
    return [observation for observation, _ in collect(source)]


def _armed_controller(clock: FakeClock, session_id: str) -> Controller:
    core = Controller(session_id, ControlConfig(), RecordingTransport(), clock)
    core.arm(True)
    return core


def _request_for(core: Controller, clock: FakeClock, observation_id: str) -> ActionRequest:
    return ActionRequest.model_validate(
        {
            "session_id": core.session_id,
            "action_id": new_id(),
            "kind": "motion",
            "args": {"forward": 0.5, "duration_ms": 200},
            "lease_id": core.lease_id or "expired",
            "environment_epoch": core.environment_epoch,
            "based_on_observation_id": observation_id,
            "issued_at_ns": clock.now_ns(),
            "expires_at_ns": clock.now_ns() + 2_000_000_000,
        }
    )


# --------------------------------------------------------------------------
# 观测本身必须过得了严格契约
# --------------------------------------------------------------------------


def test_every_step_produces_a_contract_valid_observation(replay: ReplaySource) -> None:
    observations = _sequence(replay)
    assert len(observations) == len(EXPECTED_STEPS)

    for observation in observations:
        assert isinstance(observation, Observation)
        # 严格模式 + extra=forbid 的再校验：字段类型、范围、模式全都过一遍。
        assert Observation.model_validate(observation.model_dump()) == observation
        assert observation.session_id == replay.fixture.session_id
        assert observation.environment_epoch == replay.fixture.environment_epoch
        assert observation.schema_version == 1
        assert observation.avatar_parameters == {}
        assert observation.transcript is None


def test_observations_are_frozen(replay: ReplaySource) -> None:
    observation = replay.next_observation()
    with pytest.raises(ValidationError):
        observation.window_state = "lost"  # type: ignore[misc]


def test_observation_ids_and_timestamps_are_unique_and_ordered(replay: ReplaySource) -> None:
    observations = _sequence(replay)
    ids = [observation.observation_id for observation in observations]
    assert len(set(ids)) == len(ids)

    stamps = [observation.captured_at_ns for observation in observations]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps), "每一步都应该有各自的采集时刻"
    assert stamps[0] == replay.fixture.start_ns


def test_window_state_and_failure_stats_match_the_expected_sequence(replay: ReplaySource) -> None:
    observations = _sequence(replay)
    assert [item.window_state for item in observations] == [state for state, _ in EXPECTED_STEPS]
    assert dict(replay.stats().failures) == EXPECTED_FAILURES
    assert replay.stats().ok == 5
    assert replay.stats().attempts == 13


# --------------------------------------------------------------------------
# 验收条件 1：失败绝不复用旧帧
# --------------------------------------------------------------------------


def test_failed_steps_never_expose_a_frame(replay: ReplaySource) -> None:
    for observation, frame in collect(replay):
        if observation.window_state == "visible":
            assert frame is not None
            assert observation.frame_ref is not None
            assert observation.frame_size == (frame.width, frame.height)
            continue
        assert frame is None
        assert observation.frame_ref is None
        assert observation.frame_size is None
        assert replay.frame_for(observation) is None


def test_frame_references_are_never_reused(replay: ReplaySource) -> None:
    refs = [
        observation.frame_ref
        for observation, _ in collect(replay)
        if observation.frame_ref is not None
    ]
    assert len(refs) == 5
    assert len(set(refs)) == len(refs)


def test_frame_references_are_not_paths(replay: ReplaySource) -> None:
    for observation, _ in collect(replay):
        ref = observation.frame_ref
        if ref is None:
            continue
        assert "/" not in ref and "\\" not in ref and ":" not in ref
        assert Path(ref).name == ref
        assert not ref.lower().endswith((".png", ".bmp", ".jpg", ".json"))


# --------------------------------------------------------------------------
# 验收条件 3：默认不把原始画面落盘 / 不塞进事件
# --------------------------------------------------------------------------


def test_observations_carry_no_image_bytes(replay: ReplaySource) -> None:
    for observation, frame in collect(replay):
        payload = json.loads(observation.model_dump_json())
        assert set(payload) == OBSERVATION_KEYS
        assert not (set(payload) & {"data", "bytes", "image", "frame", "path", "pixels"})
        # 观测本身很小；任何嵌进画面的字节都会让它膨胀几个数量级。
        assert len(observation.model_dump_json()) < 1024
        if frame is not None:
            assert len(frame.data) > 0
            assert frame.digest() not in observation.model_dump_json()


# --------------------------------------------------------------------------
# 与控制层的接口：观测能进 observe()，退化的观测进不了动作
# --------------------------------------------------------------------------


def test_controller_accepts_every_replay_observation(replay: ReplaySource) -> None:
    clock = FakeClock(replay.fixture.start_ns)
    core = _armed_controller(clock, replay.fixture.session_id)
    # arm() 会把 epoch 从 0 加到 1；素材必须自带与实际会话一致的 epoch。
    assert core.environment_epoch == replay.fixture.environment_epoch == 1
    source = ReplaySource(
        parse_fixture(default_fixture()),
        clock=clock,
    )

    states: list[str] = []
    for _ in EXPECTED_STEPS:
        observation = source.next_observation()
        assert core.observe(observation) is True
        states.append(core.state)

    # 黑屏那一步 window_state 是 unknown（画面不可信），控制器的既有规则是「非 visible
    # 即暂停」，所以它在第 3 步就停下了，而不是等到第 4 步最小化。这是刻意的保守行为：
    # 看不清环境就不要继续动作。原因码沿用既有的 FOCUS_LOST。
    assert states[:3] == ["ARMED_IDLE", "ARMED_IDLE", "ARMED_IDLE"]
    assert states[3] == "PAUSED"
    assert core.reason == "FOCUS_LOST"
    assert states[3:] == ["PAUSED"] * (len(EXPECTED_STEPS) - 3)
    # 暂停归暂停，观测本身仍然被接受并留档（否则没法解释「为什么停」）。
    assert len(core.observations) == len(EXPECTED_STEPS)


def test_controller_rejects_observations_that_are_not_its_own(replay: ReplaySource) -> None:
    clock = FakeClock(replay.fixture.start_ns)
    core = _armed_controller(clock, "someone-else")
    observation = replay.next_observation()
    assert core.observe(observation) is False
    assert core.state == "ARMED_IDLE"


def test_request_bound_to_a_failed_capture_is_rejected() -> None:
    source = default_replay_source()
    clock = FakeClock(source.fixture.start_ns)
    # 先让控制器处于 DISARMED（不因窗口退化而暂停），喂进一条失败观测，再 arm。
    core = Controller(source.fixture.session_id, ControlConfig(), RecordingTransport(), clock)
    loss: Observation | None = None
    while loss is None:
        candidate = source.next_observation()
        if candidate.window_state == "minimized":
            loss = candidate
        core.observe(candidate)
    assert loss is not None

    core.arm(True)
    result = core.submit(_request_for(core, clock, loss.observation_id))
    assert result.status == "REJECTED"
    assert result.reason_code == "STALE_OBSERVATION"


# --------------------------------------------------------------------------
# 接口形状：live 与 replay 是同一个协议的两个实现
# --------------------------------------------------------------------------


def test_replay_implements_the_observation_source_protocol(replay: ReplaySource) -> None:
    assert isinstance(replay, ObservationSource)
    assert isinstance(replay, ReplaySource)


def test_observation_source_protocol_is_structural() -> None:
    class Minimal:
        def next_observation(self) -> Observation:
            raise NotImplementedError

    assert isinstance(Minimal(), ObservationSource)
