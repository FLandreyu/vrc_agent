"""配置 → 适配器：默认离线、付费要显式配预算、密钥只进内存。

这里不发明任何网络：假模型的路径全部在本地跑完，真实供应商只验证「有没有配好」。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import ValidationError

from vrc_agent.clock import FakeClock
from vrc_agent.config import PerceptionConfig
from vrc_agent.providers.base import CancelToken, Deadline, ModelInput
from vrc_agent.providers.errors import ProviderError
from vrc_agent.providers.factory import (
    PAID_PROVIDERS,
    PROVIDER_NAMES,
    api_key_present,
    build_ledger,
    build_provider,
    describe_provider,
    provider_is_paid,
)
from vrc_agent.providers.fake import FakeSceneProvider
from vrc_agent.providers.openai_compat import OpenAICompatibleProvider

SECRET = "sk-live-secret-that-must-never-be-printed"
ENV_NAME = "OPENAI_API_KEY"


def model_input() -> ModelInput:
    return ModelInput(
        prompt="describe this frame",
        observation_id="obs-1",
        request_id="req-1",
        created_at_ns=5_000_000_000,
    )


# --------------------------------------------------------------------------- 配置校验


def test_the_default_configuration_is_the_offline_free_fake() -> None:
    config = PerceptionConfig()

    assert config.provider == "fake"
    assert config.budget_usd == 0.0
    assert config.send_images is False
    assert config.base_url.startswith("https://")
    assert provider_is_paid(config) is False


def test_a_paid_provider_requires_a_budget_at_load_time() -> None:
    with pytest.raises(ValidationError, match="budget"):
        PerceptionConfig(provider="openai_compat")


def test_a_paid_provider_accepts_a_budget() -> None:
    config = PerceptionConfig(provider="openai_compat", budget_usd=2.5)

    assert provider_is_paid(config) is True
    assert build_ledger(config).budget_usd == 2.5


def test_plain_http_is_only_allowed_for_loopback() -> None:
    with pytest.raises(ValidationError, match="loopback"):
        PerceptionConfig(
            provider="openai_compat", budget_usd=1.0, base_url="http://api.example.com/v1"
        )

    config = PerceptionConfig(
        provider="openai_compat", budget_usd=1.0, base_url="http://127.0.0.1:8080/v1"
    )
    assert config.base_url == "http://127.0.0.1:8080/v1"


def test_prices_must_be_configured_together() -> None:
    with pytest.raises(ValidationError, match="together"):
        PerceptionConfig(price_input_per_mtok_usd=3.0)

    config = PerceptionConfig(
        price_input_per_mtok_usd=3.0,
        price_output_per_mtok_usd=15.0,
        price_as_of="2026-01-01",
    )
    assert config.price_output_per_mtok_usd == 15.0


def test_a_price_table_without_a_date_is_rejected() -> None:
    # 没有生效日期的价格不算价：配置阶段就失败，运行期就不会出现来路不明的金额。
    with pytest.raises(ValidationError, match="price_as_of"):
        PerceptionConfig(price_input_per_mtok_usd=3.0, price_output_per_mtok_usd=15.0)
    with pytest.raises(ValidationError, match="real date"):
        PerceptionConfig(
            price_input_per_mtok_usd=3.0,
            price_output_per_mtok_usd=15.0,
            price_as_of="2026-13-40",
        )


@pytest.mark.parametrize("provider", ["fake", "openai_compat"])
def test_a_misspelt_fake_behaviour_is_rejected_whatever_the_provider(provider: str) -> None:
    changes: dict[str, Any] = {"provider": provider, "fake_behaviour": "tmeout"}
    if provider != "fake":
        changes["budget_usd"] = 1.0

    with pytest.raises(ValidationError, match="fake behaviour"):
        PerceptionConfig(**changes)


def test_an_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PerceptionConfig(provider="anthropic")


# --------------------------------------------------------------------------- 构造适配器


def test_the_fake_provider_is_built_from_configuration_and_stays_free() -> None:
    config = PerceptionConfig(model="fake-scene-v9")
    ledger = build_ledger(config)

    provider = build_provider(config, ledger=ledger, clock=FakeClock(0))

    assert isinstance(provider, FakeSceneProvider)
    assert provider.name == "fake"
    assert provider.paid is False
    assert provider.model == "fake-scene-v9"
    response = asyncio.run(provider.infer(model_input(), deadline=Deadline.unlimited()))
    assert response.provider == "fake"
    assert response.usage.cost_usd == 0.0  # 确实是 0：假模型不调用任何付费接口
    assert response.usage.image_count == 0
    assert ledger.calls == 1  # 出厂接线：假模型的调用也要进台账
    assert ledger.unknown_cost_calls == 0
    assert ledger.cost_complete is True


def test_fake_behaviour_from_configuration_reaches_the_provider() -> None:
    config = PerceptionConfig(fake_behaviour="timeout")
    provider = build_provider(config, clock=FakeClock(0))

    with pytest.raises(ProviderError) as info:
        asyncio.run(provider.infer(model_input(), deadline=Deadline.unlimited()))

    assert info.value.failure == "TIMEOUT"


def test_a_real_provider_without_a_key_fails_fast_and_says_which_variable() -> None:
    config = PerceptionConfig(provider="openai_compat", budget_usd=1.0)

    with pytest.raises(ProviderError) as info:
        build_provider(config, environ={})

    assert info.value.failure == "NOT_CONFIGURED"
    assert ENV_NAME in info.value.detail
    assert SECRET not in str(info.value)


def test_a_real_provider_is_built_from_configuration_without_echoing_the_key() -> None:
    config = PerceptionConfig(
        provider="openai_compat",
        model="vision-real-1",
        budget_usd=3.0,
        base_url="https://vision.example.com/v1/",
        timeout_ms=5000,
        max_retries=0,
        send_images=False,
    )

    provider = build_provider(config, environ={ENV_NAME: SECRET}, ledger=build_ledger(config))

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.paid is True
    assert provider.model == "vision-real-1"
    assert provider.endpoint == "https://vision.example.com/v1/chat/completions"  # 尾斜杠不会翻倍
    assert SECRET not in repr(provider)
    assert SECRET not in json.dumps(describe_provider(config, environ={ENV_NAME: SECRET}))


def test_a_custom_key_variable_is_honoured() -> None:
    config = PerceptionConfig(provider="openai_compat", budget_usd=1.0, api_key_env="MY_VISION_KEY")

    assert api_key_present(config, {"MY_VISION_KEY": SECRET}) is True
    assert api_key_present(config, {ENV_NAME: SECRET}) is False
    with pytest.raises(ProviderError):
        build_provider(config, environ={ENV_NAME: SECRET})


def test_a_cancelled_token_stops_the_fake_provider_too() -> None:
    provider = build_provider(PerceptionConfig(), clock=FakeClock(0))
    cancel = CancelToken()
    cancel.cancel()

    with pytest.raises(ProviderError) as info:
        asyncio.run(provider.infer(model_input(), deadline=Deadline.unlimited(), cancel=cancel))

    assert info.value.failure == "CANCELLED"


# --------------------------------------------------------------------------- doctor 摘要


def test_the_doctor_summary_reports_configuration_only() -> None:
    config = PerceptionConfig()
    info = describe_provider(config, environ={ENV_NAME: SECRET}, ledger=build_ledger(config))

    assert info["provider"] == "fake"
    assert info["offline"] is True
    assert info["paid"] is False
    assert info["api_key_present"] is True  # 只报「有没有配」，不报内容
    assert info["budget_usd"] == 0.0
    assert info["send_images"] is False
    assert info["fake_behaviour"] == "ok"
    assert "base_url" not in info  # 离线供应商没有端点可报
    assert info["ledger"]["calls"] == 0
    assert SECRET not in json.dumps(info)


def test_the_doctor_summary_of_a_real_provider_lists_the_endpoint_but_no_key() -> None:
    config = PerceptionConfig(
        provider="openai_compat",
        budget_usd=1.0,
        price_input_per_mtok_usd=3.0,
        price_output_per_mtok_usd=15.0,
        price_as_of="2026-01-01",
    )
    info = describe_provider(config, environ={ENV_NAME: SECRET})

    assert info["paid"] is True
    assert info["offline"] is False
    assert info["base_url"] == "https://api.openai.com/v1"
    assert info["price_configured"] is True
    assert "fake_behaviour" not in info
    assert SECRET not in json.dumps(info)


def test_the_provider_registry_is_consistent() -> None:
    assert PAID_PROVIDERS <= set(PROVIDER_NAMES)
    assert set(PROVIDER_NAMES) == {"fake", "openai_compat"}
    assert provider_is_paid(PerceptionConfig(provider="openai_compat", budget_usd=1.0)) is True
    assert api_key_present(PerceptionConfig(), {ENV_NAME: "   "}) is False
