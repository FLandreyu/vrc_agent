"""v9候选资源前拒绝、离线闭环和schema12逐框证明；不接真实资源。"""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from test_acceptance_v8_runtime import assemble_v8
from test_navigation_v6 import assert_zero

from vrc_agent.capture.frames import Frame
from vrc_agent.config import AppConfig
from vrc_agent.live_navigation import session
from vrc_agent.live_navigation.runtime import SupervisedNavigation
from vrc_agent.live_navigation.stability import load_policy
from vrc_agent.live_navigation.stability_report import validate_proof, validate_stability_report
from vrc_agent.live_navigation.stable_runtime import StableNavigationV9, stable_navigation_class


def assemble_v9(**kwargs):
    old, client, calls = assemble_v8(**kwargs)
    client.config = AppConfig(
        navigation_design="stable-inference-v9",
        navigation_motion_profile="field",
        control={"observation_max_age_ms": 4000},
    )
    client.core.config = client.config.control
    runtime = StableNavigationV9(
        client, old.source_factory, old.perception, old.world, old.task, clock=client.clock
    )
    return runtime, client, calls


def test_v9_candidate_refuses_before_all_live_resources(monkeypatch, tmp_path):
    config = AppConfig(
        navigation_design="stable-inference-v9",
        navigation_motion_profile="field",
        control={"observation_max_age_ms": 4000, "lease_extension_max": 5},
        perception={"provider": "openai_compat", "model": "memory", "budget_usd": 1.0},
    )
    monkeypatch.setattr(session, "load_config", lambda _: config)

    def forbidden(*args, **kwargs):
        pytest.fail("candidate created live resources")

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
    runtime, client, calls = assemble_v9()
    assert not calls and not client.transport.messages
    with pytest.raises(ValueError, match="requires v9 runtime"):
        SupervisedNavigation(client, None, None, runtime.world, runtime.task)
    client.config = client.config.model_copy(update={"mode": "live"})
    with pytest.raises(ValueError, match="candidate"):
        StableNavigationV9(client, forbidden, forbidden, runtime.world, runtime.task)
    assert not calls and not client.transport.messages
    assert stable_navigation_class("v9") is StableNavigationV9


@pytest.fixture(scope="module")
def report_v9():
    runtime, client, calls = assemble_v9(distractor=True)
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "SUCCESS" and calls
    assert report["schema_version"] == 12
    assert report["acceptance_semantics"] == "target-path-v8"
    assert report["run_parameters"]["freshness_ms"] == 4000
    assert report["run_parameters"]["max_inference_attempts"] == 3
    assert report["run_parameters"]["max_actions"] == 40
    assert report["run_parameters"]["task_timeout_s"] == 180
    assert report["control_budget"]["base_ms"] == 30000
    assert_zero(client)
    validate_stability_report(report)
    validate_stability_report(json.loads(json.dumps(report)))
    return report


def test_schema12_report_is_recomputable(report_v9):
    for proof in (
        report_v9["stability_proofs"]
        + report_v9["stability_prechecks"]
        + report_v9["warmup_checks"]
    ):
        assert "protected_regions" in proof and "changed_runs" in proof
        assert proof["tolerance_policy"] == "per_visible_region_ppm_clamped_v1"
    for action in report_v9["actions"]:
        derived = action["based_on_observation_id"]
        assert any(
            p.get("validates_observation_id") == derived for p in report_v9["stability_proofs"]
        )


@pytest.mark.parametrize(
    "fault",
    [
        "count",
        "allowed",
        "ppm",
        "visible",
        "missing",
        "shape",
        "label",
        "runs",
        "verdict",
        "tolerance",
        "schema11",
        "schema9",
        "schema_float",
        "live",
        "sends",
        "recognition",
        "motion",
        "arrival",
        "attempts",
        "checkpoint",
        "precheck",
        "precheck_geometry",
        "warmup_region",
        "witness_missing",
    ],
)
def test_v9_report_forgery_or_missing_evidence_refused(report_v9, fault):
    report = copy.deepcopy(report_v9)
    proof = report["stability_proofs"][0]
    row = proof["protected_regions"][0]
    decision = next(e for e in report["events"] if e["event"] == "acceptance_decision")
    if fault in {"count", "allowed", "ppm", "visible"}:
        field = {
            "count": "protected_changed_px",
            "allowed": "protected_allowed_px",
            "ppm": "protected_changed_ppm",
            "visible": "protected_visible_px",
        }[fault]
        row[field] += 1
    elif fault == "missing":
        del row["protected_allowed_px"]
    elif fault == "shape":
        row["protected_rectangle"][0] += 1
    elif fault == "label":
        row["label"] = "other"
    elif fault == "runs":
        proof["changed_runs"] = [[700 * 2160 + 1000, 700 * 2160 + 1001]]
    elif fault == "verdict":
        proof["stable"] = False
    elif fault == "tolerance":
        proof["tolerance_policy"] = "pooled"
    elif fault.startswith("schema"):
        report["schema_version"] = {"schema11": 11, "schema9": 9, "schema_float": 12.0}[fault]
    elif fault == "live":
        report["mode"] = "live"
    elif fault == "sends":
        report["sends_live_input"] = True
    elif fault == "recognition":
        decision["audit"]["target_recognition"]["unique_label"] = False
    elif fault == "motion":
        decision["audit"]["path_safety"]["forward_clearance"] = False
    elif fault == "arrival":
        decision["audit"]["arrival_geometry_met"] = not decision["audit"]["arrival_geometry_met"]
    elif fault == "attempts":
        report["run_parameters"]["max_inference_attempts"] = 4
    elif fault == "checkpoint":
        report["stability_proofs"] = [
            p for p in report["stability_proofs"] if "validates_observation_id" not in p
        ]
    elif fault == "precheck":
        report["stability_prechecks"] = []
    elif fault == "precheck_geometry":
        # 初次预检没有既往模型证据；不能凭空引入一个自洽的新框。
        report["stability_prechecks"][0].update(
            protected_regions=copy.deepcopy(proof["protected_regions"]),
            **{
                k: copy.deepcopy(proof[k])
                for k in [
                    "entity_rectangles",
                    "protected_rectangles",
                    "entity_masked_px",
                    "protected_masked_px",
                    "protected_visible_px",
                ]
            },
        )
    elif fault == "warmup_region":
        report["warmup_checks"][0]["protected_regions"] = copy.deepcopy(proof["protected_regions"])
    else:
        del proof["changed_runs"]
    with pytest.raises(ValueError):
        validate_stability_report(report)


