"""T10：延迟与事件的纯函数汇总。

核心规则：**没有样本就是未知，不是 0**——空输入的统计量必须是 `None`，
否则报告会把「没测过」显示成「0 毫秒」。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vrc_agent.telemetry.metrics import (
    LATENCY_FIELDS,
    percentile,
    percentile_report,
    read_events,
    summarize_events,
    summarize_latencies,
)


def test_empty_samples_are_unknown_not_zero() -> None:
    summary = summarize_latencies([])

    assert list(summary) == list(LATENCY_FIELDS)
    assert summary["count"] == 0
    for field in ("min_ms", "mean_ms", "p50_ms", "p95_ms", "max_ms", "sum_ms"):
        assert summary[field] is None, field


def test_summary_of_known_samples() -> None:
    summary = summarize_latencies([5, 1, 3])

    assert summary["count"] == 3
    assert summary["min_ms"] == 1
    assert summary["max_ms"] == 5
    assert summary["mean_ms"] == 3.0
    assert summary["sum_ms"] == 9
    # 最近秩（nearest-rank）取上界：p50 的秩是 ceil(0.5*3)=2，即第 2 个样本。
    assert summary["p50_ms"] == 3
    assert summary["p95_ms"] == 5


def test_negative_samples_are_clamped_not_dropped() -> None:
    summary = summarize_latencies([-4, 10])

    assert summary["count"] == 2
    assert summary["min_ms"] == 0
    assert summary["sum_ms"] == 10


def test_percentile_boundaries() -> None:
    values = list(range(1, 11))

    assert percentile(values, 0.5) == 5
    assert percentile(values, 0.95) == 10
    assert percentile(values, 1.0) == 10
    assert percentile([], 0.5) is None
    with pytest.raises(ValueError):
        percentile(values, 0.0)


def test_percentile_report_groups_by_stage() -> None:
    report = percentile_report({"perception": [4], "planning": []})

    assert report["perception"]["p95_ms"] == 4
    assert report["planning"]["count"] == 0
    assert report["planning"]["p95_ms"] is None


def test_read_events_counts_malformed_lines_without_raising(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"event": "arm", "status": "COMPLETED"}),
                "{not json",
                json.dumps(["not", "a", "dict"]),
                "",
                json.dumps({"event": "stop", "reason_code": "MANUAL_STOP"}),
            ]
        ),
        encoding="utf-8",
    )

    records, malformed = read_events(path)

    assert [record.get("event") for record in records] == ["arm", "stop"]
    assert malformed == 2


def test_read_events_of_missing_file_is_empty_not_an_error(tmp_path: Path) -> None:
    assert read_events(tmp_path / "absent.jsonl") == ([], 0)


def test_summarize_events_counts_names_statuses_and_reasons() -> None:
    summary = summarize_events(
        [
            {"event": "arm", "status": "COMPLETED"},
            {"event": "arm", "status": "COMPLETED", "reason_code": "OK"},
            {"event": "stop", "status": "PAUSED", "reason_code": "MANUAL_STOP", "value": 3},
            {"event": None, "status": 5, "reason_code": []},
        ]
    )

    assert summary["records"] == 3
    assert summary["events"] == {"arm": 2, "stop": 1}
    assert summary["statuses"] == {"COMPLETED": 2, "PAUSED": 1}
    assert summary["reason_codes"] == {"MANUAL_STOP": 1, "OK": 1}
