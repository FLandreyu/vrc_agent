"""T10：带日期与币种的价格表；未知费用绝不写成 0。

三条被这里锁住的规则：

1. 价格表必须带**真实生效日期**，否则拒绝计价（宁可 unknown，也不拿过期价格算钱）；
2. `CostEstimate` 的形状自洽：known 必须带金额，unknown 必须不带金额且必须说明原因；
3. 供应商账单值优先于本地估算，本地估算一律 `estimated=True`。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vrc_agent.config import PerceptionConfig
from vrc_agent.providers.base import ModelResponse, Usage
from vrc_agent.telemetry.cost import (
    NO_PRICE_TABLE,
    TOKENS_UNKNOWN,
    UNDATED_PRICE_TABLE,
    CostEstimate,
    PriceTable,
    cost_of_response,
    estimate_cost,
    price_table_of,
    pricing_snapshot,
)

TABLE = PriceTable(as_of="2026-01-01", input_per_mtok_usd=3.0, output_per_mtok_usd=15.0)


def priced_config(**changes: object) -> PerceptionConfig:
    options: dict[str, object] = {
        "provider": "openai_compat",
        "budget_usd": 5.0,
        "price_input_per_mtok_usd": 3.0,
        "price_output_per_mtok_usd": 15.0,
        "price_as_of": "2026-01-01",
    }
    options.update(changes)
    return PerceptionConfig(**options)


def response(usage: Usage) -> ModelResponse:
    return ModelResponse(
        request_id="req-1",
        observation_id="obs-1",
        provider="openai_compat",
        model="vision-test-1",
        payload={},
        usage=usage,
    )


# --------------------------------------------------------------------------- 价格表


def test_price_table_needs_a_date_shaped_value() -> None:
    with pytest.raises(ValidationError):
        PriceTable(as_of="2026-1-1", input_per_mtok_usd=1.0, output_per_mtok_usd=1.0)


def test_price_table_rejects_an_impossible_date() -> None:
    with pytest.raises(ValidationError):
        PriceTable(as_of="2026-13-40", input_per_mtok_usd=1.0, output_per_mtok_usd=1.0)


def test_cost_math_rounds_to_one_micro_dollar() -> None:
    assert TABLE.cost_of(1_000_000, 0) == 3.0
    assert TABLE.cost_of(0, 1_000_000) == 15.0
    assert TABLE.cost_of(10, 4) == 0.00009
    with pytest.raises(ValueError):
        TABLE.cost_of(-1, 0)


def test_price_table_of_reports_why_it_is_missing() -> None:
    table, reason = price_table_of(PerceptionConfig())
    assert table is None
    assert reason is not None and reason.startswith(NO_PRICE_TABLE)

    table, reason = price_table_of(priced_config())
    assert reason is None
    assert table is not None and table.as_of == "2026-01-01" and table.currency == "USD"


def test_price_table_of_refuses_an_undated_table() -> None:
    """配置层已经禁止「有价格没日期」，这里的守卫是第二道：绕开校验也拒付。"""
    undated = PerceptionConfig.model_construct(
        provider="openai_compat",
        budget_usd=5.0,
        price_input_per_mtok_usd=3.0,
        price_output_per_mtok_usd=15.0,
        price_as_of=None,
        price_currency="USD",
    )

    table, reason = price_table_of(undated)

    assert table is None
    assert reason is not None and reason.startswith(UNDATED_PRICE_TABLE)


# --------------------------------------------------------------------------- 费用结论


def test_known_cost_must_carry_an_amount() -> None:
    with pytest.raises(ValidationError):
        CostEstimate(known=True)


def test_unknown_cost_must_carry_a_reason_and_no_amount() -> None:
    with pytest.raises(ValidationError):
        CostEstimate(known=False, amount_usd=0.0, reason="x")
    with pytest.raises(ValidationError):
        CostEstimate(known=False)


def test_unknown_cost_is_never_zero() -> None:
    estimate = estimate_cost(Usage(), None, provider="openai_compat")

    assert estimate.known is False
    assert estimate.amount_usd is None
    assert estimate.as_report() == {
        "known": False,
        "amount_usd": None,
        "currency": None,
        "as_of": None,
        "estimated": False,
        "reason": estimate.reason,
    }


def test_supplier_bill_wins_over_local_estimate() -> None:
    usage = Usage(prompt_tokens=10, completion_tokens=4, cost_usd=0.5)

    estimate = estimate_cost(usage, TABLE, provider="openai_compat")

    assert estimate.known is True
    assert estimate.amount_usd == 0.5
    assert estimate.estimated is False


def test_tokens_unknown_is_reported_instead_of_guessing() -> None:
    usage = Usage(prompt_tokens=10)  # 缺 completion_tokens

    estimate = estimate_cost(usage, TABLE, provider="openai_compat")

    assert estimate.known is False
    assert estimate.amount_usd is None
    assert estimate.as_of == "2026-01-01"
    assert estimate.reason is not None and estimate.reason.startswith(TOKENS_UNKNOWN)


def test_local_estimate_is_marked_estimated() -> None:
    estimate = estimate_cost(Usage(prompt_tokens=10, completion_tokens=4), TABLE, provider="p")

    assert estimate.known is True
    assert estimate.estimated is True
    assert estimate.amount_usd == pytest.approx((10 * 3.0 + 4 * 15.0) / 1_000_000)
    assert estimate.currency == "USD"
    assert estimate.as_of == "2026-01-01"


def test_zero_cost_from_a_free_provider_is_known_zero() -> None:
    """假供应商真的免费：0.0 是已知的 0，不是「不知道」。"""
    estimate = cost_of_response(
        response(Usage(prompt_tokens=0, completion_tokens=0, cost_usd=0.0)), None
    )

    assert estimate.known is True
    assert estimate.amount_usd == 0.0


def test_pricing_snapshot_shows_the_missing_piece() -> None:
    assert pricing_snapshot(None, "PRICE_UNKNOWN: no price table configured") == {
        "configured": False,
        "currency": None,
        "as_of": None,
        "input_per_mtok_usd": None,
        "output_per_mtok_usd": None,
        "reason": "PRICE_UNKNOWN: no price table configured",
    }

    snapshot = pricing_snapshot(TABLE, None)

    assert snapshot["configured"] is True
    assert snapshot["as_of"] == "2026-01-01"
    assert snapshot["input_per_mtok_usd"] == 3.0
