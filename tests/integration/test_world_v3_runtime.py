"""世界v3候选的内存闭环、资源前拒绝与报告重算；不创建真实资源。"""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from test_navigation_v6 import assert_zero
from test_stability_v9_runtime import assemble_v9

from vrc_agent.live_navigation import session
from vrc_agent.live_navigation.stability_report import validate_stability_report
from vrc_agent.live_navigation.stable_runtime import StableNavigationV9
from vrc_agent.live_navigation.world import load_live_world


def assemble_world_v3(group="G2", confidence=0.72, **kwargs):
    old, client, calls = assemble_v9(**kwargs)
    world = load_live_world("v3")
    infer = old.perception.provider.infer

    async def controlled(*args, **kw):
        response = await infer(*args, **kw)
        payload = copy.deepcopy(response.payload)
        for entity in payload["entities"]:
            if entity["label"] == old.skill.goal.label:
                entity.update(label=world.landmark(group).label, confidence=confidence)
                # G4到达框比G1/G2小；首帧必须明确未到达，才会请求前进。
                if group == "G4" and len(calls) == 1:
                    entity["bbox"] = [0.47, 0.47, 0.53, 0.53]
        return response.model_copy(update={"payload": payload})

    old.perception.provider.infer = controlled
    runtime = StableNavigationV9(
        client,
        old.source_factory,
        old.perception,
        world,
        world.task(group + "-1"),
        clock=client.clock,
    )
    return runtime, client, calls


def test_candidate_world_refuses_live_before_any_resource(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("candidate world created a resource")

    for name in (
        "load_config",
        "build_provider",
        "ControllerProcess",
        "live_capture",
        "Perception",
    ):
        monkeypatch.setattr(session, name, forbidden)
    args = SimpleNamespace(
        task="G2-1",
        world_version="v3",
        mode="live",
        config="unused",
        confirm_calibration=True,
        allow_paid=True,
        send_images=True,
        state_dir=str(tmp_path),
    )
    with pytest.raises(ValueError, match="candidate world"):
        asyncio.run(session.navigation_entry(args))
    args.mode = "dry-run"
    plan = asyncio.run(session.navigation_entry(args))
    assert plan["mode"] == "plan-only" and plan["sends_live_input"] is False
    assert plan["world"]["status"] == "REQUIRES_FIELD_CALIBRATION"
    runtime, client, calls = assemble_world_v3()
    client.config = client.config.model_copy(update={"mode": "live"})
    with pytest.raises(ValueError, match="candidate world"):
        StableNavigationV9(client, forbidden, forbidden, runtime.world, runtime.task)
    assert not calls and not client.transport.messages


@pytest.fixture(scope="module")
def report_world_v3():
    runtime, client, calls = assemble_world_v3()
    report = asyncio.run(runtime.run())
    assert calls and report["outcome"] == "SUCCESS"
    assert report["schema_version"] == 12
    assert report["run_parameters"]["target_min_confidence"] == 0.7
    assert report["suite_digest"] == load_live_world("v3").digest()
    validate_stability_report(report)
    validate_stability_report(json.loads(json.dumps(report)))
    assert_zero(client)
    return report


def test_world_v3_threshold_is_recorded_and_recomputed(report_world_v3):
    assert report_world_v3["world_version"] == "vrchat-home-live-v3"


@pytest.mark.parametrize("fault", ["missing", 0.85, 0.9, True, "0.7", "digest", "live", "send"])
def test_threshold_and_candidate_report_forgery_rejected(report_world_v3, fault):
    report = copy.deepcopy(report_world_v3)
    if fault == "missing":
        del report["run_parameters"]["target_min_confidence"]
    elif fault == "digest":
        report["suite_digest"] = load_live_world("v2").digest()
    elif fault == "live":
        report["mode"] = "live"
    elif fault == "send":
        report["sends_live_input"] = True
    else:
        report["run_parameters"]["target_min_confidence"] = fault
    with pytest.raises(ValueError):
        validate_stability_report(report)


@pytest.mark.parametrize("group", ["G2", "G4"])
def test_lower_target_threshold_preserves_dirty_scene_and_path_semantics(group):
    runtime, client, _ = assemble_world_v3(group, dirty=True)
    report = asyncio.run(runtime.run())
    assert any(a["kind"] == "motion" for a in report["actions"])
    assert all(r["verification"] != "SUCCESS" for r in report["results"])
    validate_stability_report(report)
    assert_zero(client)
    runtime, client, _ = assemble_world_v3(group, dirty=True, no_clearance=True)
    report = asyncio.run(runtime.run())
    assert report["actions"] and all(a["kind"] == "wait" for a in report["actions"])
    validate_stability_report(report)
    assert_zero(client)


def test_world_v2_new_and_historical_report_threshold_compatibility():
    runtime, client, _ = assemble_v9()
    report = asyncio.run(runtime.run())
    assert report["run_parameters"]["target_min_confidence"] == 0.85
    validate_stability_report(report)
    report["run_parameters"]["target_min_confidence"] = 0.9
    with pytest.raises(ValueError, match="threshold"):
        validate_stability_report(report)
    del report["run_parameters"]["target_min_confidence"]
    validate_stability_report(report)
    assert_zero(client)
