"""音频设备枚举与选择。

设备用**名字子串**选择而不是索引：索引会随驱动与设备插拔变化，名字相对稳定。
匹配规则是确定性的——先精确同名（忽略大小写），再子串匹配，最后按索引取第一个，
因此同一份配置在任何时候都解析到同一台设备，不会随机换设备。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from vrc_agent.contracts import Contract
from vrc_agent.dialogue.errors import DialogueError

Direction = Literal["input", "output"]


class AudioDevice(Contract):
    """一台音频设备在某个宿主 API 下的可见形态。"""

    index: int
    name: Annotated[str, Field(min_length=1, max_length=256)]
    hostapi: Annotated[str, Field(min_length=1, max_length=64)]
    max_input_channels: Annotated[int, Field(ge=0, le=256)]
    max_output_channels: Annotated[int, Field(ge=0, le=256)]
    default_samplerate: Annotated[int, Field(ge=1, le=384_000)]

    @property
    def usable_for(self) -> tuple[Direction, ...]:
        directions: list[Direction] = []
        if self.max_input_channels > 0:
            directions.append("input")
        if self.max_output_channels > 0:
            directions.append("output")
        return tuple(directions)

    def supports(self, direction: Direction) -> bool:
        return direction in self.usable_for


def channel_count(device: AudioDevice, direction: Direction) -> int:
    return device.max_input_channels if direction == "input" else device.max_output_channels


def match_devices(
    devices: tuple[AudioDevice, ...],
    query: str | None,
    *,
    direction: Direction,
    hostapi: str | None = None,
) -> tuple[AudioDevice, ...]:
    """按方向（与可选宿主 API）过滤后再按名字匹配；精确匹配优先于子串匹配。

    **保持传入顺序**：后端已经按「宿主 API 偏好 + 索引」给出了确定性顺序，
    这里再排一次会把偏好丢掉（例如 WASAPI 会被低索引的 MME 盖过）。
    """
    candidates = [
        device
        for device in devices
        if device.supports(direction) and (hostapi is None or device.hostapi == hostapi)
    ]
    if query is None:
        return tuple(candidates)
    needle = query.casefold()
    exact = tuple(d for d in candidates if d.name.casefold() == needle)
    if exact:
        return exact
    return tuple(d for d in candidates if needle in d.name.casefold())


def select_device(
    devices: tuple[AudioDevice, ...],
    query: str | None,
    *,
    direction: Direction,
    hostapi: str | None = None,
    required_channels: int = 1,
) -> AudioDevice:
    """解析出一台设备；不唯一或找不到时明确失败，不做猜测。"""
    matches = match_devices(devices, query, direction=direction, hostapi=hostapi)
    if not matches:
        wanted = f"{query!r}" if query else "任何设备"
        raise DialogueError(
            "DEVICE_UNAVAILABLE",
            f"no {direction} device matches {wanted}"
            + (f" on hostapi {hostapi!r}" if hostapi else ""),
        )
    usable = [device for device in matches if channel_count(device, direction) >= required_channels]
    if not usable:
        raise DialogueError(
            "DEVICE_UNAVAILABLE",
            f"{matches[0].name!r} has fewer than {required_channels} {direction} channels",
        )
    return usable[0]


def describe_devices(devices: tuple[AudioDevice, ...]) -> list[dict[str, object]]:
    """给 CLI/doctor 用的只读视图：只有设备元数据，没有音频。"""
    return [
        {
            "index": device.index,
            "name": device.name,
            "hostapi": device.hostapi,
            "in": device.max_input_channels,
            "out": device.max_output_channels,
            "rate": device.default_samplerate,
        }
        for device in devices
    ]


def virtual_mic_hint(devices: tuple[AudioDevice, ...]) -> dict[str, object]:
    """判断本机是否具备「经 Voicemeeter 说话」的条件（只看名字，不打开设备）。"""
    outputs = match_devices(devices, "Voicemeeter Input", direction="output")
    # 同一台虚拟设备会在 MME/DirectSound/WASAPI 下各出现一次，按名字去重后再报告。
    loopbacks: dict[str, AudioDevice] = {}
    for device in match_devices(devices, "Voicemeeter Out", direction="input"):
        if "Out B" in device.name:
            loopbacks.setdefault(device.name, device)
    return {
        "playback_target_found": bool(outputs),
        "playback_target": outputs[0].name if outputs else None,
        "loopback_candidates": list(loopbacks),
        "routing_ready": bool(outputs) and bool(loopbacks),
    }
