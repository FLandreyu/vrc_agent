"""v6预算贯通、候选门槛和独立控制core安全回归；不接实机。"""

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_stability_v5_runtime import assemble
from test_stable_navigation import MemoryClient

from vrc_agent.capture.frames import Frame
from vrc_agent.clock import FakeClock
from vrc_agent.config import AppConfig
from vrc_agent.contracts import ActionProposal, Observation
from vrc_agent.control.service import bind_request
from vrc_agent.live_navigation import session
from vrc_agent.live_navigation.runtime import SupervisedNavigation
from vrc_agent.live_navigation.stability import load_policy
from vrc_agent.live_navigation.stability_report import validate_stability_report
from vrc_agent.live_navigation.stable_runtime import (
    StableNavigation,
    StableNavigationV5,
    StableNavigationV6,
    stable_navigation_class,
)
from vrc_agent.runtime import RuntimeHalt
from vrc_agent.transport.osc import AXES


def config(profile="field"):
    return AppConfig(
        navigation_design="stable-inference-v6",
        navigation_motion_profile=profile,
        control={"observation_max_age_ms": 4000},
    )


def assembly(profile="field"):
    clock = FakeClock(10_000_000_000)
    client = MemoryClient(clock)
    client.config = config("cautious")
    client.core.config = client.config.control
    old, _, calls = assemble(client, clock)
    client.config = config(profile)
    runtime = StableNavigationV6(
        client, old.source_factory, old.perception, old.world, old.task, clock=clock
    )
    return runtime, client, calls


def observation(client, age_ms=0):
    return Observation(
        session_id=client.session_id,
        environment_epoch=client.core.environment_epoch,
        captured_at_ns=client.clock.now_ns() - age_ms * 1_000_000,
        frame_ref="test-frame",
        frame_size=(2160, 1440),
        window_state="visible",
    )


def assert_zero(client):
    assert client.core.active is None
    if client.transport.messages:
        assert all(dict(client.transport.messages)[a] == (0.0,) for a in AXES)


@pytest.mark.parametrize("profile,value,duration", [("cautious", 0.2, 200), ("field", 0.4, 500)])
def test_v6_closed_loop_profiles_and_schema_isolation(profile, value, duration, monkeypatch):
    runtime, client, calls = assembly(profile)
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "SUCCESS" and calls and report["schema_version"] == 9
    motions = [a for a in report["actions"] if a["kind"] == "motion"]
    assert motions and all(
        a["args"]["forward"] == value and a["args"]["duration_ms"] == duration for a in motions
    )
    assert (
        report["run_parameters"]["freshness_ms"] == 4000
        and report["run_parameters"]["task_timeout_s"] == 180.0
    )
    assert runtime.max_actions == 40
    validate_stability_report(report)
    for patch in (
        {"schema_version": 6},
        {"schema_version": 9.0},
        {"navigation_design": "stable-inference-v5"},
        {"run_parameters": None},
    ):
        bad = {**copy.deepcopy(report), **patch}
        with pytest.raises(ValueError):
            validate_stability_report(bad)
    # 候选仍不得报告 live；冻结后不再用实际冻结状态冒充候选。
    from vrc_agent.live_navigation import stability_report

    policy, digest = load_policy("v6")
    with monkeypatch.context() as patcher:
        patcher.setattr(
            stability_report,
            "load_policy",
            lambda _: ({**policy, "status": "REQUIRES_FIELD_CALIBRATION"}, digest),
        )
        for patch in ({"mode": "live"}, {"sends_live_input": True}):
            bad = {**copy.deepcopy(report), **patch}
            bad["stability_policy"]["status"] = "REQUIRES_FIELD_CALIBRATION"
            with pytest.raises(ValueError, match="candidate"):
                validate_stability_report(bad)
    assert_zero(client)
    assert report["sends_live_input"] is False
    path = Path(".runtime/verify") / f"t08_v6-{profile}-{client.session_id}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


@pytest.mark.parametrize(
    "age,accepted", [(1530, True), (3000, True), (3999, True), (4000, False), (5000, False)]
)
def test_control_action_admission_at_new_age_boundary(age, accepted):
    _, client, _ = assembly()
    obs = observation(client, age)
    assert client.core.observe(obs)
    proposal = ActionProposal(kind="motion", args={"forward": 0.4, "duration_ms": 500})
    request = bind_request(proposal, obs, client.core.snapshot(), clock=client.clock)
    result = client.core.submit(request)
    assert (result.status == "RUNNING") == accepted
    if not accepted:
        assert result.reason_code == "STALE_OBSERVATION" and not client.transport.messages
    else:
        client.core.stop("MANUAL_STOP", emergency=True)
        client.core.stop("MANUAL_STOP", emergency=True)
    assert_zero(client)


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("duration", "OK"),
        ("freshness", "STALE_OBSERVATION"),
        ("estop", "MANUAL_STOP"),
        ("heartbeat", "HEARTBEAT_LOST"),
        ("lease", "EXPIRED_LEASE"),
    ],
)
def test_bounded_motion_zero_and_safety_priorities(fault, reason):
    _, client, _ = assembly()
    obs = observation(client, 3999 if fault == "freshness" else 0)
    assert client.core.observe(obs)
    action = bind_request(
        ActionProposal(kind="motion", args={"forward": 0.4, "duration_ms": 500}),
        obs,
        client.core.snapshot(),
        clock=client.clock,
    )
    assert client.core.submit(action).status == "RUNNING"
    if fault == "duration":
        client.advance(499)
        assert client.core.active is not None
        client.advance(1)
        assert client.core.results[action.action_id].status == "COMPLETED"
    elif fault == "estop":
        client.core.stop("MANUAL_STOP", emergency=True)
    elif fault == "freshness":
        client.advance(1)
    elif fault == "lease":
        client.advance(30000)
    else:
        client.clock.advance_ms(500)
        client.core.tick()
    assert client.core.reason == reason
    assert_zero(client)


