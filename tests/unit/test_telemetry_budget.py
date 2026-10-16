"""T10：会话预算——调用前门禁、调用后计价入账、耗尽即暂停。

与 `test_providers_base.py` 的分工：那里测台账本身，这里测**会话预算**这一层：
配置驱动、价格表折算入账、耗尽标记与暂停原因、以及「剩余额度只是上限」的如实标注。
"""

from __future__ import annotations

import pytest

from vrc_agent.config import PerceptionConfig
from vrc_agent.providers.base import ModelResponse, Usage
from vrc_agent.providers.errors import ProviderError
from vrc_agent.telemetry.budget import PAUSE_REASON, SessionBudget


def response(*, cost_usd: float | None = None, prompt: int | None = 10, completion: int | None = 4):
    return ModelResponse(
        request_id="req-1",
        observation_id="obs-1",
        provider="openai_compat",
        model="vision-test-1",
        payload={},
        usage=Usage(prompt_tokens=prompt, completion_tokens=completion, cost_usd=cost_usd),
    )


def config(**changes: object) -> PerceptionConfig:
    options: dict[str, object] = {
        "provider": "openai_compat",
        "budget_usd": 1.0,
        "price_input_per_mtok_usd": 3.0,
        "price_output_per_mtok_usd": 15.0,
        "price_as_of": "2026-01-01",
    }
    options.update(changes)
    return PerceptionConfig(**options)


def test_free_provider_needs_no_budget_and_has_no_remaining() -> None:
    budget = SessionBudget.from_config(PerceptionConfig(provider="fake"))

    snapshot = budget.snapshot()

    assert budget.paid_enabled is False
    assert snapshot["budget_usd"] == 0.0
    assert snapshot["remaining_usd"] is None
    assert snapshot["exhausted"] is False
    assert snapshot["pricing"]["configured"] is False
    assert budget.check(paid=True, provider="openai_compat") is not None  # 付费调用被拒


def test_a_configured_budget_exposes_remaining_and_price_table() -> None:
    budget = SessionBudget.from_config(config())

    snapshot = budget.snapshot()

    assert budget.paid_enabled is True
    assert snapshot["remaining_usd"] == 1.0
    assert snapshot["currency"] == "USD"
    assert snapshot["pricing"] == {
        "configured": True,
        "currency": "USD",
        "as_of": "2026-01-01",
        "input_per_mtok_usd": 3.0,
        "output_per_mtok_usd": 15.0,
        "reason": None,
    }


def test_record_prices_the_call_with_the_dated_table() -> None:
    budget = SessionBudget.from_config(config())

    estimate = budget.record(response(), paid=True)
    snapshot = budget.snapshot()

    expected = (10 * 3.0 + 4 * 15.0) / 1_000_000
    assert estimate.known is True and estimate.estimated is True
    assert snapshot["spent_usd_known"] == pytest.approx(expected)
    assert snapshot["estimated_calls"] == 1
    assert snapshot["unknown_cost_responses"] == 0
    assert snapshot["remaining_usd"] == pytest.approx(1.0 - expected)
    assert snapshot["cost_complete"] is True


def test_unpriceable_paid_call_marks_remaining_as_an_upper_bound() -> None:
    """没有价格表时费用未知：付款可能已经发生，剩余额度只能算上限。"""
    budget = SessionBudget.from_config(PerceptionConfig(provider="openai_compat", budget_usd=1.0))

    estimate = budget.record(response(), paid=True)
    snapshot = budget.snapshot()

    assert estimate.known is False and estimate.amount_usd is None
    assert snapshot["unknown_cost_calls"] == 1
    assert snapshot["unknown_cost_responses"] == 1
    assert snapshot["remaining_is_upper_bound"] is True
    assert snapshot["cost_complete"] is False
    assert snapshot["remaining_usd"] == 1.0  # 已知花费仍是 0


def test_a_free_call_with_unknown_cost_is_not_billed() -> None:
    budget = SessionBudget.from_config(PerceptionConfig(provider="fake", budget_usd=0.0))

    estimate = budget.record(response(cost_usd=None), paid=False)

    assert estimate.known is False
    assert budget.snapshot()["unknown_cost_calls"] == 0
    assert budget.snapshot()["cost_complete"] is True


def test_exhausting_the_budget_marks_a_pause_reason() -> None:
    budget = SessionBudget.from_config(config(budget_usd=0.00005))

    budget.record(response(), paid=True)
    snapshot = budget.snapshot()

    assert budget.exhausted is True
    assert budget.pause_reason is not None
    assert budget.pause_reason.startswith(PAUSE_REASON)
    assert snapshot["exhausted"] is True
    assert snapshot["pause_reason"] == budget.pause_reason


def test_an_exhausted_budget_refuses_the_next_paid_call() -> None:
    budget = SessionBudget.from_config(config(budget_usd=0.00005))
    budget.record(response(), paid=True)

    assert budget.check(paid=True) is not None
    with pytest.raises(ProviderError) as info:
        budget.authorize(paid=True)

    assert info.value.failure == "BUDGET_EXCEEDED"
    assert budget.authorize(paid=False) is None  # 免费调用照常放行


def test_a_failed_paid_call_counts_as_possibly_billed() -> None:
    budget = SessionBudget.from_config(config())

    budget.record_failure(provider="openai_compat", failure="TIMEOUT", paid=True)
    snapshot = budget.snapshot()

    assert snapshot["calls"] == 1
    assert snapshot["unknown_cost_calls"] == 1
    assert snapshot["cost_complete"] is False
    assert snapshot["remaining_is_upper_bound"] is True


def test_snapshot_keeps_the_ledger_fields_alongside_the_budget_view() -> None:
    budget = SessionBudget.from_config(config())
    budget.record(response(cost_usd=0.25), paid=True)

    snapshot = budget.snapshot()

    assert snapshot["calls"] == 1
    assert snapshot["spent_usd_known"] == 0.25
    assert snapshot["by_provider"] == {"openai_compat": 1}
    assert snapshot["paid_enabled"] is True
    # 台账的 token 统计以供应商给的 `total_tokens` 为准：本条响应只有分项，没有总数，
    # 因此如实记为「token 未知」，而不是拿分项之和冒充总数（费用估算仍可用分项）。
    assert snapshot["tokens_known"] == 0
    assert snapshot["unknown_token_calls"] == 1
