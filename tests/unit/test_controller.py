import pytest

from vrc_agent.config import ControlConfig
from vrc_agent.contracts import Observation
from vrc_agent.transport.osc import AXES


def assert_neutral(transport):
    latest = dict(transport.messages)
    assert all(latest[a] == (0.0,) for a in AXES)


def test_short_motion_completes_unverified(rig):
    core, clock, transport, request, _ = rig
    action = request()
    assert core.submit(action).status == "RUNNING"
    clock.advance_ms(200)
    core.tick()
    assert core.results[action.action_id].status == "COMPLETED"
    assert core.results[action.action_id].verification == "UNVERIFIED"
    assert core.state == "ARMED_IDLE"
    assert_neutral(transport)


def test_duplicate_does_not_repeat_or_extend(rig):
    core, clock, transport, request, _ = rig
    action = request()
    core.submit(action)
    count = transport.sent_count
    clock.advance_ms(100)
    assert core.submit(action).status == "RUNNING"
    assert transport.sent_count == count
    clock.advance_ms(100)
    core.tick()
    assert core.submit(action).status == "COMPLETED"
    assert transport.sent_count == count + 3


def test_motion_does_not_queue(rig):
    core, _, transport, request, _ = rig
    core.submit(request())
    assert core.submit(request()).reason_code == "BUSY"
    assert transport.sent_count == 3


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"lease_id": "old"}, "EXPIRED_LEASE"),
        ({"environment_epoch": 0}, "EPOCH_MISMATCH"),
        ({"session_id": "old"}, "SESSION_MISMATCH"),
        ({"based_on_observation_id": "missing"}, "STALE_OBSERVATION"),
        ({"issued_at_ns": 2_000_000_000, "expires_at_ns": 3_000_000_000}, "EXPIRED_ACTION"),
        ({"issued_at_ns": 0, "expires_at_ns": 1}, "EXPIRED_ACTION"),
    ],
)
def test_bad_action_never_reaches_transport(rig, changes, reason):
    core, _, transport, request, _ = rig
    assert core.submit(request(**changes)).reason_code == reason
    assert transport.sent_count == 0


def test_watchdog_stops_without_automatic_rearm(rig):
    core, clock, transport, request, _ = rig
    action = request(args={"forward": 1.0, "duration_ms": 500})
    core.submit(action)
    clock.advance_ms(500)
    core.tick()
    assert core.state == "PAUSED"
    assert core.reason == "HEARTBEAT_LOST"
    assert core.results[action.action_id].status == "CANCELLED"
    assert_neutral(transport)
    core.heartbeat()
    core.tick()
    assert core.state == "PAUSED"


def test_lease_expiry_and_reset_requires_human_action(rig):
    core, clock, _, _, _ = rig
    core.config = ControlConfig(lease_ms=500)
    core.lease_expires_ns = clock.now_ns() + 500_000_000
    clock.advance_ms(500)
    core.heartbeat()
    core.tick()
    assert core.reason == "EXPIRED_LEASE"
    with pytest.raises(ValueError):
        core.arm(True)
    core.tick()
    core.tick()
    core.reset()
    assert core.state == "DISARMED"


def test_estop_is_sticky_and_releases_buttons(rig):
    core, _, transport, request, _ = rig
    core.submit(request())
    transport.button("Jump", True)
    core.stop(emergency=True)
    assert dict(transport.messages)["/input/Jump"] == (0,)
    core.stop("FOCUS_LOST")
    assert core.state == "ESTOP"
    assert_neutral(transport)


def test_observation_cannot_be_refreshed_under_same_id(rig):
    core, clock, _, _, observation = rig
    clock.advance_ms(10)
    forged = Observation.model_validate(
        {**observation.model_dump(), "captured_at_ns": clock.now_ns()}
    )
    assert not core.observe(forged)


def test_old_observation_rejected(rig):
    core, clock, transport, request, _ = rig
    clock.advance_ms(1500)
    core.heartbeat()
    assert core.submit(request()).reason_code == "STALE_OBSERVATION"
    assert not transport.messages


def test_observation_expiry_during_motion(rig):
    core, clock, transport, request, _ = rig
    clock.advance_ms(1400)
    core.heartbeat()
    action = request()
    core.submit(action)
    clock.advance_ms(100)
    core.tick()
    assert core.reason == "STALE_OBSERVATION"
    assert_neutral(transport)


def test_action_deadline_shorter_than_duration(rig):
    core, clock, transport, request, _ = rig
    action = request(expires_at_ns=clock.now_ns() + 50_000_000)
    core.submit(action)
    clock.advance_ms(50)
    core.tick()
    assert core.results[action.action_id].status == "TIMED_OUT"
    assert_neutral(transport)


def test_lost_window_cancels_motion(rig):
    core, clock, transport, request, _ = rig
    core.submit(request())
    core.observe(
        Observation(
            session_id=core.session_id,
            captured_at_ns=clock.now_ns(),
            environment_epoch=core.environment_epoch,
            window_state="lost",
        )
    )
    assert core.reason == "FOCUS_LOST"
    assert_neutral(transport)


def test_partial_transport_failure_attempts_every_release(rig, monkeypatch):
    core, _, transport, request, _ = rig
    original = transport.send
    attempted = []

    def fail_one(address, values):
        attempted.append((address, values))
        if address == AXES[1]:
            raise OSError("synthetic socket failure")
        original(address, values)

    monkeypatch.setattr(transport, "send", fail_one)
    assert core.submit(request()).reason_code == "TRANSPORT_ERROR"
    assert core.state == "PAUSED"
    assert all((a, (0.0,)) in attempted for a in AXES)


def test_chat_rate_limit_and_unsupported_skills(rig):
    core, _, transport, request, _ = rig
    assert core.submit(request("chatbox", {"text": "hello"})).status == "COMPLETED"
    assert core.submit(request("chatbox", {"text": "again"})).reason_code == "RATE_LIMITED"
    assert (
        core.submit(request("interact", {"skill_id": "unknown"})).reason_code
        == "UNSUPPORTED_CAPABILITY"
    )
    assert transport.sent_count == 1


def test_cancel(rig):
    core, _, transport, request, _ = rig
    action = request()
    core.submit(action)
    assert core.cancel(action.action_id).status == "CANCELLED"
    assert_neutral(transport)


def test_session_capacity_fails_closed(rig):
    core, _, transport, request, _ = rig
    core.config = ControlConfig(max_actions_per_session=10)
    for _ in range(10):
        assert core.submit(request("interact", {"skill_id": "unknown"})).status == "REJECTED"
    assert core.submit(request()).reason_code == "CAPACITY_REACHED"
    assert core.state == "PAUSED"
    assert len(core.results) == 10
    assert not transport.messages


def test_reset_then_rearm_cannot_use_old_environment(rig):
    core, _, transport, request, _ = rig
    old_action = request()
    core.stop(emergency=True)
    core.tick()
    core.tick()
    core.reset()
    core.arm(True)
    assert core.submit(old_action).reason_code == "EPOCH_MISMATCH"
    assert not transport.messages
