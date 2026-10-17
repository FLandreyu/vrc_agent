"""控制内核续租安全边界；仅假时钟与 RecordingTransport。"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from vrc_agent.clock import FakeClock
from vrc_agent.config import AppConfig, ControlConfig, load_config
from vrc_agent.contracts import ActionProposal
from vrc_agent.control.core import Controller
from vrc_agent.control.platform import WindowsControls
from vrc_agent.control.service import Command, extend_from_hotkey
from vrc_agent.telemetry import EventLog
from vrc_agent.telemetry.codes import family_of
from vrc_agent.transport.osc import RecordingTransport


def controller(*, count=5, task_s=180):
    clock = FakeClock(10_000_000_000)
    core = Controller(
        "test",
        ControlConfig(lease_extension_max=count),
        RecordingTransport(),
        clock,
        task_timeout_s=task_s,
    )
    core.arm(True)
    return core, clock


def test_local_extension_limits_and_console(capsys):
    core, clock = controller()
    epoch, lease = core.environment_epoch, core.lease_id
    for sequence in range(1, 6):
        record = extend_from_hotkey(core)
        assert record == {
            "at_ns": clock.now_ns(),
            "granted_ms": 30000,
            "expires_ns": 40_000_000_000 + sequence * 30_000_000_000,
            "sequence": sequence,
        }
        assert core.environment_epoch == epoch and core.lease_id == lease
    assert core.control_budget()["total_ms"] == 180000
    assert "Lease extended: +60s (2/5), expires in 90s. F8=ESTOP" in capsys.readouterr().out
    with pytest.raises(ValueError):
        core.extend_local()


@pytest.mark.parametrize(
    "state", ["estop", "expired", "disarmed", "paused", "pending", "heartbeat", "default"]
)
def test_unsafe_states_cannot_extend(state):
    core, clock = controller(count=0 if state == "default" else 5)
    if state == "estop":
        core.stop(emergency=True)
    elif state == "paused":
        core.stop()
    elif state == "disarmed":
        core.stop()
        core.tick()
        core.tick()
        core.reset()
    elif state == "expired":
        clock.advance_ms(30000)
        core.heartbeat()
    elif state == "heartbeat":
        clock.advance_ms(500)
    elif state == "pending":
        core.pending_releases = 2
    before = core.lease_expires_ns
    with pytest.raises(ValueError):
        core.extend_local()
    assert core.lease_expires_ns == before and not core.lease_extensions
    if state == "pending":
        assert core.pending_releases == 2
    if state == "expired":
        assert core.reason == "EXPIRED_LEASE" and core.lease_id is None


def test_truncate_to_remaining_task_budget_then_refuse():
    core, _ = controller(task_s=45)
    assert core.extend_local()["granted_ms"] == 15000
    assert core.control_budget()["total_ms"] == 45000
    with pytest.raises(ValueError):
        core.extend_local()


def test_arm_uses_one_timestamp_for_base_expiry_with_advancing_clock():
    class AdvancingClock:
        def __init__(self):
            self.now = 10_000_000_000

        def now_ns(self):
            self.now += 100
            return self.now

    clock = AdvancingClock()
    core = Controller("test", ControlConfig(lease_extension_max=5), RecordingTransport(), clock)
    core.arm(True)
    assert core.lease_expires_ns - core.lease_started_ns == 30_000_000_000
    record = core.extend_local()
    assert record["expires_ns"] - core.lease_started_ns == 60_000_000_000


def test_extension_does_not_touch_active_action_ttl_and_final_expiry_zeros(rig):
    core, clock, transport, request, _ = rig
    core.config = ControlConfig(lease_extension_max=1)
    action = request(
        args={"forward": 0.2, "duration_ms": 500}, expires_at_ns=clock.now_ns() + 100_000_000
    )
    assert core.submit(action).status == "RUNNING"
    old_deadline, old_until = action.expires_at_ns, core.active_until_ns
    core.extend_local()
    assert core.active.expires_at_ns == old_deadline and core.active_until_ns == old_until
    clock.advance_ms(100)
    core.heartbeat()
    core.tick()
    assert core.results[action.action_id].reason_code == "EXPIRED_ACTION" and core.active is None
    clock.advance_ms(59900)
    core.heartbeat()
    core.tick()
    assert core.reason == "EXPIRED_LEASE" and core.state == "PAUSED"
    assert all(value == (0.0,) for value in dict(transport.messages).values())
    with pytest.raises(ValueError):
        core.extend_local()
    with pytest.raises(ValueError):
        core.arm(True)
    core.tick()
    core.tick()
    core.reset()
    core.arm(True)
    assert core.lease_extensions == []


@pytest.mark.parametrize("operation", ["extend", "renew", "EXTEND", "Renew", "lease_extend"])
def test_ipc_and_model_have_no_extension_capability(operation):
    with pytest.raises(ValueError):
        Command(request_id="test", operation=operation)
    with pytest.raises(ValueError):
        Command.model_validate_json(json.dumps({"request_id": "test", "operation": operation}))
    with pytest.raises(ValueError):
        ActionProposal(kind=operation, args={})


@pytest.mark.parametrize("probe", [False, True])
def test_hotkey_registration_preserves_bindings(monkeypatch, probe):
    dll = Mock()
    monkeypatch.setattr("ctypes.WinDLL", lambda *a, **kw: dll)
    controls = WindowsControls("VRChat", probe)
    bindings = [call.args for call in dll.RegisterHotKey.call_args_list]
    assert (None, 14, 0x4002, 0x78) in bindings
    assert (None, 1, 0x4000, 0x77) in bindings
    assert (None, 2, 0x4000, 0x78) in bindings
    assert (None, 3, 0x4000, 0x79) in bindings
    assert controls.keys[14] == "extend"
    controls.close()


def test_default_disabled_live_config_and_budget_validation():
    assert AppConfig().control.lease_extension_max == 0
    cfg = load_config(Path("configs/navigation-live.toml"))
    assert cfg.mode == "dry-run" and cfg.control.lease_ms == 30000
    assert cfg.control.lease_extension_ms == 30000 and cfg.control.lease_extension_max == 5
    with pytest.raises(ValueError, match="task_timeout_s"):
        AppConfig(
            navigation_design="stable-inference-v6",
            control={"lease_ms": 60000, "lease_extension_max": 5},
        )
    with pytest.raises(ValueError, match="require v6"):
        AppConfig(control={"lease_extension_max": 1})


def test_lease_event_registered_and_numeric_fields_sanitized(tmp_path):
    assert family_of("LEASE_EXTENDED") == "control"
    path = tmp_path / "events.jsonl"
    log = EventLog(path, "test")
    try:
        core, _ = controller()
        core.emit = log.emit
        core.extend_local()
    finally:
        log.close()
    event = json.loads(path.read_text().splitlines()[0])
    assert event["reason_code"] == "LEASE_EXTENDED" and event["granted_ms"] == 30000
    assert event["sequence"] == 1 and event["expires_ns"] == 70_000_000_000
