"""v2 世界装配及报告边界：全部为合成场景/dry-run，无现场输入。"""

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_stability_v4_runtime import V4Frames
from test_stable_navigation import MemoryClient

from vrc_agent.clock import FakeClock, SystemClock
from vrc_agent.config import AppConfig
from vrc_agent.control.service import ControllerProcess
from vrc_agent.live_navigation import session
from vrc_agent.live_navigation.skill import calibrated_prompt
from vrc_agent.live_navigation.stability_report import validate_stability_report
from vrc_agent.live_navigation.stable_runtime import StableNavigationV5
from vrc_agent.live_navigation.world import load_live_world
from vrc_agent.providers.base import ModelResponse

BOXES = {"G3": [0.215, 0.205, 0.755, 0.775], "G5": [0.09, 0.42, 0.93, 0.89]}


def assemble(client, clock, group):
    world = load_live_world("v2")
    inputs = []

    class Provider:
        paid = False

        async def infer(self, request, **kwargs):
            inputs.append(request)
            if isinstance(client, MemoryClient):
                client.advance(3500)
            return ModelResponse(
                request_id=request.request_id,
                observation_id=request.observation_id,
                provider="synthetic",
                model="world-v2-test",
                payload={
                    "entities": [
                        dict(
                            track_id="goal",
                            label=world.landmark(group).label,
                            bbox=BOXES[group],
                            confidence=0.99,
                            evidence_observation_id=request.observation_id,
                        )
                    ],
                    "hazards": [],
                    "uncertainties": [],
                },
            )

    runtime = StableNavigationV5(
        client,
        lambda _: V4Frames(client, clock),
        SimpleNamespace(provider=Provider()),
        world,
        world.task(group + "-1"),
        clock=clock,
    )
    return runtime, inputs


@pytest.mark.parametrize("group", ["G3", "G5"])
def test_v2_runtime_uses_selected_rules_and_prompt_and_verifies_report(group, tmp_path):
    clock = FakeClock()
    client = MemoryClient(clock)
    runtime, inputs = assemble(client, clock, group)
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "SUCCESS" and report["world_version"] == "vrchat-home-live-v2"
    assert report["suite_digest"] == load_live_world("v2").digest()
    assert client.core.active is None and client.core.state == "PAUSED"
    assert all(a["kind"] == "wait" for a in report["actions"])
    assert all(
        request.prompt == calibrated_prompt(runtime.world.landmark(group)) for request in inputs
    )
    validate_stability_report(report)
    report["calibration_confirmed"] = True  # 合成数值验证，不是现场确认。
    assert session.verified_success(runtime.world, report)
    assert not session.verified_success(load_live_world(), report)
    # 汇总测试只改报告来源标签，实际运行始终 dry-run；不可作为现场证据。
    report.update(mode="live", sends_live_input=True)
    (tmp_path / "synthetic.json").write_text(json.dumps(report), encoding="utf-8")
    result = session.summarize_reports(tmp_path, world_version="v2")
    assert result["successes"] == 1 and not result["threshold_met"]
    assert len(session.summarize_reports(tmp_path)["invalid_files"]) == 1
    if group == "G5":
        uncertain = copy.deepcopy(report)
        for event in uncertain["events"]:
            if event["event"] == "observation":
                event["has_uncertainties"] = True
        assert not session.verified_success(runtime.world, uncertain)


def test_v2_reports_cannot_mix_with_v1_or_hide_version(tmp_path):
    old, new = load_live_world(), load_live_world("v2")
    base = dict(
        mode="live",
        sends_live_input=True,
        suite_digest=new.digest(),
        world_version=new.version,
        task_id="G3-1",
        outcome="MANUAL_STOP",
        final_state="ESTOP",
    )
    (tmp_path / "new.json").write_text(json.dumps(base), encoding="utf-8")
    previous = {**base, "suite_digest": old.digest(), "world_version": old.version}
    (tmp_path / "old.json").write_text(json.dumps(previous), encoding="utf-8")
    hidden = {**base}
    hidden.pop("world_version")
    (tmp_path / "hidden.json").write_text(json.dumps(hidden), encoding="utf-8")
    wrong = {**base, "world_version": old.version}
    (tmp_path / "wrong.json").write_text(json.dumps(wrong), encoding="utf-8")
    result = session.summarize_reports(tmp_path, world_version="v2")
    assert set(result["invalid_files"]) == {"old.json", "hidden.json", "wrong.json"}
    assert result["trials"][8]["outcome"] == "MANUAL_STOP"  # G3-1，只有一条有效尝试。
    assert result["usage"]["complete"] is False


def test_world_v2_does_not_enable_candidate_live(monkeypatch):
    config = AppConfig(
        navigation_design="stable-inference-v5",
        perception={"provider": "openai_compat", "model": "synthetic", "budget_usd": 1.0},
    )
    monkeypatch.setattr(session, "load_config", lambda _: config)

    def sentinel(*args, **kwargs):
        pytest.fail("candidate must reject before resources")

    for name in ("build_provider", "ControllerProcess", "live_capture"):
        monkeypatch.setattr(session, name, sentinel)
    args = SimpleNamespace(
        task="G3-1",
        world_version="v2",
        mode="live",
        config="unused",
        confirm_calibration=True,
        allow_paid=True,
        send_images=True,
    )
    with pytest.raises(ValueError, match="field calibration"):
        asyncio.run(session.navigation_entry(args))


def test_v2_dry_run_subprocess_report(tmp_path):
    with ControllerProcess(AppConfig(), tmp_path) as client:
        client.request("arm")  # dry-run only
        runtime, _ = assemble(client, SystemClock(), "G3")
        report = asyncio.run(runtime.run())
        assert report["outcome"] == "SUCCESS", report
        validate_stability_report(report)
        assert report["world_version"] == "vrchat-home-live-v2"
        assert report["sends_live_input"] is False
        path = Path(".runtime/verify") / f"t08_world_v2-{client.session_id}.json"
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
