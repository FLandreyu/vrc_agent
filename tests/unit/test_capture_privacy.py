"""T04 隐私回归：采集包默认不落盘，事件里不带画面字节。

两条独立防线：
1. **静态**：扫 capture/ 的语法树，禁止写文件、禁止图像/截图第三方库、禁止进程与网络。
2. **动态**：把 open/write_text/write_bytes 全部换成会爆炸的哨兵，再跑一整条回放；
   只要有任何一处试图落盘，测试立刻失败。
"""

from __future__ import annotations

import ast
import builtins
import json
import os
from pathlib import Path
from typing import Any

import pytest

import vrc_agent.capture as capture_package
from vrc_agent.capture.replay import collect, default_replay_source
from vrc_agent.capture.synthetic import render_bgra
from vrc_agent.telemetry import ALLOWED_FIELDS

CAPTURE_FILES = sorted(Path(capture_package.__file__).parent.glob("*.py"))

#: 采集包不该出现的依赖：图像处理与截图库、进程与网络。
FORBIDDEN_MODULES = {
    "PIL",
    "cv2",
    "numpy",
    "mss",
    "pyautogui",
    "imageio",
    "win32gui",
    "win32ui",
    "win32con",
    "subprocess",
    "socket",
    "requests",
    "urllib",
    "shutil",
}

#: 写盘相关的属性名。读取（read_text/read_bytes）是允许的：素材要从磁盘加载。
FORBIDDEN_ATTRIBUTES = {
    "write",
    "writelines",
    "write_text",
    "write_bytes",
    "mkdir",
    "makedirs",
    "rmdir",
    "remove",
    "unlink",
    "rename",
    "touch",
    "chmod",
    "dump",
    "save",
    "savez",
    "imwrite",
    "system",
    "popen",
    "spawn",
}


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def test_capture_package_has_no_image_or_process_dependencies() -> None:
    for path in CAPTURE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                root = name.split(".")[0]
                assert root not in FORBIDDEN_MODULES, f"{path.name} 引入了 {name}"
                assert name not in FORBIDDEN_MODULES, f"{path.name} 引入了 {name}"


def test_capture_package_never_writes_to_disk() -> None:
    for path in CAPTURE_FILES:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for call in _calls(tree):
            if isinstance(call.func, ast.Name):
                assert call.func.id != "open", f"{path.name}:{call.lineno} 直接调用了 open()"
            if isinstance(call.func, ast.Attribute):
                name = call.func.attr
                assert name not in FORBIDDEN_ATTRIBUTES, (
                    f"{path.name}:{call.lineno} 调用了 {name}()"
                )
                # 只读打开也别用：文件句柄不进采集包。
                if name == "open":
                    mode = ""
                    if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
                        mode = str(call.args[1].value)
                    for keyword in call.keywords:
                        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                            mode = str(keyword.value.value)
                    assert not set(mode) & set("wax+"), (
                        f"{path.name}:{call.lineno} 以写模式打开文件"
                    )


def test_capture_package_has_no_persistence_api() -> None:
    forbidden = ("save", "write", "dump", "export", "persist", "store_to", "to_file")
    for path in CAPTURE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert not node.name.startswith(forbidden), (
                    f"{path.name}:{node.lineno} 暴露了持久化接口 {node.name}()"
                )


def test_a_full_replay_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"采集包试图写盘: args={args!r} kwargs={kwargs!r}")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(Path, "open", refuse)
    monkeypatch.setattr(Path, "write_text", refuse)
    monkeypatch.setattr(Path, "write_bytes", refuse)
    monkeypatch.setattr(os, "open", refuse)

    records = collect(default_replay_source())
    assert len(records) == 13
    assert sum(1 for _, frame in records if frame is not None) == 5


def test_telemetry_only_sees_small_non_media_fields() -> None:
    sink = RecordingSink()
    source = default_replay_source(sink=sink)
    records = collect(source)

    # 只有失败才写日志：逐帧写日志会把日志变成录像的元数据副本。
    assert len(sink.events) == 8
    assert {event for event, _ in sink.events} == {"capture"}

    for _, fields in sink.events:
        assert set(fields) <= ALLOWED_FIELDS
        assert fields["operation"] == "capture"
        assert fields["status"] == "failed"
        assert fields["reason_code"] in {
            "FRAME_BLACK",
            "WINDOW_MINIMIZED",
            "WINDOW_NOT_FOUND",
            "WINDOW_OCCLUDED",
            "CAPTURE_FAILED",
            "WINDOW_HIDDEN",
            "WINDOW_EMPTY_SIZE",
            "WINDOW_TOO_LARGE",
        }
        for value in fields.values():
            assert isinstance(value, (str, int, float)) or value is None
            assert not isinstance(value, (bytes, bytearray, memoryview, list, dict))
            if isinstance(value, str):
                assert len(value) <= 32

    blob = json.dumps([fields for _, fields in sink.events], default=str)
    for _, frame in records:
        if frame is not None:
            assert frame.digest() not in blob
            assert str(len(frame.data)) not in blob


def test_frames_are_held_only_in_memory() -> None:
    source = default_replay_source()
    records = collect(source)
    frames = [frame for _, frame in records if frame is not None]
    assert len(frames) == 5
    assert sum(len(frame.data) for frame in frames) > 0
    assert {frame.ref for frame in frames} == set(source.frames.refs())

    # 帧是字节，不是路径：没有任何一个 ref 能当文件打开。
    for frame in frames:
        assert not Path(frame.ref).exists()
    assert not Path("replay.t04_smoke.0001").exists()


def test_synthetic_renderer_is_deterministic_and_offline() -> None:
    spec = {"type": "scene", "background_bgr": [0, 0, 0], "boxes": []}
    first = render_bgra(8, 4, spec)
    second = render_bgra(8, 4, spec)
    assert first == second == b"\x00\x00\x00\xff" * 32
