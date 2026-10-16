"""一份可贯穿各模块的会话报告：事件、错误码、延迟、用量/费用、预算与每小时折算。

职责边界：本模块只做**汇总与折算**，不驱动任何模块。采集/感知/规划/动作/交流各阶段的
装配留在 `cli.py`（组合根），这样遥测层不会反向依赖 CLI。

三条与 T10 完成条件直接对应的规则：

1. 报告里的数字必须有来源：延迟来自本进程计时，费用来自台账 + 带日期的价格表，事件来自
   结构化日志；没有来源的字段是 `None` 加原因，不是 0。
2. 未知错误码原样列出（`codes.unknown_codes`），并被转成报告的 `warnings`。
3. 每小时折算只在有可用时长时给出；费用不完整时折算值必须标记为不完整而不是硬算。
"""

from __future__ import annotations

import re
import time
import uuid
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .budget import SessionBudget
from .codes import tally
from .metrics import read_events, summarize_events, summarize_latencies

REPORT_SCHEMA_VERSION = 2

#: 阶段顺序固定，报告里即使某阶段没有样本也保留条目（便于对照与发现漏跑）。
STAGES: tuple[str, ...] = (
    "capture_replay",
    "perception",
    "planning",
    "actuation",
    "dialogue",
)

#: 少于这个基数的每小时折算只是比例换算，不是实测速率（报告会给出 warning）。
SHORT_BASIS_MS = 60_000

#: 计数值/金额的精确上界（2^53）；超过它的整数在折算式里既不可信也不可精确表示。
MAX_COUNT = 2**53

#: 汇总原因里允许出现的会话标签：只认契约 `new_id()` 的十六进制形状（复核第 30 节 R1c）。
#: 这是**形状限制**，不是密钥检测；它的作用是让报表层的缺陷说明只包含固定码与有界标签，
#: 不把不可信报告里的任意串（可能夹带凭据或超长载荷）回显到会被 CLI 打印的原因串里。
SESSION_LABEL = re.compile(r"[0-9a-f]{32}")

#: 每小时折算需要的台账字段（单份台账直接取这些键，汇总取同名的 `paired_*`）。
_PROJECTION_KEYS = (
    "calls",
    "tokens_known",
    "unknown_token_calls",
    "image_count",
    "spent_usd_known",
    "unknown_cost_calls",
)
_COUNT_KEYS = (
    "calls",
    "tokens_known",
    "unknown_token_calls",
    "image_count",
    "unknown_cost_calls",
)


def hourly_projection(
    elapsed_ms: int,
    *,
    calls: int,
    tokens_known: int,
    unknown_token_calls: int = 0,
    images: int = 0,
    spent_usd_known: float = 0.0,
    unknown_cost_calls: int = 0,
) -> dict[str, object]:
    """把一次会话的用量折算成每小时估计。

    - 时长为 0（或负）时一律 `None` + `NO_DATA`：没有时长就没有速率。
    - `tokens_per_hour` 只统计**已知** token；有未知 token 的调用时标记为下界。
    - `cost_per_hour_usd` 只在费用完整（没有未知计费调用）时给出，否则 `None`。
    """
    if elapsed_ms <= 0:
        return {
            "basis_ms": elapsed_ms,
            "calls_per_hour": None,
            "tokens_per_hour": None,
            "images_per_hour": None,
            "cost_per_hour_usd": None,
            "tokens_projection_complete": unknown_token_calls == 0,
            "cost_projection_complete": False,
            "reason": "NO_DATA: session duration is zero, hourly rates are undefined",
        }
    factor = 3_600_000 / elapsed_ms
    cost_complete = unknown_cost_calls == 0
    return {
        "basis_ms": elapsed_ms,
        "calls_per_hour": round(calls * factor, 3),
        "tokens_per_hour": round(tokens_known * factor, 3),
        "images_per_hour": round(images * factor, 3),
        "cost_per_hour_usd": round(spent_usd_known * factor, 6) if cost_complete else None,
        "tokens_projection_complete": unknown_token_calls == 0,
        "cost_projection_complete": cost_complete,
        "reason": None
        if cost_complete
        else "COST_INCOMPLETE: calls with unknown cost are excluded from the projection",
    }


