"""图像真实解码与请求体检查；不访问服务或读取密钥。"""

import base64
import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, JpegImagePlugin
from pydantic import ValidationError

from vrc_agent.config import PerceptionConfig, load_config
from vrc_agent.providers import factory, images
from vrc_agent.providers.base import Deadline, ModelInput
from vrc_agent.providers.openai_compat import OpenAICompatibleProvider


def frame_input(data, size, media="application/x-bgra"):
    return ModelInput(
        prompt="synthetic",
        observation_id="frame-1",
        request_id="request-1",
        image=data,
        image_size=size,
        image_media_type=media,
    )


def image_part(provider, model_input):
    request, count = provider._build_request(model_input, Deadline.unlimited())
    parts = json.loads(request.body)["messages"][1]["content"]
    assert count == 1 and "frame-1" in parts[0]["text"]
    prefix, encoded = parts[1]["image_url"]["url"].split(",", 1)
    return prefix, base64.b64decode(encoded, validate=True)


def test_jpeg_rgb_dimensions_alpha_quality_and_metadata():
    # 非灰色可区分 R/B；alpha=0 仍保留 RGB，不能合成黑色或透明背景。
    raw = bytes([17, 83, 201, 0]) * (32 * 16)
    encoded = images.bgra_jpeg(raw, (32, 16))
    assert encoded == images.bgra_jpeg(bytes([17, 83, 201, 255]) * (32 * 16), (32, 16))
    with Image.open(BytesIO(encoded)) as decoded:
        assert decoded.format == "JPEG" and decoded.mode == "RGB" and decoded.size == (32, 16)
        assert all(abs(a - b) <= 3 for a, b in zip(decoded.getpixel((10, 10)), (201, 83, 17)))
        assert not decoded.getexif() and not decoded.info.get("icc_profile")
        assert not decoded.info.get("comment") and not decoded.info.get("progressive")
        assert JpegImagePlugin.get_sampling(decoded) == 2
        with Image.open(BytesIO(images.bgra_jpeg(raw, (32, 16), quality=25))) as low:
            assert decoded.quantization != low.quantization


@pytest.mark.parametrize("quality", [0, 96, True, 85.0, "85", None])
def test_quality_rejected_at_config_provider_and_encoder(quality):
    with pytest.raises(ValidationError):
        PerceptionConfig(jpeg_quality=quality)
    with pytest.raises(ValueError, match="jpeg_quality"):
        OpenAICompatibleProvider(model="test", api_key="synthetic", jpeg_quality=quality)
    with pytest.raises(ValueError, match="jpeg_quality"):
        images.bgra_jpeg(b"\0" * 4, (1, 1), quality=quality)


@pytest.mark.parametrize(
    "size,data",
    [
        ((0, 1), b""),
        ((1, 0), b""),
        ((-1, 1), b""),
        ((True, 1), b"\0" * 4),
        ((1.0, 1), b"\0" * 4),
        ((2, 1), b"\0" * 4),
    ],
)
def test_jpeg_rejects_invalid_dimensions(size, data):
    with pytest.raises(ValueError, match="dimensions"):
        images.bgra_jpeg(data, size)


@pytest.mark.parametrize("fmt", ["jpg", "JPEG", "webp", "", None])
def test_format_is_explicit_and_closed(fmt):
    with pytest.raises(ValidationError):
        PerceptionConfig(image_format=fmt)
    with pytest.raises(ValueError, match="image_format"):
        images.encode_bgra(b"\0" * 4, (1, 1), image_format=fmt)
    with pytest.raises(ValueError, match="image_format"):
        OpenAICompatibleProvider(model="test", api_key="synthetic", image_format=fmt)


@pytest.mark.parametrize("fmt,mime", [("png", "image/png"), ("jpeg", "image/jpeg")])
def test_factory_and_raw_request_use_selected_format(fmt, mime):
    config = PerceptionConfig(
        provider="openai_compat",
        model="test",
        budget_usd=1.0,
        send_images=True,
        image_format=fmt,
        jpeg_quality=67,
    )
    provider = factory.build_provider(config, environ={config.api_key_env: "synthetic"})
    raw = bytes([17, 83, 201, 0]) * (8 * 8)
    prefix, data = image_part(provider, frame_input(raw, (8, 8)))
    expected, _ = images.encode_bgra(raw, (8, 8), image_format=fmt, jpeg_quality=67)
    assert prefix == f"data:{mime};base64" and data == expected
    with Image.open(BytesIO(data)) as decoded:
        assert decoded.format == fmt.upper() and decoded.size == (8, 8)
    info = factory.describe_provider(config, environ={})
    assert info["image_format"] == fmt and info["jpeg_quality"] == 67


def test_actual_live_config_selects_jpeg_and_default_compatibility():
    config = load_config(Path(__file__).parents[2] / "configs/navigation-live.toml")
    assert config.mode == "dry-run" and config.navigation_design == "stable-inference-v9"
    assert config.perception.image_format == "jpeg" and config.perception.jpeg_quality == 85
    assert PerceptionConfig().image_format == "png"  # 旧配置不被静默改成有损。


def test_preencoded_jpeg_passes_through_and_no_consent_skips_encoding(monkeypatch):
    encoded = images.bgra_jpeg(bytes([17, 83, 201, 0]) * 64, (8, 8))
    provider = OpenAICompatibleProvider(model="test", api_key="synthetic", send_images=True)

    def forbidden(*args, **kwargs):
        pytest.fail("must not encode preencoded bytes or unconsented raw frames")

    monkeypatch.setattr("vrc_agent.providers.openai_compat.encode_bgra", forbidden)
    prefix, actual = image_part(provider, frame_input(encoded, (8, 8), "image/jpeg"))
    assert prefix == "data:image/jpeg;base64" and actual == encoded
    provider = OpenAICompatibleProvider(
        model="test",
        api_key="synthetic",
        send_images=False,
        image_format="jpeg",
    )
    request, count = provider._build_request(frame_input(b"\0" * 4, (1, 1)), Deadline.unlimited())
    assert count == 0 and b"image_url" not in request.body


def test_encoder_failure_has_no_png_fallback(monkeypatch):
    def failed(*args, **kwargs):
        raise OSError("synthetic encoder failure")

    monkeypatch.setattr(images, "bgra_jpeg", failed)
    with pytest.raises(OSError, match="synthetic encoder failure"):
        images.encode_bgra(b"\0" * 4, (1, 1), image_format="jpeg")
