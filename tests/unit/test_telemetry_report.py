"""T10：会话报告的汇总、每小时折算与 warnings。

被这里锁住的边界：

- 时长为 0 就没有速率（`None` + `NO_DATA`），不是 0/小时；
- 费用不完整时每小时费用是 `None` + `COST_INCOMPLETE`；
- 未知码、短基数折算、空阶段、坏日志行都必须出现在 `warnings` 里。
"""

from __future__ import annotations

from pathlib import Path

from vrc_agent.config import PerceptionConfig
from vrc_agent.telemetry import EventLog
from vrc_agent.telemetry.budget import SessionBudget
from vrc_agent.telemetry.report import (
    SHORT_BASIS_MS,
    STAGES,
    SessionRecorder,
    aggregate_usage,
    elapsed_ms_between,
    hourly_from_usage,
    hourly_projection,
)


def test_no_elapsed_time_means_no_rate() -> None:
    projection = hourly_projection(0, calls=3, tokens_known=100)

    assert projection["calls_per_hour"] is None
    assert projection["tokens_per_hour"] is None
    assert projection["cost_per_hour_usd"] is None
    assert projection["basis_ms"] == 0
    assert projection["reason"] == "NO_DATA: session duration is zero, hourly rates are undefined"


def test_a_full_hour_scales_one_to_one() -> None:
    projection = hourly_projection(
        3_600_000, calls=12, tokens_known=600, images=4, spent_usd_known=0.5
    )

    assert projection["calls_per_hour"] == 12.0
    assert projection["tokens_per_hour"] == 600.0
    assert projection["images_per_hour"] == 4.0
    assert projection["cost_per_hour_usd"] == 0.5
    assert projection["reason"] is None


def test_unknown_usage_marks_the_projection_incomplete() -> None:
    tokens = hourly_projection(3_600_000, calls=10, tokens_known=100, unknown_token_calls=2)
    cost = hourly_projection(
        3_600_000, calls=10, tokens_known=100, spent_usd_known=1.0, unknown_cost_calls=1
    )

    assert tokens["tokens_per_hour"] == 100.0
    assert tokens["tokens_projection_complete"] is False
    assert cost["cost_per_hour_usd"] is None
    assert cost["cost_projection_complete"] is False
    assert cost["reason"] is not None and cost["reason"].startswith("COST_INCOMPLETE")


def test_elapsed_ms_between_iso_timestamps() -> None:
    assert (
        elapsed_ms_between("2026-10-15T10:00:00+00:00", "2026-10-15T10:00:03.500000+00:00") == 3500
    )
    assert elapsed_ms_between("2026-10-15T10:00:05+00:00", "2026-10-15T10:00:00+00:00") == 0
    assert elapsed_ms_between("not-a-time", "2026-10-15T10:00:00+00:00") is None
    assert elapsed_ms_between(None, None) is None


def test_hourly_from_usage_uses_the_ledger_snapshot() -> None:
    usage = {
        "calls": 6,
        "tokens_known": 600,
        "unknown_token_calls": 0,
        "image_count": 6,
        "spent_usd_known": 0.6,
        "unknown_cost_calls": 0,
    }

    projection = hourly_from_usage(1_800_000, usage)

    assert projection["calls_per_hour"] == 12.0
    assert projection["cost_per_hour_usd"] == 1.2


def test_hourly_stays_unknown_when_no_report_carried_usage() -> None:
    merged = aggregate_usage([{"outcome": "SUCCESS"}, {"outcome": "FAILURE"}])

    projection = hourly_from_usage(3_600_000, merged)

    assert projection["calls_per_hour"] is None
    assert projection["cost_per_hour_usd"] is None
    assert str(projection["reason"]).startswith("NO_DATA")