def elapsed_ms_between(start_iso: object, end_iso: object) -> int | None:
    """两个 ISO 时间戳之间的毫秒数；任一不可解析时返回 `None`（不是 0）。

    只接受**带时区**的时间戳：一份报告里混入无时区的本地时间戳时，差值没有意义，
    宁可当成「没有可用时长」（复核 R5：旧实现在这里抛 `TypeError` 打断整份汇总）。
    """
    if not isinstance(start_iso, str) or not isinstance(end_iso, str):
        return None
    try:
        started = datetime.fromisoformat(start_iso)
        finished = datetime.fromisoformat(end_iso)
    except ValueError:
        return None
    if started.tzinfo is None or finished.tzinfo is None:
        return None
    try:
        return max(0, int((finished - started).total_seconds() * 1000))
    except TypeError:  # 理论上两条都带时区，这里只是不让差值异常打断汇总
        return None


def no_data_hourly(
    basis_ms: int, reason: str, *, coverage: dict[str, object] | None = None
) -> dict[str, object]:
    """「有记录但没有可用台账/时长」的每小时视图：所有费率都是 `None` + 原因。

    `basis_ms` 保留真实会话时长，方便人看出「测了多久但没数据」，但绝不用 0 冒充
    费率（复核 R4：没有预算/台账的会话曾折算出「完整」的零费率）。`coverage` 与
    正常折算保持同一形状，让「没有数据」也能说清被排除了几份来源。
    """
    projection: dict[str, object] = {
        "basis_ms": max(0, int(basis_ms)),
        "calls_per_hour": None,
        "tokens_per_hour": None,
        "images_per_hour": None,
        "cost_per_hour_usd": None,
        "tokens_projection_complete": False,
        "cost_projection_complete": False,
        "reason": reason,
    }
    if coverage is not None:
        projection["coverage"] = coverage
    return projection