def test_v8_schema_cannot_accept_v9_regional_fields():
    runtime, _, _ = assemble_v8()
    report = asyncio.run(runtime.run())
    validate_stability_report(report)
    report["stability_proofs"][0]["protected_regions"] = []
    with pytest.raises(ValueError, match="schema12"):
        validate_stability_report(report)


@pytest.mark.parametrize(
    "field",
    [
        "entity_rectangles",
        "protected_rectangles",
        "entity_masked_px",
        "protected_masked_px",
        "protected_visible_px",
    ],
)
@pytest.mark.parametrize("location", ["source", "proof", "precheck"])
def test_old_audit_fields_also_recomputed_from_observations(report_v9, field, location):
    report = copy.deepcopy(report_v9)
    if location == "source":
        record = next(e for e in report["events"] if e["event"] == "inference_source")
    else:
        record = report["stability_proofs" if location == "proof" else "stability_prechecks"][0]
    if isinstance(record[field], list):
        record[field].append([1, 1, 2, 2])
    else:
        record[field] += 1
    with pytest.raises(ValueError):
        validate_stability_report(report)


@pytest.mark.parametrize("fault", ["boolean", "float", "missing"])
def test_tolerance_declaration_uses_strict_types_in_report(report_v9, fault):
    report = copy.deepcopy(report_v9)
    declaration = report["stability_policy"]["protected_tolerance"]
    if fault == "boolean":
        declaration["min_allowed_px"] = True
    elif fault == "float":
        declaration["max_changed_ppm"] = 100.0
    else:
        del declaration["rounding"]
    with pytest.raises(ValueError):
        validate_stability_report(report)


@pytest.mark.parametrize("checkpoint", ["after_inference", "before_submit"])
def test_original_checkpoints_still_evaluate_and_retry_boundedly(checkpoint):
    runtime, client, calls = assemble_v9()
    factory = runtime.source_factory
    reject_at = 5 if checkpoint == "after_inference" else 6

    def altered_factory(authority):
        source = factory(authority)
        original = source.frame_for

        def frame_for(obs):
            frame = original(obs)
            if source.index == reject_at:
                return Frame(
                    frame.ref, b"\x60\x60\x60\xff" * (2160 * 1440), 2160, 1440, frame.captured_at_ns
                )
            return frame

        source.frame_for = frame_for
        return source

    runtime.source_factory = altered_factory
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "SUCCESS" and len(calls) >= 2
    assert any(not p["stable"] for p in report["stability_proofs"])
    assert max(p["attempt"] for p in report["stability_proofs"]) <= 3
    assert any("validates_observation_id" in p for p in report["stability_proofs"])
    assert_zero(client)
    validate_stability_report(report)


def test_freshness_boundary_stays_4000ms(report_v9):
    proof = copy.deepcopy(report_v9["stability_proofs"][0])
    policy, digest = load_policy("v9")
    proof["checked_at_ns"] = proof["current_captured_at_ns"] + 3_999_999_999
    assert validate_proof(proof, policy, digest, report_v9["session_id"], "v9")
    proof["checked_at_ns"] += 1
    with pytest.raises(ValueError, match="stale"):
        validate_proof(proof, policy, digest, report_v9["session_id"], "v9")


@pytest.mark.parametrize(
    "checkpoint,stride,calls_expected",
    [("precheck", 4, 0), ("after_inference", 5, 3), ("before_submit", 6, 3)],
)
def test_three_rejections_pause_without_action_or_fourth_attempt(
    checkpoint, stride, calls_expected
):
    runtime, client, calls = assemble_v9()
    factory = runtime.source_factory

    def altered_factory(authority):
        source = factory(authority)
        original = source.frame_for

        def frame_for(obs):
            frame = original(obs)
            if source.index % stride == 0:
                return Frame(
                    frame.ref, b"\x60\x60\x60\xff" * (2160 * 1440), 2160, 1440, frame.captured_at_ns
                )
            return frame

        source.frame_for = frame_for
        return source

    runtime.source_factory = altered_factory
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "SCENE_UNSTABLE"
    assert len(calls) == calls_expected and report["actions"] == []
    attempts = [e["attempt"] for e in report["events"] if e["event"] == "stability_attempt"]
    assert attempts == [1, 2, 3]
    assert len(report["stability_prechecks"]) == 3
    assert_zero(client)
    validate_stability_report(report)


@pytest.mark.parametrize("no_clearance", [False, True])
def test_signal_motion_and_arrival_semantics_unchanged(no_clearance):
    runtime, client, _ = assemble_v9(dirty=True, no_clearance=no_clearance)
    report = asyncio.run(runtime.run())
    assert any(a["kind"] == "motion" for a in report["actions"]) is (not no_clearance)
    assert all(r["verification"] != "SUCCESS" for r in report["results"])
    assert report["outcome"] == ("PATH_UNSAFE" if no_clearance else "EXPIRED_LEASE")
    assert_zero(client)
    validate_stability_report(report)
