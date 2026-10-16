"""感知：把一次观测交给模型，换回受约束的 `SceneState`。

`Perception.interpret()` 是感知层唯一的入口，它做四件事：能看清才调用模型、调用必须带
截止期与取消、把不可信载荷变成受约束场景、**保证迟到的响应不能覆盖更新的场景**。

刻意不做的事：不产生动作、不碰 transport、不改控制器状态、不落盘（画面只以内存字节
传给 provider，是否出网由 provider 的 `send_images` 决定，默认关闭）。
"""

from __future__ import annotations

import threading

from vrc_agent.capture.frames import Frame
from vrc_agent.clock import Clock, SystemClock
from vrc_agent.contracts import Observation, SceneState, new_id
from vrc_agent.perception.errors import PerceptionError
from vrc_agent.perception.scene import SceneDraft, build_scene, unknown_scene
from vrc_agent.providers.base import CancelToken, Deadline, ModelInput
from vrc_agent.providers.protocol import ModelProvider, ProviderSink

MS = 1_000_000

PERCEPTION_PROMPT = (
    "Describe only what is visible in this single frame of a VR application. "
    'Reply with one JSON object: {"entities": [{"track_id": str, "label": str, '
    '"bbox": [x1,y1,x2,y2], "confidence": 0..1, "evidence_observation_id": str}], '
    '"hazards": [str], "uncertainties": [str], "active_goal": str|null}. '
    "bbox is normalized to the full frame, origin at the top left. "
    "Every entity must cite this observation id as evidence. "
    "If the frame is unclear, return empty entities and put the reason in uncertainties. "
    "Never output actions, commands, leases or OSC addresses."
)


