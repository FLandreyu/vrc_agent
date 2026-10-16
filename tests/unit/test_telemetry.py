import json

from vrc_agent.telemetry import EventLog


def test_logs_do_not_persist_text_or_secrets(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", "test")
    log.emit("test", state="PAUSED", api_key="SECRET", transcript="PRIVATE", text="MESSAGE")
    log.close()
    raw = log.path.read_text(encoding="utf-8")
    assert "SECRET" not in raw and "PRIVATE" not in raw and "MESSAGE" not in raw
    assert json.loads(raw)["state"] == "PAUSED"


def test_logs_keep_parameter_value_field(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", "test")
    log.emit("parameter_received", operation="GestureLeft", value=3)
    log.close()
    record = json.loads(log.path.read_text(encoding="utf-8"))
    assert record["operation"] == "GestureLeft"
    assert record["value"] == 3
