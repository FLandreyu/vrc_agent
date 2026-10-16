"""T07 交流：聊天框队列、半双工门控、TTS 播放与自声回流诊断。

分层：

- `queue.py` / `halfduplex.py`：纯逻辑，可用假时钟做确定性测试；
- `dsp.py` / `devices.py`：设备无关的信号处理与设备选择；
- `backend.py`：真实（sounddevice/PortAudio）与离线假后端；
- `tts.py` / `asr.py` / `echo.py`：合成、识别与回流诊断；
- `session.py`：把上面这些串成一个可计时、可取消的会话循环。

隐私默认值：音频只在内存里，任何模块都不写文件；报告里只有长度、时长、能量和原因码。
"""

from vrc_agent.dialogue.asr import Asr, EnergyVad, FakeAsr, QuietAsr, Transcript
from vrc_agent.dialogue.backend import (
    AudioBackend,
    CaptureResult,
    FakeBackend,
    PlaybackResult,
    SoundDeviceBackend,
    default_fake_devices,
)
from vrc_agent.dialogue.chatbox import (
    ControllerChatSender,
    RecordingChatSender,
    SteppedControllerClock,
)
from vrc_agent.dialogue.devices import (
    AudioDevice,
    describe_devices,
    match_devices,
    select_device,
    virtual_mic_hint,
)
from vrc_agent.dialogue.echo import EchoReport, analyse, run_echo_check
from vrc_agent.dialogue.errors import DIALOGUE_FAILURES, DialogueError, DialogueFailure
from vrc_agent.dialogue.halfduplex import HalfDuplex, HalfDuplexState, HalfDuplexTransition
from vrc_agent.dialogue.queue import ChatItem, ChatQueue
from vrc_agent.dialogue.session import DialogueReport, DialogueSession, TurnRecord
from vrc_agent.dialogue.tts import MAX_UTTERANCE_MS, FakeTts, Tts, Utterance

__all__ = [
    "DIALOGUE_FAILURES",
    "MAX_UTTERANCE_MS",
    "Asr",
    "AudioBackend",
    "AudioDevice",
    "CaptureResult",
    "ChatItem",
    "ChatQueue",
    "ControllerChatSender",
    "DialogueError",
    "DialogueFailure",
    "DialogueReport",
    "DialogueSession",
    "EchoReport",
    "EnergyVad",
    "FakeAsr",
    "FakeBackend",
    "FakeTts",
    "HalfDuplex",
    "HalfDuplexState",
    "HalfDuplexTransition",
    "PlaybackResult",
    "QuietAsr",
    "RecordingChatSender",
    "SoundDeviceBackend",
    "SteppedControllerClock",
    "Transcript",
    "Tts",
    "TurnRecord",
    "Utterance",
    "analyse",
    "default_fake_devices",
    "describe_devices",
    "match_devices",
    "run_echo_check",
    "select_device",
    "virtual_mic_hint",
]
