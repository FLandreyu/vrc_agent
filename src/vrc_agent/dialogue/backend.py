"""音频后端：一个协议、一个离线假后端、一个真实（sounddevice/PortAudio）后端。

约束：

- 播放与录音都是**逐块**进行的，每块之间检查取消回调，因此「本机停止」的延迟上限
  就是一块的时长（默认 20 ms）；真实后端在取消时用 `abort()` 而不是 `stop()`，
  不等待缓冲区排空。
- 音频只在内存里以 `array("f")` 传递，**任何后端都不写文件**。
- 真实后端延迟导入 `sounddevice`：离线测试与 `--devices` 之外的功能不需要设备。
"""

from __future__ import annotations

import threading
import time
from array import array
from dataclasses import dataclass, field
from typing import Callable, Protocol

from vrc_agent.clock import Clock, SystemClock
from vrc_agent.dialogue.devices import AudioDevice, Direction, channel_count
from vrc_agent.dialogue.dsp import hash_prefix
from vrc_agent.dialogue.errors import DialogueError

#: 取消回调：返回 True 表示「立刻停止并放弃剩余内容」。
Cancel = Callable[[], bool]


def never_cancelled() -> bool:
    return False


@dataclass(frozen=True, slots=True)
class PlaybackResult:
    device: str
    requested_ms: int
    played_ms: int
    blocks: int
    cancelled: bool
    sample_hash: str

    @property
    def completed(self) -> bool:
        return not self.cancelled and self.played_ms >= self.requested_ms


@dataclass(slots=True)
class CaptureResult:
    """录到的一段采样。**只在内存里**，调用方用完即弃，不落盘、不写日志。"""

    device: str
    sample_rate: int
    channels: int
    duration_ms: int
    blocks: int
    cancelled: bool
    samples: array = field(repr=False)

    def discard(self) -> None:
        """显式清空缓冲，配合「默认不保存真人语音」。"""
        del self.samples[:]


class AudioBackend(Protocol):
    name: str

    def list_devices(self) -> tuple[AudioDevice, ...]: ...

    def default_device_indices(self) -> tuple[int | None, int | None]: ...

    def play(
        self,
        samples: array,
        *,
        sample_rate: int,
        channels: int,
        device: AudioDevice,
        cancel: Cancel = never_cancelled,
        block_ms: int = 20,
    ) -> PlaybackResult: ...

    def record(
        self,
        *,
        sample_rate: int,
        channels: int,
        device: AudioDevice,
        duration_ms: int,
        cancel: Cancel = never_cancelled,
        block_ms: int = 20,
    ) -> CaptureResult: ...


# ---------------------------------------------------------------- 离线假后端


def default_fake_devices() -> tuple[AudioDevice, ...]:
    """确定性的离线设备表：名字刻意模仿本机 Voicemeeter 的命名。"""
    return (
        AudioDevice(
            index=0,
            name="Fake Mic (test tone source)",
            hostapi="FakeHost",
            max_input_channels=2,
            max_output_channels=0,
            default_samplerate=48_000,
        ),
        AudioDevice(
            index=1,
            name="Voicemeeter Input (VB-Audio Voicemeeter VAIO)",
            hostapi="FakeHost",
            max_input_channels=0,
            max_output_channels=8,
            default_samplerate=48_000,
        ),
        AudioDevice(
            index=2,
            name="Voicemeeter Out B1 (VB-Audio Voicemeeter VAIO)",
            hostapi="FakeHost",
            max_input_channels=8,
            max_output_channels=0,
            default_samplerate=48_000,
        ),
        AudioDevice(
            index=3,
            name="Fake Speakers (test sink)",
            hostapi="FakeHost",
            max_input_channels=0,
            max_output_channels=2,
            default_samplerate=48_000,
        ),
    )


