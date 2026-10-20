"""schema13真实core/内存帧闭环与重放；没有网络、真实输入或live采集。"""

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_navigation_v6 import assert_zero
from test_stability_v9_runtime import assemble_v9

from vrc_agent.config import AppConfig
from vrc_agent.control.service import ControllerProcess
from vrc_agent.live_navigation import session
from vrc_agent.live_navigation.runtime import SupervisedNavigation
from vrc_agent.live_navigation.stability_report import validate_stability_report
from vrc_agent.live_navigation.stable_runtime import StableNavigationV10, stable_navigation_class
from vrc_agent.live_navigation.world import load_live_world
from vrc_agent.providers.base import ModelResponse


def assemble_scan(*, acquire=False, fault=None, version="v10"):
    old, client, _ = assemble_v9()
    client.config = AppConfig(
        navigation_design="stable-inference-" + version,
        navigation_motion_profile="field",
        control={"observation_max_age_ms": 4000},
    )
    client.core.config = client.config.control
    world = load_live_world("v3")
    calls = []

    async def infer(request, **kwargs):
        calls.append(request.observation_id)
        client.advance(100)
        if len(calls) == 4 and fault == "estop":
            client.core.stop("MANUAL_STOP", emergency=True)
        if len(calls) == 4 and fault == "lease":
            client.advance(30000)
        entities = [
            dict(
                track_id="goal",
                label="home_raccoon",
                bbox=[0.4, 0.4, 0.6, 0.6],
                confidence=0.72,
                evidence_observation_id=request.observation_id,
            )
        ]
        if acquire and len(calls) >= 4:
            entities.append(
                dict(
                    track_id="floor",
                    label="forward_clearance",
                    bbox=[0.2, 0.6, 0.9, 0.98],
                    confidence=0.99,
                    evidence_observation_id=request.observation_id,
                )
            )
        if acquire and len(calls) >= 5:
            entities[0]["bbox"] = [0.3, 0.3, 0.7, 0.7]
        return ModelResponse(
            request_id=request.request_id,
            observation_id=request.observation_id,
            provider="memory",
            model="memory",
            payload=dict(entities=entities, hazards=[], uncertainties=[]),
        )

    old.perception.provider.infer = infer
    runtime = stable_navigation_class(version)(
        client, old.source_factory, old.perception, world, world.task("G2-2"), clock=client.clock
    )
    return runtime, client, calls


def test_frozen_v10_live_needs_consents_and_unfrozen_still_refuses(monkeypatch, tmp_path):
    config = AppConfig(
        navigation_design="stable-inference-v10",
        navigation_motion_profile="field",
        control={"observation_max_age_ms": 4000, "lease_extension_max": 5},
        perception={"provider": "openai_compat", "model": "memory", "budget_usd": 1.0},
    )
    monkeypatch.setattr(session, "load_config", lambda _: config)

    def forbidden(*args, **kwargs):
        pytest.fail("refused live run created a resource")

    for key in ("build_provider", "Perception", "ControllerProcess", "live_capture"):
        monkeypatch.setattr(session, key, forbidden)
    args = SimpleNamespace(
        task="G2-2",
        world_version="v3",
        mode="live",
        config="unused",
        confirm_calibration=True,
        allow_paid=True,
        send_images=True,
        state_dir=str(tmp_path),
    )
    # 冻结不替代现场同意：标定/付费/图像三项缺一，仍在资源前拒绝。
    for name in ("confirm_calibration", "allow_paid", "send_images"):
        withheld = SimpleNamespace(**{**vars(args), name: False})
        with pytest.raises(ValueError, match="paid and image consent"):
            asyncio.run(session.navigation_entry(withheld))
    policy, digest = session.load_policy("v10")
    assert policy["status"] == "FROZEN"
    # 未冻结（候选）策略仍在资源前拒绝 live：冻结只改状态，不放宽该门禁。
    monkeypatch.setattr(
        session,
        "load_policy",
        lambda _: ({**policy, "status": "REQUIRES_FIELD_CALIBRATION"}, digest),
    )
    with pytest.raises(ValueError, match="field calibration"):
        asyncio.run(session.navigation_entry(args))
    monkeypatch.setattr(session, "load_policy", lambda _: (policy, digest))

    def sentinel(*args, **kwargs):
        raise RuntimeError("frozen policy gate passed")

    for key in ("build_provider", "Perception", "ControllerProcess", "live_capture"):
        monkeypatch.setattr(session, key, sentinel)
    with pytest.raises(RuntimeError, match="frozen policy gate passed"):
        asyncio.run(session.navigation_entry(args))

    runtime, client, calls = assemble_scan()
    with pytest.raises(ValueError, match="v10 runtime"):
        SupervisedNavigation(client, forbidden, forbidden, runtime.world, runtime.task)
    # 运行时自带的候选拒绝仍存在：改状态为候选后 live 构造仍拒绝。
    monkeypatch.setattr(
        "vrc_agent.live_navigation.stable_runtime.load_policy",
        lambda _: ({**policy, "status": "REQUIRES_FIELD_CALIBRATION"}, digest),
    )
    client.config = client.config.model_copy(update={"mode": "live"})
    with pytest.raises(ValueError, match="candidate"):
        StableNavigationV10(client, forbidden, forbidden, runtime.world, runtime.task)
    assert not calls and not client.transport.messages
    assert stable_navigation_class("v10") is StableNavigationV10


