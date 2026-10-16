"""语音合成（TTS）：一个协议加一个确定性的离线实现。

真实引擎（Windows SAPI、Piper、Edge TTS 等）不在代码里安装或下载模型：它们属于
`docs/validation/T07_dialogue.md` 里记录的「用户可执行步骤」。这里保证的是**接口**：
合成结果只在内存里，有明确时长上限，并且可以被取消。
"""

from __future__ import annotations

import hashlib
from array import array
from dataclasses import dataclass
from typing import Protocol

from vrc_agent.dialogue import dsp
from vrc_agent.dialogue.errors import DialogueError

#: 单条发言的播放时长上限（毫秒）。超过就截断，避免一段语音长时间占住播放通道。
MAX_UTTERANCE_MS = 15_000


@dataclass(frozen=True, slots=True)
class Utterance:
    """一段待播放的合成语音。只带字符数，不携带原文，便于日志与报告脱敏。"""

    text_chars: int
    samples: array
    sample_rate: int
    channels: int
    duration_ms: int
    tone_hz: float | None = None

    def summary(self) -> dict[str, int | float | None]:
        return {
            "text_chars": self.text_chars,
            "duration_ms": self.duration_ms,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "tone_hz": self.tone_hz,
        }


class Tts(Protocol):
    name: str

    def synthesize(
        self, text: str, *, sample_rate: int, channels: int, max_duration_ms: int = MAX_UTTERANCE_MS
    ) -> Utterance: ...


class FakeTts:
    """确定性离线合成：用文本摘要挑一个音高，时长与字符数成正比。

    它**不是**语音，只用来把「队列 → 合成 → 播放 → 取消」这条链路跑通并计时。
    """

    name = "fake-tone"

    def __init__(
        self, *, amplitude: float = 0.2, ms_per_char: int = 45, base_ms: int = 120
    ) -> None:
        if not 0.0 < amplitude <= 1.0:
            raise ValueError("amplitude must be in (0, 1]")
        if ms_per_char <= 0 or base_ms < 0:
            raise ValueError("invalid duration model")
        self.amplitude = amplitude
        self.ms_per_char = ms_per_char
        self.base_ms = base_ms

    def plan(self, text: str, max_duration_ms: int) -> tuple[float, int]:
        # 用文本摘要而不是随机数挑音高：同一句话每次都得到同一段音频。
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        tone_hz = 300.0 + int(digest[:6], 16) % 600  # 300–900 Hz，确定性
        duration_ms = min(max_duration_ms, self.base_ms + self.ms_per_char * len(text))
        return tone_hz, max(1, duration_ms)

    def synthesize(
        self, text: str, *, sample_rate: int, channels: int, max_duration_ms: int = MAX_UTTERANCE_MS
    ) -> Utterance:
        if not text:
            raise DialogueError("TEXT_INVALID", "cannot synthesize empty text")
        if max_duration_ms <= 0:
            raise DialogueError("TEXT_INVALID", "max_duration_ms must be positive")
        tone_hz, duration_ms = self.plan(text, max_duration_ms)
        samples = dsp.sine_tone(
            tone_hz, duration_ms, sample_rate, channels=channels, amplitude=self.amplitude
        )
        return Utterance(
            text_chars=len(text),
            samples=samples,
            sample_rate=sample_rate,
            channels=channels,
            duration_ms=duration_ms,
            tone_hz=tone_hz,
        )
