"""v4 运行时：判据同 v3，另在保护矩形与遮罩相交时于派生/执行前中止（复核 F1）。"""

import asyncio
import copy
import json
from pathlib import Path

import pytest
from test_stable_navigation import Frames, MemoryClient

from vrc_agent.capture.frames import Frame
from vrc_agent.clock import FakeClock, SystemClock
from vrc_agent.config import AppConfig, PerceptionConfig
from vrc_agent.control.service import ControllerProcess
from vrc_agent.live_navigation.session import summarize_reports
from vrc_agent.live_navigation.stability_report import validate_stability_report
from vrc_agent.live_navigation.stable_runtime import StableNavigationV4
from vrc_agent.live_navigation.world import load_live_world
from vrc_agent.perception.interpreter import Perception
from vrc_agent.providers.openai_compat import HttpResponse, OpenAICompatibleProvider
from vrc_agent.telemetry.budget import SessionBudget
from vrc_agent.telemetry.report import elapsed_ms_between, hourly_from_usage

# 复核 F1 的最小反例：目标 bbox 像素 [1400,100,1520,200]，外扩后右侧进入遮罩。
MASKED_BOX = [1400 / 2160, 100 / 1440, 1520 / 2160, 200 / 1440]


class V4Frames(Frames):
    def __init__(self, *args, transition=False, unstable=False):
        super().__init__(*args)
        self.transition, self.unstable = transition, unstable

    def next_observation(self):
        if isinstance(self.clock, FakeClock):
            self.clock.advance_ms(100)
        obs = super().next_observation()
        value = 20 + self.index % 3
        if (self.transition and self.index == 1) or (self.unstable and self.index % 2):
            value = 100
        self.frame = Frame(
            obs.frame_ref,
            bytes([value] * 3 + [255]) * (2160 * 1440),
            2160,
            1440,
            obs.captured_at_ns,
        )
        return obs


def assemble(client, clock, *, fault=None, transition=False, unstable=False, masked=False):
    world = load_live_world()
    calls = []
    budget = SessionBudget.from_config(PerceptionConfig(budget_usd=1.0))

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
        if masked:
            goal = MASKED_BOX
        else:
            goal = [0.4, 0.4, 0.6, 0.6] if len(calls) == 1 else [0.1, 0.4, 0.95, 0.95]
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
    runtime = StableNavigationV4(
        client,
        lambda _: V4Frames(client, clock, transition=transition, unstable=unstable),
        Perception(provider, clock=clock),
        world,
        world.task("G1-1"),
        clock=clock,
    )
    return runtime, budget, calls


def memory(**kwargs):
    clock = FakeClock()
    client = MemoryClient(clock)
    runtime, budget, calls = assemble(client, clock, **kwargs)
    report = asyncio.run(runtime.run())
    validate_stability_report(report)
    assert client.core.state in {"PAUSED", "ESTOP"}
    assert client.core.active is None
    return report, budget.snapshot(), calls


def test_v4_success_uses_schema_five_and_keeps_area_policy():
    report, _, calls = memory(transition=True)
    assert report["outcome"] == "SUCCESS", report
    assert report["schema_version"] == 5 and len(calls) == 3
    assert report["navigation_design"] == "stable-inference-v4"
    assert report["stability_policy"]["protected_mask_policy"] == "reject_on_overlap"
    assert not report["warmup_checks"][0]["stable"]
    assert report["warmup_references"][calls[0]] == [1, 2]
    assert all(p["stable"] for p in report["stability_proofs"])
    assert all("max_tile_changed" not in p for p in report["stability_proofs"])


def test_masked_protection_stops_before_any_action():
    report, _, calls = memory(masked=True)
    assert report["outcome"] == "MASKED_SCENE_EVIDENCE", report
    assert not report["actions"] and len(calls) == 1
    # 中止发生在比较之前：没有稳定性证据，也没有任何动作证据。
    assert report["stability_proofs"] == []


@pytest.mark.parametrize(
    "kwargs,outcome",
    [
        ({"unstable": True}, "WARMUP_UNSTABLE"),
        ({"fault": "timeout"}, "INFERENCE_TIMEOUT"),
        ({"fault": "lease"}, "EXPIRED_LEASE"),
        ({"fault": "estop"}, "MANUAL_STOP"),
    ],
)
def test_failure_no_actions(kwargs, outcome):
    report, _, calls = memory(**kwargs)
    assert report["outcome"] == outcome and not report["actions"], report
    if kwargs.get("unstable"):
        assert calls == [] and len(report["warmup_checks"]) == 7


def test_v4_report_mutations_and_whole_file_rejection(tmp_path):
    report, usage, _ = memory()
    bad_reports = []
    for key, value in (
        ("changed_pixels", -1),
        ("protected_changed", 1),
        ("protected_rectangles", []),
        ("stable", False),
        ("max_tile_changed", None),
        ("retained_pixels", 2_848_256.0),
    ):
        bad = copy.deepcopy(report)
        bad["stability_proofs"][0][key] = value
        bad_reports.append(bad)
    masked = copy.deepcopy(report)
    masked["stability_proofs"][0]["protected_rectangles"] = [[1368, 68, 1552, 232]]
    bad_reports.append(masked)
    bad = copy.deepcopy(report)
    bad["warmup_references"] = {}
    bad_reports.append(bad)
    bad = copy.deepcopy(report)
    bad["schema_version"] = 4
    bad_reports.append(bad)
    bad = copy.deepcopy(report)
    bad["navigation_design"] = "stable-inference-v3"
    bad_reports.append(bad)
    for index, bad in enumerate(bad_reports):
        with pytest.raises(ValueError):
            validate_stability_report(bad)
        bad.update(mode="live", sends_live_input=True, usage=usage)
        (tmp_path / f"bad-{index}.json").write_text(json.dumps(bad), encoding="utf-8")
    summary = summarize_reports(tmp_path)
    assert len(summary["invalid_files"]) == len(bad_reports)
    assert not summary["usage"]["complete"]


def test_v4_candidate_live_rejected():
    clock = FakeClock()
    client = MemoryClient(clock)
    client.config = AppConfig(mode="live")
    with pytest.raises(ValueError, match="candidate stability policy"):
        assemble(client, clock, transition=True)


def test_v4_spawn_report(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")  # dry-run only
        runtime, budget, _ = assemble(client, SystemClock(), transition=True)
        report = asyncio.run(runtime.run())
        assert report["outcome"] == "SUCCESS", report
        validate_stability_report(report)
        report["usage"] = budget.snapshot()
        report["hourly"] = hourly_from_usage(
            elapsed_ms_between(report["started_at"], report["finished_at"]), report["usage"]
        )
        path = Path(".runtime/verify") / f"t08_stability_v4-{client.session_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False)
