"""方向 A：内存 HTTP、可控时钟、真实 core；另有真实 spawn IPC 离线交付报告。"""

import asyncio
import copy
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from vrc_agent.capture.frames import Frame
from vrc_agent.clock import FakeClock, SystemClock
from vrc_agent.config import AppConfig, PerceptionConfig
from vrc_agent.contracts import ActionRequest, Observation, new_id
from vrc_agent.control.core import Controller
from vrc_agent.control.service import ControllerProcess
from vrc_agent.live_navigation.session import summarize_reports
from vrc_agent.live_navigation.stability import compare, load_policy
from vrc_agent.live_navigation.stability_report import validate_stability_report
from vrc_agent.live_navigation.stable_runtime import StableNavigation
from vrc_agent.live_navigation.world import load_live_world
from vrc_agent.perception.interpreter import Perception
from vrc_agent.providers.base import ModelInput
from vrc_agent.providers.openai_compat import HttpResponse, OpenAICompatibleProvider
from vrc_agent.telemetry.budget import SessionBudget
from vrc_agent.telemetry.report import elapsed_ms_between, hourly_from_usage
from vrc_agent.transport.osc import AXES, RecordingTransport


class MemoryClient(ControllerProcess):
    """仅测试的 IPC 替身，使用真实控制 core；绝不实例化 live transport。"""

    def __init__(self, clock):
        self.config = AppConfig()
        self.session_id = new_id()
        self.process = SimpleNamespace(pid=0)
        self.stop_heartbeat = threading.Event()
        self.clock = clock
        self.transport = RecordingTransport()
        self.core = Controller(self.session_id, self.config.control, self.transport, clock)
        self.core.heartbeat()
        self.core.arm(True)

    def advance(self, ms):
        self.clock.advance_ms(ms)
        self.core.heartbeat()
        self.core.tick()

    def request(self, operation, payload=None, timeout=None):
        self.core.heartbeat()
        self.core.tick()
        if operation == "status":
            return self.core.snapshot()
        if operation == "observe":
            return self.core.observe(Observation.model_validate_json(json.dumps(payload)))
        if operation == "submit":
            return self.core.submit(
                ActionRequest.model_validate_json(json.dumps(payload))
            ).model_dump(mode="json")
        if operation == "result":
            self.advance(200)
            return self.core.results[payload["action_id"]].model_dump(mode="json")
        if operation in {"pause", "estop"}:
            self.core.stop("MANUAL_STOP", emergency=operation == "estop")
            return self.core.snapshot()
        raise AssertionError(operation)


class Frames:
    def __init__(self, client, clock, *, change="none"):
        self.client, self.clock, self.change = client, clock, change
        self.index = 0
        self.frame = None
        self.closed = False

    def next_observation(self):
        self.index += 1
        if isinstance(self.clock, FakeClock):
            self.clock.advance_ms(1)
        now = self.clock.now_ns()
        data = bytearray(b"\x14\x20\x30\xff" * (2160 * 1440))
        if self.change == "always":
            data[0] = self.index % 256
        if self.change == "once" and self.index == 2:
            data[0] = 90
        if self.change == "presubmit" and self.index == 3:
            data[0] = 90
        if self.change == "hud":
            data[(1400 * 2160 + 20) * 4] = self.index % 256
        if self.change == "black":
            data[:] = b"\0" * len(data)
        obs = Observation(
            session_id=self.client.session_id,
            environment_epoch=self.client.request("status")["environment_epoch"],
            captured_at_ns=now,
            frame_ref=f"image-{self.index}",
            frame_size=(2160, 1440),
            window_state="visible",
        )
        self.frame = Frame(obs.frame_ref, bytes(data), 2160, 1440, now)
        return obs

    def frame_for(self, obs):
        return self.frame

    def close(self):
        self.closed = True