def hourly_from_usage(elapsed_ms: int | None, usage: dict | None) -> dict[str, object]:
    """从台账快照折算每小时用量；没有可用时长或没有台账时按「没有数据」返回。

    四条「不以 0 替代未知」的边界：

    - 完全没有台账（`usage` 为空）或一份可用台账都没有时，说明没有数据，而不是
      「用量为 0」——按没有数据返回（`NO_DATA`）；
    - 汇总时**分子与分母同源**：折算基准取「携带有效台账的那几份报告」的时长合计
      （`basis_ms_with_usage`），不再拿全部报告的时长去除部分报告的用量（复核 R5）；
    - 合并结果里 `cost_complete=False`（有未知计费调用）时，每小时费用是 `None`，
      不能把已知部分当成总费用；token 同理按 `tokens_complete` 标记下界；
    - 覆盖不完整时显式给出 `coverage`，让人看到「这份折算覆盖了几份报告」。
    """
    if not usage:
        return no_data_hourly(0, "NO_DATA: no usage ledger was recorded")
    single = "reports_with_usage" not in usage
    covered = int(usage.get("reports_with_usage") or 0)
    missing = int(usage.get("reports_without_usage") or 0)
    # 汇总层的坏样本有两种来源：台账数值非法（聚合时判定）与整份文件被拒（文件级校验）。
    invalid_usage = int(usage.get("reports_invalid_usage") or 0)
    invalid_files = int(usage.get("reports_invalid_files") or 0)
    invalid = invalid_usage + invalid_files
    if single:
        # 单份台账：它自己就是「同源」的那一份，时长必须来自这份会话的计时。
        paired = 1
        unpaired = 0
        basis_ms = int(elapsed_ms or 0)
        numbers = {key: usage.get(key) for key in _PROJECTION_KEYS}
    else:
        # 汇总：只用「台账合法且时长可测」的那一组（paired_*），分子分母同源（复核 R5a）。
        paired = int(usage.get("reports_paired") or 0)
        unpaired = int(usage.get("reports_unpaired_duration") or 0)
        basis_ms = int(usage.get("paired_basis_ms") or usage.get("basis_ms_with_usage") or 0)
        numbers = {key: usage.get(f"paired_{key}", usage.get(key)) for key in _PROJECTION_KEYS}
    coverage = {
        "source": "single_session" if single else "aggregated_reports",
        "reports_with_usage": covered,
        "reports_without_usage": missing,
        "reports_invalid_usage": invalid_usage,
        "reports_invalid_files": invalid_files,
        "reports_paired": paired,
        "reports_unpaired_duration": unpaired,
        "basis_ms_with_usage": basis_ms,
        "session_elapsed_ms": elapsed_ms,
        "complete": single or (missing == 0 and invalid == 0 and unpaired == 0),
    }
    if not single and paired == 0:
        return no_data_hourly(
            basis_ms,
            "NO_DATA: no report carried a usable usage ledger together with a measurable"
            " session duration"
            + (
                f" ({invalid} invalid, {missing} missing, {unpaired} without duration)"
                if invalid or missing or unpaired
                else ""
            ),
            coverage=coverage,
        )
    if basis_ms <= 0:
        return no_data_hourly(
            basis_ms,
            "NO_DATA: reports carrying usage have no measurable session duration",
            coverage=coverage,
        )
    counts = {key: _nonneg_int(numbers.get(key)) for key in _COUNT_KEYS}
    spent = _nonneg_usd(numbers.get("spent_usd_known"))
    missing_keys = sorted(key for key, value in counts.items() if value is None)
    if spent is None:
        missing_keys.append("spent_usd_known")
    if missing_keys:
        return no_data_hourly(
            basis_ms,
            "NO_DATA: usage ledger is missing usable " + ", ".join(missing_keys),
            coverage=coverage,
        )
    tokens_complete = bool(
        usage.get("tokens_complete", not int(counts["unknown_token_calls"] or 0))
    )
    cost_complete = bool(usage.get("cost_complete", not int(counts["unknown_cost_calls"] or 0)))
    projection = hourly_projection(
        basis_ms,
        calls=counts["calls"],
        tokens_known=counts["tokens_known"],
        unknown_token_calls=counts["unknown_token_calls"],
        images=counts["image_count"],
        spent_usd_known=spent,
        unknown_cost_calls=counts["unknown_cost_calls"],
    )
    projection["tokens_projection_complete"] = tokens_complete
    if not cost_complete and projection["cost_per_hour_usd"] is not None:
        projection["cost_per_hour_usd"] = None
        projection["cost_projection_complete"] = False
        projection["reason"] = (
            "COST_INCOMPLETE: calls with unknown cost are excluded from the projection"
        )
    projection["coverage"] = coverage
    return projection


def _nonneg_int(value: object) -> int | None:
    """严格取非负整数：布尔、缺失、负数、非数值、超出精确范围的巨数一律 `None`。

    上界与 `telemetry.MAX_INT_VALUE` 对齐：`10**400` 这种整数在 JSON 往返与倍率换算里
    都没有意义，而且旧实现的 `float()` 会在文件边界外抛 `OverflowError`（复核第 28 节 R5b）。
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    if value > MAX_COUNT:
        return None
    return value


def _nonneg_usd(value: object) -> float | None:
    """严格取非负有限金额：布尔、非数值、NaN/Inf、负数、超范围巨数一律 `None`。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int) and value > MAX_COUNT:
        return None
    try:
        amount = float(value)
    except OverflowError:  # 巨型整数：当成损坏值，不能让异常打断整批汇总
        return None
    if amount != amount or amount in (float("inf"), float("-inf")) or amount < 0:
        return None
    return amount


