"""v7候选全程只用内存客户端/合成帧；不注册热键、不出网。"""

import asyncio
import copy
from types import SimpleNamespace

import pytest
from test_navigation_v6 import assembly

from vrc_agent.config import AppConfig
from vrc_agent.live_navigation import session
from vrc_agent.live_navigation.stability_report import validate_stability_report
from vrc_agent.live_navigation.stable_runtime import (
    StableNavigation,
    StableNavigationV6,
    StableNavigationV7,
    stable_navigation_class,
)


def assemble_v7():
    old, client, calls = assembly()
    client.config = AppConfig(
        navigation_design="stable-inference-v7",
        navigation_motion_profile="field",
        control={"observation_max_age_ms": 4000},
    )
    client.core.config = client.config.control
    runtime = StableNavigationV7(
        client, old.source_factory, old.perception, old.world, old.task, clock=client.clock
    )
    return runtime, client, calls


def test_candidate_refuses_live_before_all_resources(monkeypatch, tmp_path):
    cfg = AppConfig(
        navigation_design="stable-inference-v7",
        control={"observation_max_age_ms": 4000},
        perception={"provider": "openai_compat", "model": "synthetic", "budget_usd": 1.0},
    )
    monkeypatch.setattr(session, "load_config", lambda _: cfg)

    def forbidden(*a, **kw):
        pytest.fail("candidate created a live resource")

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
    runtime, client, calls = assemble_v7()
    client.config = client.config.model_copy(update={"mode": "live"})
    with pytest.raises(ValueError, match="candidate"):
        StableNavigationV7(client, None, None, runtime.world, runtime.task, clock=client.clock)
    assert not calls and not client.transport.messages
    assert stable_navigation_class("v7") is StableNavigationV7
    for cls in (StableNavigation, StableNavigationV6):
        with pytest.raises(ValueError, match="must match"):
            cls(policy_version="v7")


@pytest.fixture(scope="module")
def report_v7():
    runtime, _, calls = assemble_v7()
    report = asyncio.run(runtime.run())
    assert report["schema_version"] == 10 and calls
    assert report["outcome"] == "SUCCESS"  # 仅为G1合成夹具的协议闭环，不声明G1目标零容差。
    assert report["run_parameters"]["freshness_ms"] == 4000
    validate_stability_report(report)
    return report


@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "live",
        "missing",
        "changed",
        "label",
        "geometry",
        "global_witness",
        "precheck_binding",
    ],
)
def test_v7_report_recomputes_source_boxes_and_counts(report_v7, fault):
    bad = copy.deepcopy(report_v7)
    proof = bad["stability_proofs"][0]
    row = proof["evidence_regions"][0]
    if fault == "schema":
        bad["schema_version"] = 9
    elif fault == "live":
        bad["mode"] = "live"
        bad["sends_live_input"] = True
    elif fault == "missing":
        del proof["evidence_regions"]
    elif fault == "changed":
        row["changed"] += 1
    elif fault == "label":
        row["label"] = "invented_label"  # 仍是1000ppm归属，必须与源实体核对才能拒绝。
    elif fault == "geometry":
        row["entity_rectangle"][0] += 1
    elif fault == "global_witness":
        proof["changed_pixels"] += 1
    else:
        bad["stability_prechecks"][0]["validates_observation_id"] = "forged"
    with pytest.raises(ValueError):
        validate_stability_report(bad)


def test_v6_schema9_still_independent(report_v7):
    runtime, _, _ = assembly()
    report = asyncio.run(runtime.run())
    assert report["schema_version"] == 9
    validate_stability_report(report)
    report["stability_proofs"][0]["changed_runs"] = []
    with pytest.raises(ValueError, match="schema10"):
        validate_stability_report(report)


def test_v7_has_no_legacy_attempt_budget_default(report_v7):
    bad = copy.deepcopy(report_v7)
    del bad["run_parameters"]["max_inference_attempts"]
    for key in ("stability_prechecks", "stability_proofs"):
        for proof in bad[key]:
            proof.pop("attempt", None)
            proof.pop("attempt_cycle", None)
    bad["events"] = [e for e in bad["events"] if e["event"] != "stability_attempt"]
    with pytest.raises(ValueError, match="attempt budget"):
        validate_stability_report(bad)
