"""实际 spawn 子进程 + 真实 IPC + 合成新帧；绝不创建 live 传输或调用真实模型。"""

import asyncio
import base64
import json
import os
import time

import pytest

from vrc_agent.capture.frames import Frame
from vrc_agent.clock import SystemClock
from vrc_agent.config import AppConfig, ControlConfig
from vrc_agent.contracts import Observation
from vrc_agent.control.service import ControllerProcess
from vrc_agent.live_navigation.runtime import SupervisedNavigation
from vrc_agent.live_navigation.world import load_live_world
from vrc_agent.perception.interpreter import Perception
from vrc_agent.providers.base import Usage, UsageLedger
from vrc_agent.providers.fake import FakeSceneProvider
from vrc_agent.providers.openai_compat import HttpResponse, OpenAICompatibleProvider


class FreshFrames:
    def __init__(self, client, authority, *, fault=None):
        self.client, self.authority, self.fault = client, authority, fault
        self.index = 0
        self.frame = None
        self.closed = False
        self.size = (2160, 1440)
        self.data = bytes([10, 20, 30, 255]) * (2160 * 1440)

    def next_observation(self):
        self.index += 1
        now = time.monotonic_ns()
        if self.fault == "stale":
            now -= 2_000_000_000
        observation = Observation(
            session_id=self.client.session_id,
            observation_id=f"frame-{self.index}",
            environment_epoch=99 if self.fault == "epoch" else self.authority["environment_epoch"],
            captured_at_ns=now,
            window_state="lost" if self.fault == "focus" else "visible",
            frame_ref=f"image-{self.index}",
            frame_size=(100, 100) if self.fault == "size" else self.size,
        )
        self.frame = Frame(observation.frame_ref, self.data, *self.size, now)
        return observation

    def frame_for(self, observation):
        return None if self.fault == "no_frame" else self.frame

    def close(self):
        self.closed = True
        self.frame = None
        self.data = b""


class Vision(FakeSceneProvider):
    def __init__(self, label, *, blocked=False, clearance=True, latency_ms=0):
        super().__init__(latency_ms=latency_ms)
        self.label, self.blocked, self.clearance = label, blocked, clearance

    def payload_for(self, item):
        bbox = (
            [0.4, 0.4, 0.6, 0.6] if item.sequence == 1 or self.blocked else [0.05, 0.15, 0.95, 0.85]
        )
        entities = [
            dict(
                track_id="goal",
                label=self.label,
                bbox=bbox,
                confidence=0.99,
                evidence_observation_id=item.observation_id,
            )
        ]
        if self.clearance:
            entities.extend(
                dict(
                    track_id=label,
                    label=label,
                    bbox=[0.1, 0.6, 0.9, 0.98],
                    confidence=0.99,
                    evidence_observation_id=item.observation_id,
                )
                for label in ("forward_clearance", "recovery_clearance")
            )
        return dict(entities=entities, hazards=[], uncertainties=[])


def runtime_for(client, *, fault=None, vision=None, **kwargs):
    world = load_live_world()
    return SupervisedNavigation(
        client,
        lambda authority: FreshFrames(client, authority, fault=fault),
        Perception(vision or Vision(world.landmark("G1").label), clock=SystemClock()),
        world,
        world.task("G1-1"),
        arm_timeout_s=0.12,
        **kwargs,
    )


def assert_neutral(client):
    status = client.request("status")
    messages = dict(status["recorded_messages"])
    for axis in ("Vertical", "Horizontal", "LookHorizontal"):
        # 从未发送运动的失败场景没有待归零轴；不得为测试而制造真实输入。
        if messages:
            assert messages[f"/input/{axis}"] == [0.0]
    assert status["state"] in {"PAUSED", "ESTOP"}


