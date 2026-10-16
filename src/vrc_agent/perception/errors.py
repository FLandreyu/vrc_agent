"""感知层的失败原因。

与 `contracts.Reason`（动作为什么被拒绝）和 `providers.ProviderError`（供应商调用为什么
失败）刻意分开：这三类原因由不同层产生，混在一起会让日志和 reason_code 变得无法判断。
"""

from __future__ import annotations

from typing import Literal

PerceptionFailure = Literal[
    #: 旧观测的结果晚于新观测返回：迟到的响应必须被丢弃，不能覆盖更新的场景。
    "OUT_OF_ORDER",
    #: 响应不属于这次请求（供应商串包）：绝不能用它构建场景。
    "MISROUTED_RESPONSE",
]

PERCEPTION_FAILURES: tuple[PerceptionFailure, ...] = (
    "OUT_OF_ORDER",
    "MISROUTED_RESPONSE",
)

_FAILURE_SET = frozenset(PERCEPTION_FAILURES)


class PerceptionError(RuntimeError):
    """感知层拒绝产出场景（不是「场景未知」——未知会返回 confidence=None 的场景）。"""

    def __init__(self, failure: PerceptionFailure, detail: str = "") -> None:
        if failure not in _FAILURE_SET:
            raise ValueError(f"unknown perception failure: {failure!r}")
        self.failure: PerceptionFailure = failure
        self.detail = detail
        super().__init__(f"{failure}: {detail}" if detail else failure)


__all__ = ["PERCEPTION_FAILURES", "PerceptionError", "PerceptionFailure"]
