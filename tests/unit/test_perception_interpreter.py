"""感知入口 `Perception.interpret()`：能看清才调用模型、调用带截止期与取消、迟到响应不得覆盖。

用 `asyncio.run()` 驱动（本仓库没有 pytest-asyncio 依赖），供应商全部是进程内假实现，
不联网、不真睡眠。
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

from vrc_agent.capture.frames import Frame
from vrc_agent.clock import FakeClock
from vrc_agent.contracts import Observation, SceneState
from vrc_agent.perception.errors import PERCEPTION_FAILURES, PerceptionError
from vrc_agent.perception.interpreter import PERCEPTION_PROMPT, Perception
from vrc_agent.providers.base import CancelToken, Deadline, ModelInput, ModelResponse, Usage
from vrc_agent.providers.errors import PROVIDER_FAILURES, ProviderError
from vrc_agent.providers.fake import FakeSceneProvider
from vrc_agent.telemetry import ALLOWED_FIELDS

SESSION = "unit-perception"
OBSERVATION_ID = "obs-unit-1"
CAPTURED_AT_NS = 10_000_000_000
MS = 1_000_000

WIDTH, HEIGHT = 4, 3
FRAME_BYTES = bytes([7, 8, 9, 255] * (WIDTH * HEIGHT))


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))


class StubProvider:
    """可控假供应商：记录输入、可选失败、可选在调用期间动时钟。"""

    name = "stub"
    paid = False

    def __init__(
        self,
        *,
        payload: Any = None,
        failure: str | None = None,
        observation_id: str | None = None,
        on_call: Any = None,
    ) -> None:
        self.calls = 0
        self.inputs: list[ModelInput] = []
        self.deadlines: list[Deadline] = []
        self.cancels: list[CancelToken | None] = []
        self._payload = payload
        self._failure = failure
        self._observation_id = observation_id
        self._on_call = on_call

    async def infer(
        self,
        model_input: ModelInput,
        *,
        deadline: Deadline = Deadline.unlimited(),
        cancel: CancelToken | None = None,
    ) -> ModelResponse:
        self.calls += 1
        self.inputs.append(model_input)
        self.deadlines.append(deadline)
        self.cancels.append(cancel)
        if self._on_call is not None:
            outcome = self._on_call(model_input)
            if inspect.isawaitable(outcome):  # 同步钩子与异步钩子都允许
                await outcome
        if self._failure is not None:
            raise ProviderError(self._failure, "stub failure")  # type: ignore[arg-type]
        payload = self._payload
        if payload is None:
            payload = {
                "entities": [
                    {
                        "track_id": "stub_entity",
                        "label": "box",
                        "bbox": [0.25, 0.25, 0.75, 0.75],
                        "confidence": 0.5,
                        "evidence_observation_id": model_input.observation_id,
                    }
                ]
            }
        return ModelResponse(
            request_id=model_input.request_id,
            observation_id=self._observation_id or model_input.observation_id,
            provider=self.name,
            model="stub-v1",
            payload=payload,
            usage=Usage(total_tokens=None, image_count=0, requests=1, cost_usd=None),
            latency_ms=1,
            attempts=1,
            finish_reason="stop",
            created_at_ns=model_input.created_at_ns,
        )


def make_frame(*, captured_at_ns: int = CAPTURED_AT_NS, ref: str = "frame-unit-1") -> Frame:
    return Frame(
        ref=ref, data=FRAME_BYTES, width=WIDTH, height=HEIGHT, captured_at_ns=captured_at_ns
    )


def make_observation(**changes: object) -> Observation:
    payload: dict[str, object] = {
        "session_id": SESSION,
        "observation_id": OBSERVATION_ID,
        "captured_at_ns": CAPTURED_AT_NS,
        "environment_epoch": 1,
        "window_state": "visible",
        "frame_ref": "frame-unit-1",
        "frame_size": (WIDTH, HEIGHT),
    }
    payload.update(changes)
    return Observation(**payload)  # type: ignore[arg-type]


def interpret(perception: Perception, observation: Observation, **kwargs: object) -> SceneState:
    return asyncio.run(
        perception.interpret(observation, **kwargs)  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("window_state", ["lost", "minimized", "unknown"])
def test_a_window_that_is_not_visible_never_calls_the_provider(window_state: str) -> None:
    clock = FakeClock(CAPTURED_AT_NS)
    provider = StubProvider()
    perception = Perception(provider, clock=clock)

    scene = interpret(
        perception,
        make_observation(window_state=window_state),
        frame=make_frame(),
    )

    assert provider.calls == 0
    assert scene.confidence is None
    assert scene.entities == ()
    assert any(window_state in text for text in scene.uncertainties)
    stats = perception.stats()
    assert stats["skipped_not_visible"] == 1
    assert stats["calls"] == 0
    assert stats["scenes"] == 0


def test_a_visible_window_without_frame_bytes_never_calls_the_provider() -> None:
    provider = StubProvider()
    perception = Perception(provider, clock=FakeClock(CAPTURED_AT_NS))

    scene = interpret(perception, make_observation(), frame=None)

    assert provider.calls == 0
    assert scene.confidence is None
    assert perception.stats()["skipped_no_frame"] == 1


def test_an_observation_older_than_the_limit_never_calls_the_provider() -> None:
    clock = FakeClock(CAPTURED_AT_NS + 2_000 * MS)
    provider = StubProvider()
    perception = Perception(provider, clock=clock, max_observation_age_ms=1500)

    scene = interpret(perception, make_observation(), frame=make_frame())

    assert provider.calls == 0
    assert scene.confidence is None
    assert perception.stats()["skipped_stale"] == 1


def test_a_fresh_visible_observation_is_interpreted_with_evidence() -> None:
    sink = RecordingSink()
    provider = StubProvider()
    perception = Perception(provider, clock=FakeClock(CAPTURED_AT_NS), sink=sink)

    scene = interpret(perception, make_observation(), frame=make_frame())

    assert provider.calls == 1
    assert scene.observation_ids == (OBSERVATION_ID,)
    assert scene.confidence == pytest.approx(0.5)
    assert scene.entities[0].evidence_observation_id == OBSERVATION_ID
    stats = perception.stats()
    assert (stats["calls"], stats["scenes"], stats["unknown_scenes"]) == (1, 1, 0)
    assert [event for event, _ in sink.events] == ["perception_scene"]


def test_the_frame_reaches_the_provider_only_as_in_memory_bytes() -> None:
    provider = StubProvider()
    perception = Perception(provider, clock=FakeClock(CAPTURED_AT_NS))
    frame = make_frame()

    interpret(perception, make_observation(), frame=frame)

    sent = provider.inputs[0]
    assert sent.image == FRAME_BYTES
    assert sent.image_ref == frame.ref
    assert sent.image_size == (WIDTH, HEIGHT)
    assert sent.image_media_type == "application/x-bgra"
    assert sent.prompt == PERCEPTION_PROMPT
    assert sent.session_id == SESSION
    assert sent.environment_epoch == 1
    assert sent.observation_id == OBSERVATION_ID
    # 输入里没有文件路径，也没有 URL。
    assert "/" not in sent.image_ref and "\\" not in sent.image_ref


def test_deadline_and_cancel_token_are_forwarded_to_the_provider() -> None:
    provider = StubProvider()
    perception = Perception(provider, clock=FakeClock(CAPTURED_AT_NS))
    deadline = Deadline.from_now(FakeClock(CAPTURED_AT_NS), 250)
    token = CancelToken()

    interpret(perception, make_observation(), frame=make_frame(), deadline=deadline, cancel=token)

    assert provider.deadlines[0] == deadline
    assert provider.cancels[0] is token


def test_an_older_observation_after_a_newer_one_is_rejected() -> None:
    clock = FakeClock(CAPTURED_AT_NS)
    provider = StubProvider()
    perception = Perception(provider, clock=clock)

    interpret(perception, make_observation(), frame=make_frame())
    assert provider.calls == 1

    with pytest.raises(PerceptionError) as excinfo:
        interpret(
            perception,
            make_observation(observation_id="obs-unit-old", captured_at_ns=CAPTURED_AT_NS - MS),
            frame=make_frame(),
        )

    assert excinfo.value.failure == "OUT_OF_ORDER"
    assert excinfo.value.failure in PERCEPTION_FAILURES
    # 乱序输入在调用模型之前就被挡住。
    assert provider.calls == 1


def test_a_response_that_cites_another_observation_is_rejected() -> None:
    provider = StubProvider(observation_id="obs-somewhere-else")
    perception = Perception(provider, clock=FakeClock(CAPTURED_AT_NS))

    with pytest.raises(PerceptionError) as excinfo:
        interpret(perception, make_observation(), frame=make_frame())

    assert excinfo.value.failure == "MISROUTED_RESPONSE"
    stats = perception.stats()
    assert stats["calls"] == 1
    assert stats["scenes"] == 0  # 没有场景流出


@pytest.mark.parametrize("behaviour", ["timeout", "cancelled", "error", "refused"])
def test_provider_failures_propagate_and_produce_no_scene(behaviour: str) -> None:
    provider = FakeSceneProvider(behaviour=behaviour)  # type: ignore[arg-type]
    perception = Perception(provider, clock=FakeClock(CAPTURED_AT_NS))

    with pytest.raises(ProviderError) as excinfo:
        interpret(perception, make_observation(), frame=make_frame())

    assert excinfo.value.failure in PROVIDER_FAILURES
    stats = perception.stats()
    assert stats["scenes"] == 0
    assert stats["calls"] == 1


def test_an_observation_that_ages_out_during_inference_becomes_unknown() -> None:
    clock = FakeClock(CAPTURED_AT_NS)

    def age_the_clock(model_input: ModelInput) -> None:
        clock.advance_ms(5000)

    provider = StubProvider(on_call=age_the_clock)
    perception = Perception(provider, clock=clock, max_observation_age_ms=1500)

    scene = interpret(perception, make_observation(), frame=make_frame())

    assert provider.calls == 1
    assert scene.confidence is None
    assert scene.entities == ()
    assert any("aged out" in text for text in scene.uncertainties)
    stats = perception.stats()
    assert stats["stale_after_call"] == 1
    assert stats["scenes"] == 0  # 迟到又过期的结果不产出场景


def test_a_late_response_cannot_overwrite_a_newer_scene() -> None:
    """先发的旧观测如果晚于新观测返回，必须被丢弃（迟到的响应不得覆盖更新的场景）。"""
    clock = FakeClock(CAPTURED_AT_NS)
    release = asyncio.Event()

    class LateProvider:
        name = "late"
        paid = False

        def __init__(self) -> None:
            self.calls = 0

        async def infer(
            self,
            model_input: ModelInput,
            *,
            deadline: Deadline = Deadline.unlimited(),
            cancel: CancelToken | None = None,
        ) -> ModelResponse:
            self.calls += 1
            if model_input.observation_id == OBSERVATION_ID:
                await release.wait()  # 旧观测的响应故意迟到
            else:
                release.set()
            payload = {
                "entities": [
                    {
                        "track_id": "entity_1",
                        "label": "box",
                        "bbox": [0.1, 0.1, 0.5, 0.5],
                        "confidence": 0.5,
                        "evidence_observation_id": model_input.observation_id,
                    }
                ]
            }
            return ModelResponse(
                request_id=model_input.request_id,
                observation_id=model_input.observation_id,
                provider="late",
                model="late-v1",
                payload=payload,
                usage=Usage(requests=1),
                latency_ms=1,
                attempts=1,
                finish_reason="stop",
                created_at_ns=model_input.created_at_ns,
            )

    provider = LateProvider()
    perception = Perception(provider, clock=clock)
    older = make_observation()
    newer = make_observation(
        observation_id="obs-unit-2", captured_at_ns=CAPTURED_AT_NS + MS, frame_ref="frame-unit-2"
    )

    async def run_both() -> list[object]:
        return await asyncio.gather(
            perception.interpret(older, frame=make_frame()),
            perception.interpret(newer, frame=make_frame(captured_at_ns=CAPTURED_AT_NS + MS)),
            return_exceptions=True,
        )

    results = asyncio.run(run_both())

    assert provider.calls == 2
    outcomes = [type(result).__name__ for result in results]
    assert outcomes == ["PerceptionError", "SceneState"]
    error = results[0]
    assert isinstance(error, PerceptionError)
    assert error.failure == "OUT_OF_ORDER"
    newer_scene = results[1]
    assert isinstance(newer_scene, SceneState)
    assert newer_scene.observation_ids == ("obs-unit-2",)
    # 只有一条场景被记入统计：迟到的旧结果没有覆盖它。
    assert perception.stats()["scenes"] == 1


def test_a_payload_without_usable_entities_is_counted_as_unknown() -> None:
    provider = StubProvider(payload={"entities": [{"track_id": "", "label": "x"}]})
    perception = Perception(provider, clock=FakeClock(CAPTURED_AT_NS))

    scene = interpret(perception, make_observation(), frame=make_frame())

    assert scene.confidence is None
    assert scene.entities == ()
    stats = perception.stats()
    assert (stats["scenes"], stats["unknown_scenes"], stats["dropped_entities"]) == (1, 1, 1)


def test_entity_limit_is_applied_and_counted() -> None:
    payload = {
        "entities": [
            {
                "track_id": f"track_{index}",
                "label": "box",
                "bbox": [0.1, 0.1, 0.4, 0.4],
                "confidence": 0.9,
                "evidence_observation_id": OBSERVATION_ID,
            }
            for index in range(3)
        ]
    }
    provider = StubProvider(payload=payload)
    perception = Perception(provider, clock=FakeClock(CAPTURED_AT_NS), max_entities=1)

    scene = interpret(perception, make_observation(), frame=make_frame())

    assert len(scene.entities) == 1
    assert perception.stats()["overflow_entities"] == 2


def test_telemetry_events_only_carry_whitelisted_small_fields() -> None:
    sink = RecordingSink()
    provider = StubProvider(
        payload={"entities": [], "uncertainties": ["too dark"], "tool": {"command": "jump"}}
    )
    perception = Perception(provider, clock=FakeClock(CAPTURED_AT_NS), sink=sink)

    interpret(perception, make_observation(), frame=make_frame())

    assert sink.events
    for event, fields in sink.events:
        assert isinstance(event, str) and event
        assert set(fields) <= ALLOWED_FIELDS
        for value in fields.values():
            assert isinstance(value, (str, int, float, bool)) or value is None
            assert len(str(value)) <= 64
    names = [event for event, _ in sink.events]
    assert "perception_scene" in names
    assert "perception_action_like_fields" in names
    assert any(
        event == "perception_action_like_fields" and fields.get("status") == "ignored"
        for event, fields in sink.events
    )


def test_constructor_rejects_impossible_settings() -> None:
    provider = StubProvider()
    with pytest.raises(ValueError):
        Perception(provider, max_entities=0)
    with pytest.raises(ValueError):
        Perception(provider, max_observation_age_ms=-1)
    with pytest.raises(ValueError):
        Perception(provider, prompt="")


def test_perception_never_touches_a_controller_state() -> None:
    """感知只输出场景：没有任何动作、租约或 OSC 字段，也没有可执行的 kind/args。"""
    provider = StubProvider()
    perception = Perception(provider, clock=FakeClock(CAPTURED_AT_NS))

    scene = interpret(perception, make_observation(), frame=make_frame())
    dumped = scene.model_dump()

    assert not {"kind", "args", "lease_id", "expires_at_ns", "action", "osc"} & set(dumped)
    assert set(scene.__class__.model_fields) == {
        "schema_version",
        "event_id",
        "session_id",
        "scene_id",
        "observation_ids",
        "generated_at_ns",
        "environment_epoch",
        "entities",
        "hazards",
        "uncertainties",
        "active_goal",
        "confidence",
    }
