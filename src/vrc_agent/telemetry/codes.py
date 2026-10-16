"""结构化事件与报告使用的错误码登记表。

设计约束（T10 完成条件与 ARCHITECTURE 第 10 节）：

- 错误码是**闭集**：每一层的失败原因由该层自己的 `Literal` 定义，登记表只做汇总与家族
  归类，不发明新码、不改写已有码。
- 报告里出现登记表之外的码时必须**原样保留并显式列出**，不能悄悄丢掉、也不能并进最近
  的家族——未知码往往正是新缺陷的第一个信号。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from vrc_agent.capture.errors import CAPTURE_FAILURES
from vrc_agent.contracts import REASONS
from vrc_agent.dialogue.errors import DIALOGUE_FAILURES
from vrc_agent.perception.errors import PERCEPTION_FAILURES
from vrc_agent.providers.errors import PROVIDER_FAILURES

#: 导航批次的失败分类（`navigation_sim.classify` 的输出）。
NAVIGATION_FAILURE_CLASSES: tuple[str, ...] = (
    "NONE",
    "TAKEOVER",
    "STUCK",
    "TARGET_UNOBSERVED",
    "ACTION_BUDGET",
    "MODEL_TIMEOUT",
    "OBSERVATION_INVALID",
    "AUTHORITY_LOST",
    "CONTROL_FAILED",
    "UNVERIFIED",
    "RUNTIME_FAILED",
)

#: 离线/受监管运行时自己产生的结局码（不在 `contracts.Reason` 闭集里的那些）。
RUNTIME_CODES: tuple[str, ...] = (
    "SUCCESS",
    "MODEL_LATE",
    "REPLAY_EXHAUSTED",
    "RUNTIME_ERROR",
    "UNSAFE_SCENE",
    "INVALID_SCENE_EVIDENCE",
    "MISSING_FRAME",
    "ACTION_LIMIT",
    "RECOVERY_EXHAUSTED",
    "TARGET_UNOBSERVED",
)

#: 验证结论（`ResultVerifier` 与 `NavigationSkill` 的三态）。`UNKNOWN` 表示「这一帧还看不清」，
#: 不是失败——报告必须能把它与 `FAILURE` 区分开。
VERIFICATION_CODES: tuple[str, ...] = ("SUCCESS", "FAILURE", "UNKNOWN")

#: 遥测自身使用的码：报告里必须能区分「没有数据」和「数据是 0」。
TELEMETRY_CODES: tuple[str, ...] = (
    "NO_DATA",
    "PRICE_UNKNOWN",
    "PRICE_DATE_MISSING",
    "TOKENS_UNKNOWN",
    "COST_INCOMPLETE",
)


@dataclass(frozen=True, slots=True)
class CodeSpec:
    code: str
    family: str


_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("control", REASONS),
    ("provider", PROVIDER_FAILURES),
    ("perception", PERCEPTION_FAILURES),
    ("capture", CAPTURE_FAILURES),
    ("dialogue", DIALOGUE_FAILURES),
    ("navigation", NAVIGATION_FAILURE_CLASSES),
    ("runtime", RUNTIME_CODES),
    ("verification", VERIFICATION_CODES),
    ("telemetry", TELEMETRY_CODES),
)


def _build() -> dict[str, CodeSpec]:
    registry: dict[str, CodeSpec] = {}
    for family, codes in _SOURCES:
        for code in codes:
            # 同一取值可以出现在多层的闭集里（例如 TRANSPORT_ERROR）：保留首个家族，
            # 但绝不允许同码在两处给出**不同**拼写——闭集本身保证不会。
            registry.setdefault(code, CodeSpec(code=code, family=family))
    return registry


CODE_REGISTRY: dict[str, CodeSpec] = _build()

#: 已登记的全部错误码，顺序稳定（按家族与声明顺序），供文档与报告枚举。
KNOWN_CODES: tuple[str, ...] = tuple(CODE_REGISTRY)


def registry() -> dict[str, CodeSpec]:
    return dict(CODE_REGISTRY)


def families() -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for code, spec in CODE_REGISTRY.items():
        grouped.setdefault(spec.family, []).append(code)
    return {family: tuple(codes) for family, codes in grouped.items()}


def is_known(code: str) -> bool:
    return code in CODE_REGISTRY


def family_of(code: str) -> str | None:
    """未知码返回 `None`；调用方必须把它当成未知处理，而不是归入任何家族。"""
    spec = CODE_REGISTRY.get(code)
    return None if spec is None else spec.family


def codes_of(*reports: object) -> Iterator[str]:
    """从任意嵌套结构里捞出候选错误码。

    只认字段名：`reason_code`、`failure`、`error_code`、`failure_class`、`outcome`、
    `stop_reason`、`verification`。这样报告的 JSON 形状变化时不会误伤（也绝不猜测
    字符串里的内容是不是码）。
    """
    fields = (
        "reason_code",
        "failure",
        "error_code",
        "failure_class",
        "outcome",
        "stop_reason",
        "verification",
    )
    stack = list(reports)
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key in fields and isinstance(value, str) and value:
                    yield value
                elif isinstance(value, (dict, list, tuple)):
                    stack.append(value)
        elif isinstance(item, (list, tuple)):
            stack.extend(item)


def tally(codes: Iterable[str]) -> dict[str, object]:
    """按家族与码计数；未知码单独列出，不与已知码合并。"""
    counter: Counter[str] = Counter()
    for code in codes:
        if code:
            counter[code] += 1
    by_family: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    for code, count in counter.items():
        family = family_of(code)
        if family is None:
            unknown[code] += count
        else:
            by_family[family] += count
    return {
        "total": sum(counter.values()),
        "by_code": dict(sorted(counter.items())),
        "by_family": dict(sorted(by_family.items())),
        "unknown_codes": dict(sorted(unknown.items())),
    }


__all__ = [
    "CODE_REGISTRY",
    "KNOWN_CODES",
    "NAVIGATION_FAILURE_CLASSES",
    "RUNTIME_CODES",
    "TELEMETRY_CODES",
    "VERIFICATION_CODES",
    "CodeSpec",
    "codes_of",
    "families",
    "family_of",
    "is_known",
    "registry",
    "tally",
]
