import pytest

from vrc_agent.clock import FakeClock
from vrc_agent.config import ControlConfig
from vrc_agent.contracts import ActionRequest, Observation, new_id
from vrc_agent.control.core import Controller
from vrc_agent.transport.osc import RecordingTransport


@pytest.fixture
def rig():
    clock = FakeClock()
    transport = RecordingTransport()
    core = Controller("test-session", ControlConfig(), transport, clock)
    core.arm(True)
    observation = Observation(
        session_id=core.session_id,
        captured_at_ns=clock.now_ns(),
        environment_epoch=core.environment_epoch,
        window_state="visible",
    )
    core.observe(observation)

    def request(kind="motion", args=None, **changes):
        value = {
            "session_id": core.session_id,
            "action_id": new_id(),
            "kind": kind,
            "args": args if args is not None else {"forward": 0.5, "duration_ms": 200},
            "lease_id": core.lease_id or "expired",
            "environment_epoch": core.environment_epoch,
            "based_on_observation_id": observation.observation_id,
            "issued_at_ns": clock.now_ns(),
            "expires_at_ns": clock.now_ns() + 2_000_000_000,
        }
        value.update(changes)
        return ActionRequest.model_validate(value)

    return core, clock, transport, request, observation
