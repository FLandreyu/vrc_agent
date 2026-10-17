"""本地按键、真实 dry-run IPC、报告防伪；无真实输入/注册热键。"""

import asyncio
import copy
import json
import threading
from unittest.mock import Mock

import pytest
from test_navigation_v6 import assembly
from test_probe_safety import named, run_worker, stopped_with

from vrc_agent.config import AppConfig
from vrc_agent.control import service
from vrc_agent.control.service import ControllerProcess
from vrc_agent.live_navigation.stability_report import validate_stability_report


def test_worker_local_extension_and_same_tick_estop_priority():
    trace = run_worker(
        [(0, ["arm"]), (0.1, ["extend"]), (0.2, ["estop", "extend"])], extension_max=5
    )
    records = named(trace, "lease_extended")
    assert len(records) == 1 and records[0][2]["granted_ms"] == 30000
    assert records[0][2]["reason_code"] == "LEASE_EXTENDED"
    assert stopped_with(trace, "MANUAL_STOP")


def test_worker_cannot_extend_after_expiry():
    trace = run_worker(
        [(0, ["arm"]), (0.1, ["extend"]), (0.61, ["extend"])],
        lease_ms=500,
        extension_ms=100,
        extension_max=1,
    )
    assert len(named(trace, "lease_extended")) == 1
    assert stopped_with(trace, "EXPIRED_LEASE")
    assert named(trace, "human_command_rejected")


def test_worker_pressed_probe_button_blocks_extension():
    trace = run_worker([(0, ["arm"]), (0.1, ["probe_use"]), (0.2, ["extend"])], extension_max=5)
    assert not named(trace, "lease_extended")
    assert named(trace, "human_command_rejected")


def test_worker_focus_loss_blocks_extension():
    trace = run_worker([(0, ["arm"]), (0.2, ["extend"])], extension_max=5, focus_lost_at=0.1)
    assert not named(trace, "lease_extended")
    assert stopped_with(trace, "FOCUS_LOST")


def test_real_dry_run_ipc_extend_and_renew_rejected(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        before = client.request("status")
        for operation in ("extend", "renew"):
            with pytest.raises(ValueError):
                client.request(operation)
        after = client.request("status")
        assert after["state"] == "DISARMED" and after["lease_extensions"] == []
        assert after["lease_expires_ns"] == before["lease_expires_ns"]


def test_console_notification_prints_in_parent_not_controller(monkeypatch, capsys):
    client = object.__new__(ControllerProcess)
    client.lock = threading.Lock()
    client.connection = Mock()
    client.connection.recv_bytes.side_effect = [
        json.dumps(
            {
                "notification": "lease_extended",
                "message": "Lease extended: +30s (1/5), expires in 60s. F8=ESTOP",
            }
        ).encode(),
        b'{"request_id":"test","ok":true,"data":{}}',
    ]
    monkeypatch.setattr(service, "new_id", lambda: "test")
    assert client.request("status") == {}
    assert "Lease extended: +30s (1/5), expires in 60s. F8=ESTOP" in capsys.readouterr().out


@pytest.fixture(scope="module")
def extended_report():
    runtime, client, _ = assembly()
    client.config = client.config.model_copy(
        update={"control": client.config.control.model_copy(update={"lease_extension_max": 5})}
    )
    client.core.config = client.config.control
    # 模拟现场操作者事件：在模型/运行时开始前直接驱动假控制内核，绝无 provider 自动续租。
    client.core.extend_local()
    client.advance(31000)
    report = asyncio.run(runtime.run())
    validate_stability_report(report)
    assert report["control_budget"]["elapsed_ms"] > 30000
    assert report["control_budget"]["extensions"] == 1
    assert report["control_budget"]["total_ms"] == 60000
    return report


@pytest.mark.parametrize(
    "fault",
    [
        "missing_event",
        "count",
        "total",
        "sequence",
        "expiry",
        "grant",
        "late",
        "elapsed_without_records",
        "ttl",
        "schema",
        "legacy_event",
    ],
)
def test_lease_report_tampering_rejected(extended_report, fault):
    bad = copy.deepcopy(extended_report)
    budget, records = bad["control_budget"], bad["lease_extensions"]
    if fault == "missing_event":
        bad["events"] = [e for e in bad["events"] if e["event"] != "lease_extended"]
    elif fault == "count":
        budget["extensions"] = 6
    elif fault == "total":
        budget["total_ms"] = 180001
    elif fault == "sequence":
        records[0]["sequence"] = 6
    elif fault == "expiry":
        records[0]["expires_ns"] += 1
    elif fault == "grant":
        records[0]["granted_ms"] = 30001
    elif fault == "late":
        records[0]["at_ns"] = budget["armed_at_ns"] + 30000 * 1_000_000
    elif fault == "elapsed_without_records":
        bad["lease_extensions"] = []
        bad["events"] = [e for e in bad["events"] if e["event"] != "lease_extended"]
        budget["extensions"] = 0
        budget["total_ms"] = budget["base_ms"]
    elif fault == "ttl":
        bad["actions"][0]["expires_at_ns"] = bad["actions"][0]["issued_at_ns"] + 1_000_000_001
    elif fault == "legacy_event":
        bad["schema_version"] = 8
        del bad["lease_extensions"], bad["control_budget"]
    else:
        bad["schema_version"] = 8
    # 同步伪造镜像事件也不能绕过记录的数值、次序和到期校验。
    if fault in {"sequence", "expiry", "grant", "late"}:
        for event in bad["events"]:
            if event["event"] == "lease_extended":
                event.update(records[0])
    with pytest.raises(ValueError):
        validate_stability_report(bad)
