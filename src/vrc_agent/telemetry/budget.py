"""会话预算：调用前门禁、调用后计价入账、耗尽即暂停。

与 `UsageLedger` 的分工：台账只统计「已经发生了什么」，本类负责

- **调用前**判定这次付费调用还允不允许（`authorize`/`check`）；
- **调用后**把回来的用量按带日期的价格表折算成费用再入账（估计值标记 `estimated`）；
- 预算耗尽时给出明确的 `pause_reason`，控制层据此停在 PAUSED，而不是继续试下一次。

未知费用永远单独计数：只要有一次调用可能已经产生费用却无法计价，`remaining_usd` 就只是
上限（`remaining_is_upper_bound=True`），报告里必须照实说明。
"""

from __future__ import annotations

from vrc_agent.config import PerceptionConfig
from vrc_agent.providers.base import ModelResponse, UsageLedger
from vrc_agent.providers.errors import ProviderError, ProviderFailure

from .cost import CostEstimate, PriceTable, cost_of_response, price_table_of, pricing_snapshot

#: 预算耗尽时控制层使用的暂停原因（`contracts.Reason` 的取值）。
PAUSE_REASON = "BUDGET_EXCEEDED"


class SessionBudget:
    def __init__(
        self,
        *,
        budget_usd: float = 0.0,
        currency: str = "USD",
        ledger: UsageLedger | None = None,
        table: PriceTable | None = None,
        table_reason: str | None = None,
        provider: str = "",
    ) -> None:
        self._ledger = UsageLedger(budget_usd=budget_usd) if ledger is None else ledger
        self._table = table
        self._table_reason = table_reason
        self._provider = provider
        self._currency = currency
        self._exhausted = False
        self._pause_reason: str | None = None
        self._estimated_calls = 0
        self._unknown_calls = 0

    @classmethod
    def from_config(cls, config: PerceptionConfig) -> SessionBudget:
        table, reason = price_table_of(config)
        return cls(
            budget_usd=config.budget_usd,
            currency=config.price_currency,
            table=table,
            table_reason=reason,
            provider=config.provider,
        )

    # ------------------------------------------------------------ 只读视图

    @property
    def ledger(self) -> UsageLedger:
        return self._ledger

    @property
    def paid_enabled(self) -> bool:
        """预算 > 0 才算启用付费；0 表示「只允许假供应商」，不是「余额 0」。"""
        return self._ledger.budget_usd > 0

    @property
    def exhausted(self) -> bool:
        return self._exhausted

    @property
    def pause_reason(self) -> str | None:
        return self._pause_reason

    @property
    def table(self) -> PriceTable | None:
        return self._table

    # ------------------------------------------------------------ 门禁

    def check(self, *, paid: bool, provider: str | None = None) -> str | None:
        """只判断、不抛异常：返回应当暂停的原因，`None` 表示可以继续。"""
        if not paid:
            return None
        name = provider or self._provider or "provider"
        try:
            self._ledger.ensure_affordable(provider=name, paid=True)
        except ProviderError as exc:
            if exc.failure == "BUDGET_EXCEEDED":
                self._mark_exhausted(str(exc))
                return self._pause_reason
            raise
        return None

    def authorize(self, *, paid: bool, provider: str | None = None) -> None:
        """付费调用前门禁：不允许就抛 `BUDGET_EXCEEDED`，并先记下暂停原因。"""
        reason = self.check(paid=paid, provider=provider)
        if reason is not None:
            raise ProviderError("BUDGET_EXCEEDED", reason)

    # ------------------------------------------------------------ 入账

    def record(self, response: ModelResponse, *, paid: bool | None = None) -> CostEstimate:
        """计价并写入台账；返回本次的费用结论（未知时 `known=False`）。"""
        is_paid = self.paid_enabled if paid is None else paid
        cost = cost_of_response(response, self._table, missing_reason=self._table_reason)
        usage = response.usage
        if not usage.cost_known and cost.known:
            usage = usage.model_copy(
                update={"cost_usd": cost.amount_usd, "cost_estimated": cost.estimated}
            )
        self._ledger.record(response.model_copy(update={"usage": usage}), paid=is_paid)
        if cost.known and cost.estimated:
            self._estimated_calls += 1
        elif not cost.known:
            self._unknown_calls += 1
        if self.paid_enabled and self._ledger.spent_usd_known >= self._ledger.budget_usd:
            self._mark_exhausted(
                f"{response.provider} budget exhausted: "
                f"{self._ledger.spent_usd_known:.6f}/{self._ledger.budget_usd:.6f} {self._currency}"
            )
        return cost

    def record_failure(self, *, provider: str, failure: ProviderFailure, paid: bool) -> None:
        """失败的调用可能已经被计费：交给台账按未知费用计数。"""
        self._ledger.record_failure(provider=provider, failure=failure, paid=paid)

    # ------------------------------------------------------------ 报告

    def snapshot(self) -> dict[str, object]:
        ledger = self._ledger.snapshot()
        budget = float(ledger["budget_usd"])
        remaining = None
        if self.paid_enabled:
            remaining = round(max(0.0, budget - float(ledger["spent_usd_known"])), 6)
        return {
            **ledger,
            "currency": self._currency,
            "paid_enabled": self.paid_enabled,
            "remaining_usd": remaining,
            # 有未知费用时，剩余额度只能算上限：可能已经花掉一部分却没人知道。
            "remaining_is_upper_bound": int(ledger["unknown_cost_calls"]) > 0,
            "exhausted": self._exhausted,
            "pause_reason": self._pause_reason,
            "estimated_calls": self._estimated_calls,
            "unknown_cost_responses": self._unknown_calls,
            "pricing": pricing_snapshot(self._table, self._table_reason),
        }

    # ------------------------------------------------------------ 内部

    def _mark_exhausted(self, detail: str) -> None:
        self._exhausted = True
        if self._pause_reason is None:
            self._pause_reason = f"{PAUSE_REASON}: {detail}"


__all__ = ["PAUSE_REASON", "SessionBudget"]
