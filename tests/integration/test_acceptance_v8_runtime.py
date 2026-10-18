"""v8 候选装配/报告重算；仅内存帧、内存控制器和模拟 HTTP。"""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from test_navigation_v6 import assembly

from vrc_agent.config import AppConfig
from vrc_agent.live_navigation import session
from vrc_agent.live_navigation.runtime import SupervisedNavigation
from vrc_agent.live_navigation.stability_report import validate_stability_report
from vrc_agent.live_navigation.stable_runtime import StableNavigationV8, stable_navigation_class


def assemble_v8(dirty=False, distractor=False, no_clearance=False):
    old, client, calls = assembly()
    client.config = AppConfig(
        navigation_design="stable-inference-v8",
        navigation_motion_profile="field",
        control={"observation_max_age_ms": 4000},
    )
    client.core.config = client.config.control
    infer = old.perception.provider.infer

    async def controlled(*args, **kwargs):
        response = await infer(*args, **kwargs)
        payload = copy.deepcopy(response.payload)
        if dirty:
            payload.update(hazards=["synthetic obstacle"], uncertainties=["synthetic unknown"])
        if no_clearance:
            payload["entities"] = [
                e for e in payload["entities"] if e["label"] != "forward_clearance"
            ]
            payload["entities"][0]["bbox"] = [0.4, 0.4, 0.6, 0.6]
        if distractor:
            payload["entities"].append(
                {
                    **payload["entities"][0],
                    "label": "irrelevant",
                    "track_id": "low",
                    "confidence": 0.6,
                }
            )
        return response.model_copy(update={"payload": payload})

    old.perception.provider.infer = controlled
    runtime = StableNavigationV8(
        client, old.source_factory, old.perception, old.world, old.task, clock=client.clock
    )
    return runtime, client, calls


def test_candidate_refuses_live_before_resources_and_base_bypass(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="independent ControllerProcess"):
        SupervisedNavigation(object(), None, None, None, None)
    config = AppConfig(
        navigation_design="stable-inference-v8",
        control={"observation_max_age_ms": 4000},
        perception={"provider": "openai_compat", "model": "memory", "budget_usd": 1.0},
    )
    monkeypatch.setattr(session, "load_config", lambda _: config)

    def forbidden(*args, **kwargs):
        pytest.fail("candidate created a resource")

    for name in ("build_provider", "ControllerProcess", "live_capture", "Perception"):
        monkeypatch.setattr(session, name, forbidden)
    args = SimpleNamespace(
        task="G2-1",
        world_version="v2",
        mode="live",
        config="unused",
        confirm_calibration=True,
        allow_paid=True,
        send_images=True,
        state_dir=str(tmp_path),
    )
    with pytest.raises(ValueError, match="field calibration"):
        asyncio.run(session.navigation_entry(args))
    runtime, client, calls = assemble_v8()
    with pytest.raises(ValueError, match="requires v8 runtime"):
        SupervisedNavigation(client, None, None, runtime.world, runtime.task)
    client.config = client.config.model_copy(update={"mode": "live"})
    with pytest.raises(ValueError, match="candidate"):
        StableNavigationV8(client, None, None, runtime.world, runtime.task)
    assert not calls and not client.transport.messages
    assert stable_navigation_class("v8") is StableNavigationV8


@pytest.fixture(scope="module")
def report_v8():
    runtime, client, _ = assemble_v8(distractor=True)
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "SUCCESS" and report["schema_version"] == 11
    assert client.core.active is None
    validate_stability_report(report)
    return report


def test_scope_separates_irrelevant_minimum_and_success_needs_clean_evidence(report_v8):
    source = next(e for e in report_v8["events"] if e["event"] == "inference_source")
    assert source["source_confidence"] == 0.6 and source["confidence"] == 0.99
    assert report_v8["run_parameters"]["freshness_ms"] == 4000
    assert report_v8["run_parameters"]["max_actions"] == 40


