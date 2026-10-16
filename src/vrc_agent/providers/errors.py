"""模型供应商的失败原因码。

与 `contracts.Reason` 刻意分开：后者描述「动作请求为什么被控制器拒绝」，这里的失败
发生在模型调用层，而且必须能区分「值得重试」与「重试也不会变好」。
"""

from __future__ import annotations

from typing import Literal

ProviderFailure = Literal[
    "NOT_CONFIGURED",
    "UNSUPPORTED",
    "CANCELLED",
    "TIMEOUT",
    "TRANSPORT_ERROR",
    "HTTP_ERROR",
    "MALFORMED_RESULT",
    "REFUSED",
    "BUDGET_EXCEEDED",
]

#: 固定顺序，便于遥测与文档引用。
PROVIDER_FAILURES: tuple[ProviderFailure, ...] = (
    "NOT_CONFIGURED",
    "UNSUPPORTED",
    "CANCELLED",
    "TIMEOUT",
    "TRANSPORT_ERROR",
    "HTTP_ERROR",
    "MALFORMED_RESULT",
    "REFUSED",
    "BUDGET_EXCEEDED",
)

#: 默认可重试的失败：网络层抖动、429 与 5xx。
#: 其余要么是确定性的（畸形结果、被拒绝、未配置），要么重试只会重复等待或重复计费
#: （CANCELLED 明确不该重试；TIMEOUT 由适配器按剩余预算显式决定）。
RETRYABLE_FAILURES: frozenset[str] = frozenset({"TRANSPORT_ERROR", "HTTP_ERROR"})


class ProviderError(RuntimeError):
    """模型调用失败的唯一通道。

    适配器只允许抛这一种「预期内」异常；其他异常（编程错误）原样向上传播，
    不做降级——降级会把 bug 伪装成「又一次模型失败」。
    """

    def __init__(
        self,
        failure: ProviderFailure,
        detail: str = "",
        *,
        retryable: bool | None = None,
        status: int | None = None,
        attempts: int = 1,
    ) -> None:
        if failure not in PROVIDER_FAILURES:
            raise ValueError(f"unknown provider failure: {failure!r}")
        super().__init__(f"{failure}: {detail}" if detail else failure)
        self.failure: ProviderFailure = failure
        self.detail = detail
        self.retryable = failure in RETRYABLE_FAILURES if retryable is None else retryable
        self.status = status
        self.attempts = attempts