class FakeBackend:
    """离线、确定性、无设备的后端：用虚拟时钟推进时间，便于逐块断言取消时间点。"""

    name = "fake"

    def __init__(
        self,
        *,
        devices: tuple[AudioDevice, ...] | None = None,
        clock: Clock | None = None,
        fail_play: bool = False,
        fail_record: bool = False,
        capture_tone_hz: float | None = None,
        capture_amplitude: float = 0.3,
    ) -> None:
        self._devices = devices or default_fake_devices()
        self.clock: Clock = clock or SystemClock()
        self.fail_play = fail_play
        self.fail_record = fail_record
        self.capture_tone_hz = capture_tone_hz
        self.capture_amplitude = capture_amplitude
        self.playback_log: list[PlaybackResult] = []
        self.capture_log: list[CaptureResult] = []
        self.opened: list[str] = []

    def _advance(self, milliseconds: int) -> None:
        """只有可推进的时钟（FakeClock）才推进；真实时钟本来就自己走。"""
        advance = getattr(self.clock, "advance_ms", None)
        if callable(advance):
            advance(milliseconds)

    def list_devices(self) -> tuple[AudioDevice, ...]:
        return self._devices

    def default_device_indices(self) -> tuple[int | None, int | None]:
        inputs = [d.index for d in self._devices if d.supports("input")]
        outputs = [d.index for d in self._devices if d.supports("output")]
        return (inputs[0] if inputs else None, outputs[0] if outputs else None)

    def resolve(self, index: int, direction: Direction) -> AudioDevice:
        for device in self._devices:
            if device.index == index:
                if not device.supports(direction):
                    raise DialogueError(
                        "DEVICE_UNAVAILABLE", f"{device.name!r} has no {direction} channels"
                    )
                return device
        raise DialogueError("DEVICE_UNAVAILABLE", f"no device with index {index}")

    # ---------------------------------------------------------------- 播放

    def play(
        self,
        samples: array,
        *,
        sample_rate: int,
        channels: int,
        device: AudioDevice,
        cancel: Cancel = never_cancelled,
        block_ms: int = 20,
    ) -> PlaybackResult:
        if self.fail_play:
            raise DialogueError("STREAM_UNAVAILABLE", "fake playback failure injected")
        if not device.supports("output"):
            raise DialogueError("DEVICE_UNAVAILABLE", f"{device.name!r} cannot play audio")
        self.opened.append(f"out:{device.name}")
        block_frames = max(1, sample_rate * block_ms // 1000)
        block_samples = block_frames * channels
        total_frames = len(samples) // channels
        requested_ms = total_frames * 1000 // sample_rate
        played_frames = 0
        blocks = 0
        cancelled = False
        while played_frames < total_frames:
            if cancel():
                cancelled = True
                break
            step = min(block_samples, len(samples) - played_frames * channels)
            played_frames += step // channels
            blocks += 1
            self._advance(block_ms)
        played = min(played_frames, total_frames)
        result = PlaybackResult(
            device=device.name,
            requested_ms=requested_ms,
            played_ms=played * 1000 // sample_rate,
            blocks=blocks,
            cancelled=cancelled,
            sample_hash=hash_prefix(array("f", samples[: played * channels])),
        )
        self.playback_log.append(result)
        return result

    # ---------------------------------------------------------------- 录音

    def record(
        self,
        *,
        sample_rate: int,
        channels: int,
        device: AudioDevice,
        duration_ms: int,
        cancel: Cancel = never_cancelled,
        block_ms: int = 20,
    ) -> CaptureResult:
        if self.fail_record:
            raise DialogueError("STREAM_UNAVAILABLE", "fake capture failure injected")
        if not device.supports("input"):
            raise DialogueError("DEVICE_UNAVAILABLE", f"{device.name!r} cannot record audio")
        self.opened.append(f"in:{device.name}")
        block_frames = max(1, sample_rate * block_ms // 1000)
        target_frames = max(1, sample_rate * duration_ms // 1000)
        captured = array("f")
        blocks = 0
        cancelled = False
        while len(captured) // channels < target_frames:
            if cancel():
                cancelled = True
                break
            frames = min(block_frames, target_frames - len(captured) // channels)
            for _ in range(frames * channels):
                captured.append(0.0)
            blocks += 1
            self._advance(block_ms)
        if self.capture_tone_hz is not None and captured:
            from vrc_agent.dialogue import dsp

            tone = dsp.sine_tone(
                self.capture_tone_hz,
                max(1, len(captured) // channels * 1000 // sample_rate),
                sample_rate,
                channels=1,
                amplitude=self.capture_amplitude,
            )
            for index in range(min(len(captured), len(tone))):
                captured[index] = tone[index]
        result = CaptureResult(
            device=device.name,
            sample_rate=sample_rate,
            channels=channels,
            duration_ms=len(captured) // channels * 1000 // sample_rate,
            blocks=blocks,
            cancelled=cancelled,
            samples=captured,
        )
        self.capture_log.append(result)
        return result


# ---------------------------------------------------------------- 真实后端


class SoundDeviceBackend:
    """真实设备后端：PortAudio（随 `sounddevice` 提供），只用原始 float32 缓冲。"""

    name = "sounddevice"

    def __init__(self, *, hostapi_preference: tuple[str, ...] = ()) -> None:
        self._cache: tuple[AudioDevice, ...] | None = None
        self.hostapi_preference = hostapi_preference
        self.played_ms = 0
        self.recorded_ms = 0

    @staticmethod
    def _sd():
        try:
            import sounddevice  # noqa: PLC0415

            return sounddevice
        except Exception as error:  # pragma: no cover - 只在缺依赖时触发
            raise DialogueError(
                "STREAM_UNAVAILABLE", f"sounddevice unavailable: {error}"
            ) from error

    def list_devices(self) -> tuple[AudioDevice, ...]:
        if self._cache is not None:
            return self._cache
        sounddevice = self._sd()
        try:
            hostapis = sounddevice.query_hostapis()
            entries = sounddevice.query_devices()
        except Exception as error:
            raise DialogueError("STREAM_UNAVAILABLE", f"device query failed: {error}") from error
        devices = []
        for index, entry in enumerate(entries):
            devices.append(
                AudioDevice(
                    index=index,
                    name=entry["name"].strip() or f"device {index}",
                    hostapi=hostapis[entry["hostapi"]]["name"],
                    max_input_channels=int(entry["max_input_channels"]),
                    max_output_channels=int(entry["max_output_channels"]),
                    default_samplerate=int(entry["default_samplerate"]),
                )
            )
        ranked = sorted(
            devices,
            key=lambda device: (
                self.hostapi_preference.index(device.hostapi)
                if device.hostapi in self.hostapi_preference
                else len(self.hostapi_preference),
                device.index,
            ),
        )
        self._cache = tuple(ranked)
        return self._cache

    def default_device_indices(self) -> tuple[int | None, int | None]:
        sounddevice = self._sd()
        try:
            inputs, outputs = sounddevice.default.device
        except Exception:  # pragma: no cover - 极少数无默认设备的环境
            return (None, None)
        in_index = None if inputs is None or inputs < 0 else int(inputs)
        out_index = None if outputs is None or outputs < 0 else int(outputs)
        return (in_index, out_index)

    def play(
        self,
        samples: array,
        *,
        sample_rate: int,
        channels: int,
        device: AudioDevice,
        cancel: Cancel = never_cancelled,
        block_ms: int = 20,
    ) -> PlaybackResult:
        sounddevice = self._sd()
        if not device.supports("output"):
            raise DialogueError("DEVICE_UNAVAILABLE", f"{device.name!r} cannot play audio")
        block_frames = max(1, sample_rate * block_ms // 1000)
        data = samples.tobytes()
        bytes_per_frame = channels * 4
        block_bytes = block_frames * bytes_per_frame
        total_frames = len(samples) // channels
        requested_ms = total_frames * 1000 // sample_rate
        cursor = 0
        frames_written = 0
        blocks = 0
        cancelled = False
        try:
            stream = sounddevice.RawOutputStream(
                samplerate=sample_rate,
                blocksize=block_frames,
                channels=channels,
                dtype="float32",
                device=device.index,
            )
        except Exception as error:
            raise DialogueError("STREAM_UNAVAILABLE", f"cannot open output: {error}") from error
        try:
            with stream:
                while cursor < len(data):
                    if cancel():
                        cancelled = True
                        break
                    real_bytes = min(block_bytes, len(data) - cursor)
                    chunk = data[cursor : cursor + real_bytes]
                    cursor += real_bytes
                    frames_written += real_bytes // bytes_per_frame
                    if real_bytes < block_bytes:
                        chunk = chunk + bytes(block_bytes - real_bytes)
                    # 阻塞写入天然按块计时；取消时用 abort() 放弃缓冲，不等待排空。
                    stream.write(bytes(chunk))
                    blocks += 1
                if cancelled:
                    stream.abort()
        except DialogueError:
            raise
        except Exception as error:
            raise DialogueError("STREAM_UNAVAILABLE", f"playback failed: {error}") from error
        played_frames = min(frames_written, total_frames)
        played_ms = played_frames * 1000 // sample_rate
        self.played_ms += played_ms
        return PlaybackResult(
            device=device.name,
            requested_ms=requested_ms,
            played_ms=played_ms,
            blocks=blocks,
            cancelled=cancelled,
            sample_hash=hash_prefix(array("f", samples[: played_frames * channels])),
        )

    def record(
        self,
        *,
        sample_rate: int,
        channels: int,
        device: AudioDevice,
        duration_ms: int,
        cancel: Cancel = never_cancelled,
        block_ms: int = 20,
    ) -> CaptureResult:
        sounddevice = self._sd()
        if not device.supports("input"):
            raise DialogueError("DEVICE_UNAVAILABLE", f"{device.name!r} cannot record audio")
        block_frames = max(1, sample_rate * block_ms // 1000)
        target_frames = max(1, sample_rate * duration_ms // 1000)
        captured = array("f")
        blocks = 0
        cancelled = False
        try:
            stream = sounddevice.RawInputStream(
                samplerate=sample_rate,
                blocksize=block_frames,
                channels=channels,
                dtype="float32",
                device=device.index,
            )
        except Exception as error:
            raise DialogueError("STREAM_UNAVAILABLE", f"cannot open input: {error}") from error
        try:
            with stream:
                while len(captured) // channels < target_frames:
                    if cancel():
                        cancelled = True
                        break
                    block, _overflowed = stream.read(block_frames)
                    captured.frombytes(bytes(block))
                    blocks += 1
                if cancelled:
                    stream.abort()
        except DialogueError:
            raise
        except Exception as error:
            raise DialogueError("STREAM_UNAVAILABLE", f"capture failed: {error}") from error
        recorded = min(len(captured) // channels, target_frames)
        duration = recorded * 1000 // sample_rate
        self.recorded_ms += duration
        return CaptureResult(
            device=device.name,
            sample_rate=sample_rate,
            channels=channels,
            duration_ms=duration,
            blocks=blocks,
            cancelled=cancelled,
            samples=array("f", captured[: recorded * channels]),
        )


def wait_until_stopped(stop: threading.Event, timeout_ms: int) -> bool:
    """给真实播放/录音路径用的辅助等待（返回是否在超时前被置位）。"""
    return stop.wait(timeout_ms / 1000.0)


def sleep_ms(milliseconds: int) -> None:
    time.sleep(max(0, milliseconds) / 1000.0)


def channels_for(device: AudioDevice, direction: Direction, requested: int) -> int:
    """设备支持的通道数上限内的最小可用值，避免把 8 通道设备全打开。"""
    available = channel_count(device, direction)
    if available < requested:
        raise DialogueError(
            "DEVICE_UNAVAILABLE",
            f"{device.name!r} provides {available} {direction} channels, need {requested}",
        )
    return requested
