"""T10 命令行集成：一份贯穿各模块的离线报告，以及日志保留命令。

只跑离线路径：`report` 用回放素材 + 假模型 + 假音频后端，不会碰 live 输入、
不会写媒体、不会调付费供应商。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vrc_agent.cli import main
from vrc_agent.telemetry.report import STAGES

REPORT_2 = ["report", "--scenario", "estop", "--turns", "2", "--max-steps", "2"]


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out.strip() else {}
    return code, payload, captured.err


def test_report_covers_every_stage_in_one_offline_document(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, payload, err = run_cli(capsys, *REPORT_2, "--state-dir", str(tmp_path))

    assert code == 0 and err == ""
    assert payload["schema_version"] == 2
    assert payload["offline"] is True
    assert payload["sends_live_input"] is False
    assert payload["writes_media"] is False
    assert list(payload["stages"]) == list(STAGES)
    assert payload["capture"]["observations"] > 0
    assert payload["perception"]["steps"] == 2
    assert payload["runtime"]["outcome"] == "MANUAL_STOP"
    assert payload["runtime"]["sequence_digest"]
    assert payload["dialogue"]["records"] == 4


def test_report_writes_a_structured_log_that_holds_no_free_text(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, payload, _ = run_cli(capsys, *REPORT_2, "--state-dir", str(tmp_path))

    assert code == 0
    log_path = Path(payload["event_log"]["path"])
    assert log_path.parent == tmp_path
    assert payload["event_log"]["records"] > 0
    assert payload["event_log"]["malformed_lines"] == 0
    assert payload["event_log"]["writer"]["rejected"] == 0
    assert payload["event_log"]["writer"]["failed"] is False

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    allowed = {
        "schema_version",
        "event_id",
        "session_id",
        "event",
        "monotonic_ns",
        "utc",
        "action_id",
        "status",
        "state",
        "reason_code",
        "pid",
        "mode",
        "count",
        "operation",
        "value",
    }
    assert all(set(record) <= allowed for record in records)
    assert {"session_start", "capture_replay", "runtime_outcome", "session_end"} <= {
        record["event"] for record in records
    }


def test_report_states_that_the_fake_provider_is_free(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _, payload, _ = run_cli(capsys, *REPORT_2, "--state-dir", str(tmp_path))

    usage = payload["usage"]
    assert usage["calls"] > 0
    assert usage["spent_usd_known"] == 0.0
    assert usage["cost_complete"] is True
    assert usage["pricing"]["configured"] is False
    assert str(usage["pricing"]["reason"]).startswith("PRICE_UNKNOWN")
    assert payload["hourly"]["cost_per_hour_usd"] == 0.0


def test_report_errors_are_honest_about_a_missing_fixture(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, payload, err = run_cli(
        capsys, "report", "--fixture", str(tmp_path / "absent.json"), "--state-dir", str(tmp_path)
    )

    assert code != 0
    assert payload == {}
    assert "absent.json" in err or "No such file" in err or "not found" in err


def test_report_rejects_invalid_turn_counts(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run_cli(capsys, "report", "--turns", "0")

    assert code == 2
    assert "--turns" in err


def test_logs_defaults_to_a_dry_run(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    old = tmp_path / "old.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    stamp = (datetime.now(UTC) - timedelta(days=30)).timestamp()
    os.utime(old, (stamp, stamp))

    code, payload, err = run_cli(capsys, "logs", "--state-dir", str(tmp_path))

    assert code == 0 and err == ""
    assert payload["applied"] is False
    assert [entry["name"] for entry in payload["candidates"]] == ["old.jsonl"]
    assert payload["deleted"] == []
    assert old.exists() is True


def test_logs_apply_deletes_only_expired_files(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    old = tmp_path / "old.jsonl"
    fresh = tmp_path / "fresh.jsonl"
    for path, days in ((old, 30), (fresh, 0)):
        path.write_text("{}\n", encoding="utf-8")
        stamp = (datetime.now(UTC) - timedelta(days=days)).timestamp()
        os.utime(path, (stamp, stamp))

    code, payload, _ = run_cli(capsys, "logs", "--state-dir", str(tmp_path), "--apply")

    assert code == 0
    assert payload["applied"] is True
    assert payload["deleted"] == ["old.jsonl"]
    assert old.exists() is False
    assert fresh.exists() is True
