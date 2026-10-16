"""唯一 OSC 映射层；dry-run 不创建 socket，live 仅连接 IPv4 loopback。"""

import socket
import time
from collections import deque

from pythonosc.osc_message import OscMessage, ParseError
from pythonosc.osc_message_builder import OscMessageBuilder

from vrc_agent.config import OscConfig
from vrc_agent.contracts import ChatArgs, MotionArgs, ParameterSample

AXES = ("/input/Vertical", "/input/Horizontal", "/input/LookHorizontal")
# UseRight / GrabRight / DropRight 为 T00 桌面交互实测新增；有效性由探针实测定，不预设结论。
BUTTONS = frozenset({"Jump", "Run", "UseRight", "GrabRight", "DropRight"})


def encode_message(address: str, values: tuple) -> bytes:
    builder = OscMessageBuilder(address=address)
    for value in values:
        if type(value) is bool:
            tag = "T" if value else "F"
        elif type(value) is int:
            tag = "i"
        elif type(value) is float:
            tag = "f"
        elif type(value) is str:
            tag = "s"
        else:
            raise TypeError("unsupported OSC type")
        builder.add_arg(value, tag)
    return builder.build().dgram


class MappedTransport:
    def __init__(self, config: OscConfig):
        self.config = config
        self.motion_touched = False
        self.buttons: set[str] = set()
        self.avatar_touched: set[str] = set()

    def send(self, address: str, values: tuple) -> None:
        raise NotImplementedError

    def motion(self, args: MotionArgs) -> None:
        self.motion_touched = True  # 在第一次发送之前记录，部分失败也能释放。
        for address, value in zip(AXES, (args.forward, args.strafe, args.yaw), strict=True):
            self.send(address, (float(value),))

    def button(self, name: str, pressed: bool) -> None:
        if name not in BUTTONS or type(pressed) is not bool:
            raise ValueError("unsupported button")
        if pressed:
            self.buttons.add(name)
        self.send(f"/input/{name}", (int(pressed),))
        # 释放后仍跟踪，急停可重复释放，不受 UDP 无确认影响。

    def chat(self, args: ChatArgs) -> None:
        self.send("/chatbox/input", (args.text, True, False))

    def avatar(self, name: str, value: bool | int | float) -> None:
        spec = self.config.avatar_parameters.get(name)
        if spec is None or not spec.accepts(value):
            raise ValueError("avatar parameter is not allowlisted or has invalid value")
        self.avatar_touched.add(name)
        self.send(f"/avatar/parameters/{name}", (value,))

    def _best_effort(self, messages: list[tuple[str, tuple]]) -> None:
        errors = []
        for address, args in messages:
            try:
                self.send(address, args)
            except OSError as exc:
                errors.append(exc)
        if errors:
            raise OSError("one or more release messages failed") from errors[0]

    def release_motion(self) -> None:
        if self.motion_touched:
            self._best_effort([(a, (0.0,)) for a in AXES])

    def neutralize(self) -> None:
        messages = [(a, (0.0,)) for a in AXES] if self.motion_touched else []
        messages += [(f"/input/{name}", (0,)) for name in sorted(self.buttons)]
        messages += [
            (f"/avatar/parameters/{name}", (self.config.avatar_parameters[name].reset_value,))
            for name in sorted(self.avatar_touched)
        ]
        self._best_effort(messages)

    def poll(self) -> list[dict]:
        return []

    def close(self) -> None:
        pass


class RecordingTransport(MappedTransport):
    def __init__(self, config: OscConfig | None = None):
        super().__init__(config or OscConfig())
        self.messages: deque[tuple[str, tuple]] = deque(maxlen=1024)
        self.sent_count = 0

    def send(self, address: str, values: tuple) -> None:
        self.messages.append((address, values))
        self.sent_count += 1


class OscTransport(MappedTransport):
    def __init__(self, config: OscConfig):
        super().__init__(config)
        self.receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.receiver.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self.receiver.bind((config.host, config.receive_port))
            self.receiver.setblocking(False)
            self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sender.setblocking(False)
        except BaseException:
            self.receiver.close()
            raise

    def send(self, address: str, values: tuple) -> None:
        self.sender.sendto(
            encode_message(address, values), (self.config.host, self.config.send_port)
        )

    def poll(self) -> list[dict]:
        events = []
        for _ in range(32):  # 限制每个 tick 的入站工作量。
            try:
                packet, source = self.receiver.recvfrom(65535)
            except BlockingIOError:
                break
            if source[0] != "127.0.0.1" or len(packet) > 8192:
                continue
            try:
                message = OscMessage(packet)
                if message.address == "/avatar/change" and len(message.params) == 1:
                    if type(message.params[0]) is str:
                        events.append({"kind": "avatar_change"})
                    continue
                prefix = "/avatar/parameters/"
                if not message.address.startswith(prefix) or len(message.params) != 1:
                    continue
                name = message.address[len(prefix) :]
                spec = self.config.avatar_parameters.get(name)
                value = message.params[0]
                if spec is not None and spec.accepts(value):
                    sample = ParameterSample(value=value, received_at_ns=time.monotonic_ns())
                    events.append(
                        {
                            "kind": "parameter",
                            "name": name,
                            "sample": sample.model_dump(mode="json"),
                        }
                    )
            except (ParseError, ValueError, TypeError, IndexError):
                continue
        return events

    def close(self) -> None:
        self.receiver.close()
        self.sender.close()
