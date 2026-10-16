import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from vrc_agent.config import AppConfig
from vrc_agent.contracts import ActionProposal, Observation
from vrc_agent.control.platform import signal_stop
from vrc_agent.control.service import ControllerProcess, bind_request


def start_motion(client, duration_ms=500):
    state = client.request("arm")
    observation = Observation(
        session_id=client.session_id,
        captured_at_ns=time.monotonic_ns(),
        frame_size=(640, 480),
        environment_epoch=state["environment_epoch"],
        window_state="visible",
    )
    assert client.request("observe", observation.model_dump(mode="json"))
    proposal = ActionProposal.model_validate(
        {"kind": "motion", "args": {"forward": 0.5, "duration_ms": duration_ms}}
    )
    action = bind_request(proposal, observation, state)
    assert client.request("submit", action.model_dump(mode="json"))["status"] == "RUNNING"
    return action


def wait_state(client, state, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = client.request("status")
        if current["state"] == state:
            return current
        time.sleep(0.02)
    raise AssertionError(f"controller did not reach {state}: {current}")


def test_process_motion_completion_and_duplicate(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        assert client.process.pid != os.getpid()
        assert client.request("status")["state"] == "DISARMED"
        action = start_motion(client, 100)
        assert client.request("submit", action.model_dump(mode="json"))["status"] in {
            "RUNNING",
            "COMPLETED",
        }
        status = wait_state(client, "ARMED_IDLE")
        assert status["sent_count"] == 6
        assert (
            client.request("result", {"action_id": action.action_id})["verification"]
            == "UNVERIFIED"
        )


def test_independent_stop_signal(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        action = start_motion(client)
        signal_stop(tmp_path)
        status = wait_state(client, "ESTOP")
        result = client.request("result", {"action_id": action.action_id})
        assert result["status"] == "CANCELLED"
        assert dict(status["recorded_messages"])["/input/Vertical"] == [0.0]
        time.sleep(0.1)
        assert client.request("status")["state"] == "ESTOP"


def test_missing_heartbeat_stops_even_with_open_pipe(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path, heartbeat=False) as client:
        start_motion(client)
        status = wait_state(client, "PAUSED")
        assert status["reason_code"] == "HEARTBEAT_LOST"
        assert dict(status["recorded_messages"])["/input/Vertical"] == [0.0]


def test_single_instance_lock(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path):
        with pytest.raises(RuntimeError):
            ControllerProcess(AppConfig(), tmp_path)
    with ControllerProcess(AppConfig(), tmp_path) as fresh:
        assert fresh.request("status")["state"] == "DISARMED"


def test_terminate_main_process_releases_and_exits_controller(tmp_path):
    helper = Path(__file__).parents[1] / "helpers" / "crash_parent.py"
    parent = subprocess.Popen(
        [sys.executable, str(helper), str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready = json.loads(parent.stdout.readline())
        killed_at = time.monotonic_ns()
        parent.kill()
        parent.wait(timeout=5)
        log_path = tmp_path / f"{ready['session_id']}.jsonl"
        deadline = time.monotonic() + 5
        events = []
        while time.monotonic() < deadline:
            if log_path.exists():
                events = [
                    json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
                ]
                if any(e["event"] == "controller_exited" for e in events):
                    break
            time.sleep(0.02)
        assert any(e["event"] == "controller_exited" for e in events)
        stopped = next(
            e for e in events if e["event"] == "stopped" and e["reason_code"] == "HEARTBEAT_LOST"
        )
        assert stopped["monotonic_ns"] - killed_at < 500_000_000
        assert any(
            e["event"] == "inputs_released" and e["count"] == 3 and e["monotonic_ns"] >= killed_at
            for e in events
        )
        assert any(
            e.get("action_id") == ready["action_id"] and e.get("status") == "CANCELLED"
            for e in events
        )
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)


def test_cli_default_is_offline(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "vrc_agent", "run", "--state-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["mode"] == "dry-run"
    assert report["result"]["status"] == "COMPLETED"
    assert report["result"]["verification"] == "UNVERIFIED"


def test_stop_cli_is_independent_of_parent_commands(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")
        stopped = subprocess.run(
            [sys.executable, "-m", "vrc_agent", "stop", "--state-dir", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert stopped.returncode == 0, stopped.stderr
        assert wait_state(client, "ESTOP")["reason_code"] == "MANUAL_STOP"