class Perception:
    """观测 → 场景。实现 ARCHITECTURE 第 4 节的 `Perception.interpret(observation)`。"""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        clock: Clock | None = None,
        max_entities: int = 64,
        max_observation_age_ms: int = 1500,
        prompt: str = PERCEPTION_PROMPT,
        sink: ProviderSink | None = None,
    ) -> None:
        if max_entities < 1:
            raise ValueError("max_entities must be >= 1")
        if max_observation_age_ms < 0:
            raise ValueError("max_observation_age_ms must be >= 0")
        if not prompt:
            raise ValueError("prompt must not be empty")
        self._provider = provider
        self._clock: Clock = clock or SystemClock()
        self._max_entities = max_entities
        self._max_age_ms = max_observation_age_ms
        self._prompt = prompt
        self._sink = sink
        self._lock = threading.Lock()
        self._last_captured_at_ns: int | None = None
        self._calls = 0
        self._scenes = 0
        self._unknown_scenes = 0
        self._skipped_not_visible = 0
        self._skipped_no_frame = 0
        self._skipped_stale = 0
        self._stale_after_call = 0
        self._dropped_entities = 0
        self._overflow_entities = 0
        self._action_like = 0

    @property
    def provider(self) -> ModelProvider:
        return self._provider

    def stats(self) -> dict[str, int]:
        """本会话的计数。语义要点（监控读数时别误判）：

        - `scenes` / `unknown_scenes` 只统计**走过模型通道**的场景；`unknown_scenes`
          是模型回答不可用（载荷畸形、没有可用实体）而得的 unknown 场景。
        - 被守卫挡下的观测**不产生场景**，只计入 `skipped_not_visible` /
          `skipped_no_frame` / `skipped_stale`（「没问过」不等于「问过但看不清」）。
        - 账目是完整的：`calls + skipped_* == interpret()` 的调用次数。
        """
        with self._lock:
            return {
                "calls": self._calls,
                "scenes": self._scenes,
                "unknown_scenes": self._unknown_scenes,
                "skipped_not_visible": self._skipped_not_visible,
                "skipped_no_frame": self._skipped_no_frame,
                "skipped_stale": self._skipped_stale,
                "stale_after_call": self._stale_after_call,
                "dropped_entities": self._dropped_entities,
                "overflow_entities": self._overflow_entities,
                "action_like_fields": self._action_like,
            }

    async def interpret(
        self,
        observation: Observation,
        *,
        frame: Frame | None = None,
        deadline: Deadline = Deadline.unlimited(),
        cancel: CancelToken | None = None,
    ) -> SceneState:
        """返回受约束的场景。

        - 看不清（窗口非 visible、没有画面、观测过旧）→ `confidence=None` 的空场景，**不调用模型**。
        - 供应商失败 → `ProviderError` 原样抛出（没有场景，比给一个可疑场景更安全）。
        - 迟到的响应（旧观测的结果晚于新观测）→ `PerceptionError("OUT_OF_ORDER")`，不产出场景。
        """
        now_ns = self._clock.now_ns()
        visible = self._guard_live(observation, frame=frame, now_ns=now_ns)
        if visible is not None:
            return visible

        assert frame is not None  # _guard_live 已经排除了没有画面的情况
        request = ModelInput(
            prompt=self._prompt,
            observation_id=observation.observation_id,
            request_id=new_id(),
            sequence=self._next_sequence(observation),
            image=frame.data,
            image_media_type="application/x-bgra",
            image_ref=frame.ref,
            image_size=frame.size,
            session_id=observation.session_id,
            environment_epoch=observation.environment_epoch,
            created_at_ns=observation.captured_at_ns,
        )
        with self._lock:
            self._calls += 1
        response = await self._provider.infer(
            request, deadline=deadline, cancel=cancel or CancelToken()
        )
        if (
            response.request_id != request.request_id
            or response.observation_id != observation.observation_id
        ):
            self._emit("perception_failure", status="MISROUTED_RESPONSE", operation="interpret")
            raise PerceptionError(
                "MISROUTED_RESPONSE",
                f"response for request {response.request_id!r} cites observation "
                f"{response.observation_id!r}; expected {observation.observation_id!r}",
            )
        now_ns = self._clock.now_ns()
        self._guard_ordering(observation, now_ns=now_ns)
        age_ms = self._age_ms(observation, now_ns=now_ns)
        if self._max_age_ms and age_ms > self._max_age_ms:
            with self._lock:
                self._stale_after_call += 1
            reason = f"observation aged out during inference ({age_ms} ms > {self._max_age_ms} ms)"
            self._emit("perception_skipped", status="stale_after_call", operation="interpret")
            return unknown_scene(observation, reason=reason, generated_at_ns=now_ns)
        draft = build_scene(
            response.payload,
            observation,
            generated_at_ns=now_ns,
            max_entities=self._max_entities,
        )
        self._record(draft)
        return draft.scene

    # ---------------------------------------------------------------- 内部

    def _guard_live(
        self,
        observation: Observation,
        *,
        frame: Frame | None,
        now_ns: int,
    ) -> SceneState | None:
        """前置检查；返回非 None 表示这次不调用模型，直接给「未知」场景。"""
        if observation.window_state != "visible":
            with self._lock:
                self._skipped_not_visible += 1
            self._emit(
                "perception_skipped",
                status=observation.window_state,
                state=observation.window_state,
                operation="interpret",
            )
            return unknown_scene(
                observation,
                reason=f"window_state={observation.window_state}: nothing to look at",
                generated_at_ns=now_ns,
            )
        if frame is None or not frame.data:
            with self._lock:
                self._skipped_no_frame += 1
            self._emit("perception_skipped", status="no_frame", operation="interpret")
            return unknown_scene(
                observation,
                reason="no frame bytes available for this observation",
                generated_at_ns=now_ns,
            )
        age_ms = self._age_ms(observation, now_ns=now_ns)
        if self._max_age_ms and age_ms > self._max_age_ms:
            with self._lock:
                self._skipped_stale += 1
            self._emit("perception_skipped", status="stale", operation="interpret")
            return unknown_scene(
                observation,
                reason=f"observation is {age_ms} ms old (limit {self._max_age_ms} ms)",
                generated_at_ns=now_ns,
            )
        with self._lock:
            if (
                self._last_captured_at_ns is not None
                and observation.captured_at_ns < self._last_captured_at_ns
            ):
                # 明确的乱序输入：不是「未知场景」，而是调用方出了错，直接拒绝。
                raise PerceptionError(
                    "OUT_OF_ORDER",
                    f"observation captured at {observation.captured_at_ns} is older than "
                    f"the last interpreted one ({self._last_captured_at_ns})",
                )
            self._last_captured_at_ns = observation.captured_at_ns
        return None

    def _guard_ordering(self, observation: Observation, *, now_ns: int) -> None:
        """响应回来时再查一次：期间有更新的观测被解读，则本次响应必须丢弃。"""
        with self._lock:
            latest = self._last_captured_at_ns
        if latest is not None and observation.captured_at_ns < latest:
            self._emit("perception_failure", status="OUT_OF_ORDER", operation="interpret")
            raise PerceptionError(
                "OUT_OF_ORDER",
                f"response for observation captured at {observation.captured_at_ns} arrived "
                f"after a newer one ({latest}); scene discarded",
            )

    def _age_ms(self, observation: Observation, *, now_ns: int) -> int:
        return max(0, (now_ns - observation.captured_at_ns) // MS)

    def _next_sequence(self, observation: Observation) -> int:
        with self._lock:
            return self._calls

    def _record(self, draft: SceneDraft) -> None:
        with self._lock:
            self._scenes += 1
            self._dropped_entities += draft.dropped_entities
            self._overflow_entities += draft.overflow_entities
            self._action_like += len(draft.action_like_fields)
            if draft.scene.confidence is None:
                self._unknown_scenes += 1
        if draft.action_like_fields:
            self._emit(
                "perception_action_like_fields",
                status="ignored",
                operation="interpret",
                count=len(draft.action_like_fields),
            )
        self._emit(
            "perception_scene",
            status="unknown" if draft.scene.confidence is None else "ok",
            operation="interpret",
            count=len(draft.scene.entities),
        )

    def _emit(self, event: str, **fields: object) -> None:
        if self._sink is not None:
            self._sink.emit(event, **fields)


__all__ = ["PERCEPTION_PROMPT", "Perception"]
