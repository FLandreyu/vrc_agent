import socket
import time

import pytest
from pythonosc.osc_message import OscMessage

from vrc_agent.config import AvatarParameter, OscConfig
from vrc_agent.contracts import ChatArgs, MotionArgs
from vrc_agent.transport.osc import AXES, OscTransport, RecordingTransport, encode_message


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_dry_run_never_creates_socket(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run created a network socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    transport = RecordingTransport()
    transport.motion(MotionArgs(forward=0.5, duration_ms=100))
    transport.neutralize()
    assert len(transport.messages) == 6


def test_real_loopback_packets_and_type_tags():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sink:
        sink.bind(("127.0.0.1", 0))
        sink.settimeout(1)
        transport = OscTransport(
            OscConfig(send_port=sink.getsockname()[1], receive_port=free_port())
        )
        try:
            transport.motion(MotionArgs(forward=0.5, duration_ms=100))
            for address in AXES:
                raw = sink.recv(8192)
                message = OscMessage(raw)
                assert message.address == address
                assert b",f\x00" in raw
                assert type(message.params[0]) is float
            transport.button("Jump", True)
            raw = sink.recv(8192)
            assert b",i\x00" in raw and OscMessage(raw).params == [1]
            transport.chat(ChatArgs(text="测试"))
            raw = sink.recv(8192)
            assert b",sTF\x00" in raw
            assert OscMessage(raw).params == ["测试", True, False]
            transport.neutralize()
            packets = [OscMessage(sink.recv(8192)) for _ in range(4)]
            assert all(p.params == [0.0] for p in packets[:3])
            assert packets[3].params == [0]
        finally:
            transport.close()


def test_receive_allowlist_malformed_packets_and_exclusive_port():
    config = OscConfig(
        send_port=free_port(),
        receive_port=free_port(),
        avatar_parameters={
            "Happy": AvatarParameter(type="bool", reset_value=False),
        },
    )
    transport = OscTransport(config)
    try:
        with pytest.raises(OSError):
            OscTransport(config)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as producer:
            for packet in [
                b"garbage",
                encode_message("/avatar/parameters/Unknown", (True,)),
                encode_message("/avatar/parameters/Happy", (1,)),
                encode_message("/avatar/parameters/Happy", (True,)),
                encode_message("/avatar/change", ("avtr_test",)),
            ]:
                producer.sendto(packet, (config.host, config.receive_port))
        events = []
        deadline = time.monotonic() + 1
        while len(events) < 2 and time.monotonic() < deadline:
            events.extend(transport.poll())
            time.sleep(0.01)
        assert len(events) == 2
        assert events[0]["name"] == "Happy"
        assert events[0]["sample"]["value"] is True
        assert events[0]["sample"]["received_at_ns"] > 0
        assert events[1] == {"kind": "avatar_change"}
    finally:
        transport.close()


def test_avatar_reset_and_exact_wire_type():
    config = OscConfig(avatar_parameters={"Smile": AvatarParameter(type="float", reset_value=0.0)})
    transport = RecordingTransport(config)
    with pytest.raises(ValueError):
        transport.avatar("Smile", 1)
    transport.avatar("Smile", 0.5)
    transport.neutralize()
    assert list(transport.messages)[-1] == ("/avatar/parameters/Smile", (0.0,))