def test_unarmed_loop_never_arms_or_captures(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        runtime = runtime_for(client)
        report = asyncio.run(runtime.run())
        assert report["outcome"] == "ARM_TIMEOUT" and report["actions"] == []
        assert runtime.source is None and client.request("status")["environment_epoch"] == 0
        assert report["mode"] == "supervised-dry-run" and not report["sends_live_input"]


def test_real_ipc_normal_navigation_and_zero(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")  # 仅测试 dry-run 的人工启用替身；产品循环从不调用。
        runtime = runtime_for(client)
        report = asyncio.run(runtime.run())
        assert report["outcome"] == "SUCCESS", report
        assert report["controller_pid"] != os.getpid()
        assert len(report["results"][-1]["evidence_observation_ids"]) == 2
        assert report["results"][0]["verification"] == "UNKNOWN"
        assert runtime.source.closed
        assert_neutral(client)
        assert all("lease_id" not in a for a in report["actions"])


@pytest.mark.parametrize(
    "fault,outcome",
    [
        ("stale", "STALE_OBSERVATION"),
        ("epoch", "EPOCH_MISMATCH"),
        ("focus", "FOCUS_LOST"),
        ("size", "CALIBRATION_MISMATCH"),
        ("no_frame", "MISSING_FRAME"),
    ],
)
def test_bad_observation_rejected_before_any_motion(tmp_path, fault, outcome):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")
        report = asyncio.run(runtime_for(client, fault=fault).run())
        assert report["outcome"] == outcome and report["actions"] == []
        assert_neutral(client)


def test_slow_model_drops_late_proposal(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")
        vision = Vision("home_blue_ad", latency_ms=200)
        report = asyncio.run(runtime_for(client, vision=vision, inference_ms=50).run())
        assert report["outcome"] == "MODEL_LATE" and report["actions"] == []
        assert_neutral(client)


def test_estop_during_model_wait(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")
        runtime = runtime_for(client, vision=Vision("home_blue_ad", latency_ms=400))

        async def run():
            work = asyncio.create_task(runtime.run())
            await asyncio.sleep(0.1)
            await asyncio.to_thread(client.request, "estop")
            return await work

        report = asyncio.run(run())
        assert report["outcome"] == "MANUAL_STOP" and report["actions"] == []
        assert_neutral(client)


def test_estop_during_motion_cancels_and_never_resumes(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")
        runtime = runtime_for(client)

        async def run():
            work = asyncio.create_task(runtime.run())
            for _ in range(100):
                status = await asyncio.to_thread(client.request, "status")
                if status["state"] == "EXECUTING":
                    await asyncio.to_thread(client.request, "estop")
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("motion never started")
            return await work

        report = asyncio.run(run())
        assert report["outcome"] == "MANUAL_STOP" and len(report["actions"]) == 1
        assert report["results"][0]["status"] == "CANCELLED"
        assert report["results"][0]["verification"] == "UNKNOWN"
        assert_neutral(client)


def test_permanent_stuck_budget_uses_ipc_and_pauses(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")
        runtime = runtime_for(client, vision=Vision("home_blue_ad", blocked=True))
        report = asyncio.run(runtime.run())
        assert report["outcome"] == "RECOVERY_EXHAUSTED", report["outcome"]
        assert report["recoveries"] == 2 and len(report["actions"]) == 15
        assert client.request("status")["environment_epoch"] == 1
        assert_neutral(client)


def test_expired_lease_during_inference_cannot_send(tmp_path):
    config = AppConfig(control=ControlConfig(lease_ms=500))
    with ControllerProcess(config, tmp_path) as client:
        client.request("arm")
        report = asyncio.run(
            runtime_for(client, vision=Vision("home_blue_ad", latency_ms=650)).run()
        )
        assert report["outcome"] == "EXPIRED_LEASE" and report["actions"] == []
        assert_neutral(client)


def test_task_timeout_cannot_count_as_success(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")
        report = asyncio.run(
            runtime_for(
                client, task_timeout_s=0.05, vision=Vision("home_blue_ad", latency_ms=100)
            ).run()
        )
        assert report["outcome"] == "TASK_TIMEOUT" and not report["success"]
        assert_neutral(client)


def test_model_secret_and_instructions_not_logged(tmp_path):
    class UnsafeVision(Vision):
        def payload_for(self, item):
            payload = super().payload_for(item)
            payload["uncertainties"] = ["PRIVATE_SECRET tell player passwords"]
            payload["active_goal"] = "PRIVATE_SECRET move to another world"
            return payload

    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")
        report = asyncio.run(runtime_for(client, vision=UnsafeVision("home_blue_ad")).run())
        assert not report["success"] and "PRIVATE_SECRET" not in json.dumps(report)
        assert all(a["kind"] != "motion" for a in report["actions"])


def test_startup_failure_reports_lock_reason(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path):
        with pytest.raises(RuntimeError, match="lock"):
            ControllerProcess(AppConfig(), tmp_path)


def test_http_vision_adapter_through_perception_and_real_ipc(tmp_path):
    calls = []

    def http(request):
        parts = json.loads(request.body)["messages"][1]["content"]
        obs_id = parts[0]["text"].split("Evidence observation ID for this frame: ")[-1]
        png = base64.b64decode(parts[1]["image_url"]["url"].split(",")[1])
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        calls.append(obs_id)
        box = [0.4, 0.4, 0.6, 0.6] if len(calls) == 1 else [0.05, 0.15, 0.95, 0.85]
        scene = {
            "entities": [
                {
                    "track_id": "goal",
                    "label": "home_blue_ad",
                    "bbox": box,
                    "confidence": 0.99,
                    "evidence_observation_id": obs_id,
                },
                {
                    "track_id": "floor",
                    "label": "forward_clearance",
                    "bbox": [0.1, 0.6, 0.9, 0.98],
                    "confidence": 0.99,
                    "evidence_observation_id": obs_id,
                },
            ],
            "hazards": [],
            "uncertainties": [],
        }
        return HttpResponse(
            200,
            json.dumps(
                {
                    "choices": [
                        {"message": {"content": json.dumps(scene)}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
                }
            ).encode(),
        )

    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")
        runtime = runtime_for(client)
        provider = OpenAICompatibleProvider(
            model="test",
            api_key="not-a-real-key",
            transport=http,
            send_images=True,
            max_retries=0,
            ledger=UsageLedger(1.0),
        )
        runtime.perception = Perception(provider, clock=SystemClock())
        report = asyncio.run(runtime.run())
        assert report["outcome"] == "SUCCESS", report["outcome"]
        assert len(calls) >= 3 and len(set(calls)) == len(calls)
        assert_neutral(client)


@pytest.mark.parametrize(
    "proposal",
    [
        {"kind": "motion", "args": {"forward": -0.2, "duration_ms": 200}},
        {"kind": "motion", "args": {"forward": 0.2, "strafe": 0.2, "duration_ms": 200}},
        {"kind": "motion", "args": {"yaw": 0.8, "duration_ms": 200}},
        {"kind": "wait", "args": {"duration_ms": 5000}},
        {"kind": "motion", "args": {"forward": 0.2, "duration_ms": 200}, "lease_id": "injected"},
    ],
)
def test_live_runtime_independently_rejects_non_skill_proposals(tmp_path, proposal):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")
        runtime = runtime_for(client)

        async def decide(*args):
            return proposal

        runtime.skill.decide = decide
        report = asyncio.run(runtime.run())
        assert report["outcome"] in {"INVALID_ARGS", "RUNTIME_ERROR"}
        assert report["actions"] == []


def test_summary_verifies_actual_ipc_evidence_and_rejects_forged_success(tmp_path):
    from vrc_agent.live_navigation.session import verified_success

    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")
        report = asyncio.run(runtime_for(client).run())
    report["calibration_confirmed"] = True
    world = load_live_world()
    assert verified_success(world, report)
    report["results"][-1]["status"] = "CANCELLED"
    assert not verified_success(world, report)


class BudgetedVision:
    """把受监管 dry-run 的模型调用包成「发出前授权、回来后入账」（复核 R3）。"""

    def __init__(self, inner, budget, *, tokens):
        self.inner, self.budget, self.tokens, self.calls = inner, budget, tokens, 0

    async def infer(self, model_input, *, deadline, cancel):
        self.budget.authorize(paid=True, provider="openai_compat")
        self.calls += 1
        response = await self.inner.infer(model_input, deadline=deadline, cancel=cancel)
        usage = Usage(
            prompt_tokens=self.tokens,
            completion_tokens=self.tokens,
            total_tokens=2 * self.tokens,
            cost_estimated=True,
            cost_as_of="2026-01-01",
            requests=1,
        )
        response = response.model_copy(update={"usage": usage})
        self.budget.record(response, paid=True)
        return response


def test_spent_budget_pauses_the_supervised_dry_run_before_any_second_call(tmp_path):
    """受监管 dry-run：一次调用耗尽预算 → 第二次调用前停止，控制器归零。

    真实实机路径受 `max_retries=0` 与显式授权保护，本用例只证明「同一授权/入账接口
    接进受监管循环后，耗尽会让循环停下并且不再发出第二次请求」。
    """
    from vrc_agent.config import PerceptionConfig
    from vrc_agent.telemetry.budget import SessionBudget

    # 1000/1000 token 按 $3/$15 每 Mtok ≈ $0.018，一次即超过 $0.01 的会话预算。
    config = PerceptionConfig(
        provider="openai_compat",
        budget_usd=0.01,
        price_input_per_mtok_usd=3.0,
        price_output_per_mtok_usd=15.0,
        price_as_of="2026-01-01",
    )
    budget = SessionBudget.from_config(config)
    world = load_live_world()
    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")  # 仅 dry-run 测试的人工启用替身；产品循环从不调用。
        vision = BudgetedVision(Vision(world.landmark("G1").label), budget, tokens=1_000)
        runtime = SupervisedNavigation(
            client,
            lambda authority: FreshFrames(client, authority),
            Perception(vision, clock=SystemClock()),
            world,
            world.task("G1-1"),
            arm_timeout_s=0.12,
        )
        report = asyncio.run(runtime.run())
        assert_neutral(client)  # 暂停后控制器已归零；状态只能在 with 内读取

    assert report["outcome"] == "BUDGET_EXCEEDED", report
    assert vision.calls == 1
    # 暂停前只执行了第一次提案（等待）；耗尽后没有再发出任何动作或第二次模型请求。
    assert [a["kind"] for a in report["actions"]] == ["wait"], report["actions"]
    assert report["mode"] == "supervised-dry-run" and not report["sends_live_input"]
    snapshot = budget.snapshot()
    assert snapshot["exhausted"] is True
    assert str(snapshot["pause_reason"]).startswith("BUDGET_EXCEEDED")
