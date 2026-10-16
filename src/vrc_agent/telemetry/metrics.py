"""延迟与事件的纯函数汇总。

两条硬规则贯穿本模块：

1. **没有样本就是未知，不是 0**：空输入的统计量返回 `None`，调用方因此不会把
   「没测过」显示成「耗时 0ms」。
2. 汇总不修改原始记录：报告只从事件与计时读数据，不回写、不推断缺失字段。
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

#: 报告里延迟统计的字段顺序（也是 JSON 键顺序）。
LATENCY_FIELDS: tuple[str, ...] = (
    "count",
    "min_ms",
    "mean_ms",
    "p50_ms",
    "p95_ms",
    "max_ms",
    "sum_ms",
)


def percentile(sorted_samples: Sequence[int], fraction: float) -> int | None:
    """最近秩（nearest-rank）百分位；空输入返回 `None`。"""
    if not sorted_samples:
        return None
    if not 0.0 < fraction <= 1.0:
        raise ValueError("fraction must be in (0, 1]")
    rank = max(1, math.ceil(fraction * len(sorted_samples)))
    return sorted_samples[min(rank, len(sorted_samples)) - 1]


def summarize_latencies(samples: Iterable[int]) -> dict[str, int | None]:
    """毫秒样本 → 统计量。空输入时除 `count` 外全部为 `None`（未知 ≠ 0）。"""
    values = sorted(max(0, int(sample)) for sample in samples)
    if not values:
        return dict.fromkeys(LATENCY_FIELDS, None) | {"count": 0}
    total = sum(values)
    return {
        "count": len(values),
        "min_ms": values[0],
        "mean_ms": round(total / len(values), 3),
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "max_ms": values[-1],
        "sum_ms": total,
    }


def read_events(path: Path) -> tuple[list[dict], int]:
    """读 JSONL 事件文件；返回 `(记录, 无法解析的行数)`。

    坏行只计数不抛异常：一个被截断的日志文件不应该让整份报告取不到数。缺失文件返回
    空列表（`0` 条记录），由调用方决定这算不算「没有数据」。
    """
    if not path.exists():
        return [], 0
    records: list[dict] = []
    malformed = 0
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(record, dict):
                records.append(record)
            else:
                malformed += 1
    return records, malformed


def summarize_events(records: Iterable[dict]) -> dict[str, object]:
    """事件名、状态与原因码的计数；不解读内容，只计数。"""
    events: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for record in records:
        name = record.get("event")
        if isinstance(name, str) and name:
            events[name] += 1
        status = record.get("status")
        if isinstance(status, str) and status:
            statuses[status] += 1
        reason = record.get("reason_code")
        if isinstance(reason, str) and reason:
            reasons[reason] += 1
    return {
        "records": sum(events.values()),
        "events": dict(sorted(events.items())),
        "statuses": dict(sorted(statuses.items())),
        "reason_codes": dict(sorted(reasons.items())),
    }


def percentile_report(samples: dict[str, Iterable[int]]) -> dict[str, dict[str, int | None]]:
    """按阶段名汇总延迟：`{stage: summarize_latencies(samples)}`。"""
    return {stage: summarize_latencies(values) for stage, values in samples.items()}


__all__ = [
    "LATENCY_FIELDS",
    "percentile",
    "percentile_report",
    "read_events",
    "summarize_events",
    "summarize_latencies",
]
