"""对话会话：把发言队列、半双工门控、TTS 播放与监听串成一个可计时的循环。

完成条件对应关系（`docs/TASKS.md` 第 3 节 T07）：

- **20 轮一对一交流有时间记录** → `run_conversation()` 为每一次动作写 `TurnRecord`
  （起止时间、时长、字符数、状态、原因码），报告里 `turn_count` 就是轮数。
- **播放能被本机停止立即取消** → 每次播放都带取消回调，回调在**一个音频块**内生效；
  被取消的回合记 `CANCELLED` 并带上真正播出的毫秒数。
- **暂停期间不积压旧发言** → `pause()` 同时清空待发队列（返回清掉几条）并把半双工
  切到 PAUSED；恢复后队列是空的，不会补播旧内容。
- **默认不保存真人语音** → 采集缓冲在转写后立刻 `discard()`，报告只带字符数与能量；
  本模块不写任何文件。
"""

from __future__ import annotations

import threading
import time
from typing import Annotated, Callable, Literal

from pydantic import Field, ValidationError

from vrc_agent.clock import Clock, SystemClock
from vrc_agent.contracts import ChatArgs, Contract, Identifier, NanoTime, new_id
from vrc_agent.dialogue import dsp
from vrc_agent.dialogue.asr import Asr, Transcript
from vrc_agent.dialogue.backend import AudioBackend, PlaybackResult
from vrc_agent.dialogue.devices import AudioDevice
from vrc_agent.dialogue.errors import DialogueError
from vrc_agent.dialogue.halfduplex import HalfDuplex
from vrc_agent.dialogue.queue import ChatQueue
from vrc_agent.dialogue.tts import MAX_UTTERANCE_MS, FakeTts, Tts

TurnAction = Literal["chatbox", "playback", "listen"]
TurnStatus = Literal["COMPLETED", "CANCELLED", "DROPPED", "SKIPPED"]
TurnDirection = Literal["out", "in"]


class TurnRecord(Contract):
    """一次动作的计时记录。**不含文本、不含音频**，只有长度、时长与原因码。"""

    turn_id: Identifier = Field(default_factory=new_id)
    index: Annotated[int, Field(ge=0)]
    direction: TurnDirection
    action: TurnAction
    status: TurnStatus
    reason_code: Annotated[str, Field(max_length=64)]
    started_at_ns: NanoTime
    ended_at_ns: NanoTime
    duration_ms: Annotated[int, Field(ge=0)]
    text_chars: Annotated[int, Field(ge=0)] = 0
    played_ms: Annotated[int, Field(ge=0)] = 0
    requested_ms: Annotated[int, Field(ge=0)] = 0


class DialogueReport(Contract):
    session_id: Identifier
    mode: Literal["offline", "live"]
    turn_count: Annotated[int, Field(ge=0)]
    records: tuple[TurnRecord, ...]
    total_ms: Annotated[int, Field(ge=0)]
    chat: dict[str, int | str | None]
    half_duplex: dict[str, int | str]
    audio: dict[str, int | str | bool]
    saves_media: bool = False
    media_policy: str = "no audio, transcript or text is written to disk"


