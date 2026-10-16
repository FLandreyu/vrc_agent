"""v5 声明放行部分交叠，完全遮罩拒绝；不接实机或外部服务。"""

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_stability_v4_runtime import V4Frames
from test_stable_navigation import MemoryClient

from vrc_agent.capture.frames import Frame
from vrc_agent.clock import FakeClock, SystemClock
from vrc_agent.config import AppConfig, PerceptionConfig
from vrc_agent.control.service import ControllerProcess
from vrc_agent.live_navigation import session
from vrc_agent.live_navigation.stability_report import validate_stability_report
from vrc_agent.live_navigation.stable_runtime import (
    StableNavigation,
    StableNavigationV5,
    stable_navigation_class,
)
from vrc_agent.live_navigation.world import load_live_world
from vrc_agent.perception.interpreter import Perception
from vrc_agent.providers.openai_compat import HttpResponse, OpenAICompatibleProvider
from vrc_agent.telemetry.budget import SessionBudget

PARTIAL = [1400 / 2160, 100 / 1440, 1520 / 2160, 200 / 1440]
FULL = [1536 / 2160, 100 / 1440, 1540 / 2160, 200 / 1440]


def assemble(client, clock, *, box=None, change=None, fault=None, unstable=False):
    world, calls = load_live_world(), []
    budget = SessionBudget.from_config(PerceptionConfig(budget_usd=1.0))

    class Frames(V4Frames):
        def next_observation(self):
            obs = super().next_observation()
            if change and calls:
                data = bytearray(self.frame.data)
                x = 1540 if change == "masked" else 1450
                at = (150 * 2160 + x) * 4
                # 每次推理返回后再次变化，避免新参考正确纳入一次性变化后恢复执行。
                value = 100 if change == "masked" or len(calls) % 2 else 140
                data[at : at + 3] = bytes([value]) * 3
                self.frame = Frame(self.frame.ref, bytes(data), 2160, 1440, obs.captured_at_ns)
            return obs

    def http(request):
        body = json.loads(request.body)
        obs = body["messages"][1]["content"][0]["text"].split(
            "Evidence observation ID for this frame: "
        )[-1]
        calls.append(obs)
        if isinstance(client, MemoryClient):
            client.advance(8100 if fault == "timeout" else 3500)
            if fault == "lease":
                client.advance(30000)
            if fault == "estop":
                client.core.stop("MANUAL_STOP", emergency=True)
        goal = box or ([0.4, 0.4, 0.6, 0.6] if len(calls) == 1 else [0.05, 0.19, 0.95, 0.9])
        payload = dict(
            entities=[
                dict(
                    track_id="goal",
                    label=world.landmark("G1").label,
                    bbox=goal,
                    confidence=0.99,
                    evidence_observation_id=obs,
                ),
                dict(
                    track_id="clear",
                    label="forward_clearance",
                    bbox=[0.2, 0.6, 0.9, 0.98],
                    confidence=0.99,
                    evidence_observation_id=obs,
                ),
            ],
            hazards=[],
            uncertainties=[],
        )
        return HttpResponse(
            200,
            json.dumps(
                {
                    "choices": [
                        {"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}
                    ]
                }
            ).encode(),
        )

    provider = OpenAICompatibleProvider(
        model="memory",
        api_key="synthetic",
        clock=clock,
        transport=http,
        send_images=True,
        max_retries=0,
        prepare_in_thread=True,
        accounting=budget,
    )
    runtime = StableNavigationV5(
        client,
        lambda _: Frames(client, clock, unstable=unstable),
        Perception(provider, clock=clock),
        world,
        world.task("G1-1"),
        clock=clock,
    )
    if box:
        runtime.skill.limits = runtime.skill.limits.model_copy(update={"max_actions": 1})
    return runtime, budget, calls


def memory(**kwargs):
    clock = FakeClock()
    client = MemoryClient(clock)
    runtime, budget, calls = assemble(client, clock, **kwargs)
    report = asyncio.run(runtime.run())
    validate_stability_report(report)
    assert client.core.active is None and client.core.state in {"PAUSED", "ESTOP"}
    assert all(result["status"] == "COMPLETED" for result in report["results"])
    return runtime, client, report, budget, calls


def test_partial_masked_change_declared_allowed_not_a_missed_detection():
    _, _, report, _, calls = memory(box=PARTIAL, change="masked")
    assert len(report["actions"]) == 1 and report["actions"][0]["kind"] == "motion"
    assert report["schema_version"] == 6 and report["navigation_design"] == "stable-inference-v5"
    assert report["stability_policy"]["protected_mask_policy"] == "exclude_masked"
    assert len(calls) == 2
    assert all(p["stable"] for p in report["stability_proofs"])
    assert report["stability_proofs"][0]["protected_masked_px"] > 0
    source = next(e for e in report["events"] if e["event"] == "inference_source")
    assert source["protected_masked_px"] > 0 and source["entity_masked_px"] == 0


def test_unmasked_change_rejected_after_partial_overlap():
    _, _, report, _, _ = memory(box=PARTIAL, change="visible")
    assert not report["actions"] and report["outcome"] == "SCENE_UNSTABLE"
    assert report["stability_proofs"][0]["protected_changed"] == 1


def test_fully_masked_raw_bbox_stops_even_if_margin_visible():
    _, _, report, _, calls = memory(box=FULL)
    assert report["outcome"] == "MASKED_SCENE_EVIDENCE"
    assert not report["actions"] and len(calls) == 1


def test_derive_and_before_submit_apply_same_visibility_rule():
    runtime, _, report, _, _ = memory(box=PARTIAL)
    source, frame, scene = runtime.reference
    masked_entity = scene.entities[0].model_copy(update={"bbox": tuple(FULL)})
    hidden_scene = scene.model_copy(update={"entities": (masked_entity,)})
    # derive 对全部实体实施原 bbox 完全遮罩检查；不依赖标签过滤。
    assert runtime.masked_scene(hidden_scene)
    assert not runtime.masked_scene(scene)
    runtime.reference = source, frame, hidden_scene

    async def sentinel(*args, **kwargs):
        pytest.fail("must reject before comparison")

    runtime.fresh_proof = sentinel
    with pytest.raises(Exception, match="MASKED_SCENE_EVIDENCE"):
        asyncio.run(runtime.before_submit(source, hidden_scene))
    assert report["actions"]  # 部分交叠对照确实可派发。


@pytest.mark.parametrize(
    "kwargs,outcome",
    [
        ({"unstable": True}, "WARMUP_UNSTABLE"),
        ({"fault": "timeout"}, "INFERENCE_TIMEOUT"),
        ({"fault": "lease"}, "EXPIRED_LEASE"),
        ({"fault": "estop"}, "MANUAL_STOP"),
    ],
)
def test_v5_failure_zero_actions(kwargs, outcome):
    _, _, report, _, _ = memory(**kwargs)
    assert report["outcome"] == outcome and not report["actions"]


def test_v5_report_tampering_and_schema_isolation(tmp_path):
    _, _, report, budget, _ = memory()
    assert report["outcome"] == "SUCCESS"
    bad = []
    for key, value in [
        ("protected_masked_px", -1),
        ("protected_visible_px", True),
        ("entity_masked_px", 1),
        ("entity_rectangles", []),
        ("protected_rectangles", []),
        ("protected_changed", 1),
    ]:
        changed = copy.deepcopy(report)
        changed["stability_proofs"][0][key] = value
        bad.append(changed)
    for schema in (5, 6.0):
        changed = copy.deepcopy(report)
        changed["schema_version"] = schema
        bad.append(changed)
    changed = copy.deepcopy(report)
    source = next(e for e in changed["events"] if e["event"] == "inference_source")
    source["protected_masked_px"] = -1
    bad.append(changed)
    changed = copy.deepcopy(report)
    source = next(e for e in changed["events"] if e["event"] == "inference_source")
    source["protected_rectangles"][0][0] = float(source["protected_rectangles"][0][0])
    bad.append(changed)
    changed = copy.deepcopy(report)
    changed["warmup_references"] = {}
    bad.append(changed)
    changed = copy.deepcopy(report)
    changed["navigation_design"] = "stable-inference-v4"
    bad.append(changed)
    for i, changed in enumerate(bad):
        with pytest.raises(ValueError):
            validate_stability_report(changed)
        changed.update(mode="live", sends_live_input=True, usage=budget.snapshot())
        (tmp_path / f"bad-{i}.json").write_text(json.dumps(changed), encoding="utf-8")
    assert len(session.summarize_reports(tmp_path)["invalid_files"]) == len(bad)


@pytest.mark.parametrize("version", ["v1", "v2", "v3", "v4", "v5"])
def test_all_candidate_versions_rejected_before_resource_creation(monkeypatch, version):
    config = AppConfig(
        navigation_design="stable-inference-" + version,
        perception={"provider": "openai_compat", "model": "synthetic", "budget_usd": 1.0},
    )
    monkeypatch.setattr(session, "load_config", lambda _: config)

    def sentinel(*args, **kwargs):
        pytest.fail("must reject before live resources")

    for name in ("build_provider", "ControllerProcess", "live_capture"):
        monkeypatch.setattr(session, name, sentinel)
    args = SimpleNamespace(
        task="G1-1",
        mode="live",
        config="unused",
        confirm_calibration=True,
        allow_paid=True,
        send_images=True,
    )
    with pytest.raises(ValueError, match="field calibration"):
        asyncio.run(session.navigation_entry(args))


def test_factory_and_runtime_candidate_rejection():
    assert stable_navigation_class("v5") is StableNavigationV5
    with pytest.raises(ValueError, match="must match"):
        StableNavigation(policy_version="v5")
    client = MemoryClient(FakeClock())
    client.config = AppConfig(mode="live")
    with pytest.raises(ValueError, match="candidate stability"):
        assemble(client, client.clock)


def test_v5_real_dry_run_process_report(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")  # dry-run only
        runtime, budget, _ = assemble(client, SystemClock())
        report = asyncio.run(runtime.run())
        assert report["outcome"] == "SUCCESS", report
        validate_stability_report(report)
        report["usage"] = budget.snapshot()
        path = Path(".runtime/verify") / f"t08_stability_v5-{client.session_id}.json"
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