def assembly(client, clock, *, latency=3500, fault=None, change="none", entered=None, release=None):
    world = load_live_world()
    calls = []
    budget = SessionBudget.from_config(PerceptionConfig(budget_usd=1.0))

    def http(request):
        body = json.loads(request.body)
        calls.append(body)
        if entered is not None:
            entered.set()
            assert release.wait(3)
        evidence = body["messages"][1]["content"][0]["text"].split(
            "Evidence observation ID for this frame: "
        )[-1]
        if isinstance(client, MemoryClient):
            client.advance(latency)
            if fault == "estop":
                client.core.stop("MANUAL_STOP", emergency=True)
            elif fault == "epoch":
                client.core.environment_epoch += 1
            elif fault == "lease":
                client.advance(30000)
            elif fault == "pause":
                client.core.stop("FOCUS_LOST")
        entities = [
            dict(
                track_id="goal",
                label=world.landmark("G1").label,
                bbox=([0.4, 0.4, 0.6, 0.6] if len(calls) == 1 else [0.1, 0.15, 0.95, 0.85]),
                confidence=0.99,
                evidence_observation_id=evidence,
            )
        ]
        entities.append(
            dict(
                track_id="clear",
                label="forward_clearance",
                bbox=[0.2, 0.6, 0.9, 0.98],
                confidence=0.99,
                evidence_observation_id=evidence,
            )
        )
        payload = dict(entities=entities, hazards=[], uncertainties=[])
        if fault == "untrusted":
            payload["uncertainties"] = ["uncertain"]
        content = "" if fault == "empty" else json.dumps(payload)
        return HttpResponse(
            200,
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {"content": content},
                            "finish_reason": "length" if fault == "empty" else "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 20, "total_tokens": 40},
                }
            ).encode(),
        )

    provider = OpenAICompatibleProvider(
        model="memory-stub",
        api_key="synthetic",
        timeout_ms=8000,
        max_retries=0,
        disable_thinking=True,
        prepare_in_thread=True,
        send_images=True,
        transport=http,
        clock=clock,
        accounting=budget,
    )
    runtime = StableNavigation(
        client,
        lambda _: Frames(client, clock, change=change),
        Perception(provider, clock=clock),
        world,
        world.task("G1-1"),
        clock=clock,
    )
    return runtime, budget, calls


def run_memory(**kwargs):
    clock = FakeClock()
    client = MemoryClient(clock)
    runtime, budget, calls = assembly(client, clock, **kwargs)
    report = asyncio.run(runtime.run())
    validate_stability_report(report)
    if client.transport.messages:
        assert all(dict(client.transport.messages)[axis] == (0.0,) for axis in AXES)
    assert runtime.source.closed
    return report, budget.snapshot(), calls


def test_3500ms_normal_and_hud_noise():
    report, usage, calls = run_memory(change="hud")
    assert report["outcome"] == "SUCCESS", report
    assert len(report["results"][-1]["evidence_observation_ids"]) == 2
    assert usage["calls"] == len(calls) == 3
    assert all(body["thinking"] == {"type": "disabled"} for body in calls)
    assert (
        report["stability_proofs"][0]["current_captured_at_ns"]
        - report["stability_proofs"][0]["reference_captured_at_ns"]
        >= 3_500_000_000
    )


@pytest.mark.parametrize(
    "kwargs,outcome",
    [
        ({"latency": 8100}, "INFERENCE_TIMEOUT"),
        ({"fault": "empty"}, "MODEL_MALFORMED_RESULT"),
        ({"fault": "estop"}, "MANUAL_STOP"),
        ({"fault": "epoch"}, "EPOCH_MISMATCH"),
        ({"fault": "lease"}, "EXPIRED_LEASE"),
        ({"fault": "pause"}, "FOCUS_LOST"),
        ({"change": "always"}, "SCENE_UNSTABLE"),
        ({"change": "black"}, "SCENE_UNSTABLE"),
    ],
)
def test_fail_closed(kwargs, outcome):
    report, _, _ = run_memory(**kwargs)
    assert report["outcome"] == outcome, report
    assert report["actions"] == []


def test_reinfer_after_change():
    report, _, calls = run_memory(change="once")
    assert report["outcome"] == "SUCCESS", report
    assert len(calls) == 4
    assert report["stability_proofs"][0]["equal"] is False


def test_change_before_submit_discards_old_scene():
    report, _, calls = run_memory(change="presubmit")
    assert report["outcome"] == "SUCCESS", report
    rejected = report["stability_proofs"][1]
    assert not rejected["equal"] and len(calls) == 4
    old_observation = report["stability_proofs"][0]["current_observation_id"]
    assert all(a["based_on_observation_id"] != old_observation for a in report["actions"])


def test_stable_untrusted_scene_never_success():
    report, _, _ = run_memory(fault="untrusted")
    assert not report["success"]
    assert all(r["verification"] != "SUCCESS" for r in report["results"])


