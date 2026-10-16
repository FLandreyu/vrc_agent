"""T08 真实控制内核 + 动作驱动像素世界；离线结果不冒充实机验收。"""

import asyncio
import json
import socket
import subprocess
import sys

import pytest

from vrc_agent.contracts import ActionProposal
from vrc_agent.navigation_sim import build_navigation, classify, run_navigation_suite
from vrc_agent.skills import load_first_world


@pytest.fixture(scope="module")
def suite():
    return asyncio.run(run_navigation_suite())


def assert_zero(trace):
    for result in trace["results"]:
        action = next(a for a in trace["actions"] if a["action_id"] == result["action_id"])
        if action["kind"] == "motion" and result["started_at_ns"] is not None:
            for address in ("/input/Vertical", "/input/Horizontal", "/input/LookHorizontal"):
                assert any(
                    m["at_ns"] == result["finished_at_ns"]
                    and m["address"] == address
                    and m["values"] == [0.0]
                    for m in trace["messages"]
                )
    for address in {m["address"] for m in trace["messages"]}:
        assert [m for m in trace["messages"] if m["address"] == address][-1]["values"] == [0.0]


def test_fixed_denominator_counts_every_failure_and_takeover(suite):
    assert (
        suite["suite_digest"] == "ea1688d3a0235e0a9af03b18a5177208fe80bf74b414ced0d49e696812a0370d"
    )
    assert suite["total"] == len(suite["trials"]) == 20
    assert len({t["task_id"] for t in suite["trials"]}) == 20
    assert suite["successes"] == 16 and suite["success_rate"] == 0.8
    assert suite["offline_threshold_met"] and suite["live_acceptance"] == "UNTESTED"
    assert suite["failure_counts"] == {
        "MODEL_TIMEOUT": 1,
        "STUCK": 1,
        "TAKEOVER": 1,
        "TARGET_UNOBSERVED": 1,
    }


@pytest.mark.parametrize("index", range(20))
def test_every_trial_has_evidence_and_zero_at_each_motion_end(suite, index):
    trial = suite["trials"][index]
    trace = trial["trace"]
    assert_zero(trace)
    assert trace["final_state"] in {"PAUSED", "ESTOP"}
    assert trial["recoveries"] <= 2
    assert not trace["sends_live_input"]
    if trial["success"]:
        result = trace["results"][-1]
        assert result["verification"] == "SUCCESS"
        assert len(set(result["evidence_observation_ids"])) == 2
        goal = load_first_world().landmark(trial["landmark_id"]).goal
        for obs_id in result["evidence_observation_ids"]:
            proof = next(
                e["evidence"]
                for e in trace["events"]
                if e["event"] == "navigation_feedback" and e["evidence"]["observation_id"] == obs_id
            )
            assert proof["valid_target"] and proof["target_area"] >= goal.arrival_area
            assert proof["centre_error"] <= 0.1
    if trial["injected_fault"] == "temporary_block":
        assert trial["success"] and trial["recoveries"] == 1
        steps = [e["skill"] for e in trace["events"] if e["event"] == "navigation_feedback"]
        assert steps[3:6] == ["recover_wait", "recover_strafe", "recover_observe"]


def test_permanent_stuck_has_exact_budget_and_no_auto_arm(suite):
    trial = suite["trials"][17]
    assert trial["failure_class"] == "STUCK" and trial["recoveries"] == 2
    assert len(trial["trace"]["actions"]) == 15  # 3 approaches + 2*(3 recovery + 3 approaches)
    assert {a["environment_epoch"] for a in trial["trace"]["actions"]} == {1}
    assert trial["trace"]["final_state"] == "PAUSED"


def test_deterministic_complete_report_and_no_socket(monkeypatch, suite):
    def forbidden(*args, **kwargs):
        raise AssertionError("navigation must not open sockets")

    # Windows asyncio 初始化自身可能需要 socketpair，先创建事件循环，再安装哨兵。
    loop = asyncio.new_event_loop()
    with monkeypatch.context() as patch:
        patch.setattr(socket, "socket", forbidden)
        assert loop.run_until_complete(run_navigation_suite()) == suite
    loop.close()


def test_plant_changes_only_from_executed_input():
    world = load_first_world()
    runtime = build_navigation(world, world.tasks[0])

    async def wait_only(scene, recent):
        return ActionProposal(kind="wait", args={"duration_ms": 100})

    runtime.planner.decide = wait_only
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "ACTION_LIMIT"
    assert all(r["verification"] != "SUCCESS" for r in report["results"])
    assert runtime.source.size == world.tasks[0].initial_size
    assert not any(m["values"][0] != 0 for m in report["messages"])


def test_estop_during_recovery_never_resumes():
    world = load_first_world()
    runtime = build_navigation(world, world.tasks[3])
    stop_at = runtime.clock.now_ns() + 850_000_000
    runtime.controller.estop_at_ns = stop_at
    trace = asyncio.run(runtime.run())
    assert runtime.navigation.recoveries == 1
    assert classify(trace) == "TAKEOVER"
    assert trace["final_state"] == "ESTOP"
    assert not any(m["at_ns"] >= stop_at and m["values"][0] != 0 for m in trace["messages"])
    assert_zero(trace)


def test_missing_new_evidence_after_motion_does_not_succeed():
    world = load_first_world()
    runtime = build_navigation(world, world.tasks[0])
    original = runtime.source.next_observation
    first = original()
    runtime.source.next_observation = lambda: first
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "STALE_OBSERVATION"
    assert all(r["verification"] != "SUCCESS" for r in report["results"])
    assert_zero(report)


def test_cli_and_reject_live_flag(suite):
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "vrc_agent", "navigate-suite"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(result.stdout) == suite
    denied = subprocess.run(
        [sys.executable, "-m", "vrc_agent", "navigate-suite", "--mode", "live"],
        capture_output=True,
        text=True,
    )
    assert denied.returncode == 2


def test_takeover_overrides_previous_success(suite):
    trace = {**suite["trials"][0]["trace"], "final_state": "ESTOP"}
    assert classify(trace) == "TAKEOVER"