def aggregate_usage(reports: Iterable[dict]) -> dict[str, object]:
    """把若干份报告里的 `usage` 台账快照合并成一份（逐份严格校验，复核 R4/R5）。

    四条不能被「看起来是全绿」掩盖的规则：

    1. `usage` 缺失的报告计入 `reports_without_usage`，`{}` 这种空快照既不能当合法零台账，
       也不能被静默当成 0 美元：它会让合并结果**不完整**；
    2. 任何一份台账里出现非法数值（负数/布尔/字符串/NaN/巨型数、by_provider 非映射、缺
       计数字段）时，这份报告整份计入 `reports_invalid_usage` 并丢弃，不参与分子；
    3. `cost_complete`/`tokens_complete` 只有在「每一份都是合法台账」「没有未知计费/未知
       token 调用」「每份都自带完整的完整性标志」时才为 True —— 来源说 `cost_complete=False`
       时合并结果不能把它升级成 True（复核第 28 节 R4）；
    4. **分子与分母同源（R5a）**：每小时折算只使用 `paired_*` 一组数——既携带合法台账、
       又有可测正时长的那些报告。时长缺失或非法的报告只进 `reports_unpaired_duration`，
       既不进分子也不进分母；旧实现拿「全部有时长的报告」做分母、「全部有台账的报告」做
       分子，2 次调用 / 60 秒曾被算成 120 次/小时。
    """
    calls = tokens = unknown_cost = unknown_tokens = images = 0
    spent = 0.0
    by_provider: Counter[str] = Counter()
    without = 0
    with_usage = 0
    invalid = 0
    invalid_reasons: list[str] = []
    basis_ms_with_usage = 0
    paired = 0
    paired_calls = paired_tokens = paired_unknown_tokens = paired_unknown_cost = 0
    paired_images = 0
    paired_spent = 0.0
    unpaired = 0
    sources_cost_complete = True
    sources_tokens_complete = True
    for report in reports:
        if not isinstance(report, dict):
            without += 1
            continue
        usage = report.get("usage")
        # 报告文件是不可信输入，原因串不能回显任意内容（复核第 30 节 R1c 的「固定码」原则
        # 同样适用于报表层）：只保留契约 `new_id()` 的十六进制会话标识，其余写 `unknown`。
        raw_id = report.get("session_id")
        session_id = (
            raw_id if isinstance(raw_id, str) and SESSION_LABEL.fullmatch(raw_id) else "unknown"
        )
        if not isinstance(usage, dict) or not usage:
            without += 1
            continue
        reason = usage_defect(usage)
        if reason is not None:
            invalid += 1
            if len(invalid_reasons) < 20:
                invalid_reasons.append(f"{session_id}: {reason}")
            continue
        with_usage += 1
        # 已通过 `usage_defect`，所有计数字段与金额字段必然存在且合法（复核 R4c：这里
        # 不再写 `... or 0`/`... or 0.0`——那正是把「缺失」折成「已知零」的写法）。
        this_calls = _nonneg_int(usage["calls"])
        this_tokens = _nonneg_int(usage["tokens_known"])
        this_unknown_cost = _nonneg_int(usage["unknown_cost_calls"])
        this_unknown_tokens = _nonneg_int(usage["unknown_token_calls"])
        this_images = _nonneg_int(usage["image_count"])
        this_spent = _nonneg_usd(usage["spent_usd_known"])
        calls += this_calls if this_calls is not None else 0
        tokens += this_tokens if this_tokens is not None else 0
        unknown_cost += this_unknown_cost if this_unknown_cost is not None else 0
        unknown_tokens += this_unknown_tokens if this_unknown_tokens is not None else 0
        images += this_images if this_images is not None else 0
        spent += this_spent if this_spent is not None else 0.0
        for name, count in (usage.get("by_provider") or {}).items():
            by_provider[str(name)] += _nonneg_int(count) or 0
        # 来源自带的完整性标志必须被保留：合并只能把它们「与」起来，不能升级成完整。
        sources_cost_complete = sources_cost_complete and usage.get("cost_complete") is True
        sources_tokens_complete = sources_tokens_complete and bool(
            usage.get("tokens_complete", this_unknown_tokens == 0)
        )
        elapsed = elapsed_ms_between(report.get("started_at"), report.get("finished_at"))
        if elapsed is None or elapsed <= 0:
            unpaired += 1
            continue
        basis_ms_with_usage += elapsed
        paired += 1
        paired_calls += this_calls
        paired_tokens += this_tokens
        paired_unknown_tokens += this_unknown_tokens
        paired_unknown_cost += this_unknown_cost
        paired_images += this_images
        paired_spent += this_spent
    complete = with_usage > 0 and without == 0 and invalid == 0 and unpaired == 0
    return {
        "calls": calls,
        "spent_usd_known": round(spent, 6),
        "unknown_cost_calls": unknown_cost,
        "cost_complete": unknown_cost == 0 and complete and sources_cost_complete,
        "tokens_known": tokens,
        "unknown_token_calls": unknown_tokens,
        "tokens_complete": unknown_tokens == 0 and complete and sources_tokens_complete,
        "image_count": images,
        "by_provider": dict(sorted(by_provider.items())),
        "reports_with_usage": with_usage,
        "reports_without_usage": without,
        "reports_invalid_usage": invalid,
        "invalid_usage_reasons": invalid_reasons,
        "basis_ms_with_usage": basis_ms_with_usage,
        # 分子/分母同源集合（R5a）：下面的 `paired_*` 才是折算用的数。
        "reports_paired": paired,
        "reports_unpaired_duration": unpaired,
        "paired_basis_ms": basis_ms_with_usage,
        "paired_calls": paired_calls,
        "paired_tokens_known": paired_tokens,
        "paired_unknown_token_calls": paired_unknown_tokens,
        "paired_unknown_cost_calls": paired_unknown_cost,
        "paired_image_count": paired_images,
        "paired_spent_usd_known": round(paired_spent, 6),
        "complete": complete,
    }


