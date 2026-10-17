"""试点动作后抓帧竞态；只用内存 core/传输与可控时钟。"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_stability_v4_runtime import V4Frames
from test_stable_navigation import MemoryClient

from vrc_agent.capture.frames import Frame
from vrc_agent.clock import FakeClock
from vrc_agent.contracts import Observation
from vrc_agent.live_navigation import runtime as runtime_module
from vrc_agent.live_navigation.runtime import SupervisedNavigation
from vrc_agent.live_navigation.skill import CLEARANCE_GUIDANCE
from vrc_agent.live_navigation.stability_report import validate_stability_report
from vrc_agent.live_navigation.stable_runtime import StableNavigationV5
from vrc_agent.live_navigation.world import load_live_world
from vrc_agent.providers.base import ModelResponse
from vrc_agent.runtime import RuntimeHalt
from vrc_agent.transport.osc import AXES

PIXELS = b"\x14\x20\x30\xff" * (2160 * 1440)


class ObservedClient(MemoryClient):
    def __init__(self, clock):
        super().__init__(clock)
        self.operations = []

    def request(self, operation, payload=None, timeout=None):
        self.operations.append((operation, payload))
        return super().request(operation, payload, timeout)


class ScriptedFrames:
    def __init__(self, client):
        self.client = client
        self.plan = iter(())
        self.fallback = lambda: self.make("seed", client.clock.now_ns())
        self.calls = 0
        self.frame_ids = []

    def make(self, identifier, captured_at_ns, **updates):
        return Observation(
            session_id=self.client.session_id,
            environment_epoch=self.client.core.environment_epoch,
            observation_id=identifier,
            captured_at_ns=captured_at_ns,
            frame_ref="frame-" + identifier,
            frame_size=(2160, 1440),
            window_state="visible",
        ).model_copy(update=updates)

    def next_observation(self):
        self.calls += 1
        return next(self.plan, self.fallback)()

    def frame_for(self, observation):
        self.frame_ids.append(observation.observation_id)
        return Frame(observation.frame_ref, PIXELS, 2160, 1440, observation.captured_at_ns)


def assembly(monkeypatch, *, max_age_ms=1500, advance=True):
    clock = FakeClock(10_000_000_000)
    client = ObservedClient(clock)
    client.config = client.config.model_copy(
        update={
            "control": client.config.control.model_copy(
                update={"observation_max_age_ms": max_age_ms}
            )
        }
    )
    source = ScriptedFrames(client)
    world = load_live_world("v2")
    runtime = SupervisedNavigation(client, None, None, world, world.task("G2-1"), clock=clock)
    runtime.source = source
    original_sleep = asyncio.sleep
    sleeps = []

    async def wait(seconds):
        sleeps.append(seconds)
        if advance:
            client.advance(round(seconds * 1000))
        await original_sleep(0)

    monkeypatch.setattr(runtime_module.asyncio, "sleep", wait)
    return runtime, client, source, sleeps


async def seed(runtime, client):
    await runtime.wait_for_arm()
    previous, _, _ = await runtime.capture()
    client.advance(1)
    return previous, client.clock.now_ns()


def submitted(client):
    return [payload for operation, payload in client.operations if operation == "observe"]


def test_post_action_skips_duplicate_regression_and_equal_boundary(monkeypatch):
    runtime, client, source, sleeps = assembly(monkeypatch)

    async def drive():
        previous, after = await seed(runtime, client)
        source.plan = iter(
            [
                lambda: previous,  # 已接受的ID，且时间早于动作完成。
                lambda: source.make(previous.observation_id, client.clock.now_ns()),  # 只有ID重复。
                lambda: source.make("regressed", previous.captured_at_ns - 1),
                lambda: source.make("equal", after),
                lambda: source.make("new", client.clock.now_ns()),
            ]
        )
        observation, frame, age = await runtime.capture(after_ns=after)
        assert observation.observation_id == "new"
        assert observation.captured_at_ns > max(after, previous.captured_at_ns)
        assert frame.captured_at_ns == observation.captured_at_ns and age == 1_500_000_000
        assert runtime.seen == {previous.observation_id, "new"}
        assert runtime.last_capture == observation.captured_at_ns

    asyncio.run(drive())
    assert source.calls == 6 and sleeps == [0.03] * 4
    assert [o["observation_id"] for o in submitted(client)] == ["seed", "new"]
    assert source.frame_ids == ["seed", "new"]  # 丢弃的缓存帧不进入控制层。
    assert not client.transport.messages


@pytest.mark.parametrize("max_age_ms", [300, 1500])
def test_retry_has_one_shared_freshness_deadline(monkeypatch, max_age_ms):
    runtime, client, source, _ = assembly(monkeypatch, max_age_ms=max_age_ms)

    async def drive():
        previous, after = await seed(runtime, client)
        started = client.clock.now_ns()
        source.fallback = lambda: source.make("equal", after)
        with pytest.raises(RuntimeHalt, match="^STALE_OBSERVATION$"):
            await runtime.capture(after_ns=after)
        assert client.clock.now_ns() - started <= max_age_ms * 1_000_000
        assert runtime.seen == {previous.observation_id} and len(submitted(client)) == 1

    asyncio.run(drive())
    assert source.calls <= 51 and not client.transport.messages


def test_retry_attempt_limit_even_if_test_clock_does_not_advance(monkeypatch):
    runtime, client, source, sleeps = assembly(monkeypatch, advance=False)

    async def drive():
        _, after = await seed(runtime, client)
        source.fallback = lambda: source.make("equal", after)
        with pytest.raises(RuntimeHalt, match="^STALE_OBSERVATION$"):
            await runtime.capture(after_ns=after)

    asyncio.run(drive())
    assert source.calls == 51 and len(sleeps) == 49 and len(submitted(client)) == 1


def test_fresh_frame_at_deadline_is_not_forwarded(monkeypatch):
    runtime, client, source, _ = assembly(monkeypatch, max_age_ms=300)

    async def drive():
        _, after = await seed(runtime, client)

        def late():
            client.advance(300)
            return source.make("late", client.clock.now_ns())

        source.plan = iter([late])
        with pytest.raises(RuntimeHalt, match="^STALE_OBSERVATION$"):
            await runtime.capture(after_ns=after)
        assert len(submitted(client)) == 1 and "late" not in runtime.seen

    asyncio.run(drive())


@pytest.mark.parametrize("fault", ["duplicate", "equal", "regressed", "expired", "future"])
def test_no_after_ns_preserves_immediate_rejection(monkeypatch, fault):
    runtime, client, source, sleeps = assembly(monkeypatch)

    async def drive():
        previous, _ = await seed(runtime, client)
        stamp = {
            "duplicate": client.clock.now_ns(),
            "equal": previous.captured_at_ns,
            "regressed": previous.captured_at_ns - 1,
            "expired": client.clock.now_ns() - 1_500_000_000,
            "future": client.clock.now_ns() + 1,
        }[fault]
        identifier = previous.observation_id if fault == "duplicate" else "bad"
        source.fallback = lambda: source.make(identifier, stamp)
        with pytest.raises(RuntimeHalt, match="^STALE_OBSERVATION$"):
            await runtime.capture()

    asyncio.run(drive())
    assert source.calls == 2 and not sleeps and len(submitted(client)) == 1


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("epoch", "EPOCH_MISMATCH"),
        ("focus", "FOCUS_LOST"),
        ("size", "CALIBRATION_MISMATCH"),
        ("expired", "STALE_OBSERVATION"),
        ("future", "STALE_OBSERVATION"),
        ("frame", "MISSING_FRAME"),
    ],
)
def test_after_ns_does_not_retry_unsafe_or_invalid_sources(monkeypatch, fault, reason):
    runtime, client, source, sleeps = assembly(monkeypatch)

    async def drive():
        previous, after = await seed(runtime, client)
        updates = {
            "epoch": {"environment_epoch": previous.environment_epoch + 1},
            "focus": {"window_state": "occluded"},
            "size": {"frame_size": (100, 100)},
        }.get(fault, {})
        stamp = after if updates else after + 1
        if fault == "expired":
            stamp = client.clock.now_ns() - 1_500_000_000
        elif fault == "future":
            stamp = client.clock.now_ns() + 1
        else:
            client.advance(1)
        source.fallback = lambda: source.make("bad", stamp, **updates)
        if fault == "frame":
            source.frame_for = lambda _: None
        with pytest.raises(RuntimeHalt, match="^" + reason + "$"):
            await runtime.capture(after_ns=after)

    asyncio.run(drive())
    assert source.calls == 2 and not sleeps and len(submitted(client)) == 1


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("estop", "MANUAL_STOP"),
        ("epoch", "EPOCH_MISMATCH"),
        ("lease", "EXPIRED_LEASE"),
        ("pause", "FOCUS_LOST"),
    ],
)
def test_authority_change_while_waiting_aborts_without_observe(monkeypatch, fault, reason):
    runtime, client, source, _ = assembly(monkeypatch)

    async def drive():
        _, after = await seed(runtime, client)

        def interrupt():
            if fault == "epoch":
                client.core.environment_epoch += 1
            elif fault == "lease":
                client.advance(30001)
            else:
                client.core.stop(
                    "MANUAL_STOP" if fault == "estop" else "FOCUS_LOST", emergency=fault == "estop"
                )
            return source.make("new", client.clock.now_ns())

        source.plan = iter([lambda: source.make("equal", after), interrupt])
        with pytest.raises(RuntimeHalt, match="^" + reason + "$"):
            await runtime.capture(after_ns=after)

    asyncio.run(drive())
    assert len(submitted(client)) == 1 and not client.transport.messages


@pytest.mark.parametrize("clearance,outcome", [(True, "SUCCESS"), (False, "PATH_UNSAFE")])
def test_world_v2_v5_jpeg_closed_loop_with_post_action_race(clearance, outcome):
    clock = FakeClock(1_000_000_000)
    client = ObservedClient(clock)
    client.config = client.config.model_copy(
        update={
            "perception": client.config.perception.model_copy(
                update={"image_format": "jpeg", "jpeg_quality": 85}
            )
        }
    )
    world, inputs = load_live_world("v2"), []

    class RaceFrames(V4Frames):
        dropped = False

        def next_observation(self):
            observation = super().next_observation()
            results = [r for r in client.core.results.values() if r.status == "COMPLETED"]
            if results and not self.dropped:
                self.dropped = True
                end = results[-1].finished_at_ns
                observation = observation.model_copy(update={"captured_at_ns": end})
                self.frame = Frame(self.frame.ref, self.frame.data, 2160, 1440, end)
            return observation

    class Provider:
        async def infer(self, request, **kwargs):
            inputs.append(request)
            assert CLEARANCE_GUIDANCE in request.prompt and request.image_media_type == "image/jpeg"
            client.advance(3500)
            box = (
                [0.05, 0.19, 0.95, 0.9]
                if clearance and len(inputs) > 1
                else [0.41, 0.44, 0.57, 0.6]
            )
            entities = [
                dict(
                    track_id="goal",
                    label=world.landmark("G2").label,
                    bbox=box,
                    confidence=0.99,
                    evidence_observation_id=request.observation_id,
                )
            ]
            if clearance:
                entities.append(
                    dict(
                        track_id="floor",
                        label="forward_clearance",
                        bbox=[0.1, 0.55, 0.9, 0.98],
                        confidence=0.95,
                        evidence_observation_id=request.observation_id,
                    )
                )
            return ModelResponse(
                request_id=request.request_id,
                observation_id=request.observation_id,
                provider="synthetic",
                model="pilot-regression",
                payload=dict(entities=entities, hazards=[], uncertainties=[]),
            )

    runtime = StableNavigationV5(
        client,
        lambda _: RaceFrames(client, clock),
        SimpleNamespace(provider=Provider()),
        world,
        world.task("G2-1"),
        clock=clock,
    )
    report = asyncio.run(runtime.run())
    assert report["outcome"] == outcome and runtime.source.dropped
    assert report["world_version"] == world.version and not report["sends_live_input"]
    assert any(a["kind"] == "motion" for a in report["actions"]) == clearance
    assert all(r["status"] == "COMPLETED" for r in report["results"])
    assert all(r["verification"] != "SUCCESS" for r in report["results"]) or clearance
    validate_stability_report(report)
    assert client.core.active is None and client.core.state == "PAUSED"
    if client.transport.messages:
        assert all(dict(client.transport.messages)[axis] == (0.0,) for axis in AXES)
    path = Path(".runtime/verify") / f"t08_pilot-{outcome}-{client.session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