class DialogueSession:
    """一次会话的状态容器；线程安全的部分只有「停止」这一个开关。"""

    def __init__(
        self,
        backend: AudioBackend,
        *,
        session_id: Identifier | None = None,
        clock: Clock | None = None,
        queue: ChatQueue | None = None,
        half_duplex: HalfDuplex | None = None,
        tts: Tts | None = None,
        asr: Asr | None = None,
        output_device: AudioDevice | None = None,
        input_device: AudioDevice | None = None,
        sample_rate: int = 48_000,
        channels: int = 1,
        block_ms: int = 20,
        max_utterance_ms: int = MAX_UTTERANCE_MS,
        mode: Literal["offline", "live"] = "offline",
        stop_requested: Callable[[], bool] | None = None,
        chat_sender: Callable[[str], str] | None = None,
        sink: object | None = None,
    ) -> None:
        self.backend = backend
        self.session_id = session_id or new_id()
        self.clock: Clock = clock or getattr(backend, "clock", None) or SystemClock()
        self.queue = queue or ChatQueue(session_id=self.session_id, clock=self.clock)
        self.half_duplex = half_duplex or HalfDuplex(clock=self.clock)
        self.tts: Tts = tts or FakeTts()
        self.asr: Asr | None = asr
        self.output_device = output_device
        self.input_device = input_device
        self.sample_rate = sample_rate
        self.channels = channels
        self.block_ms = block_ms
        self.max_utterance_ms = max_utterance_ms
        self.mode = mode
        self.stop_requested = stop_requested
        self.chat_sender = chat_sender
        self.sink = sink
        self._stopped = threading.Event()
        self.stop_reason: str | None = None
        self.dropped_on_pause = 0
        self.throttle_waits = 0
        self.throttle_wait_ms = 0
        self._records: list[TurnRecord] = []
        self.last_transcript: Transcript | None = None

    # ------------------------------------------------------------ 状态控制

    def _should_stop(self) -> bool:
        if self._stopped.is_set():
            return True
        return bool(self.stop_requested and self.stop_requested())

    def stop(self, reason: str = "MANUAL_STOP") -> int:
        """本机停止：立刻取消播放、清空待发，并把半双工切到终态。返回清掉的待发条数。"""
        self.stop_reason = reason
        self._stopped.set()
        dropped = self.queue.pause(reason)
        self.dropped_on_pause += dropped
        if self.half_duplex.state != "STOPPED":
            try:
                self.half_duplex.stop(reason)
            except DialogueError:
                pass
        return dropped

    def pause(self, reason: str = "PAUSED") -> int:
        dropped = self.queue.pause(reason)
        self.dropped_on_pause += dropped
        if self.half_duplex.state not in {"PAUSED", "STOPPED"}:
            self.half_duplex.pause(reason)
        return dropped

    def resume(self) -> None:
        if self.half_duplex.state == "STOPPED":
            raise DialogueError("PAUSED", "session was stopped; reset is required")
        self.queue.resume()
        if self.half_duplex.state == "PAUSED":
            self.half_duplex.resume()

    # ------------------------------------------------------------ 记录

    def _record(
        self,
        *,
        index: int,
        direction: TurnDirection,
        action: TurnAction,
        status: TurnStatus,
        reason_code: str,
        started_ns: int,
        text_chars: int = 0,
        played_ms: int = 0,
        requested_ms: int = 0,
    ) -> TurnRecord:
        ended = self.clock.now_ns()
        record = TurnRecord(
            index=index,
            direction=direction,
            action=action,
            status=status,
            reason_code=reason_code,
            started_at_ns=started_ns,
            ended_at_ns=ended,
            duration_ms=max(0, (ended - started_ns) // 1_000_000),
            text_chars=text_chars,
            played_ms=played_ms,
            requested_ms=requested_ms,
        )
        self._records.append(record)
        if self.sink is not None:
            # 只发白名单字段：动作、状态、原因码、字符数、时长。**没有文本、没有音频。**
            self.sink.emit(
                "dialogue_turn",
                operation=record.action,
                status=record.status,
                reason_code=record.reason_code,
                count=record.text_chars,
                value=record.duration_ms,
            )
        return record

    # ------------------------------------------------------------ 说话

    def say(
        self,
        text: str,
        *,
        index: int,
        source: Literal["model", "operator"] = "model",
        use_chatbox: bool | None = None,
        use_tts: bool = True,
    ) -> tuple[TurnRecord, ...]:
        """把一段回复发出去：先入队（可失败的明确通道），再按需发 chatbox、播放语音。

        两条通道的节流语义不同：**chatbox 受队列节流**（客户端对聊天框有频率限制），
        语音通道不受它限制，只受半双工与取消约束。轮到 chatbox 但还没到最小间隔时，
        会等到该发的时刻（不是直接丢），只有等到的内容已过期才记 `EXPIRED`。

        `use_chatbox=None` 表示「有发送器就发」：没配传输时不会为每条回复留下一条
        SKIPPED 记录。
        """
        if use_chatbox is None:
            use_chatbox = self.chat_sender is not None
        records: list[TurnRecord] = []
        started = self.clock.now_ns()
        expired_before = self.queue.dropped_expired
        if use_chatbox:
            try:
                self.queue.enqueue(text, source=source)
            except DialogueError as error:
                return (
                    self._record(
                        index=index,
                        direction="out",
                        action="chatbox",
                        status="DROPPED",
                        reason_code=error.failure,
                        started_ns=started,
                        text_chars=len(text),
                    ),
                )
            item = self.queue.take() or self._wait_for_slot()
            if item is None:
                reason = self.queue.pause_reason or (
                    "EXPIRED" if self.queue.dropped_expired > expired_before else "THROTTLED"
                )
                records.append(
                    self._record(
                        index=index,
                        direction="out",
                        action="chatbox",
                        status="DROPPED",
                        reason_code=reason,
                        started_ns=started,
                        text_chars=len(text),
                    )
                )
            else:
                records.append(self._send_chatbox(item.text, index=index))
        else:
            # 不发的通道也不要污染队列；但文本仍然要过同一套 chatbox 契约校验。
            try:
                ChatArgs(text=text)
            except ValidationError:
                return (
                    self._record(
                        index=index,
                        direction="out",
                        action="playback",
                        status="DROPPED",
                        reason_code="TEXT_INVALID",
                        started_ns=started,
                        text_chars=len(text),
                    ),
                )
        if use_tts:
            records.append(self._speak(text, index=index))
        return tuple(records)

    def _send_chatbox(self, text: str, *, index: int) -> TurnRecord:
        started = self.clock.now_ns()
        if self.chat_sender is None:
            return self._record(
                index=index,
                direction="out",
                action="chatbox",
                status="SKIPPED",
                reason_code="NO_TRANSPORT",
                started_ns=started,
                text_chars=len(text),
            )
        try:
            status = self.chat_sender(text)
        except Exception as error:  # 传输层失败不该带走整段会话
            status = f"ERROR:{type(error).__name__}"
        return self._record(
            index=index,
            direction="out",
            action="chatbox",
            status="COMPLETED" if status == "OK" else "DROPPED",
            reason_code=status,
            started_ns=started,
            text_chars=len(text),
        )

    def _wait_for_slot(self) -> object | None:
        """等到队列允许发送的那一刻再取一次；内容会先过期就不等。"""
        due = self.queue.next_due_ns()
        head = self.queue.peek()
        if due is None or head is None or head.expires_at_ns <= due:
            return None
        delay_ms = max(0, (due - self.clock.now_ns()) // 1_000_000)
        if delay_ms <= 0:
            return self.queue.take()
        self.throttle_waits += 1
        self.throttle_wait_ms += delay_ms
        advance = getattr(self.clock, "advance_ms", None)
        if callable(advance):
            advance(delay_ms)
        else:  # 真实时钟就真的等（上限 2 秒，避免把会话卡住）
            time.sleep(min(delay_ms, 2_000) / 1000.0)
        return self.queue.take()

    def _speak(self, text: str, *, index: int) -> TurnRecord:
        started = self.clock.now_ns()
        if self.output_device is None:
            return self._record(
                index=index,
                direction="out",
                action="playback",
                status="SKIPPED",
                reason_code="DEVICE_UNAVAILABLE",
                started_ns=started,
                text_chars=len(text),
            )
        try:
            utterance = self.tts.synthesize(
                text,
                sample_rate=self.sample_rate,
                channels=self.channels,
                max_duration_ms=self.max_utterance_ms,
            )
        except DialogueError as error:
            return self._record(
                index=index,
                direction="out",
                action="playback",
                status="DROPPED",
                reason_code=error.failure,
                started_ns=started,
                text_chars=len(text),
            )
        self.half_duplex.begin_speaking()
        try:
            result: PlaybackResult = self.backend.play(
                utterance.samples,
                sample_rate=self.sample_rate,
                channels=self.channels,
                device=self.output_device,
                cancel=self._should_stop,
                block_ms=self.block_ms,
            )
        except DialogueError as error:
            # 设备打不开/中途出错：如实记录失败，不让它带走整段会话。
            return self._record(
                index=index,
                direction="out",
                action="playback",
                status="DROPPED",
                reason_code=error.failure,
                started_ns=started,
                text_chars=utterance.text_chars,
                requested_ms=utterance.duration_ms,
            )
        finally:
            if self.half_duplex.state == "SPEAKING":
                self.half_duplex.finish_speaking()
        return self._record(
            index=index,
            direction="out",
            action="playback",
            status="CANCELLED" if result.cancelled else "COMPLETED",
            reason_code="CANCELLED" if result.cancelled else "OK",
            started_ns=started,
            text_chars=utterance.text_chars,
            played_ms=result.played_ms,
            requested_ms=result.requested_ms,
        )

    # ------------------------------------------------------------ 监听

    def listen(
        self, *, index: int, duration_ms: int = 300, speak_transcript: bool = False
    ) -> TurnRecord:
        """录一段并转写。播放中（或暂停/停止）一律门控：**不录音、不转写**。"""
        started = self.clock.now_ns()
        if not self.half_duplex.can_listen:
            blocks = max(1, duration_ms // self.block_ms)
            self.half_duplex.note_gated_capture(blocks)
            return self._record(
                index=index,
                direction="in",
                action="listen",
                status="SKIPPED",
                reason_code="MIC_GATED",
                started_ns=started,
            )
        if self.input_device is None or self.asr is None:
            return self._record(
                index=index,
                direction="in",
                action="listen",
                status="SKIPPED",
                reason_code="NO_ASR",
                started_ns=started,
            )
        try:
            capture = self.backend.record(
                sample_rate=self.sample_rate,
                channels=self.channels,
                device=self.input_device,
                duration_ms=duration_ms,
                cancel=self._should_stop,
                block_ms=self.block_ms,
            )
        except DialogueError as error:
            return self._record(
                index=index,
                direction="in",
                action="listen",
                status="DROPPED",
                reason_code=error.failure,
                started_ns=started,
            )
        blocks = capture.blocks
        self.half_duplex.note_capture_blocks(blocks)
        try:
            transcript = self.asr.transcribe(
                capture.samples, sample_rate=self.sample_rate, channels=self.channels
            )
        finally:
            capture.discard()  # 默认不保存真人语音：转写完立刻丢弃缓冲
        self.last_transcript = transcript
        reason = "OK" if transcript.is_speech else "SILENCE"
        if capture.cancelled:
            reason = "CANCELLED"
        return self._record(
            index=index,
            direction="in",
            action="listen",
            status="CANCELLED" if capture.cancelled else "COMPLETED",
            reason_code=reason,
            started_ns=started,
            text_chars=transcript.char_count,
        )

    # ------------------------------------------------------------ 20 轮循环

    def run_conversation(
        self,
        *,
        turns: int = 20,
        replies: tuple[str, ...] = (),
        listen_ms: int = 300,
        use_chatbox: bool | None = None,
        use_tts: bool = True,
    ) -> DialogueReport:
        """跑固定的多轮对话：每轮先听（可被门控），再回复。全部动作都有计时记录。

        `use_chatbox=None` 与 `say()` 一致：配了发送器才走 chatbox 通道。
        """
        if turns <= 0:
            raise ValueError("turns must be positive")
        if use_chatbox is None:
            use_chatbox = self.chat_sender is not None
        if self.half_duplex.state == "IDLE":
            self.half_duplex.begin_listening()
        started = self.clock.now_ns()
        for index in range(turns):
            self.listen(index=index, duration_ms=listen_ms)
            if self._should_stop():
                break
            reply = replies[index] if index < len(replies) else f"reply {index + 1}"
            self.say(reply, index=index, use_chatbox=use_chatbox, use_tts=use_tts)
            if self._should_stop():
                break
        return self.report(turn_count=turns, started_ns=started)

    # ------------------------------------------------------------ 报告

    def report(self, *, turn_count: int, started_ns: int | None = None) -> DialogueReport:
        started = (
            started_ns
            if started_ns is not None
            else (self._records[0].started_at_ns if self._records else self.clock.now_ns())
        )
        ended = self.clock.now_ns()
        playback = [record for record in self._records if record.action == "playback"]
        listens = [record for record in self._records if record.action == "listen"]
        # 按**记录**汇总，而不是读后端计数器：假后端没有计数器，真实后端的计数器也只覆盖
        # 最后一个流实例，直接读会得到 0 或漏掉前面几次播放。
        audio = {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "block_ms": self.block_ms,
            "played_ms": sum(record.played_ms for record in playback),
            "requested_ms": sum(record.requested_ms for record in playback),
            "listen_ms": sum(record.duration_ms for record in listens),
            "playback_records": len(playback),
            "listen_records": len(listens),
        }
        return DialogueReport(
            session_id=self.session_id,
            mode=self.mode,
            turn_count=turn_count,
            records=tuple(self._records),
            total_ms=max(0, (ended - started) // 1_000_000),
            chat=self.queue.stats(),
            half_duplex=self.half_duplex.stats(),
            audio=audio,
        )

    def stats(self) -> dict[str, object]:
        return {
            "records": len(self._records),
            "stopped": self._stopped.is_set(),
            "stop_reason": self.stop_reason,
            "dropped_on_pause": self.dropped_on_pause,
            "throttle_waits": self.throttle_waits,
            "throttle_wait_ms": self.throttle_wait_ms,
            "chat": self.queue.stats(),
            "half_duplex": self.half_duplex.stats(),
        }


def discard_free(samples: "array") -> None:  # noqa: F821 - 便捷函数，供调用方显式清缓冲
    """显式丢弃一段采样（`array("f")`），用于「默认不保存真人语音」的自检。"""
    del samples[:]
    assert dsp.frames_of(samples, 1) == 0