def test_hourly_refuses_to_price_an_incomplete_bill() -> None:
    merged = aggregate_usage(
        [
            {
                "started_at": "2026-10-15T10:00:00+00:00",
                "finished_at": "2026-10-15T10:30:00+00:00",
                "usage": {
                    "calls": 2,
                    "tokens_known": 20,
                    "spent_usd_known": 0.2,
                    "unknown_cost_calls": 0,
                    "unknown_token_calls": 0,
                    "image_count": 0,
                    "cost_complete": True,
                    "tokens_complete": True,
                },
            },
            {
                "started_at": "2026-10-15T11:00:00+00:00",
                "finished_at": "2026-10-15T11:30:00+00:00",
                "usage": {
                    "calls": 1,
                    "tokens_known": 0,
                    "spent_usd_known": 0.0,
                    "unknown_cost_calls": 1,
                    "unknown_token_calls": 0,
                    "image_count": 0,
                    "cost_complete": False,
                    "tokens_complete": True,
                },
            },
        ]
    )

    projection = hourly_from_usage(3_600_000, merged)

    assert merged["cost_complete"] is False
    assert merged["basis_ms_with_usage"] == 3_600_000
    assert projection["calls_per_hour"] == 3.0
    assert projection["cost_per_hour_usd"] is None
    assert projection["cost_projection_complete"] is False
    assert str(projection["reason"]).startswith("COST_INCOMPLETE")


def test_aggregate_usage_merges_snapshots_and_flags_incomplete_cost() -> None:
    reports = [
        {
            "started_at": "2026-10-15T10:00:00+00:00",
            "finished_at": "2026-10-15T10:30:00+00:00",
            "usage": {
                "calls": 2,
                "tokens_known": 200,
                "unknown_token_calls": 0,
                "image_count": 2,
                "spent_usd_known": 0.2,
                "unknown_cost_calls": 0,
                "cost_complete": True,
                "tokens_complete": True,
            },
        },
        {
            "started_at": "2026-10-15T11:00:00+00:00",
            "finished_at": "2026-10-15T11:30:00+00:00",
            "usage": {
                "calls": 3,
                "tokens_known": 0,
                "unknown_token_calls": 1,
                "image_count": 3,
                "spent_usd_known": 0.3,
                "unknown_cost_calls": 1,
                "cost_complete": False,
                "tokens_complete": False,
            },
        },
        {"outcome": "SUCCESS"},  # 没有 usage 的报告要单独计数
    ]

    merged = aggregate_usage(reports)

    assert merged["calls"] == 5
    assert merged["tokens_known"] == 200
    assert merged["unknown_token_calls"] == 1
    assert merged["spent_usd_known"] == 0.5
    assert merged["cost_complete"] is False
    assert merged["reports_with_usage"] == 2
    assert merged["reports_without_usage"] == 1
    # 分子与分母同源的集合：两份都既有台账又有可测时长。
    assert merged["reports_paired"] == 2
    assert merged["paired_basis_ms"] == 3_600_000
    assert merged["paired_calls"] == 5
    assert merged["reports_unpaired_duration"] == 0
    assert merged["complete"] is False  # 还缺一份 usage，整体不完整


def test_aggregate_usage_of_nothing_is_incomplete_not_zero() -> None:
    # 复核 R4：没有任何台账来源时，合并结果不是「合法零台账」而是「不完整」：
    # cost_complete/tokens_complete/complete 必须为 False，否则报告会把缺数据说成没花钱。
    merged = aggregate_usage([])

    assert merged["calls"] == 0
    assert merged["cost_complete"] is False
    assert merged["tokens_complete"] is False
    assert merged["complete"] is False
    assert merged["reports_with_usage"] == 0


def test_aggregate_usage_of_an_empty_snapshot_is_not_a_complete_zero() -> None:
    # 空 dict（旧版本或损坏写入）不能被当成「零调用、费用完整」。
    merged = aggregate_usage([{"session_id": "s-1", "usage": {}}])

    assert merged["reports_with_usage"] == 0
    assert merged["reports_without_usage"] == 1
    assert merged["cost_complete"] is False
    assert merged["complete"] is False


# --------------------------------------------------------------------------- 记录器