def usage_defect(usage: dict) -> str | None:
    """检查一份台账快照是否可用；返回缺陷说明或 `None`（合法）。

    只校验本版本会写出的字段形状：负数/布尔/字符串/NaN 计数值，以及
    `by_provider`（供应商 → 调用次数）不是「非负整数映射」的情况都算损坏。

    本版本的 `UsageLedger.snapshot()`/`SessionBudget.snapshot()` 一定会写出调用数、
    token 数、未知计费/未知 token 调用数、图片数、已知费用与 `cost_complete`；**缺任何一个
    都不是「用量为 0」**（复核 R4：没有调用数曾被 `or 0` 折成「0 次调用、0 张图片」的完整
    台账；复核第 30 节 R4c：缺失的已知费用曾被折成 `0.0` 美元并宣称费用完整），因此缺字段
    同样算损坏。`unknown_cost_calls` 缺省时也无法判断费用是否完整——这正是把未知折成已知零
    的入口，所以同样要求必须存在。合法的**零金额**台账（`spent_usd_known=0.0` 且
    `cost_complete=true`）仍然有效：本函数拒绝的是「缺字段」，不是「值为零」。
    """
    for key in (
        "calls",
        "tokens_known",
        "unknown_cost_calls",
        "unknown_token_calls",
        "image_count",
    ):
        if key in usage and _nonneg_int(usage.get(key)) is None:
            return f"usage.{key} is not a non-negative integer"
    if "spent_usd_known" in usage and _nonneg_usd(usage.get("spent_usd_known")) is None:
        return "usage.spent_usd_known is not a non-negative finite amount"
    for key in ("cost_complete", "tokens_complete"):
        if key in usage and not isinstance(usage.get(key), bool):
            return f"usage.{key} is not a boolean"
    for key in (
        "calls",
        "image_count",
        "tokens_known",
        "unknown_cost_calls",
        "unknown_token_calls",
        "spent_usd_known",
        "cost_complete",
    ):
        if key not in usage:
            return f"usage.{key} is missing"
    by_provider = usage.get("by_provider")
    if by_provider is not None:
        if not isinstance(by_provider, dict):
            return "usage.by_provider is not a mapping"
        for index, (name, count) in enumerate(by_provider.items()):
            if not isinstance(name, str) or not name:
                return "usage.by_provider has an invalid provider name"
            if _nonneg_int(count) is None:
                # 不回显供应商名：它来自不可信报告文件，可能夹带任意串（复核第 30 节 R1c
                # 的「原因只允许固定码」原则同样适用于报表层），所以只写序号。
                return f"usage.by_provider[{index}] is not a non-negative integer"
    return None