def test_signals_allow_evidenced_motion_but_never_fake_success():
    runtime, client, _ = assemble_v8(dirty=True, distractor=True)
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "EXPIRED_LEASE"
    assert any(a["kind"] == "motion" for a in report["actions"])
    assert all(r["verification"] != "SUCCESS" for r in report["results"])
    feedback = [e for e in report["events"] if e["event"] == "navigation_feedback"]
    assert feedback and all(e["evidence"]["valid_target"] for e in feedback)
    assert all(e["evidence"]["acceptance"]["scope"]["has_hazards"] for e in feedback)
    assert client.core.active is None
    decisions = [e["audit"] for e in report["events"] if e["event"] == "acceptance_decision"]
    assert any(a["proposal_permitted"] and a["path_safety"]["has_hazards"] for a in decisions)
    assert any(
        a["arrival_geometry_met"]
        and a["arrival_review_required"]
        and not a["scope"]["automatic_success_allowed"]
        for a in decisions
    )
    validate_stability_report(report)
    bad = copy.deepcopy(report)
    row = next(e for e in bad["events"] if e["event"] == "navigation_feedback")
    row["evidence"]["acceptance"]["arrival_review_required"] = False
    with pytest.raises(ValueError):
        validate_stability_report(bad)
    bad = copy.deepcopy(report)
    bad["results"][0]["verification"] = "SUCCESS"
    with pytest.raises(ValueError):
        validate_stability_report(bad)


def test_signals_without_clearance_still_pause():
    runtime, client, _ = assemble_v8(dirty=True, no_clearance=True)
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "PATH_UNSAFE"
    assert all(a["kind"] == "wait" for a in report["actions"])
    assert all(r["verification"] != "SUCCESS" for r in report["results"])
    assert client.core.active is None
    validate_stability_report(report)


@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "semantics",
        "missing",
        "recognition",
        "path",
        "scope",
        "meta",
        "arrival",
        "decision",
        "live",
        "generated",
        "old_schema",
        "cache_missing",
        "cache_mismatch",
        "false_success",
        "invalid_source_confidence",
        "motion_effect",
        "arrival_effect",
        "missing_signals",
        "signal_type",
        "previous_digest",
    ],
)
def test_report_rejects_forgery_and_missing_fields(report_v8, fault):
    report = copy.deepcopy(report_v8)
    decision = next(e for e in report["events"] if e["event"] == "acceptance_decision")
    feedback = next(e for e in report["events"] if e["event"] == "navigation_feedback")
    if fault == "schema":
        report["schema_version"] = 10
    elif fault == "semantics":
        report["acceptance_semantics"] = "v6"
    elif fault == "missing":
        del feedback["evidence"]["acceptance"]
    elif fault == "recognition":
        decision["audit"]["target_recognition"]["unique_label"] = False
    elif fault == "path":
        decision["audit"]["path_safety"]["forward_clearance"] = False
    elif fault == "scope":
        decision["audit"]["scope"]["confidence"] = 0.6
    elif fault == "arrival":
        feedback["evidence"]["acceptance"]["arrival_geometry_met"] = not feedback["evidence"][
            "acceptance"
        ]["arrival_geometry_met"]
    elif fault == "meta":
        del next(e for e in report["events"] if e["event"] == "observation")[
            "observation_metadata"
        ]["frame_ref"]
    elif fault == "decision":
        report["events"].remove(decision)
    elif fault == "live":
        report.update(mode="live", sends_live_input=True)
    elif fault == "generated":
        next(e for e in report["events"] if e["event"] == "observation")[
            "scene_generated_at_ns"
        ] = 0
    elif fault == "cache_missing":
        del report["acceptance_observations"]
    elif fault == "cache_mismatch":
        report["acceptance_observations"][0]["frame_ref"] = None
    elif fault == "false_success":
        report["results"][0]["verification"] = "SUCCESS"
    elif fault == "invalid_source_confidence":
        next(e for e in report["events"] if e["event"] == "inference_source")[
            "source_confidence"
        ] = -1
    elif fault == "motion_effect":
        decision["audit"]["scope"]["motion_effect"] = "veto_if_any"
    elif fault == "arrival_effect":
        del decision["audit"]["scope"]["arrival_effect"]
    elif fault == "missing_signals":
        del decision["audit"]["path_safety"]["has_hazards"]
    elif fault == "signal_type":
        decision["audit"]["path_safety"]["has_uncertainties"] = 0
    elif fault == "previous_digest":
        report["stability_policy"]["digest"] = (
            "908af1df0eb214af1e26312d1eb308a1094feb96f37a478136894364e7bfc0bc"
        )
    else:
        report.update(navigation_design="stable-inference-v6", schema_version=9)
    with pytest.raises(ValueError):
        validate_stability_report(report)
    # 实际报告应可写成 JSON 后再次重算，不依赖内存模型对象。
    validate_stability_report(json.loads(json.dumps(report_v8)))