def test_recorder_keeps_every_stage_even_without_samples() -> None:
    recorder = SessionRecorder(clock=lambda: 0, session_id="s-1")

    report = recorder.build()

    assert list(report["stages"]) == list(STAGES)
    assert all(report["stages"][name]["samples"] == 0 for name in STAGES)
    assert report["session_id"] == "s-1"
    assert report["schema_version"] == 2
    # 复核 R4：没有预算对象 → 没有台账 → 用量未知，而不是「零费率且完整」。
    assert report["usage"] is None
    assert report["hourly"]["cost_per_hour_usd"] is None
    assert report["hourly"]["calls_per_hour"] is None
    assert report["hourly"]["tokens_projection_complete"] is False
    assert str(report["hourly"]["reason"]).startswith("NO_DATA")
    assert any("usage ledger" in warning for warning in report["warnings"])


def test_recorder_counts_successes_failures_and_codes() -> None:
    recorder = SessionRecorder(clock=lambda: 0)

    recorder.stage_ms("perception", 5, ok=True)
    recorder.stage_ms("perception", 7, ok=False, code="MODEL_TIMEOUT")
    recorder.count("scenes", 2)

    report = recorder.build()

    assert report["stages"]["perception"]["samples"] == 2
    assert report["stages"]["perception"]["successes"] == 1
    assert report["stages"]["perception"]["failures"] == 1
    assert report["stages"]["perception"]["latency"]["max_ms"] == 7
    assert report["codes"]["by_code"] == {"MODEL_TIMEOUT": 1}
    assert report["counters"] == {"scenes": 2}


def test_a_stage_that_raises_is_recorded_and_reraised() -> None:
    recorder = SessionRecorder(clock=lambda: 0)

    try:
        with recorder.stage("planning"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    report = recorder.build()

    assert report["stages"]["planning"]["failures"] == 1
    assert "RuntimeError" in report["codes"]["unknown_codes"]
    assert report["warnings"] and "RuntimeError" in report["warnings"][0]


def test_warnings_cover_unknown_codes_short_basis_and_empty_stages() -> None:
    recorder = SessionRecorder(clock=lambda: 0)
    recorder.note_code("NOT_A_REAL_CODE")
    recorder.stage_ms("perception", 1)

    report = recorder.build()
    warnings = " | ".join(report["warnings"])

    assert "NOT_A_REAL_CODE" in warnings
    assert "no samples recorded for stages" in warnings


def test_short_basis_warning_appears_for_a_fast_run() -> None:
    ticks = iter([0, 5_000_000, 5_000_000])  # 5ms 的运行
    recorder = SessionRecorder(clock=lambda: next(ticks, 5_000_000))
    recorder.stage_ms("perception", 5)

    report = recorder.build()

    assert report["elapsed_ms"] < SHORT_BASIS_MS
    assert any("scale factor" in warning for warning in report["warnings"])


def test_report_reads_the_structured_log_it_wrote(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path, "s-1")
    log.emit("session_start", mode="offline", operation="report")
    log.emit("runtime_outcome", status="COMPLETED", reason_code="SUCCESS")
    log.close()

    recorder = SessionRecorder(session_id="s-1", log_path=path, clock=lambda: 0)
    recorder.stage_ms("planning", 3)
    report = recorder.build()

    assert report["event_log"]["path"] == str(path)
    assert report["event_log"]["events"] == {"runtime_outcome": 1, "session_start": 1}
    assert report["event_log"]["statuses"] == {"COMPLETED": 1}
    assert report["event_log"]["reason_codes"] == {"SUCCESS": 1}
    assert report["event_log"]["malformed_lines"] == 0
    assert report["events"] == {"records": 2, "events": {"runtime_outcome": 1, "session_start": 1}}


def test_a_deleted_log_is_reported_as_no_log(tmp_path: Path) -> None:
    recorder = SessionRecorder(log_path=tmp_path / "gone.jsonl", clock=lambda: 0)

    report = recorder.build()

    assert report["event_log"]["records"] == 0
    assert report["event_log"]["path"] is not None


def test_usage_and_hourly_come_from_the_budget(tmp_path: Path) -> None:
    budget = SessionBudget.from_config(PerceptionConfig(provider="fake"))
    recorder = SessionRecorder(clock=lambda: 0, budget=budget)
    recorder.stage_ms("perception", 1)

    report = recorder.build()

    assert report["usage"]["calls"] == 0
    assert report["usage"]["cost_complete"] is True
    assert report["hourly"]["basis_ms"] == 0
    assert report["hourly"]["calls_per_hour"] is None
