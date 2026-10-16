"""T07 隐私与边界回归：对话层不落盘、不出网，报告与遥测里没有文本、没有音频。

两条防线（沿用 T04/T05 的做法）：

1. **静态**：扫 `dialogue/` 的语法树——不许出现 `open()`、写盘属性、`wave`/`soundfile`
   这类会把音频写文件的库，也不许 `print()`；除 `chatbox.py`（它本来就负责把文本交给
   控制层）之外，其余模块不得导入 `control`/`transport`。
2. **动态**：把所有文件写入与联网入口换成会爆炸的哨兵，再跑一整轮 20 轮对话。
"""

from __future__ import annotations

import ast
import builtins
import json
import os
import socket
import urllib.request
from pathlib import Path
from typing import Any

import pytest

import vrc_agent.dialogue as dialogue_package
from vrc_agent.clock import FakeClock
from vrc_agent.dialogue import ChatQueue, DialogueSession, FakeAsr, FakeBackend, FakeTts, HalfDuplex
from vrc_agent.telemetry import ALLOWED_FIELDS

DIALOGUE_FILES = sorted(Path(dialogue_package.__file__).parent.glob("*.py"))

#: 会把音频写进文件的库，以及进程/网络相关的东西：对话层一律不许用。
FORBIDDEN_MODULES = {
    "wave",
    "soundfile",
    "pydub",
    "scipy",
    "librosa",
    "subprocess",
    "shutil",
    "tempfile",
    "socket",
    "urllib",
    "http",
    "requests",
    "PIL",
    "cv2",
    "numpy",
}

FORBIDDEN_ATTRIBUTES = {
    "write",
    "writelines",
    "write_text",
    "write_bytes",
    "mkdir",
    "makedirs",
    "rename",
    "remove",
    "unlink",
    "touch",
    "chmod",
    "save",
    "savez",
    "writeframes",
    "urlopen",
    "system",
    "popen",
}

#: 只有这个文件可以依赖控制层（它的职责就是把文本交给控制层）。
CONTROL_AWARE_FILE = "chatbox.py"

#: 例外：`stream.write()` 写的是 PortAudio 的播放缓冲，不是文件。
ALLOWED_WRITE_RECEIVERS = {"stream"}


def _imports(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append((node.module or "", node.lineno))
    return found


def test_the_dialogue_package_cannot_persist_audio_or_text() -> None:
    for path in DIALOGUE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name, lineno in _imports(tree):
            roots = set(name.split("."))
            assert not roots & FORBIDDEN_MODULES, f"{path.name}:{lineno} 引入了 {name}"
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id != "open", f"{path.name}:{node.lineno} 调用了 open()"
                    assert node.func.id != "print", f"{path.name}:{node.lineno} 直接打印了内容"
                if isinstance(node.func, ast.Attribute):
                    receiver = node.func.value
                    receiver_name = receiver.id if isinstance(receiver, ast.Name) else ""
                    if node.func.attr in FORBIDDEN_ATTRIBUTES:
                        assert receiver_name in ALLOWED_WRITE_RECEIVERS, (
                            f"{path.name}:{node.lineno} 调用了 {node.func.attr}()"
                        )


def test_only_the_chatbox_adapter_talks_to_the_control_layer() -> None:
    for path in DIALOGUE_FILES:
        if path.name == CONTROL_AWARE_FILE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name, lineno in _imports(tree):
            assert not name.startswith(("vrc_agent.control", "vrc_agent.transport")), (
                f"{path.name}:{lineno} 不应依赖控制层或传输层（{name}）"
            )


def test_the_audio_backend_is_the_only_place_importing_sounddevice() -> None:
    locations: set[str] = set()
    for path in DIALOGUE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name, _ in _imports(tree):
            if name.split(".")[0] == "sounddevice":
                locations.add(path.name)
    assert locations == {"backend.py"}


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))


def make_session(*, sink: RecordingSink | None = None) -> DialogueSession:
    clock = FakeClock(1_000_000_000)
    backend = FakeBackend(clock=clock)
    devices = backend.list_devices()
    return DialogueSession(
        backend,
        clock=clock,
        session_id="privacy-test",
        queue=ChatQueue(session_id="privacy-test", clock=clock, min_interval_ms=0),
        half_duplex=HalfDuplex(clock=clock),
        tts=FakeTts(),
        asr=FakeAsr(script=tuple(f"secret user line {index}" for index in range(20))),
        output_device=next(d for d in devices if "Voicemeeter Input" in d.name),
        input_device=next(d for d in devices if d.supports("input")),
        sample_rate=48_000,
        channels=1,
        block_ms=20,
        sink=sink,
    )


def test_a_full_conversation_writes_nothing_and_calls_nobody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"对话链路试图落盘或联网: args={args!r} kwargs={kwargs!r}")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(Path, "open", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    monkeypatch.setattr(Path, "write_text", refuse)
    monkeypatch.setattr(Path, "write_bytes", refuse)
    monkeypatch.setattr(os, "open", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)

    session = make_session()
    report = session.run_conversation(turns=20, replies=tuple(f"reply {i}" for i in range(20)))

    assert report.turn_count == 20
    assert len(report.records) == 40
    assert {record.status for record in report.records} == {"COMPLETED"}


def test_telemetry_carries_no_text_and_only_whitelisted_fields() -> None:
    sink = RecordingSink()
    session = make_session(sink=sink)
    report = session.run_conversation(
        turns=5, replies=("alpha", "beta", "gamma", "delta", "epsilon")
    )

    assert sink.events
    for _, fields in sink.events:
        assert set(fields) <= ALLOWED_FIELDS
        for value in fields.values():
            assert isinstance(value, (str, int, float))

    blob = json.dumps(
        [[event, fields] for event, fields in sink.events], ensure_ascii=False, default=str
    )
    assert "secret user line" not in blob
    assert "alpha" not in blob
    assert "epsilon" not in blob
    assert "chars" not in blob  # 字段名也不该自造：白名单里没有它

    report_blob = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert "secret user line" not in report_blob
    assert "alpha" not in report_blob


def test_recorded_audio_never_survives_the_turn() -> None:
    session = make_session()
    session.half_duplex.begin_listening()
    session.listen(index=0, duration_ms=200)
    backend = session.backend
    assert len(backend.capture_log[-1].samples) == 0  # 转写完立刻清空
    assert session.last_transcript is not None
    # 转写对象本身也只有摘要，没有原始音频
    assert "samples" not in json.dumps(session.last_transcript.summary())
