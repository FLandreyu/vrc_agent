"""供应商公共构件：截止期、取消、用量、预算台账、有限重试。

这些是「模型调用不得拖住控制进程」的机制层保证，全部用假时钟与假睡眠驱动，不真等待。
"""

from __future__ import annotations

import asyncio

import pytest

from vrc_agent.clock import FakeClock
from vrc_agent.providers.base import (
    CancelToken,
    Deadline,
    ModelInput,
    ModelResponse,
    Usage,
    UsageLedger,
    run_attempts,
)
from vrc_agent.providers.errors import PROVIDER_FAILURES, RETRYABLE_FAILURES, ProviderError

MS = 1_000_000


def make_response(**changes: object) -> ModelResponse:
    payload: dict[str, object] = {
        "request_id": "req-1",
        "observation_id": "obs-1",
        "provider": "stub",
        "model": "stub-v1",
        "payload": {"entities": []},
        "usage": Usage(requests=1),
        "latency_ms": 3,
        "attempts": 1,
        "finish_reason": "stop",
        "created_at_ns": 1,
    }
    payload.update(changes)
    return ModelResponse(**payload)  # type: ignore[arg-type]


# ------------------------------------------------------------------ 截止期与取消


def test_a_deadline_without_a_limit_never_expires() -> None:
    deadline = Deadline.unlimited()
    assert deadline.at_ns is None
    assert deadline.remaining_ms(0) is None
    assert deadline.expired(10**18) is False
    deadline.raise_if_expired(10**18)  # 不抛


def test_a_deadline_is_absolute_and_rounds_up_to_whole_milliseconds() -> None:
    clock = FakeClock(1_000_000_000)
    deadline = Deadline.from_now(clock, 250)
    assert deadline.at_ns == 1_000_000_000 + 250 * MS
    assert deadline.remaining_ms(deadline.at_ns - 1) == 1  # 向上取整，不留半毫秒
    assert deadline.remaining_ms(deadline.at_ns) == 0
    assert deadline.expired(deadline.at_ns - 1) is False
    assert deadline.expired(deadline.at_ns) is True
    with pytest.raises(ProviderError) as excinfo:
        deadline.raise_if_expired(deadline.at_ns)
    assert excinfo.value.failure == "TIMEOUT"


def test_deadline_requires_a_positive_timeout() -> None:
    clock = FakeClock(0)
    for timeout_ms in (0, -1):
        with pytest.raises(ValueError):
            Deadline.from_now(clock, timeout_ms)


def test_a_cancelled_token_stops_further_work() -> None:
    token = CancelToken()
    assert token.cancelled is False
    token.raise_if_cancelled()
    token.cancel()
    assert token.cancelled is True
    with pytest.raises(ProviderError) as excinfo:
        token.raise_if_cancelled()
    assert excinfo.value.failure == "CANCELLED"


# ------------------------------------------------------------------ 用量


def test_unknown_usage_stays_unknown_and_is_never_reported_as_zero() -> None:
    usage = Usage(requests=1)
    assert usage.tokens_known is False
    assert usage.cost_known is False
    assert usage.total_tokens is None
    assert usage.cost_usd is None

    known = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15, requests=1, cost_usd=0.0)
    assert known.tokens_known is True
    assert known.cost_known is True


# ------------------------------------------------------------------ 预算台账


def test_free_calls_need_no_budget() -> None:
    ledger = UsageLedger(budget_usd=0.0)
    ledger.ensure_affordable(provider="fake", paid=False)  # 不抛
    assert ledger.snapshot()["budget_usd"] == 0.0


def test_a_paid_call_without_a_budget_is_refused() -> None:
    ledger = UsageLedger(budget_usd=0.0)
    with pytest.raises(ProviderError) as excinfo:
        ledger.ensure_affordable(provider="openai_compat", paid=True)
    assert excinfo.value.failure == "BUDGET_EXCEEDED"


