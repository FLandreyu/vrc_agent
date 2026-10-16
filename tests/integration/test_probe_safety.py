"""T00 复核 R1–R3 的 worker 层安全回归。

离线驱动真实 `_worker`（替换外部设施与传输为内存桩）：不创建 socket、不注册热键、
不触发真实输入，时间由假时钟控制。期望行为：

- R1：租约/心跳已过期时，按钮的"按下"边沿不得发送，且按键必须留痕；
- R2：连按不得延长按下时间，未释放前的新脉冲被拒绝；与运动动作互斥；
- R3：释放失败如实记录 `probe_release_failed`；被拒绝的提交记 `probe_rejected`；
  发送失败（FAILED）单独记 `probe_send_failed`——均不得记为 `probe_dispatched`。
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

from vrc_agent.config import AppConfig, ControlConfig
from vrc_agent.contracts import ActionProposal, new_id
from vrc_agent.control import service
from vrc_agent.control.service import PROBE_BUTTONS, PROBE_MOTIONS, Command
from vrc_agent.transport.osc import RecordingTransport


def run_worker(
    schedule, *, lease_ms=30000, heartbeat_ms=1000, fail_release=False, fail_address=None
):
    """驱动真实 _worker；schedule 为 [(时间, [按键事件...]), ...]，返回事件 trace。"""
    current = [0.0]
    trace = []
    steps = iter(schedule)

    class Transport(RecordingTransport):
        def send(self, address, values):
            if fail_address is not None and address == fail_address:
                trace.append([current[0], "send_failed", address, list(values)])
                raise OSError("test injected send failure")
            if fail_release and values == (0,):
                trace.append([current[0], "send_failed", address, list(values)])
                raise OSError("test injected release failure")
            trace.append([current[0], "send", address, list(values)])
            super().send(address, values)

    def events():
        try:
            at, batch = next(steps)
        except StopIteration:
            raise RuntimeError("end of bounded test schedule") from None
        current[0] = at
        return batch

    controls = Mock()
    controls.events.side_effect = events
    controls.focus_ok.return_value = True
    signal = Mock()
    signal.triggered.return_value = False
    log = Mock()
    log.emit.side_effect = lambda event, **fields: trace.append([current[0], event, fields])
    fake_time = SimpleNamespace(
        monotonic=lambda: 100 + current[0],
        monotonic_ns=lambda: 100_000_000_000 + round(current[0] * 1e9),
        sleep=lambda _: None,
    )
    clock = SimpleNamespace(now_ns=fake_time.monotonic_ns)
    config = AppConfig(
        mode="live",
        control=ControlConfig(
            enable_motion_probe=True,
            lease_ms=lease_ms,
            heartbeat_timeout_ms=heartbeat_ms,
        ),
    )
    with ExitStack() as stack:
        for attr, replacement in {
            "time": fake_time,
            "threading": SimpleNamespace(Thread=Mock, Event=service.threading.Event),
            "SystemClock": lambda: clock,
            "InstanceLock": lambda *_: Mock(),
            "StopSignal": lambda _: signal,
            "WindowsControls": lambda *_: controls,
            "OscTransport": Transport,
            "EventLog": lambda *_: log,
        }.items():
            stack.enter_context(patch.object(service, attr, replacement))
        service._worker(Mock(), config.model_dump_json(), ".runtime", new_id())
    return trace


def sends(trace, address):
    return [row for row in trace if row[1] == "send" and row[2] == address]


def named(trace, name):
    return [row for row in trace if row[1] == name]


def stopped_with(trace, reason):
    return [row for row in named(trace, "stopped") if row[2]["reason_code"] == reason]


# ---- R1：过期授权时不得发送按钮按下 ----


@pytest.mark.parametrize(
    ("kwargs", "at", "reason"),
    [
        ({"lease_ms": 500}, 0.6, "EXPIRED_LEASE"),
        ({"heartbeat_ms": 1000}, 1.2, "HEARTBEAT_LOST"),
    ],
)
def test_stale_authorization_blocks_button_press(kwargs, at, reason):
    trace = run_worker([(0, ["arm"]), (at, ["probe_use"])], **kwargs)
    assert sends(trace, "/input/UseRight") == []
    assert stopped_with(trace, reason)
    rejected = [row for row in named(trace, "human_command_rejected")]
    assert any(row[2]["operation"] == "probe_use" for row in rejected)


# ---- R2：连按拒绝、不延长；与运动互斥 ----


def test_retrigger_rejected_does_not_extend_hold():
    trace = run_worker(
        [
            (0, ["arm"]),
            (0.1, ["probe_use"]),
            (0.2, ["probe_use"]),
            (0.3, ["probe_use"]),
            (0.45, []),
        ]
    )
    presses = [row for row in sends(trace, "/input/UseRight") if row[3] == [1]]
    releases = [row for row in sends(trace, "/input/UseRight") if row[3] == [0]]
    assert len(presses) == 1
    assert releases
    hold = releases[0][0] - presses[0][0]
    assert 0.29 <= hold <= 0.36
    rejected = [row for row in named(trace, "human_command_rejected")]
    assert len([row for row in rejected if row[2]["operation"] == "probe_use"]) == 2


def test_button_hold_blocks_motion_probe():
    trace = run_worker(
        [
            (0, ["arm"]),
            (0.1, ["probe_use"]),
            (0.2, ["probe_forward"]),
            (0.45, []),
            (0.5, ["probe_forward"]),
        ]
    )
    # 按住期间的按键被拒；释放后的按键成功（真实执行的 forward=0.2 只出现一次）。
    executed = [row for row in sends(trace, "/input/Vertical") if row[3] == [0.2]]
    assert len(executed) == 1
    rejected = [row for row in named(trace, "human_command_rejected")]
    assert len([row for row in rejected if row[2]["operation"] == "probe_forward"]) == 1


def test_motion_execution_blocks_button_probe():
    trace = run_worker(
        [
            (0, ["arm"]),
            (0.1, ["probe_forward"]),
            (0.15, ["probe_use"]),
            (0.4, ["probe_use"]),
        ]
    )
    presses = [row for row in sends(trace, "/input/UseRight") if row[3] == [1]]
    assert len(presses) == 1
    rejected = [row for row in named(trace, "human_command_rejected")]
    assert len([row for row in rejected if row[2]["operation"] == "probe_use"]) == 1


# ---- R3：释放失败与提交拒绝的日志真实性 ----


def test_release_failure_logged_as_failed_not_released():
    trace = run_worker([(0, ["arm"]), (0.1, ["probe_use"]), (0.41, [])], fail_release=True)
    failed = [row for row in named(trace, "probe_release_failed")]
    assert any(row[2]["operation"] == "UseRight" for row in failed)
    assert not named(trace, "probe_released")


def test_rejected_chat_submit_logged_as_probe_rejected():
    trace = run_worker([(0, ["arm"]), (0.1, ["probe_chat"]), (0.2, ["probe_chat"])])
    dispatched = [row for row in named(trace, "probe_dispatched")]
    rejected = [row for row in named(trace, "probe_rejected")]
    assert len([row for row in dispatched if row[2]["operation"] == "probe_chat"]) == 1
    chat_rejected = [row for row in rejected if row[2]["operation"] == "probe_chat"]
    assert len(chat_rejected) == 1
    assert chat_rejected[0][2]["reason_code"] == "RATE_LIMITED"


def test_chat_send_failure_logged_as_send_failed():
    trace = run_worker([(0, ["arm"]), (0.1, ["probe_chat"])], fail_address="/chatbox/input")
    assert not [row for row in trace if row[1] == "send" and row[2] == "/chatbox/input"]
    failed = [
        row for row in named(trace, "probe_send_failed") if row[2]["operation"] == "probe_chat"
    ]
    assert len(failed) == 1
    assert failed[0][2]["reason_code"] == "TRANSPORT_ERROR"
    dispatched = [
        row for row in named(trace, "probe_dispatched") if row[2]["operation"] == "probe_chat"
    ]
    assert not dispatched


def test_motion_send_failure_logged_as_send_failed():
    trace = run_worker([(0, ["arm"]), (0.1, ["probe_forward"])], fail_address="/input/Vertical")
    assert sends(trace, "/input/Vertical") == []
    failed = [
        row for row in named(trace, "probe_send_failed") if row[2]["operation"] == "probe_forward"
    ]
    assert len(failed) == 1
    assert failed[0][2]["reason_code"] == "TRANSPORT_ERROR"
    dispatched = [
        row for row in named(trace, "probe_dispatched") if row[2]["operation"] == "probe_forward"
    ]
    assert not dispatched


# ---- 入口拒绝与重复归零（复核确认项，固化为回归） ----


def test_probe_names_rejected_at_pipeline_entrypoints():
    for name in [*PROBE_MOTIONS, *PROBE_BUTTONS, "probe_chat"]:
        with pytest.raises(ValidationError):
            Command.model_validate({"request_id": new_id(), "operation": name})
        with pytest.raises(ValidationError):
            ActionProposal.model_validate({"kind": name, "args": {}, "expected_outcome": "x"})


def test_repeated_button_neutralize_emits_zeroes():
    transport = RecordingTransport()
    for name in PROBE_BUTTONS.values():
        transport.button(name, True)
    transport.neutralize()
    transport.neutralize()
    zeros = [row for row in transport.messages if row[1] == (0,)]
    assert len(zeros) == 6