@pytest.mark.parametrize("rectangle", [(1000, 600, 1001, 601), (800, 300, 1400, 1200)])
def test_character_object_or_mirror_changes_are_rejected(rectangle):
    policy, _ = load_policy()
    original = b"\x14\x20\x30\xff" * (2160 * 1440)
    altered = bytearray(original)
    x1, y1, x2, y2 = rectangle
    for y in range(y1, y2):
        altered[(y * 2160 + x1) * 4 : (y * 2160 + x2) * 4] = b"\xff" * ((x2 - x1) * 4)
    a = Frame("a", original, 2160, 1440, 1)
    b = Frame("b", bytes(altered), 2160, 1440, 2)
    assert compare(a, b, policy)["equal"] is False


def test_evidence_tampering_rejected():
    report, _, _ = run_memory()
    for field in ("current_rgb_digest", "policy_digest"):
        bad = copy.deepcopy(report)
        bad["stability_proofs"][0][field] = "f" * 64
        with pytest.raises(ValueError):
            validate_stability_report(bad)
    bad = copy.deepcopy(report)
    del bad["stability_proofs"]
    with pytest.raises(ValueError):
        validate_stability_report(bad)


def test_spawn_ipc_delivery_report(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")  # dry-run 测试替身；产品不发送 arm。
        runtime, budget, _ = assembly(client, SystemClock(), latency=0)
        report = asyncio.run(runtime.run())
        assert report["outcome"] == "SUCCESS", report
        assert report["controller_pid"] > 0 and not report["sends_live_input"]
        validate_stability_report(report)
        report["usage"] = budget.snapshot()
        report["hourly"] = hourly_from_usage(
            elapsed_ms_between(report["started_at"], report["finished_at"]), report["usage"]
        )
        output = Path(".runtime/verify")
        output.mkdir(parents=True, exist_ok=True)
        path = output / f"t08_direction_a-{client.session_id}.json"
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)


@pytest.mark.parametrize("emergency", [True, False])
def test_cancel_inflight_wait_and_account_unknown(emergency):
    clock = FakeClock()
    client = MemoryClient(clock)
    entered, release = threading.Event(), threading.Event()
    runtime, budget, calls = assembly(client, clock, entered=entered, release=release)

    async def exercise():
        task = asyncio.create_task(runtime.run())
        try:
            while not entered.is_set():
                await asyncio.sleep(0.005)
            client.core.stop("MANUAL_STOP" if emergency else "FOCUS_LOST", emergency=emergency)
            started = time.monotonic()
            report = await asyncio.wait_for(task, timeout=0.5)
            assert time.monotonic() - started < 0.15  # 非实时 OS；逻辑轮询仍为 30ms。
            assert report["actions"] == []
            assert report["outcome"] == ("MANUAL_STOP" if emergency else "FOCUS_LOST")
            assert runtime.cancel.cancelled
            assert budget.snapshot()["unknown_cost_calls"] == 1
            assert len(calls) == 1
        finally:
            release.set()  # 模拟远端最终返回；不得产生新动作或第二笔账。
        await asyncio.sleep(0.03)
        assert runtime.actions == [] and budget.snapshot()["calls"] == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("disabled", [False, True])
def test_thinking_typed_body_and_no_extra_body(disabled):
    bodies = []

    def transport(request):
        bodies.append(json.loads(request.body))
        return HttpResponse(
            200, b'{"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}]}'
        )

    provider = OpenAICompatibleProvider(
        model="stub", api_key="synthetic", disable_thinking=disabled, transport=transport
    )
    asyncio.run(provider.infer(ModelInput(prompt="json", observation_id="obs", request_id="req")))
    assert ("thinking" in bodies[0]) is disabled
    if disabled:
        assert bodies[0]["thinking"] == {"type": "disabled"}
    with pytest.raises(ValueError):
        PerceptionConfig(disable_thinking="true")
    with pytest.raises(ValueError):
        PerceptionConfig(extra_body={"arbitrary": True})


def test_bad_evidence_invalidates_whole_report(tmp_path):
    report, usage, _ = run_memory()
    report.update(mode="live", sends_live_input=True, usage=usage)  # 仅临时目录测试载荷。
    report["stability_proofs"][0]["equal"] = False
    (tmp_path / "bad.json").write_text(json.dumps(report), encoding="utf-8")
    summary = summarize_reports(tmp_path)
    assert summary["invalid_files"] == ["bad.json"]
    assert not summary["usage"]["complete"]
    assert summary["successes"] == 0


def test_candidate_policy_cannot_be_used_by_live_controller():
    clock = FakeClock()
    client = MemoryClient(clock)
    # 仅改变测试替身配置标签，不创建任何 OSC transport 或子进程。
    client.config = AppConfig(mode="live")
    with pytest.raises(ValueError, match="candidate stability policy"):
        assembly(client, clock)