def test_a_paid_call_is_refused_once_the_known_spend_reaches_the_budget() -> None:
    ledger = UsageLedger(budget_usd=0.5)
    ledger.ensure_affordable(provider="openai_compat", paid=True)
    ledger.record(
        make_response(usage=Usage(total_tokens=1000, requests=1, cost_usd=0.5)), paid=True
    )
    assert ledger.spent_usd_known == pytest.approx(0.5)
    with pytest.raises(ProviderError) as excinfo:
        ledger.ensure_affordable(provider="openai_compat", paid=True)
    assert excinfo.value.failure == "BUDGET_EXCEEDED"


def test_the_ledger_separates_known_spend_from_unknown_cost_calls() -> None:
    ledger = UsageLedger(budget_usd=10.0)

    ledger.record(make_response(usage=Usage(total_tokens=100, requests=1, cost_usd=0.02)))
    assert ledger.cost_complete is True

    # 付费但价格未知：可能已经花掉钱，必须单独计数，且不能污染已花费金额。
    ledger.record(make_response(usage=Usage(requests=1)), paid=True)
    assert ledger.unknown_cost_calls == 1
    assert ledger.cost_complete is False
    assert ledger.spent_usd_known == pytest.approx(0.02)

    # 免费调用即使用量未知，也不是「未知费用」。
    ledger.record(make_response(usage=Usage(requests=1)), paid=False)
    assert ledger.unknown_cost_calls == 1

    snapshot = ledger.snapshot()
    assert snapshot["calls"] == 3
    assert snapshot["unknown_cost_calls"] == 1
    assert snapshot["cost_complete"] is False
    assert snapshot["unknown_token_calls"] == 2
    assert snapshot["tokens_known"] == 100
    assert snapshot["by_provider"] == {"stub": 3}


def test_failed_paid_calls_are_counted_as_possibly_billed() -> None:
    ledger = UsageLedger(budget_usd=10.0)
    ledger.record_failure(provider="openai_compat", failure="TIMEOUT", paid=True)
    snapshot = ledger.snapshot()
    assert snapshot["calls"] == 1
    assert snapshot["unknown_cost_calls"] == 1
    assert snapshot["cost_complete"] is False


def test_the_ledger_rejects_a_negative_budget() -> None:
    with pytest.raises(ValueError):
        UsageLedger(budget_usd=-0.01)


# ------------------------------------------------------------------ 有限重试


def test_attempts_retry_only_retryable_failures() -> None:
    clock = FakeClock(0)
    sleeps: list[float] = []

    async def flaky(index: int) -> str:
        if index == 0:
            raise ProviderError("TRANSPORT_ERROR", "connection reset")
        return "ok"

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    result = asyncio.run(
        run_attempts(
            flaky,
            max_retries=2,
            backoff_ms=100,
            deadline=Deadline.unlimited(),
            cancel=CancelToken(),
            clock=clock,
            sleep=sleep,
        )
    )
    assert result == "ok"
    assert sleeps == [0.1]  # 退避随尝试次数线性增长


def test_a_non_retryable_failure_is_raised_immediately() -> None:
    clock = FakeClock(0)
    calls = 0

    async def broken(index: int) -> str:
        nonlocal calls
        calls += 1
        raise ProviderError("MALFORMED_RESULT", "not json")

    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(
            run_attempts(
                broken,
                max_retries=3,
                backoff_ms=1,
                deadline=Deadline.unlimited(),
                cancel=CancelToken(),
                clock=clock,
            )
        )
    assert excinfo.value.failure == "MALFORMED_RESULT"
    assert calls == 1


def test_retries_stop_at_the_configured_limit() -> None:
    clock = FakeClock(0)
    calls = 0

    async def always_flaky(index: int) -> str:
        nonlocal calls
        calls += 1
        raise ProviderError("HTTP_ERROR", "HTTP 503", status=503)

    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(
            run_attempts(
                always_flaky,
                max_retries=2,
                backoff_ms=0,
                deadline=Deadline.unlimited(),
                cancel=CancelToken(),
                clock=clock,
            )
        )
    assert calls == 3  # 首次 + 两次重试
    assert excinfo.value.status == 503


