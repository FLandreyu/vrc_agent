"""带日期与币种的价格表，以及「未知绝不记成 0」的费用估算。

三条规则：

1. 价格表**必须带生效日期**：没有日期的价格不拿来算钱（否则一个过期的数字会被当成
   今天的价格），此时费用是 unknown 并给出原因。
2. 没有价格表、token 未知、供应商没回账单值 → 费用 **unknown**，`amount_usd is None`；
   只有确实知道是 0（例如假供应商 0 token）才写 0.0。
3. 本地按价格表算出来的数字一律标记 `estimated=True`：它是估算，不是供应商账单。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from vrc_agent.config import PerceptionConfig
from vrc_agent.contracts import Contract
from vrc_agent.providers.base import ModelResponse, Usage

#: 缺价格表时报告里使用的固定原因码（登记在 `telemetry.codes`）。
NO_PRICE_TABLE = "PRICE_UNKNOWN"
UNDATED_PRICE_TABLE = "PRICE_DATE_MISSING"
TOKENS_UNKNOWN = "TOKENS_UNKNOWN"


class PriceTable(Contract):
    """一次计价所依据的价格表：币种、生效日期、输入/输出每百万 token 单价。"""

    currency: Literal["USD"] = "USD"
    as_of: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    input_per_mtok_usd: Annotated[float, Field(ge=0.0, le=10_000.0)]
    output_per_mtok_usd: Annotated[float, Field(ge=0.0, le=10_000.0)]

    @field_validator("as_of")
    @classmethod
    def valid_date(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as exc:  # pragma: no cover - 正则已挡住大部分写法
            raise ValueError(f"as_of is not a real date: {value!r}") from exc
        return value

    def cost_of(self, prompt_tokens: int, completion_tokens: int) -> float:
        """按每百万 token 单价计算；结果四舍五入到 1e-6 美元。"""
        if prompt_tokens < 0 or completion_tokens < 0:
            raise ValueError("token counts must be >= 0")
        amount = (
            prompt_tokens * self.input_per_mtok_usd + completion_tokens * self.output_per_mtok_usd
        ) / 1_000_000
        return round(amount, 6)


class CostEstimate(Contract):
    """一次调用的费用结论。`known=False` 时 `amount_usd` 必须是 `None` 且必须有原因。"""

    known: bool
    amount_usd: Annotated[float, Field(ge=0.0)] | None = None
    currency: str | None = None
    as_of: str | None = None
    estimated: bool = False
    reason: str | None = None

    @model_validator(mode="after")
    def valid_shape(self):
        if self.known and self.amount_usd is None:
            raise ValueError("a known cost must carry amount_usd")
        if not self.known and self.amount_usd is not None:
            raise ValueError("an unknown cost must not carry amount_usd")
        if not self.known and not self.reason:
            raise ValueError("an unknown cost must explain why it is unknown")
        return self

    def as_report(self) -> dict[str, object]:
        return {
            "known": self.known,
            "amount_usd": self.amount_usd,
            "currency": self.currency,
            "as_of": self.as_of,
            "estimated": self.estimated,
            "reason": self.reason,
        }


def price_table_of(config: PerceptionConfig) -> tuple[PriceTable | None, str | None]:
    """从感知配置取出价格表；缺日期时返回 `(None, 原因)`，**拒绝用无日期的价格计价**。"""
    if config.price_input_per_mtok_usd is None or config.price_output_per_mtok_usd is None:
        return None, f"{NO_PRICE_TABLE}: no price table configured"
    if config.price_as_of is None:
        return None, f"{UNDATED_PRICE_TABLE}: price table has no as_of date"
    return (
        PriceTable(
            currency=config.price_currency,
            as_of=config.price_as_of,
            input_per_mtok_usd=config.price_input_per_mtok_usd,
            output_per_mtok_usd=config.price_output_per_mtok_usd,
        ),
        None,
    )


def estimate_cost(
    usage: Usage,
    table: PriceTable | None,
    *,
    provider: str,
    missing_reason: str | None = None,
) -> CostEstimate:
    """给一次用量定价；不清楚就返回 unknown，绝不返回 0 冒充已知。"""
    if usage.cost_usd is not None:
        return CostEstimate(
            known=True,
            amount_usd=float(usage.cost_usd),
            currency="USD",
            as_of=None if table is None else table.as_of,
            estimated=usage.cost_estimated,
        )
    if table is None:
        reason = missing_reason or f"{NO_PRICE_TABLE}: no price table for provider {provider!r}"
        return CostEstimate(known=False, reason=reason)
    if usage.prompt_tokens is None or usage.completion_tokens is None:
        return CostEstimate(
            known=False,
            currency=table.currency,
            as_of=table.as_of,
            reason=f"{TOKENS_UNKNOWN}: prompt/completion token counts missing",
        )
    return CostEstimate(
        known=True,
        amount_usd=table.cost_of(usage.prompt_tokens, usage.completion_tokens),
        currency=table.currency,
        as_of=table.as_of,
        estimated=True,
    )


def cost_of_response(
    response: ModelResponse,
    table: PriceTable | None,
    *,
    missing_reason: str | None = None,
) -> CostEstimate:
    return estimate_cost(
        response.usage,
        table,
        provider=response.provider,
        missing_reason=missing_reason,
    )


def pricing_snapshot(table: PriceTable | None, reason: str | None) -> dict[str, object]:
    """报告里的价格表视图：配置与原因都写清楚，缺什么一目了然。"""
    return {
        "configured": table is not None,
        "currency": None if table is None else table.currency,
        "as_of": None if table is None else table.as_of,
        "input_per_mtok_usd": None if table is None else table.input_per_mtok_usd,
        "output_per_mtok_usd": None if table is None else table.output_per_mtok_usd,
        "reason": reason,
    }


__all__ = [
    "NO_PRICE_TABLE",
    "TOKENS_UNKNOWN",
    "UNDATED_PRICE_TABLE",
    "CostEstimate",
    "PriceTable",
    "cost_of_response",
    "estimate_cost",
    "price_table_of",
    "pricing_snapshot",
]
