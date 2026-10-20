"""B1：真实dry-run进程预算接线＋假时钟续租/归零＋schema12报告。"""

import asyncio
import copy
import json
from pathlib import Path

import pytest
from test_stability_v9_runtime import assemble_v9

from vrc_agent.clock import FakeClock
from vrc_agent.config import AppConfig
from vrc_agent.contracts import ActionProposal, Observation
from vrc_agent.control.core import Controller
from vrc_agent.control.service import ControllerProcess, bind_request, extend_from_hotkey
from vrc_agent.live_navigation.leases import validate_leases
from vrc_agent.live_navigation.stability import load_policy
from vrc_agent.live_navigation.stability_report import validate_stability_report
from vrc_agent.transport.osc import AXES, RecordingTransport


@pytest.fixture(scope="module")
def v9_worker_budget(tmp_path_factory):
    config = AppConfig(
        navigation_design="stable-inference-v9",
        navigation_motion_profile="field",
        control={"observation_max_age_ms": 4000, "lease_extension_max": 5},
    )
    assert config.mode == "dry-run"
    with ControllerProcess(config, tmp_path_factory.mktemp("v9-budget")) as client:
        budget = client.initial_status["control_budget"]
        assert client.initial_status["state"] == "DISARMED"
        assert budget["task_timeout_ms"] == 180000
        assert budget["base_ms"] == 30000
        assert budget["extension_ms"] == config.control.lease_extension_ms == 30000
        assert budget["extension_max"] == config.control.lease_extension_max == 5
        assert budget["extensions"] == 0 and budget["total_ms"] == 30000
        status = client.request("status")
        assert status["control_budget"] == budget
        assert status["recorded_messages"] == [] and status["sent_count"] == 0
        # 接线修复不增加管道控制能力；真实dry-run也不接受extend/renew。
        for operation in ("extend", "renew"):
            with pytest.raises(ValueError):
                client.request(operation)
    return config, budget


def test_v9_real_controller_process_budget(v9_worker_budget):
    assert v9_worker_budget[1]["task_timeout_ms"] == 180000


@pytest.mark.parametrize(
    "version,timeout_ms",
    [("v6", 180000), ("v7", 180000), ("v8", 180000), ("v5", 30000), ("legacy", 30000)],
)
def test_existing_process_budgets_and_30s_fallback_unchanged(tmp_path, version, timeout_ms):
    design = f"stable-inference-{version}" if version != "legacy" else version
    config = AppConfig(
        navigation_design=design,
        control={"lease_extension_max": 5 if timeout_ms == 180000 else 0},
    )
    assert config.mode == "dry-run"
    with ControllerProcess(config, tmp_path) as client:
        budget = client.initial_status["control_budget"]
        assert budget["task_timeout_ms"] == timeout_ms
        assert budget["base_ms"] == 30000
        assert budget["extension_ms"] == config.control.lease_extension_ms
        assert budget["extension_max"] == config.control.lease_extension_max


def test_v9_process_preserves_custom_extension_parameters(tmp_path):
    config = AppConfig(
        navigation_design="stable-inference-v9",
        control={"lease_extension_ms": 17000, "lease_extension_max": 3},
    )
    with ControllerProcess(config, tmp_path) as client:
        budget = client.initial_status["control_budget"]
        assert budget["task_timeout_ms"] == 180000
        assert budget["base_ms"] == 30000
        assert budget["extension_ms"] == 17000 and budget["extension_max"] == 3


def test_unknown_design_not_enabled_by_budget_fix():
    with pytest.raises(ValueError, match="navigation_design"):
        AppConfig(navigation_design="stable-inference-v11")


