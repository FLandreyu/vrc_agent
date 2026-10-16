"""供应商适配器的公共构件：截止期、取消令牌、用量、预算台账与有限重试。

设计约束（ARCHITECTURE 第 3、4、9 节）：

- 模型调用必须可取消、必须有截止期，并且不得阻塞控制进程。
- 用量未知时一律记 `None`，**不能写成 0**；费用未知时 `cost_usd` 也是 `None`。
- 未设置付费预算时只有假供应商可用；真实供应商必须显式开启并配预算。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol, TypeVar

from pydantic import Field

from vrc_agent.clock import Clock
from vrc_agent.contracts import Contract, Identifier, NanoTime
from vrc_agent.providers.errors import ProviderError, ProviderFailure

MS = 1_000_000

T = TypeVar("T")


class CancelToken:
    """协作式取消。

    置位后新的尝试立即失败；已经在阻塞中的调用无法被抢占（UDP/TCP 读没有安全的
    跨线程中断手段），只能在它返回后立刻放手——因此每次调用都必须带超时。
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise ProviderError("CANCELLED", "cancel token is set")


@dataclass(frozen=True, slots=True)
class Deadline:
    """绝对截止期（单调时钟纳秒）。`at_ns is None` 表示不设限（仅离线假供应商用）。"""

    at_ns: int | None = None

    @classmethod
    def unlimited(cls) -> Deadline:
        return cls(None)

    @classmethod
    def from_now(cls, clock: Clock, timeout_ms: int) -> Deadline:
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        return cls(clock.now_ns() + timeout_ms * MS)

    def remaining_ms(self, now_ns: int) -> int | None:
        if self.at_ns is None:
            return None
        remaining = self.at_ns - now_ns
        return max(0, -(-remaining // MS))  # 向上取整到整毫秒

    def expired(self, now_ns: int) -> bool:
        return self.at_ns is not None and now_ns >= self.at_ns

    def raise_if_expired(self, now_ns: int) -> None:
        if self.expired(now_ns):
            raise ProviderError("TIMEOUT", "deadline exceeded")


class Usage(Contract):
    """一次调用的用量。所有未知量保持 `None`，绝不填 0 冒充已知。"""

    prompt_tokens: Annotated[int, Field(ge=0)] | None = None
    completion_tokens: Annotated[int, Field(ge=0)] | None = None
    total_tokens: Annotated[int, Field(ge=0)] | None = None
    image_count: Annotated[int, Field(ge=0)] = 0
    requests: Annotated[int, Field(ge=1)] = 1
    #: 供应商账单值或本地按价格表估算值；None 表示**费用未知**。
    cost_usd: Annotated[float, Field(ge=0)] | None = None
    #: True 表示 cost_usd 由本地价格表估算，不是供应商返回的账单值。
    cost_estimated: bool = False
    #: 本地估算所用的价格表生效日期与币种；供应商账单值不使用这两个字段。
    cost_as_of: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None
    cost_currency: Literal["USD"] | None = None

    @property
    def cost_known(self) -> bool:
        return self.cost_usd is not None

    @property
    def tokens_known(self) -> bool:
        return self.total_tokens is not None


@dataclass(frozen=True, slots=True)
class ModelInput:
    """一次模型调用的输入。

    画面只以**内存字节**形式存在（`image`），绝不携带文件路径；`image_ref` 是
    capture 的受限引用，用于把模型输出回溯到具体观测。
    """

    prompt: str
    observation_id: str
    request_id: str
    sequence: int = 0
    image: bytes | None = None
    image_media_type: str = "image/png"
    image_ref: str | None = None
    image_size: tuple[int, int] | None = None
    session_id: str = ""
    environment_epoch: int = 0
    created_at_ns: int = 0

    def __post_init__(self) -> None:
        if not self.prompt:
            raise ValueError("prompt must not be empty")
        if not self.observation_id:
            raise ValueError("observation_id must not be empty")
        if not self.request_id:
            raise ValueError("request_id must not be empty")
        if self.sequence < 0:
            raise ValueError("sequence must be >= 0")
        if self.image is not None and not self.image:
            raise ValueError("image must not be empty when provided")
        if self.image is not None and self.image_size is None:
            raise ValueError("image_size is required when an image is attached")
        if self.image is None and self.image_ref is not None:
            raise ValueError("image_ref without image bytes cannot be attached")


class ModelResponse(Contract):
    """供应商返回的**未验证**结构化输出 + 用量。payload 是不可信输入。"""

    request_id: Identifier
    observation_id: Identifier
    provider: Annotated[str, Field(min_length=1, max_length=64)]
    model: Annotated[str, Field(min_length=1, max_length=128)]
    payload: dict[str, Any]
    usage: Usage = Field(default_factory=Usage)
    latency_ms: Annotated[int, Field(ge=0)] = 0
    attempts: Annotated[int, Field(ge=1)] = 1
    finish_reason: Annotated[str, Field(max_length=64)] | None = None
    created_at_ns: NanoTime = 0


class UsageLedger:
    """会话级用量与预算台账。

    未知费用单独计数：`spent_usd_known` 只累加**已知**费用，`unknown_cost_calls`
    记录有多少次调用可能已经产生费用却无法计价。只要后者非零，`cost_complete`
    就是 False——报告里必须显式说明，不能把未知当作 0。
    """

    def __init__(self, budget_usd: float = 0.0) -> None:
        if budget_usd < 0:
            raise ValueError("budget_usd must be >= 0")
        self._budget_usd = float(budget_usd)
        self._lock = threading.Lock()
        self._calls = 0
        self._spent_usd_known = 0.0
        self._unknown_cost_calls = 0
        self._unknown_token_calls = 0
        self._tokens_known = 0
        self._images = 0
        self._by_provider: dict[str, int] = {}

    @property
    def budget_usd(self) -> float:
        return self._budget_usd

    def ensure_affordable(self, *, provider: str, paid: bool) -> None:
        """付费调用前的门禁：没有预算就不允许发付费请求。"""
        if not paid:
            return
        with self._lock:
            if self._budget_usd <= 0:
                raise ProviderError(
                    "BUDGET_EXCEEDED",
                    f"{provider} is a paid provider but no budget is configured",
                )
            if self._spent_usd_known >= self._budget_usd:
                raise ProviderError(
                    "BUDGET_EXCEEDED",
                    f"{provider} budget exhausted: {self._spent_usd_known:.6f}/{self._budget_usd:.6f} USD",
                )

    def authorize(self, *, paid: bool, provider: str | None = None) -> None:
        """`Accounting` 协议入口：与 `ensure_affordable` 同一判定。

        `SessionBudget.authorize` 会顺手记下暂停原因；适配器只依赖这个协议，
        因此「经预算包装层」与「直接挂台账」两条装配路径行为一致（复核 R3）。
        """
        self.ensure_affordable(provider=provider or "provider", paid=paid)

    def record(self, response: ModelResponse, *, paid: bool = False) -> None:
        usage = response.usage
        with self._lock:
            self._calls += 1
            self._by_provider[response.provider] = self._by_provider.get(response.provider, 0) + 1
            self._images += usage.image_count
            if usage.cost_known:
                self._spent_usd_known += float(usage.cost_usd or 0.0)
            elif paid:
                self._unknown_cost_calls += 1
            if usage.tokens_known:
                self._tokens_known += int(usage.total_tokens or 0)
            else:
                self._unknown_token_calls += 1

    def record_failure(self, *, provider: str, failure: ProviderFailure, paid: bool) -> None:
        """失败的调用也可能已经被计费（超时、连接中断），必须如实计数。"""
        with self._lock:
            self._calls += 1
            self._by_provider[provider] = self._by_provider.get(provider, 0) + 1
            if paid:
                self._unknown_cost_calls += 1
            self._unknown_token_calls += 1

    @property
    def calls(self) -> int:
        with self._lock:
            return self._calls

    @property
    def spent_usd_known(self) -> float:
        with self._lock:
            return self._spent_usd_known

    @property
    def unknown_cost_calls(self) -> int:
        with self._lock:
            return self._unknown_cost_calls

    @property
    def cost_complete(self) -> bool:
        with self._lock:
            return self._unknown_cost_calls == 0

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "calls": self._calls,
                "budget_usd": self._budget_usd,
                "spent_usd_known": round(self._spent_usd_known, 6),
                "unknown_cost_calls": self._unknown_cost_calls,
                "cost_complete": self._unknown_cost_calls == 0,
                "tokens_known": self._tokens_known,
                "unknown_token_calls": self._unknown_token_calls,
                "image_count": self._images,
                "by_provider": dict(self._by_provider),
            }


class Accounting(Protocol):
    """适配器需要的「预算闸门 + 台账入账」最小接口。

    `UsageLedger`（只统计）和 `telemetry.budget.SessionBudget`（带价格表与暂停原因）
    都满足它。适配器只依赖这个协议，装配层可以把预算包装层直接传进来（复核 R3：
    以前只把裸台账传进来，预算快照因此永远看不到授权/入账发生过）。
    """

    def authorize(self, *, paid: bool, provider: str | None = None) -> None: ...

    def record(self, response: ModelResponse, *, paid: bool | None = None) -> object: ...

    def record_failure(self, *, provider: str, failure: ProviderFailure, paid: bool) -> None: ...


async def run_attempts(
    attempt: Callable[[int], Awaitable[T]],
    *,
    max_retries: int,
    backoff_ms: int,
    deadline: Deadline,
    cancel: CancelToken,
    clock: Clock,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """有限重试：只重试标记为 retryable 的失败，且每次都要重新检查取消与截止期。"""
    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")
    last: ProviderError | None = None
    for index in range(max_retries + 1):
        cancel.raise_if_cancelled()
        deadline.raise_if_expired(clock.now_ns())
        try:
            return await attempt(index)
        except ProviderError as exc:
            last = exc
            remaining = max_retries - index
            if not exc.retryable or remaining <= 0:
                raise
        wait_ms = backoff_ms * (index + 1)
        if wait_ms and not deadline.expired(clock.now_ns() + wait_ms * MS):
            await sleep(wait_ms / 1000)
    raise last if last is not None else RuntimeError("run_attempts produced no result")