def test_a_cancelled_or_expired_call_never_reaches_the_provider_again() -> None:
    clock = FakeClock(0)
    calls = 0

    async def nothing(index: int) -> str:
        nonlocal calls
        calls += 1
        return "ok"

    cancelled = CancelToken()
    cancelled.cancel()
    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(
            run_attempts(
                nothing,
                max_retries=1,
                backoff_ms=0,
                deadline=Deadline.unlimited(),
                cancel=cancelled,
                clock=clock,
            )
        )
    assert excinfo.value.failure == "CANCELLED"
    assert calls == 0

    expired = Deadline.from_now(clock, 50)
    clock.advance_ms(50)
    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(
            run_attempts(
                nothing,
                max_retries=1,
                backoff_ms=0,
                deadline=expired,
                cancel=CancelToken(),
                clock=clock,
            )
        )
    assert excinfo.value.failure == "TIMEOUT"
    assert calls == 0


def test_backoff_is_skipped_when_it_would_exceed_the_deadline() -> None:
    """来不及退避就不睡：立刻再试一次，而不是先睡过截止期。

    重试本身仍是安全的：适配器每次尝试都用剩余预算重算 HTTP 超时（`_timeout_s`），
    所以「不睡直接重试」不会让调用活过截止期。
    """
    clock = FakeClock(0)
    sleeps: list[float] = []
    calls = 0

    async def flaky(index: int) -> str:
        nonlocal calls
        calls += 1
        raise ProviderError("TRANSPORT_ERROR", "reset")

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    deadline = Deadline.from_now(clock, 100)  # 最短的退避是 500ms，注定来不及
    with pytest.raises(ProviderError):
        asyncio.run(
            run_attempts(
                flaky,
                max_retries=1,
                backoff_ms=500,
                deadline=deadline,
                cancel=CancelToken(),
                clock=clock,
                sleep=sleep,
            )
        )
    assert calls == 2
    assert sleeps == []  # 明知来不及就不睡


def test_run_attempts_rejects_a_negative_retry_budget() -> None:
    async def nothing(index: int) -> str:
        return "ok"

    with pytest.raises(ValueError):
        asyncio.run(
            run_attempts(
                nothing,
                max_retries=-1,
                backoff_ms=0,
                deadline=Deadline.unlimited(),
                cancel=CancelToken(),
                clock=FakeClock(0),
            )
        )


def test_retryable_set_matches_the_documented_failures() -> None:
    assert RETRYABLE_FAILURES == {"TRANSPORT_ERROR", "HTTP_ERROR"}
    assert "CANCELLED" not in RETRYABLE_FAILURES
    assert set(PROVIDER_FAILURES) == {
        "NOT_CONFIGURED",
        "UNSUPPORTED",
        "CANCELLED",
        "TIMEOUT",
        "TRANSPORT_ERROR",
        "HTTP_ERROR",
        "MALFORMED_RESULT",
        "REFUSED",
        "BUDGET_EXCEEDED",
    }


def test_provider_error_rejects_an_unknown_failure_code() -> None:
    with pytest.raises(ValueError):
        ProviderError("NOPE", "not a real failure")  # type: ignore[arg-type]


def test_model_input_is_frozen_and_validated() -> None:
    with pytest.raises(ValueError):
        ModelInput(prompt="", observation_id="obs-1", request_id="req-1")
    with pytest.raises(ValueError):
        ModelInput(prompt="p", observation_id="", request_id="req-1")
    with pytest.raises(ValueError):
        # 带画面就必须带尺寸；反之没有画面就不能只给引用。
        ModelInput(prompt="p", observation_id="obs-1", request_id="req-1", image=b"1234")
    with pytest.raises(ValueError):
        ModelInput(prompt="p", observation_id="obs-1", request_id="req-1", image_ref="frame-1")
    with pytest.raises(ValueError):
        ModelInput(prompt="p", observation_id="obs-1", request_id="req-1", image=b"")
    ok = ModelInput(
        prompt="p",
        observation_id="obs-1",
        request_id="req-1",
        image=b"1234",
        image_size=(1, 1),
        image_ref="frame-1",
    )
    assert ok.sequence == 0 and ok.environment_epoch == 0