def test_180_second_task_never_extends_30_second_lease_or_epoch():
    runtime, client, _ = assembly()

    async def drive():
        await runtime.wait_for_arm()
        assert runtime.ends_at - client.clock.now_ns() / 1e9 == 180.0
        assert runtime.authority["lease_expires_ns"] - client.clock.now_ns() == 30_000_000_000
        client.core.environment_epoch += 1
        with pytest.raises(RuntimeHalt, match="EPOCH_MISMATCH"):
            await runtime.check_authority()
        client.core.environment_epoch -= 1
        client.advance(30000)
        with pytest.raises(RuntimeHalt, match="EXPIRED_LEASE"):
            await runtime.check_authority()

    asyncio.run(drive())
    assert_zero(client)


def test_runtime_capture_and_before_submit_use_same_4000ms_budget():
    runtime, client, _ = assembly()
    obs = observation(client, 1530)
    frame = Frame(obs.frame_ref, b"x" * (2160 * 1440 * 4), 2160, 1440, obs.captured_at_ns)
    runtime.source = SimpleNamespace(next_observation=lambda: obs, frame_for=lambda _: frame)

    async def drive():
        await runtime.wait_for_arm()
        result, _, max_age = await runtime.capture()
        assert result == obs and max_age == 4_000_000_000

        async def proof(*args, **kwargs):
            return None, None, {"stable": True}

        runtime.reference = (obs, frame, SimpleNamespace(entities=()))
        runtime.fresh_proof = proof
        assert (await runtime.before_submit(obs, None))[0] == obs

    asyncio.run(drive())


@pytest.mark.parametrize("status", ["FROZEN", "REQUIRES_FIELD_CALIBRATION"])
def test_candidates_reject_before_provider_and_session_freshness_wiring(
    monkeypatch, tmp_path, status
):
    cfg = config().model_copy(
        update={
            "perception": config().perception.model_copy(
                update={"provider": "openai_compat", "model": "synthetic", "budget_usd": 1.0}
            )
        }
    )
    monkeypatch.setattr(session, "load_config", lambda _: cfg)
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

    def forbidden(*args, **kw):
        pytest.fail("candidate must reject before resource creation")

    monkeypatch.setattr(session, "build_provider", forbidden)
    policy, digest = load_policy("v6")
    assert policy["status"] == "FROZEN"
    monkeypatch.setattr(session, "load_policy", lambda _: ({**policy, "status": status}, digest))
    if status != "FROZEN":
        monkeypatch.setattr(session, "Perception", forbidden)
        monkeypatch.setattr(session, "ControllerProcess", forbidden)
        with pytest.raises(ValueError, match="field calibration"):
            asyncio.run(session.navigation_entry(args))
        return
    monkeypatch.setattr(session, "build_provider", lambda *a, **kw: object())
    captured = []
    monkeypatch.setattr(session, "Perception", lambda *a, **kw: captured.append(kw))

    def reached(*args, **kw):
        raise RuntimeError("test sentinel before controller")

    monkeypatch.setattr(session, "ControllerProcess", reached)
    with pytest.raises(RuntimeError, match="test sentinel"):
        asyncio.run(session.navigation_entry(args))
    assert captured[0]["max_observation_age_ms"] == 4000


def test_factory_cannot_route_v6_through_older_base():
    assert stable_navigation_class("v6") is StableNavigationV6
    with pytest.raises(ValueError, match="must match"):
        StableNavigation(policy_version="v6")
    with pytest.raises(ValueError, match="must match"):
        StableNavigationV5(policy_version="v6")
    runtime, client, _ = assembly()
    with pytest.raises(ValueError, match="requires v6"):
        SupervisedNavigation(client, None, runtime.perception, runtime.world, runtime.task)


@pytest.mark.parametrize("profile", ["cautious", "field"])
def test_v6_runtime_only_frozen_allows_live_before_capture(profile, monkeypatch):
    runtime, client, calls = assembly(profile)
    client.config = client.config.model_copy(update={"mode": "live"})
    frozen = StableNavigationV6(client, None, None, runtime.world, runtime.task, clock=client.clock)
    assert frozen.source is None and not calls and not client.transport.messages
    from vrc_agent.live_navigation import stable_runtime

    policy, digest = load_policy("v6")
    assert policy["status"] == "FROZEN"
    monkeypatch.setattr(
        stable_runtime,
        "load_policy",
        lambda _: ({**policy, "status": "REQUIRES_FIELD_CALIBRATION"}, digest),
    )
    with pytest.raises(ValueError, match="candidate"):
        StableNavigationV6(client, None, None, runtime.world, runtime.task, clock=client.clock)
    assert not calls and not client.transport.messages


@pytest.mark.parametrize(
    "args",
    [
        {"forward": 1.0, "duration_ms": 500},
        {"forward": 0.2, "duration_ms": 200},
        {"yaw": 1.0, "duration_ms": 500},
        {"forward": 0.4, "strafe": 0.2, "duration_ms": 500},
    ],
)
def test_model_proposal_cannot_replace_runtime_envelope(args):
    runtime, client, _ = assembly()

    async def decide(*unused):
        runtime.skill.motion_envelope = SimpleNamespace(accepts=lambda _: True)
        return ActionProposal(kind="motion", args=args)

    runtime.skill.decide = decide
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "INVALID_ARGS" and not report["actions"]
    assert_zero(client)
