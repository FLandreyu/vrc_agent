"""T07 命令行集成：离线场景、退出码、JSON 形状与付费/实机门禁。

测试只跑离线路径（`--live-audio` 会真的出声，绝不在这里触发），
另有一条只读的设备枚举用例，在没有音频后端或没有设备时跳过。
"""

from __future__ import annotations

import json

import pytest

from vrc_agent.cli import main

TURNS_20 = ["dialogue", "--turns", "20"]
TURNS_3 = ["dialogue", "--turns", "3"]


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out.strip() else {}
    return code, payload, captured.err


def test_offline_dialogue_reports_turns_and_timings(capsys: pytest.CaptureFixture[str]) -> None:
    code, payload, err = run_cli(capsys, *TURNS_3)

    assert code == 0 and err == ""
    assert payload["mode"] == "dialogue-offline"
    assert payload["scenario"] == "turns"
    assert payload["live_audio"] is False
    assert payload["saves_media"] is False
    assert payload["sends_input"] is False
    assert payload["audio_backend"] == "fake"

    conversation = payload["conversation"]
    assert conversation["turn_count"] == 3
    assert len(conversation["records"]) == 6
    assert {record["status"] for record in conversation["records"]} == {"COMPLETED"}
    assert all(record["duration_ms"] > 0 for record in conversation["records"])
    assert conversation["saves_media"] is False
    assert payload["devices"]["output"].startswith("Voicemeeter Input")


def test_twenty_turn_conversation_is_the_acceptance_evidence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, payload, _ = run_cli(capsys, *TURNS_20)

    assert code == 0
    conversation = payload["conversation"]
    assert conversation["turn_count"] == 20
    assert len(conversation["records"]) == 40
    listens = [r for r in conversation["records"] if r["action"] == "listen"]
    playbacks = [r for r in conversation["records"] if r["action"] == "playback"]
    assert len(listens) == len(playbacks) == 20
    assert all(r["played_ms"] == r["requested_ms"] for r in playbacks)
    assert conversation["total_ms"] == sum(r["duration_ms"] for r in conversation["records"])


def test_the_stop_scenario_cancels_playback_within_a_block(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, payload, _ = run_cli(capsys, "dialogue", "--scenario", "cancel")

    assert code == 0
    assert payload["details"]["cancelled_after_ms"] == 100
    playbacks = [r for r in payload["conversation"]["records"] if r["action"] == "playback"]
    assert len(playbacks) == 1
    record = playbacks[0]
    assert record["status"] == "CANCELLED"
    assert record["reason_code"] == "CANCELLED"
    assert record["played_ms"] == 100
    assert record["requested_ms"] > 1_000  # 请求的是好几秒，实际只播到停止那一刻


def test_the_pause_scenario_drops_pending_speech_and_never_replays_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, payload, _ = run_cli(capsys, "dialogue", "--scenario", "pause")

    assert code == 0
    details = payload["details"]
    assert details["pending_before_pause"] == 5
    assert details["dropped_on_pause"] == 5
    assert details["pending_after_pause"] == 0
    assert details["expired_dropped"] == 1  # 过期内容到点即弃
    conversation = payload["conversation"]
    assert conversation["chat"]["sent"] == 1  # 只发出恢复之后那一条
    assert conversation["chat"]["dropped_expired"] == 1


def test_the_echo_scenario_detects_a_simulated_loopback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, payload, _ = run_cli(capsys, "dialogue", "--scenario", "echo")

    assert code == 0
    echo = payload["echo"]
    assert echo["verdict"] == "loopback_detected"
    assert echo["tone_ratio"] > 0.5 and echo["played_ms"] > 0 and echo["recorded_ms"] > 0
    assert payload["details"]["loopback_simulated"] is True
    assert "samples" not in echo


def test_chatbox_replies_go_through_the_offline_control_core(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, payload, _ = run_cli(capsys, "dialogue", "--turns", "2", "--with-chatbox")

    assert code == 0
    records = payload["conversation"]["records"]
    chatbox = [r for r in records if r["action"] == "chatbox"]
    assert len(chatbox) == 2
    assert {r["reason_code"] for r in chatbox} == {"OK"}
    assert any("control core" in note for note in payload["notes"])


def test_live_audio_and_offline_chatbox_cannot_be_combined(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _, err = run_cli(capsys, "dialogue", "--with-chatbox", "--live-audio")
    assert code == 2
    assert err.strip()


def test_turn_bounds_are_enforced(capsys: pytest.CaptureFixture[str]) -> None:
    for value in ("0", "201"):
        code, _, err = run_cli(capsys, "dialogue", "--turns", value)
        assert code == 2
        assert err.strip()


def test_doctor_reports_the_dialogue_block_without_opening_a_device(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, payload, _ = run_cli(capsys, "doctor")

    assert code == 0
    block = payload["dialogue"]
    assert block["enabled"] is False
    assert block["saves_media"] is False
    assert block["sample_rate"] == 48_000
    assert block["block_ms"] == 20
    assert block["max_pending"] == 8
    assert block["detail"].startswith("sounddevice")
    assert "devices" not in block  # doctor 不枚举设备表


def test_device_enumeration_is_read_only_and_never_plays_sound(
    capsys: pytest.CaptureFixture[str],
) -> None:
    pytest.importorskip("sounddevice")
    code, payload, _ = run_cli(capsys, "dialogue", "--devices")

    assert code == 0
    assert payload["mode"] == "dialogue-devices"
    assert payload["opens_stream"] is False
    assert payload["saves_media"] is False
    assert payload["device_count"] == len(payload["devices"])
    assert payload["device_count"] >= 1
    assert payload["virtual_mic"]["playback_target_found"] is True
    assert payload["note"].startswith("read-only enumeration")
