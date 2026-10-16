"""T05 假供应商：确定性、免费、不出网，且能模拟文档列出的每一种故障。"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from vrc_agent.clock import FakeClock
from vrc_agent.contracts import new_id
from vrc_agent.providers import (
    CancelToken,
    Deadline,
    FakeProvider,
    FakeSceneProvider,
    ModelInput,
    ModelProvider,
    ProviderError,
    UsageLedger,
)
from vrc_agent.providers.factory import PAID_PROVIDERS, PROVIDER_NAMES
from vrc_agent.providers.fake import FAKE_BEHAVIOURS, SYNTHETIC_LABEL, SYNTHETIC_UNCERTAINTY

FRAME_BYTES = bytes(range(256)) * 4  # 伪造的画面字节，只为证明它不会被回显或计数


def make_input(*, observation_id: str = "obs-1", image: bytes | None = None) -> ModelInput:
    return ModelInput(
        prompt="describe what is visible",
        observation_id=observation_id,
        request_id=new_id(),
        image=image,
        image_size=(2, 2) if image is not None else None,
        image_ref="frame-abc" if image is not None else None,
        session_id="test-session",
        environment_epoch=1,
        created_at_ns=1_000_000_000,
    )


def run_infer(provider: FakeSceneProvider, model_input: ModelInput, **kwargs: object):
    return asyncio.run(provider.infer(model_input, **kwargs))  # type: ignore[arg-type]


def test_the_fake_provider_is_free_and_offline() -> None:
    provider = FakeSceneProvider()
    assert provider.name == "fake"
    assert provider.paid is False
    assert provider.model == "fake-scene-v1"
    assert "fake" in PROVIDER_NAMES
    assert "fake" not in PAID_PROVIDERS


def test_the_fake_provider_satisfies_the_model_provider_protocol() -> None:
    assert isinstance(FakeSceneProvider(), ModelProvider)


def test_the_fake_behaviour_set_is_closed_and_documented() -> None:
    assert FAKE_BEHAVIOURS == ("ok", "garbage", "error", "timeout", "refused", "cancelled")
    with pytest.raises(ValueError):
        FakeSceneProvider(behaviour="nonsense")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        FakeSceneProvider(latency_ms=-1)


def test_the_default_payload_cites_this_observation() -> None:
    model_input = make_input(observation_id="obs-42")
    response = run_infer(FakeSceneProvider(), model_input)

    assert response.request_id == model_input.request_id
    assert response.observation_id == "obs-42"
    assert response.provider == "fake"
    assert response.attempts == 1
    assert response.finish_reason == "stop"

    entity = response.payload["entities"][0]
    assert entity["label"] == SYNTHETIC_LABEL
    assert entity["evidence_observation_id"] == "obs-42"
    assert entity["track_id"] == "synthetic-obs-42"
    assert entity["last_seen_ns"] == model_input.created_at_ns
    assert response.payload["uncertainties"] == [SYNTHETIC_UNCERTAINTY]


def test_the_fake_payload_is_deterministic() -> None:
    model_input = make_input()
    first = run_infer(FakeSceneProvider(), model_input)
    second = run_infer(FakeSceneProvider(), model_input)
    assert json.dumps(first.payload, sort_keys=True) == json.dumps(second.payload, sort_keys=True)


def test_different_observations_get_different_track_ids() -> None:
    first = run_infer(FakeSceneProvider(), make_input(observation_id="obs-1"))
    second = run_infer(FakeSceneProvider(), make_input(observation_id="obs-2"))
    assert first.payload["entities"][0]["track_id"] != second.payload["entities"][0]["track_id"]


def test_a_custom_payload_is_deep_copied() -> None:
    provider = FakeSceneProvider(payload={"entities": [], "uncertainties": ["nothing"]})
    first = run_infer(provider, make_input())
    first.payload["uncertainties"].append("mutated by the caller")
    second = run_infer(provider, make_input())
    assert second.payload == {"entities": [], "uncertainties": ["nothing"]}


def test_the_fake_usage_is_a_real_zero_not_an_unknown() -> None:
    # 假模型不调用任何模型、也不发送画面：0 是真实值，所以费用是「已知的 0」，
    # 不能像「没有价格表的真实供应商」那样记成未知。
    response = run_infer(FakeSceneProvider(), make_input(image=FRAME_BYTES))
    usage = response.usage
    assert usage.tokens_known is True
    assert usage.cost_known is True
    assert usage.total_tokens == 0
    assert usage.cost_usd == 0.0
    assert usage.cost_estimated is False
    assert usage.image_count == 0
    assert usage.requests == 1


def test_the_fake_never_echoes_frame_bytes() -> None:
    response = run_infer(FakeSceneProvider(), make_input(image=FRAME_BYTES))
    text = json.dumps(response.payload, sort_keys=True)
    assert FRAME_BYTES.hex() not in text
    assert "frame-abc" not in text
    assert response.usage.image_count == 0


def test_garbage_behaviour_returns_a_payload_without_entities() -> None:
    response = run_infer(FakeSceneProvider(behaviour="garbage"), make_input())
    assert "entities" not in response.payload
    assert response.payload == {"unexpected": ["not", "a", "scene"]}


@pytest.mark.parametrize(
    ("behaviour", "failure", "retryable"),
    [
        ("timeout", "TIMEOUT", False),
        ("cancelled", "CANCELLED", False),
        ("error", "TRANSPORT_ERROR", True),
        ("refused", "REFUSED", False),
    ],
)
def test_documented_behaviours_raise_their_failure(
    behaviour: str, failure: str, retryable: bool
) -> None:
    provider = FakeSceneProvider(behaviour=behaviour)  # type: ignore[arg-type]
    with pytest.raises(ProviderError) as excinfo:
        run_infer(provider, make_input())
    assert excinfo.value.failure == failure
    assert excinfo.value.retryable is retryable


def test_a_pre_cancelled_token_stops_before_answering() -> None:
    token = CancelToken()
    token.cancel()
    with pytest.raises(ProviderError) as excinfo:
        run_infer(FakeSceneProvider(), make_input(), cancel=token)
    assert excinfo.value.failure == "CANCELLED"


def test_an_expired_deadline_is_a_timeout() -> None:
    clock = FakeClock(now_ns=5_000_000_000)
    provider = FakeSceneProvider(clock=clock)
    with pytest.raises(ProviderError) as excinfo:
        run_infer(provider, make_input(), deadline=Deadline(at_ns=clock.now_ns()))
    assert excinfo.value.failure == "TIMEOUT"


def test_an_unlimited_deadline_never_expires() -> None:
    clock = FakeClock(now_ns=5_000_000_000)
    response = run_infer(
        FakeSceneProvider(clock=clock), make_input(), deadline=Deadline.unlimited()
    )
    assert response.observation_id == "obs-1"


def test_a_configured_latency_actually_waits() -> None:
    started = time.monotonic()
    run_infer(FakeSceneProvider(latency_ms=10), make_input())
    assert time.monotonic() - started >= 0.005


def test_the_ledger_counts_successes_and_failures_separately() -> None:
    ledger = UsageLedger()
    provider = FakeSceneProvider(ledger=ledger)
    run_infer(provider, make_input())
    run_infer(provider, make_input())
    with pytest.raises(ProviderError):
        run_infer(FakeSceneProvider(behaviour="error", ledger=ledger), make_input())

    snapshot = ledger.snapshot()
    assert snapshot["calls"] == 3
    assert snapshot["by_provider"] == {"fake": 3}
    # 假供应商免费：费用是已知的 0，失败的调用也没花钱。
    assert snapshot["spent_usd_known"] == 0.0
    assert snapshot["unknown_cost_calls"] == 0
    assert snapshot["cost_complete"] is True
    assert snapshot["tokens_known"] == 0
    assert snapshot["unknown_token_calls"] == 1  # 失败那次没有 usage
    assert snapshot["image_count"] == 0


def test_the_fake_action_example_is_still_a_fixed_pulse() -> None:
    # T01 的固定动作示例必须保持不变：它不是视觉能力，只是离线占位。
    proposal = asyncio.run(FakeProvider().infer())
    assert proposal.kind == "motion"
    assert proposal.args.forward == 0.25
    assert proposal.args.duration_ms == 200