class SessionRecorder:
    """会话期间收集阶段计时、错误码与事件；`build()` 产出一份自包含报告。"""

    def __init__(
        self,
        *,
        session_id: str | None = None,
        mode: str = "offline",
        budget: SessionBudget | None = None,
        log_path: Path | None = None,
        clock=time.monotonic_ns,
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex
        self.mode = mode
        self.budget = budget
        self.log_path = None if log_path is None else Path(log_path)
        self._clock = clock
        self._started_ns = clock()
        self._samples: dict[str, list[int]] = {stage: [] for stage in STAGES}
        self._successes: Counter[str] = Counter()
        self._failures: Counter[str] = Counter()
        self._codes: list[str] = []
        self._events: Counter[str] = Counter()
        self._counters: Counter[str] = Counter()

    # ------------------------------------------------------------ 记录

    def stage_ms(self, name: str, duration_ms: int, *, ok: bool = True, code: str | None = None):
        """登记一次阶段耗时（毫秒）及其结果。阶段名允许超出固定集合（新增模块时）。"""
        self._samples.setdefault(name, []).append(max(0, int(duration_ms)))
        if ok:
            self._successes[name] += 1
        else:
            self._failures[name] += 1
        if code:
            self.note_code(code)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """计时一个阶段；抛异常时记为失败并把异常类型登记成码（未知码会被标出）。"""
        started = self._clock()
        try:
            yield
        except Exception as exc:
            self.stage_ms(
                name, (self._clock() - started) // 1_000_000, ok=False, code=type(exc).__name__
            )
            raise
        else:
            self.stage_ms(name, (self._clock() - started) // 1_000_000, ok=True)

    def note_code(self, code: str | None) -> None:
        if code:
            self._codes.append(str(code))

    def note_codes(self, codes: Iterable[str]) -> None:
        for code in codes:
            self.note_code(code)

    def note_event(self, name: str, *, count: int = 1) -> None:
        self._events[name] += count

    def count(self, name: str, amount: int = 1) -> None:
        self._counters[name] += amount

    # ------------------------------------------------------------ 产出

    def build(self, *, extra: dict | None = None, starting: int | None = None) -> dict[str, object]:
        elapsed_ms = max(0, (self._clock() - self._started_ns) // 1_000_000)
        stages = {
            name: {
                "samples": len(self._samples.get(name, ())),
                "successes": self._successes.get(name, 0),
                "failures": self._failures.get(name, 0),
                "latency": summarize_latencies(self._samples.get(name, ())),
            }
            for name in dict.fromkeys((*STAGES, *self._samples))
        }
        codes = tally(self._codes)
        events: dict[str, object] = {
            "records": sum(self._events.values()),
            "events": dict(sorted(self._events.items())),
        }
        log_view: dict[str, object] = {
            "path": None if self.log_path is None else str(self.log_path),
            "records": 0,
            "malformed_lines": 0,
        }
        if self.log_path is not None:
            records, malformed = read_events(self.log_path)
            summary = summarize_events(records)
            log_view = {**log_view, **summary, "malformed_lines": malformed}
            events = {"records": summary["records"], "events": summary["events"]}
        usage = None if self.budget is None else self.budget.snapshot()
        report: dict[str, object] = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "session_id": self.session_id,
            "mode": self.mode,
            "generated_at": datetime.now(UTC).isoformat(),
            "elapsed_ms": elapsed_ms,
            "stages": stages,
            "codes": codes,
            "events": events,
            "event_log": log_view,
            "counters": dict(sorted(self._counters.items())),
        }
        if starting is not None:
            report["starting_counters"] = starting
        report["usage"] = usage
        if usage is None:
            # 没有预算对象就没有台账：这段会话的用量是**未知**，不是 0。只给出时长与原因
            # （复核 R4：旧实现生成「完整」的零费率，看起来像测过且没花钱）。
            report["hourly"] = no_data_hourly(
                elapsed_ms, "NO_DATA: this session has no usage ledger (budget not configured)"
            )
        else:
            # 有台账对象不等于台账可用（复核第 28 节 R4b）：损坏的快照同样只能报 NO_DATA，
            # 不能让 `or 0` 把它折成「零用量、零费用」的完整折算。
            defect = usage_defect(usage) if isinstance(usage, dict) else "usage is not a mapping"
            report["hourly"] = (
                no_data_hourly(elapsed_ms, f"NO_DATA: usage ledger is unusable ({defect})")
                if defect is not None
                else hourly_from_usage(elapsed_ms, usage)
            )
        if extra:
            report.update(extra)
        report["warnings"] = self._warnings(report)
        return report

    @staticmethod
    def _warnings(report: dict) -> list[str]:
        warnings: list[str] = []
        codes = report.get("codes") or {}
        unknown = codes.get("unknown_codes") or {}
        if unknown:
            warnings.append(
                "unknown error codes present: " + ", ".join(f"{k}×{v}" for k, v in unknown.items())
            )
        usage = report.get("usage")
        if usage is None:
            warnings.append(
                "no usage ledger was recorded: cost and token usage are unknown, not zero"
            )
        elif isinstance(usage, dict):
            defect = usage_defect(usage)
            if defect is not None:
                warnings.append(
                    f"usage ledger is unusable ({defect}): cost and token usage are unknown"
                )
            elif int(usage.get("unknown_cost_calls") or 0) > 0:
                warnings.append(
                    "cost is incomplete: "
                    f"{usage['unknown_cost_calls']} call(s) may have been billed but cannot be priced"
                )
            if defect is None and int(usage.get("unknown_token_calls") or 0) > 0:
                warnings.append(f"token usage unknown for {usage['unknown_token_calls']} call(s)")
        stages = report.get("stages") or {}
        empty = [
            name
            for name, entry in stages.items()
            if isinstance(entry, dict) and entry.get("samples") == 0
        ]
        if empty:
            warnings.append("no samples recorded for stages: " + ", ".join(empty))
        log = report.get("event_log") or {}
        if log.get("path") is None:
            warnings.append("no structured event log was recorded for this session")
        elif log.get("malformed_lines"):
            warnings.append(f"{log['malformed_lines']} event log line(s) could not be parsed")
        hourly = report.get("hourly") or {}
        basis = hourly.get("basis_ms")
        if isinstance(basis, int) and 0 < basis < SHORT_BASIS_MS:
            warnings.append(
                f"hourly projection scaled from only {basis} ms of runtime; "
                "treat it as a scale factor, not a measured rate"
            )
        return warnings


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "STAGES",
    "SessionRecorder",
    "aggregate_usage",
    "elapsed_ms_between",
    "hourly_from_usage",
    "hourly_projection",
    "no_data_hourly",
    "usage_defect",
]
