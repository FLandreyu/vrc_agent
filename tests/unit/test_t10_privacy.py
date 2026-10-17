"""T10 隐私：结构化日志只留白名单字段，疑似凭据与原始媒体一律不落盘。

这里锁的是「异常日志无 key/授权头/原始媒体」这条完成条件，测试分三层：

1. 净化函数本身的判定（保留/打码/丢弃）；
2. 真的写一份 JSONL 再读回来，断言文件字节里没有密钥、授权头、base64 媒体；
3. 结构上禁止遥测层依赖采集/交流/控制子系统——避免以后有人顺手把原始帧或转写文本
   接进日志。
"""

from __future__ import annotations

import ast
import base64
import json
from pathlib import Path

from vrc_agent.telemetry import (
    ALLOWED_FIELDS,
    DROP,
    KEEP,
    MAX_FIELD_TEXT,
    REDACT,
    SENSITIVE_MARKERS,
    EventLog,
    sanitize_value,
)

TELEMETRY_DIR = Path(__file__).resolve().parents[2] / "src" / "vrc_agent" / "telemetry"
#: 登记错误码要读各层的闭集（`*.errors` 是纯常量模块），但绝不能碰持有原始字节、
#: 音频设备或传输句柄的模块。
FORBIDDEN_IMPORTS = (
    "vrc_agent.capture.frames",
    "vrc_agent.capture.live",
    "vrc_agent.capture.win32",
    "vrc_agent.capture.source",
    "vrc_agent.dialogue.backend",
    "vrc_agent.dialogue.asr",
    "vrc_agent.dialogue.tts",
    "vrc_agent.dialogue.echo",
    "vrc_agent.dialogue.session",
    "vrc_agent.control.service",
    "vrc_agent.control.transport",
)

SECRET = "sk-live-0123456789abcdef"
AUTHORIZATION = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
RAW_FRAME = base64.b64encode(bytes(range(64))).decode()
TRANSCRIPT = "小爱小爱，把客厅的灯关掉好吗"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --------------------------------------------------------------------------- 净化


def test_scalars_and_none_are_kept() -> None:
    for value in ("COMPLETED", 42, 3.5, True, False, None):
        decision, kept = sanitize_value(value)

        assert decision == KEEP
        assert kept == value


def test_collections_are_dropped_instead_of_serialised() -> None:
    for value in ({"entities": []}, ["a", "b"], b"\x00\xff", object()):
        decision, kept = sanitize_value(value)

        assert decision == DROP
        assert kept is None


def test_long_text_is_dropped_not_truncated_into_the_log() -> None:
    decision, kept = sanitize_value("x" * (MAX_FIELD_TEXT + 1))

    assert decision == DROP
    assert kept is None


def test_a_long_identifier_with_separators_is_still_kept() -> None:
    text = "session-" + "a" * 30 + "-log"  # 41 字符，带分隔符，不是密文块

    decision, kept = sanitize_value(text)

    assert decision == KEEP
    assert kept == text


def test_free_text_and_transcripts_are_dropped() -> None:
    for text in (TRANSCRIPT, "hello world", "line\nbreak", 'quote "x"', "café"):
        decision, kept = sanitize_value(text)

        assert decision == DROP, text
        assert kept is None


def test_opaque_blobs_are_dropped_even_when_short() -> None:
    for blob in (
        RAW_FRAME,
        "a" * 40,
        "0123456789abcdef" * 4,
        base64.b64encode(b"\x00" * 30).decode(),
    ):
        decision, kept = sanitize_value(blob)

        assert decision == DROP, blob
        assert kept is None


def test_action_ids_survive_the_opaque_blob_rule() -> None:
    # 契约里的 action_id 是 uuid4().hex（32 位十六进制），必须原样保留，
    # 否则控制路径的关键字段会在日志里消失。
    for action_id in ("759a82ee2be9490b85840465b6ca2cdd", "action-0001"):
        decision, kept = sanitize_value(action_id)

        assert decision == KEEP, action_id
        assert kept == action_id


def test_identifiers_paths_and_enums_are_kept() -> None:
    for text in (
        "COMPLETED",
        "MANUAL_STOP",
        "action-0001",
        "offline",
        "/avatar/parameters/x",
        r"C:\Users\sunyongjie\AppData\Local\vrc.jsonl",
        "s-1",
    ):
        decision, kept = sanitize_value(text)

        assert decision == KEEP, text
        assert kept == text


def test_credentials_are_redacted_without_leaking_the_value() -> None:
    for text in (
        f"api_key={SECRET}",
        f"Authorization: {AUTHORIZATION}",
        "apikey=abcdef",
        "data:image/png;base64,iVBORw0KGgo=",
        "data:audio/wav;base64,UklGRg==",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        f"sk-{SECRET}",
    ):
        decision, kept = sanitize_value(text)

        assert decision == REDACT, text
        assert kept == "[redacted]"
        assert SECRET not in str(kept)
        assert kept not in SENSITIVE_MARKERS


