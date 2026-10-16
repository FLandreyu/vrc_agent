"""假模型。

- `FakeProvider`：T01 的固定动作示例，给 `run --mode dry-run` 用，签名保持不动。
- `FakeSceneProvider`：T05 的离线假视觉模型，实现 `ModelProvider` 协议，
  从输入元数据**确定性**地产出结构化场景载荷；零费用、无网络、无随机。

假模型的载荷刻意带 `synthetic` 前缀并在 uncertainties 里写明「合成载荷，不代表真实
感知」，避免被误当成 T06 的视觉闭环能力。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal, NoReturn

from vrc_agent.clock import Clock, SystemClock
from vrc_agent.contracts import ActionProposal
from vrc_agent.providers.base import (
    Accounting,
    CancelToken,
    Deadline,
    ModelInput,
    ModelResponse,
    Usage,
    UsageLedger,
)
from vrc_agent.providers.errors import ProviderError, ProviderFailure

#: 假模型可以模拟的故障，用于离线测试超时/错误路径，不需要网络。
FakeBehaviour = Literal["ok", "garbage", "error", "timeout", "refused", "cancelled"]

FAKE_BEHAVIOURS: tuple[FakeBehaviour, ...] = (
    "ok",
    "garbage",
    "error",
    "timeout",
    "refused",
    "cancelled",
)

SYNTHETIC_LABEL = "synthetic_landmark"
SYNTHETIC_UNCERTAINTY = "fake provider: synthetic payload, not real perception"


def default_scene_payload(
    model_input: ModelInput,
    *,
    label: str = SYNTHETIC_LABEL,
    confidence: float = 0.6,
    bbox: tuple[float, float, float, float] = (0.1, 0.1, 0.3, 0.3),
) -> dict[str, Any]:
    """确定性的合成场景载荷：证据指向本次观测，标签带 synthetic 前缀。"""
    return {
        "entities": [
            {
                "track_id": f"synthetic-{model_input.observation_id}"[:64],
                "label": label,
                "bbox": list(bbox),
                "confidence": confidence,
                "evidence_observation_id": model_input.observation_id,
                "last_seen_ns": model_input.created_at_ns,
            }
        ],
        "hazards": [],
        "uncertainties": [SYNTHETIC_UNCERTAINTY],
        "active_goal": None,
    }


class FakeProvider:
    """固定动作示例，不包含视觉感知，也不代表智能导航。"""

    async def infer(self) -> ActionProposal:
        await asyncio.sleep(0)
        return ActionProposal.model_validate(
            {
                "kind": "motion",
                "args": {"forward": 0.25, "duration_ms": 200},
                "expected_outcome": "offline input pulse only; world outcome unverified",
            }
        )


class FakeSceneProvider:
    """离线假视觉模型：确定性、免费、不出网。

    假模型**从不发送任何内容**：画面字节即使被传入也只留在内存里，所以 `image_count`
    恒为 0，token 与费用是**确实的 0**（不是未知）。
    """

    name = "fake"
    paid = False

    def __init__(
        self,
        *,
        model: str = "fake-scene-v1",
        behaviour: FakeBehaviour = "ok",
        payload: dict[str, Any] | None = None,
        latency_ms: int = 0,
        clock: Clock | None = None,
        ledger: UsageLedger | None = None,
        accounting: Accounting | None = None,
    ) -> None:
        if behaviour not in FAKE_BEHAVIOURS:
            raise ValueError(f"unknown fake behaviour: {behaviour!r}")
        if latency_ms < 0:
            raise ValueError("latency_ms must be >= 0")
        self.model = model
        self.behaviour: FakeBehaviour = behaviour
        self._payload = payload
        self._latency_ms = latency_ms
        self._clock: Clock = clock or SystemClock()
        self._ledger = ledger
        # 假供应商是免费的，但「离线运行时的预算快照」同样要能看到调用与失败（复核 R3）。
        self._accounting: Accounting | None = accounting if accounting is not None else ledger

    def payload_for(self, model_input: ModelInput) -> dict[str, Any]:
        if self._payload is not None:
            return json.loads(json.dumps(self._payload))  # 深拷贝，调用方改不脏内部状态
        return default_scene_payload(model_input)

    def _fail(self, failure: ProviderFailure, detail: str) -> NoReturn:
        if self._accounting is not None:
            self._accounting.record_failure(provider=self.name, failure=failure, paid=self.paid)
        raise ProviderError(failure, detail)

    async def infer(
        self,
        model_input: ModelInput,
        *,
        deadline: Deadline = Deadline.unlimited(),
        cancel: CancelToken | None = None,
    ) -> ModelResponse:
        started = self._clock.now_ns()
        if self._latency_ms:
            await asyncio.sleep(self._latency_ms / 1000)
        token = cancel or CancelToken()
        if token.cancelled:
            self._fail("CANCELLED", "fake provider was configured to cancel")
        if deadline.expired(self._clock.now_ns()):
            self._fail("TIMEOUT", "deadline exceeded before the fake provider answered")
        if self.behaviour == "cancelled":
            self._fail("CANCELLED", "fake provider was configured to cancel")
        if self.behaviour == "timeout":
            self._fail("TIMEOUT", "fake provider was configured to time out")
        if self.behaviour == "error":
            self._fail("TRANSPORT_ERROR", "fake provider was configured to fail")
        if self.behaviour == "refused":
            self._fail("REFUSED", "fake provider was configured to refuse")
        if self.behaviour == "garbage":
            payload: dict[str, Any] = {"unexpected": ["not", "a", "scene"]}
        else:
            payload = self.payload_for(model_input)
        response = ModelResponse(
            request_id=model_input.request_id,
            observation_id=model_input.observation_id,
            provider=self.name,
            model=self.model,
            payload=payload,
            # 假供应商不调用任何模型、也不发送画面：token/费用/图片数都是确实的 0。
            usage=Usage(
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                image_count=0,
                requests=1,
                cost_usd=0.0,
                cost_estimated=False,
            ),
            latency_ms=max(0, (self._clock.now_ns() - started) // 1_000_000),
            attempts=1,
            finish_reason="stop",
            created_at_ns=self._clock.now_ns(),
        )
        if self._accounting is not None:
            self._accounting.record(response, paid=self.paid)
        return response
