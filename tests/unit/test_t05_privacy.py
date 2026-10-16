"""T05 隐私与边界回归：感知与供应商包默认不出网、不落盘，画面字节不进日志。

两条独立防线（沿用 T04 的做法）：
1. **静态**：扫 `perception/` 与 `providers/` 的语法树——感知层禁止任何网络/写盘依赖，
   供应商层只允许 `urllib` 这一条出网通路，两边都不许直接 `open()` 或写文件。
2. **动态**：把 `open`/`write_*`/`os.open`/`socket`/`urlopen` 全换成会爆炸的哨兵，
   再跑一整条「回放 → 感知」离线链路；任何一次落盘或联网都会立刻失败。
"""

from __future__ import annotations

import ast
import asyncio
import builtins
import json
import os
import socket
import urllib.request
from pathlib import Path
from typing import Any

import pytest

import vrc_agent.perception as perception_package
import vrc_agent.providers as providers_package
from vrc_agent.capture.replay import ReplaySource, parse_fixture
from vrc_agent.capture.synthetic import default_fixture
from vrc_agent.clock import FakeClock
from vrc_agent.config import PerceptionConfig
from vrc_agent.perception import Perception
from vrc_agent.providers.fake import FakeSceneProvider
from vrc_agent.telemetry import ALLOWED_FIELDS

PERCEPTION_FILES = sorted(Path(perception_package.__file__).parent.glob("*.py"))
PROVIDER_FILES = sorted(Path(providers_package.__file__).parent.glob("*.py"))

#: 图像处理、截图、进程与写盘相关的第三方依赖：两个包都不许引入。
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
    "shutil",
    "tempfile",
    "socket",
    "requests",
    "httpx",
}

#: 感知层是纯本地的：出网只允许发生在 provider 适配器里。
NETWORK_MODULES = {"urllib", "http", "ssl", "smtplib", "ftplib", "xmlrpc", "webbrowser"}

#: 写盘相关的属性名。读文件（read_text）在感知/供应商包同样不允许——它们不碰磁盘。
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

#: 供应商适配器允许的出网通路：白名单里只有标准库 urllib。
PROVIDER_ALLOWED_NETWORK = {"urllib.error", "urllib.request", "urllib.parse"}


def _imports(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append((node.module or "", node.lineno))
    return found


def _module_roots(name: str) -> set[str]:
    parts = name.split(".")
    return {parts[0], name}


def test_the_perception_package_is_local_only() -> None:
    for path in PERCEPTION_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name, lineno in _imports(tree):
            roots = _module_roots(name)
            assert not roots & FORBIDDEN_MODULES, f"{path.name}:{lineno} 引入了 {name}"
            assert not roots & NETWORK_MODULES, (
                f"{path.name}:{lineno} 引入了网络模块 {name}（出网只允许在 provider 适配器里）"
            )


def test_the_provider_package_only_reaches_the_network_through_urllib() -> None:
    for path in PROVIDER_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name, lineno in _imports(tree):
            roots = _module_roots(name)
            assert not roots & FORBIDDEN_MODULES, f"{path.name}:{lineno} 引入了 {name}"
            if roots & NETWORK_MODULES:
                assert name in PROVIDER_ALLOWED_NETWORK, (
                    f"{path.name}:{lineno} 用了未列入白名单的出网通路 {name}"
                )


def test_neither_package_touches_the_filesystem() -> None:
    for path in [*PERCEPTION_FILES, *PROVIDER_FILES]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                assert node.func.id != "open", f"{path.name}:{node.lineno} 调用了 open()"
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr not in FORBIDDEN_ATTRIBUTES, (
                    f"{path.name}:{node.lineno} 调用了 {node.func.attr}()"
                )


def test_the_only_network_call_lives_in_the_real_provider() -> None:
    """全仓库只有一处出网调用，它在真实供应商适配器里，感知层一处都没有。"""
    for path in PERCEPTION_FILES:
        assert "urlopen" not in path.read_text(encoding="utf-8"), f"{path.name} 试图出网"

    locations = {
        path.name for path in PROVIDER_FILES if "urlopen" in path.read_text(encoding="utf-8")
    }
    assert locations == {"openai_compat.py"}


def test_the_real_provider_never_sends_frames_by_default() -> None:
    # 配置层的默认值就是「不发画面」；这是需要本机用户明确决定才会改变的行为。
    assert PerceptionConfig().send_images is False


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))


def perceive_offline(steps: int = 13, *, sink: RecordingSink | None = None) -> list[Any]:
    """跑完整的「回放 → 感知」链路，返回场景列表。"""
    fixture = parse_fixture(default_fixture())
    clock = FakeClock(fixture.start_ns)
    source = ReplaySource(fixture, clock=clock, sink=sink)
    perception = Perception(FakeSceneProvider(clock=clock), clock=clock, sink=sink)

    async def drive() -> list[Any]:
        scenes: list[Any] = []
        for _ in range(steps):
            observation = source.next_observation()
            frame = source.frame_for(observation)
            scenes.append(await perception.interpret(observation, frame=frame))
        return scenes

    return asyncio.run(drive())


def test_an_offline_perception_run_writes_nothing_and_calls_nobody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"感知链路试图落盘或联网: args={args!r} kwargs={kwargs!r}")

    # 落盘哨兵。
    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(Path, "open", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    monkeypatch.setattr(Path, "write_text", refuse)
    monkeypatch.setattr(Path, "write_bytes", refuse)
    monkeypatch.setattr(os, "open", refuse)
    # 联网哨兵：解析域名、建立连接、发 HTTP 三条路全堵死。
    # （不去 patch socket.socket 本身：asyncio 的事件循环自己要用 socketpair。）
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)

    scenes = perceive_offline()

    assert len(scenes) == 13
    assert sum(1 for scene in scenes if scene.entities) == 5  # 看得见的 5 帧
    assert all(scene.environment_epoch == 1 for scene in scenes)


def test_telemetry_carries_no_prompt_no_payload_and_no_frame_bytes() -> None:
    sink = RecordingSink()
    scenes = perceive_offline(sink=sink)

    assert scenes  # 链路真的跑过了
    for _, fields in sink.events:
        assert set(fields) <= ALLOWED_FIELDS
        for value in fields.values():
            assert isinstance(value, (str, int, float)) or value is None
            assert not isinstance(value, (bytes, bytearray, memoryview, list, dict))

    blob = json.dumps([[event, fields] for event, fields in sink.events], default=str)
    assert "You describe a single screenshot" not in blob  # system prompt 不进日志
    assert "synthetic_landmark" not in blob  # 模型载荷不进日志
    assert "describe" not in blob
    for scene in scenes:
        for entity in scene.entities:
            assert entity.track_id not in blob
            assert entity.label not in blob


def test_a_scene_payload_is_never_serialised_into_the_event_log() -> None:
    sink = RecordingSink()
    perceive_offline(sink=sink)

    names = {event for event, _ in sink.events}
    # 采集只记失败（T04 的约定），感知只记「发生了什么」，都不记模型说了什么。
    assert names <= {"capture", "perception_scene", "perception_skipped", "perception_failure"}
    assert "perception_scene" in names
