"""JPEG 只改变上传字节；原始帧证明、截止期与控制归零不变。"""

import asyncio
import base64
import json
from io import BytesIO

import pytest
from PIL import Image
from test_stability_v5_runtime import PARTIAL, assemble
from test_stable_navigation import MemoryClient

from vrc_agent.clock import FakeClock
from vrc_agent.live_navigation import stable_runtime
from vrc_agent.live_navigation.stability_report import validate_stability_report
from vrc_agent.providers.images import encode_bgra
from vrc_agent.transport.osc import AXES


def jpeg_runtime(**kwargs):
    clock = FakeClock()
    client = MemoryClient(clock)
    client.config = client.config.model_copy(
        update={
            "perception": client.config.perception.model_copy(
                update={"image_format": "jpeg", "jpeg_quality": 85}
            )
        }
    )
    runtime, _, calls = assemble(client, clock, **kwargs)
    return runtime, client, calls


def assert_released(client):
    assert client.core.active is None
    last = dict(client.transport.messages)
    if last:  # 未动过时无归零报文；动过则要求所有轴确实有归零证据。
        assert all(last[axis] == (0.0,) for axis in AXES)


def test_jpeg_upload_uses_original_bgra_for_stability(monkeypatch):
    runtime, client, calls = jpeg_runtime()
    inputs, uploads = [], []

    def encode(data, size, **options):
        inputs.append(data)
        assert options == {"image_format": "jpeg", "jpeg_quality": 85}
        return encode_bgra(data, size, **options)

    monkeypatch.setattr(stable_runtime, "encode_bgra", encode)
    provider = runtime.perception.provider
    original = provider._transport

    def http(request):
        url = json.loads(request.body)["messages"][1]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        data = base64.b64decode(url.split(",", 1)[1], validate=True)
        with Image.open(BytesIO(data)) as image:
            assert image.format == "JPEG" and image.size == runtime.world.frame_size
        uploads.append(data)
        return original(request)

    provider._transport = http
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "SUCCESS" and len(calls) == len(uploads) == len(inputs) >= 2
    assert runtime.reference[1].data is inputs[-1]  # 证明引用的是编码前原帧。
    assert len(inputs[-1]) == 2160 * 1440 * 4 and not inputs[-1].startswith(b"\xff\xd8")
    assert report["stability_policy"]["inference_ms"] == 8000
    assert all(p["stable"] for p in report["stability_proofs"])
    validate_stability_report(report)
    assert client.core.state == "PAUSED"
    assert_released(client)


@pytest.mark.parametrize(
    "fault,outcome",
    [
        ("timeout", "INFERENCE_TIMEOUT"),
        ("estop", "MANUAL_STOP"),
    ],
)
def test_jpeg_inference_failure_has_no_motion(fault, outcome):
    runtime, client, calls = jpeg_runtime(fault=fault)
    report = asyncio.run(runtime.run())
    assert report["outcome"] == outcome and not report["actions"] and len(calls) == 1
    assert_released(client)


@pytest.mark.parametrize(
    "fault,outcome",
    [
        ("timeout", "IMAGE_ENCODING_TIMEOUT"),
        ("estop", "MANUAL_STOP"),
    ],
)
def test_jpeg_encoding_timeout_or_estop_prevents_model_call(monkeypatch, fault, outcome):
    runtime, client, calls = jpeg_runtime()

    def interrupted(*args, **kwargs):
        if fault == "timeout":
            client.advance(1001)
        else:
            client.core.stop("MANUAL_STOP", emergency=True)
        return b"late synthetic image", "image/jpeg"

    monkeypatch.setattr(stable_runtime, "encode_bgra", interrupted)
    report = asyncio.run(runtime.run())
    assert report["outcome"] == outcome and not calls and not report["actions"]
    assert_released(client)


def test_jpeg_does_not_hide_single_pixel_protected_change():
    runtime, client, _ = jpeg_runtime(box=PARTIAL, change="visible")
    report = asyncio.run(runtime.run())
    assert report["outcome"] == "SCENE_UNSTABLE" and not report["actions"]
    assert report["stability_proofs"][0]["protected_changed"] == 1
    assert_released(client)