def test_non_finite_floats_are_dropped() -> None:
    assert sanitize_value(float("nan"))[0] == DROP
    assert sanitize_value(float("inf"))[0] == DROP


# --------------------------------------------------------------------------- 落盘


def test_the_log_keeps_only_allowed_fields(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path, "s-1")
    log.emit(
        "perception_scene",
        status="COMPLETED",
        count=1,
        text=TRANSCRIPT,
        prompt="what do you see",
        api_key=SECRET,
        authorization=AUTHORIZATION,
        image=RAW_FRAME,
        entities=[{"label": "target"}],
    )
    log.close()

    (record,) = read_jsonl(path)

    assert set(record) == {
        "schema_version",
        "event_id",
        "session_id",
        "event",
        "monotonic_ns",
        "utc",
        "status",
        "count",
    }
    assert set(ALLOWED_FIELDS) <= set(record) | set(ALLOWED_FIELDS)
    assert record["status"] == "COMPLETED"
    assert record["count"] == 1


def test_no_secret_or_media_ever_reaches_the_file(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path, "s-1")
    log.emit("action_id", value=f"Bearer {SECRET}")
    log.emit("mode", value=RAW_FRAME)
    log.emit("state", value=TRANSCRIPT)
    log.close()

    raw = path.read_text(encoding="utf-8")

    assert SECRET not in raw
    assert AUTHORIZATION not in raw
    assert RAW_FRAME not in raw
    assert TRANSCRIPT not in raw
    assert base64.b64encode(bytes(range(64))).decode() not in raw

    records = read_jsonl(path)
    assert records[0]["value"] == "[redacted]"
    assert "value" not in records[1]  # base64 媒体 → 丢弃
    assert "value" not in records[2]  # 中文转写 → 丢弃


def test_writer_statistics_show_what_was_refused(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path, "s-1")
    log.emit("perception_scene", status="COMPLETED", value=f"api_key={SECRET}", count={"a": 1})
    log.emit("perception_scene", status="COMPLETED", api_key=SECRET, entities=[{"a": 1}])
    log.close()

    stats = log.stats()

    assert stats["written"] == 2
    assert stats["redacted"] == 1  # f"api_key=…" 命中标记 → 占位符
    # 复核 R1：未登记字段名（api_key、entities）不再静默跳过，而是计入 rejected。
    # 2 个越界字段名 + 1 个非标量 count = 3。
    assert stats["rejected"] == 3
    # 复核第 28 节 R1a：原因码固定、不回显未知 key —— 两个越界字段折叠成同一个标签，
    # 否则 `rejected_reasons` 会被 CLI 打印，等于把任意 key（可能夹带凭据）搬进报告。
    assert stats["rejected_reasons"] == {
        "unregistered_field:unknown_field": 2,
        "count:non_scalar:dict": 1,
    }
    assert stats["failed"] is False
    assert stats["path"] == str(path)

    records = read_jsonl(path)
    # 不在白名单里的字段名（api_key/entities）根本不参与净化，直接忽略。
    assert "api_key" not in records[1]
    assert "entities" not in records[1]
    assert records[0]["value"] == "[redacted]"
    assert "count" not in records[0]


def test_report_does_not_copy_log_text_into_the_summary(tmp_path: Path) -> None:
    from vrc_agent.telemetry.report import SessionRecorder

    path = tmp_path / "events.jsonl"
    log = EventLog(path, "s-1")
    log.emit("session_start", mode="offline", api_key=SECRET, text=TRANSCRIPT)
    log.close()

    recorder = SessionRecorder(session_id="s-1", log_path=path, clock=lambda: 0)
    report = recorder.build()
    blob = json.dumps(report, ensure_ascii=False)

    assert SECRET not in blob
    assert TRANSCRIPT not in blob
    assert RAW_FRAME not in blob


# --------------------------------------------------------------------------- 结构


def test_the_telemetry_package_does_not_reach_into_media_subsystems() -> None:
    offenders: list[str] = []
    for module in sorted(TELEMETRY_DIR.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.startswith(FORBIDDEN_IMPORTS):
                    offenders.append(f"{module.name}: {name}")

    assert offenders == []


def test_allowed_field_set_is_the_documented_one() -> None:
    assert ALLOWED_FIELDS == frozenset(
        {
            "action_id",
            "status",
            "state",
            "reason_code",
            "pid",
            "mode",
            "count",
            "operation",
            "value",
            "at_ns",
            "granted_ms",
            "expires_ns",
            "sequence",
        }
    )