@pytest.mark.parametrize("extend", [False, True])
def test_worker_budget_funds_first_local_extension_but_expiry_still_zeros(v9_worker_budget, extend):
    config, budget = v9_worker_budget
    clock = FakeClock(10_000_000_000)
    transport = RecordingTransport()
    # 使用真实worker返回的预算，不能用默认构造值掩盖枚举漏项；时钟/输入完全在内存。
    core = Controller(
        "v9-memory",
        config.control,
        transport,
        clock,
        task_timeout_s=budget["task_timeout_ms"] / 1000,
    )
    core.heartbeat()
    initial = core.arm(True)  # 测试夹具本地授权，不经模型/管道，不注册真实热键。
    assert core.lease_expires_ns - clock.now_ns() == 30_000_000_000
    original_epoch, original_lease = core.environment_epoch, core.lease_id

    def motion():
        obs = Observation(
            session_id=core.session_id,
            environment_epoch=core.environment_epoch,
            captured_at_ns=clock.now_ns(),
            window_state="visible",
            frame_ref="memory",
            frame_size=(2160, 1440),
        )
        assert core.observe(obs)
        request = bind_request(
            ActionProposal(kind="motion", args={"forward": 0.4, "duration_ms": 500}),
            obs,
            core.snapshot(),
            clock=clock,
        )
        assert core.submit(request).status == "RUNNING"
        return request

    first = motion()
    action_deadline, active_until = first.expires_at_ns, core.active_until_ns
    if extend:
        record = extend_from_hotkey(core, notify=lambda _: None)
        assert record["granted_ms"] == 30000 and record["sequence"] == 1
        assert record["expires_ns"] == initial["lease_expires_ns"] + 30_000_000_000
        assert core.active.expires_at_ns == action_deadline and core.active_until_ns == active_until
    assert core.environment_epoch == original_epoch and core.lease_id == original_lease
    assert core.lease_expires_ns - clock.now_ns() == (60000 if extend else 30000) * 1_000_000
    clock.advance_ms(500)
    core.heartbeat()
    core.tick()
    assert core.results[first.action_id].status == "COMPLETED" and core.active is None
    clock.advance_ms((core.lease_expires_ns - clock.now_ns()) // 1_000_000 - 50)
    core.heartbeat()
    core.tick()
    last = motion()
    expiry = core.lease_expires_ns
    clock.advance_ms(50)
    core.heartbeat()
    core.tick()
    assert core.reason == "EXPIRED_LEASE" and core.active is None and core.lease_id is None
    assert core.results[last.action_id].status == "CANCELLED"
    assert all(dict(transport.messages)[axis] == (0.0,) for axis in AXES)
    with pytest.raises(ValueError):
        extend_from_hotkey(core, notify=lambda _: None)
    with pytest.raises(ValueError):
        core.arm(True)
    assert core.lease_expires_ns == expiry


def test_v9_schema12_report_replays_worker_budget_and_local_extension(v9_worker_budget):
    config, budget = v9_worker_budget
    runtime, client, calls = assemble_v9()
    client.config = config
    client.core = Controller(
        client.session_id,
        config.control,
        client.transport,
        client.clock,
        task_timeout_s=budget["task_timeout_ms"] / 1000,
    )
    client.core.heartbeat()
    client.core.arm(True)
    record = extend_from_hotkey(client.core, notify=lambda _: None)
    assert record["granted_ms"] == 30000
    client.advance(31000)
    report = asyncio.run(runtime.run())
    assert calls and report["outcome"] == "SUCCESS" and report["schema_version"] == 12
    assert report["control_budget"]["task_timeout_ms"] == 180000
    assert report["control_budget"]["base_ms"] == 30000
    assert report["control_budget"]["extensions"] == 1
    assert report["control_budget"]["total_ms"] == 60000
    assert report["control_budget"]["elapsed_ms"] > 30000
    policy, _ = load_policy("v9")
    validate_leases(report, policy)
    validate_stability_report(report)
    validate_stability_report(json.loads(json.dumps(report)))
    assert client.core.active is None
    assert all(dict(client.transport.messages)[axis] == (0.0,) for axis in AXES)
    # 原B1的30s任务预算必须再现拒绝，不能靠改报告声明或放宽校验器修复。
    bad = copy.deepcopy(report)
    bad["control_budget"]["task_timeout_ms"] = 30000
    for validator in (lambda r: validate_leases(r, policy), validate_stability_report):
        with pytest.raises(ValueError, match="task timeout"):
            validator(bad)
    path = Path(".runtime/verify/t08_v9_b1_report.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