def test_candidate_budget_mapping_is_wired_to_180s_and_worker_stays_disarmed(tmp_path):
    # 冻结前阻断（预算枚举漏v10）已按另案授权修复：真实dry-run worker须拿到策略180s，
    # 不能再靠内存core默认值掩盖生产装配；修复不增加管道能力，进程仍DISARMED且无发送。
    config = AppConfig(navigation_design="stable-inference-v10")
    with ControllerProcess(config, tmp_path) as client:
        status = client.request("status")
        assert status["state"] == "DISARMED"
        assert status["control_budget"]["task_timeout_ms"] == 180000
        assert status["control_budget"]["base_ms"] == 30000
        assert status["sent_count"] == 0 and status["recorded_messages"] == []
        for operation in ("extend", "renew"):
            with pytest.raises(ValueError):
                client.request(operation)


@pytest.fixture(scope="module")
def acquired_report():
    runtime, client, calls = assemble_scan(acquire=True)
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "SUCCESS" and len(calls) == 6
    assert report["schema_version"] == 13
    assert report["run_parameters"]["target_min_confidence"] == 0.7
    assert report["run_parameters"]["freshness_ms"] == 4000
    assert report["run_parameters"]["max_actions"] == 40
    assert report["run_parameters"]["max_inference_attempts"] == 3
    assert report["control_budget"]["base_ms"] == 30000
    validate_stability_report(report)
    validate_stability_report(json.loads(json.dumps(report)))
    assert_zero(client)
    Path(".runtime/verify/t08_v10_acquired.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report


def test_third_denial_yaws_then_new_evidence_allows_separate_translation(acquired_report):
    actions = acquired_report["actions"]
    assert [a["kind"] for a in actions[:3]] == ["wait", "wait", "motion"]
    assert actions[2]["args"]["yaw"] == 1.0 and actions[2]["args"]["duration_ms"] == 200
    assert actions[2]["args"]["forward"] == actions[2]["args"]["strafe"] == 0
    assert actions[3]["args"]["forward"] == 0.4 and actions[3]["args"]["yaw"] == 0
    scan = next(e for e in acquired_report["events"] if e["event"] == "path_scan_observed")
    assert scan["clearance_acquired"] is True and scan["fresh_observation"] is True
    assert scan["after_observation_id"] != actions[2]["based_on_observation_id"]
    assert any(
        p.get("validates_observation_id") == actions[3]["based_on_observation_id"]
        for p in acquired_report["stability_proofs"]
    )
    assert all(a["expires_at_ns"] - a["issued_at_ns"] == 1_000_000_000 for a in actions)


def test_four_scans_then_original_path_unsafe_limit_and_v9_unchanged():
    runtime, client, _ = assemble_scan()
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "PATH_UNSAFE"
    scans = [e for e in report["events"] if e["event"] == "path_scan_triggered"]
    assert [e["decision_index"] for e in scans] == [3, 6, 9, 12]
    assert [e["direction"] for e in scans] == [1, -1, 1, -1]
    assert len([a for a in report["actions"] if a["kind"] == "motion"]) == 4
    assert len([e for e in report["events"] if e["event"] == "acceptance_decision"]) == 15
    validate_stability_report(report)
    assert_zero(client)
    runtime, client, _ = assemble_scan(version="v9")
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "PATH_UNSAFE" and report["schema_version"] == 12
    assert all(a["kind"] == "wait" for a in report["actions"])
    assert not any(e["event"].startswith("path_scan") for e in report["events"])
    validate_stability_report(report)
    assert_zero(client)


@pytest.mark.parametrize(
    "fault",
    [
        "sequence",
        "direction",
        "trigger",
        "streak",
        "eligible",
        "yaw_audit",
        "acquired",
        "fresh",
        "missing_event",
        "missing_feedback",
        "schema12",
        "schema_float",
        "missing_scan",
        "forged_waits",
        "stale_source",
        "precheck_only",
        "mixed_motion",
        "early_scan",
    ],
)
def test_schema13_forgery_and_missing_scan_evidence_rejected(acquired_report, fault):
    report = copy.deepcopy(acquired_report)
    decision = next(
        e
        for e in report["events"]
        if e["event"] == "acceptance_decision" and e["decision_index"] == 3
    )
    scan = decision["audit"]["path_scan"]
    if fault in {"sequence", "direction", "streak"}:
        scan[{"streak": "denied_streak"}.get(fault, fault)] = 99
    elif fault in {"trigger", "eligible"}:
        scan["triggered" if fault == "trigger" else fault] = False
    elif fault == "yaw_audit":
        scan["yaw_audit"]["proposal_permitted"] = False
    elif fault in {"acquired", "fresh"}:
        e = next(e for e in report["events"] if e["event"] == "path_scan_observed")
        e["clearance_acquired" if fault == "acquired" else "fresh_observation"] = False
    elif fault == "missing_event":
        report["events"] = [e for e in report["events"] if e["event"] != "path_scan_triggered"]
    elif fault == "missing_feedback":
        report["events"] = [e for e in report["events"] if e["event"] != "path_scan_observed"]
    elif fault in {"schema12", "schema_float"}:
        report["schema_version"] = 12 if fault == "schema12" else 13.0
    elif fault == "missing_scan":
        del decision["audit"]["path_scan"]
    elif fault == "forged_waits":
        e = next(e for e in report["events"] if e["event"] == "navigation_feedback")
        e["path_waits"] = 0
    elif fault == "stale_source":
        e = next(
            e
            for e in report["events"]
            if e["event"] == "inference_source"
            and e["at_ns"] > report["results"][2]["finished_at_ns"]
        )
        e["captured_at_ns"] = report["results"][2]["finished_at_ns"] - 1
    elif fault == "mixed_motion":
        report["actions"][2]["args"]["forward"] = 0.4
    elif fault == "early_scan":
        e = next(
            e
            for e in report["events"]
            if e["event"] == "acceptance_decision" and e["decision_index"] == 2
        )
        e["audit"]["path_scan"]["triggered"] = True
    else:
        key = report["actions"][2]["based_on_observation_id"]
        for proof in report["stability_proofs"]:
            if proof.get("validates_observation_id") == key:
                del proof["validates_observation_id"]
    with pytest.raises(ValueError):
        validate_stability_report(report)


def test_unfrozen_v10_policy_still_cannot_claim_live(acquired_report, monkeypatch):
    """冻结后 live 报告本身合法；但候选态声称 live 仍必须拒（不得靠冻结放宽）。"""
    report = copy.deepcopy(acquired_report)
    report.update(mode="live", sends_live_input=True)
    policy, digest = session.load_policy("v10")
    assert policy["status"] == "FROZEN"
    validate_stability_report(report)  # 冻结策略：live 声明自洽，不再被拒。
    candidate = {**policy, "status": "REQUIRES_FIELD_CALIBRATION"}
    report["stability_policy"] = {"digest": digest, **candidate}
    monkeypatch.setattr(
        "vrc_agent.live_navigation.stability_report.load_policy",
        lambda _version: (candidate, digest),
    )
    with pytest.raises(ValueError, match="cannot claim live"):
        validate_stability_report(report)


@pytest.mark.parametrize("fault,outcome", [("estop", "MANUAL_STOP"), ("lease", "EXPIRED_LEASE")])
def test_scan_post_observation_keeps_estop_and_expiry_priority(fault, outcome):
    runtime, client, _ = assemble_scan(fault=fault)
    report = asyncio.run(runtime.run())
    assert report["outcome"] == outcome
    assert len([a for a in report["actions"] if a["kind"] == "motion"]) == 1
    validate_stability_report(report)
    assert_zero(client)
