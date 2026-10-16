"""语音识别（ASR）：一个协议、一个确定性离线实现、一个基于能量的语音门。

真实识别引擎（Whisper、Vosk、Windows 语音等）不在代码里安装：属于验证记录里的
「用户可执行步骤」。这里提供的是接口，以及一个即使没有识别引擎也能用的东西——
**能量门**：判断一段采样里到底有没有人在说话，用于半双工与「暂停期间不积压」。

隐私：转写文本只在内存里传递，`Transcript` 的 `summary()` 只输出字符数；本模块不写盘。
"""

from __future__ import annotations

import hashlib
from array import array
from dataclasses import dataclass
from typing import Protocol

from vrc_agent.dialogue import dsp


@dataclass(frozen=True, slots=True)
class Transcript:
    """一次识别的结果；`confidence=None` 表示引擎没给置信度（不是 0）。"""

    text: str
    confidence: float | None
    duration_ms: int
    is_speech: bool
    rms: float
    source: str

    @property
    def char_count(self) -> int:
        return len(self.text)

    def summary(self) -> dict[str, int | float | bool | str | None]:
        """脱敏摘要：**不含文本**，只含长度、时长、能量与来源。"""
        return {
            "chars": self.char_count,
            "confidence": self.confidence,
            "duration_ms": self.duration_ms,
            "is_speech": self.is_speech,
            "rms": round(self.rms, 6),
            "source": self.source,
        }


class Asr(Protocol):
    name: str

    def transcribe(self, samples: array, *, sample_rate: int, channels: int) -> Transcript: ...


class EnergyVad:
    """基于 RMS 的语音门：不需要识别引擎，只回答「有没有声音」。"""

    def __init__(self, *, threshold: float = 0.01) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold must be in (0, 1]")
        self.threshold = threshold

    def is_speech(self, samples: array) -> bool:
        return dsp.rms(samples) >= self.threshold


class QuietAsr:
    """只做能量判断的「识别器」：有声音就返回占位文本，没声音就返回空。

    这不是识别，只是让**没有识别引擎时**的链路仍有明确语义：
    `is_speech=False` 时下游不得凭空编出内容。
    """

    name = "energy-gate"

    def __init__(self, *, threshold: float = 0.01, placeholder: str = "[voice]") -> None:
        self.vad = EnergyVad(threshold=threshold)
        self.placeholder = placeholder

    def transcribe(self, samples: array, *, sample_rate: int, channels: int) -> Transcript:
        energy = dsp.rms(samples)
        speech = energy >= self.vad.threshold if samples else False
        return Transcript(
            text=self.placeholder if speech else "",
            confidence=None,
            duration_ms=dsp.duration_ms_of(samples, sample_rate, channels),
            is_speech=speech,
            rms=energy,
            source=self.name,
        )


class FakeAsr:
    """确定性离线识别：按脚本返回转写，用于 20 轮对话与隐私测试。"""

    name = "fake-script"

    def __init__(self, script: tuple[str, ...] = (), *, rms: float = 0.2) -> None:
        self.script = list(script)
        self.rms = rms
        self.calls = 0

    def transcribe(self, samples: array, *, sample_rate: int, channels: int) -> Transcript:
        self.calls += 1
        text = self.script.pop(0) if self.script else ""
        return Transcript(
            text=text,
            confidence=0.9 if text else 0.0,
            duration_ms=dsp.duration_ms_of(samples, sample_rate, channels),
            is_speech=bool(text),
            rms=self.rms if text else 0.0,
            source=self.name,
        )


def text_digest(text: str, length: int = 12) -> str:
    """转写文本的摘要（用于对齐两次运行是不是同一句），**不是**可逆的存储。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]
