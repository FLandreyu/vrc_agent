"""T10：日志保留策略——默认只列不删，范围只限目标目录里的普通文件。"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vrc_agent.telemetry.retention import (
    DEFAULT_KEEP_DAYS,
    LOG_SUFFIX,
    plan_prune,
    prune_logs,
)

NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)


def aged(path: Path, *, days: float, payload: str = "{}\n") -> Path:
    path.write_text(payload, encoding="utf-8")
    stamp = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def test_only_files_older_than_the_retention_window_are_candidates(tmp_path: Path) -> None:
    old = aged(tmp_path / f"old{LOG_SUFFIX}", days=DEFAULT_KEEP_DAYS + 1)
    fresh = aged(tmp_path / f"fresh{LOG_SUFFIX}", days=1)

    entries = plan_prune(tmp_path, now=NOW)

    assert [entry.path.name for entry in entries] == [old.name]
    assert fresh.exists()
    assert entries[0].age_days > DEFAULT_KEEP_DAYS


def test_candidates_are_sorted_from_oldest_to_newest(tmp_path: Path) -> None:
    aged(tmp_path / f"a{LOG_SUFFIX}", days=30)
    aged(tmp_path / f"b{LOG_SUFFIX}", days=20)
    aged(tmp_path / f"c{LOG_SUFFIX}", days=10)

    entries = plan_prune(tmp_path, keep_days=5, now=NOW)

    assert [entry.path.name for entry in entries] == ["a.jsonl", "b.jsonl", "c.jsonl"]


def test_other_suffixes_and_directories_are_left_alone(tmp_path: Path) -> None:
    aged(tmp_path / "keep.txt", days=99)
    aged(tmp_path / f"keep{LOG_SUFFIX}", days=99)
    nested = tmp_path / "nested"
    nested.mkdir()
    aged(nested / f"inner{LOG_SUFFIX}", days=99)

    entries = plan_prune(tmp_path, keep_days=1, now=NOW)

    assert [entry.path.name for entry in entries] == ["keep.jsonl"]


def test_a_missing_directory_is_not_an_error(tmp_path: Path) -> None:
    assert plan_prune(tmp_path / "absent", now=NOW) == []


def test_negative_keep_days_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        plan_prune(tmp_path, keep_days=-1, now=NOW)


def test_dry_run_lists_candidates_without_deleting(tmp_path: Path) -> None:
    target = aged(tmp_path / f"old{LOG_SUFFIX}", days=99, payload="x" * 32)

    report = prune_logs(tmp_path, keep_days=1, now=NOW)

    assert report["applied"] is False
    assert report["candidates"][0]["name"] == target.name
    assert report["candidate_bytes"] == 32
    assert report["deleted"] == []
    assert report["freed_bytes"] == 0
    assert "dry run" in str(report["note"])
    assert target.exists()


def test_apply_deletes_only_the_candidates(tmp_path: Path) -> None:
    doomed = aged(tmp_path / f"old{LOG_SUFFIX}", days=99, payload="y" * 16)
    kept = aged(tmp_path / f"new{LOG_SUFFIX}", days=0)

    report = prune_logs(tmp_path, keep_days=1, apply=True, now=NOW)

    assert report["applied"] is True
    assert report["deleted"] == [doomed.name]
    assert report["freed_bytes"] == 16
    assert report["errors"] == []
    assert doomed.exists() is False
    assert kept.exists() is True
