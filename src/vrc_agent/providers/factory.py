"""把配置变成适配器实例。

这个模块是**唯一**读取供应商密钥的地方：密钥只从环境变量进内存，不写配置、不回显、
不进日志。`describe_provider()` 供 `doctor` 使用，输出里只有「有没有配」这一个布尔，
没有任何密钥内容。

单独成文件（而不是塞进 `providers/__init__.py`）是为了避免
`vrc_agent.config` → `vrc_agent.providers` → `vrc_agent.config` 的循环导入。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from vrc_agent.clock import Clock
from vrc_agent.config import PerceptionConfig
from vrc_agent.providers.base import Accounting, UsageLedger
from vrc_agent.providers.errors import ProviderError
from vrc_agent.providers.fake import FakeSceneProvider
from vrc_agent.providers.openai_compat import OpenAICompatibleProvider
from vrc_agent.providers.protocol import ModelProvider, ProviderSink

#: 已知的供应商名；配置校验用同一个元组，避免两份清单走偏。
PROVIDER_NAMES: tuple[str, ...] = ("fake", "openai_compat")

PAID_PROVIDERS: frozenset[str] = frozenset({"openai_compat"})


def provider_is_paid(config: PerceptionConfig) -> bool:
    return config.provider in PAID_PROVIDERS


def api_key_present(config: PerceptionConfig, environ: Mapping[str, str] | None = None) -> bool:
    """密钥是否存在于环境中。**只返回布尔**，绝不返回或记录密钥内容。"""
    source = os.environ if environ is None else environ
    return bool(source.get(config.api_key_env, "").strip())


def build_provider(
    config: PerceptionConfig,
    *,
    clock: Clock | None = None,
    ledger: UsageLedger | None = None,
    accounting: Accounting | None = None,
    sink: ProviderSink | None = None,
    environ: Mapping[str, str] | None = None,
    prepare_in_thread: bool = False,
) -> ModelProvider:
    """按配置构造适配器。真实供应商缺少密钥时立刻报 NOT_CONFIGURED。

    `ledger` 是裸台账（只统计）；`accounting` 是「闸门 + 入账」协议对象，
    预算包装层（`SessionBudget`）应当从这里传入，否则预算快照看不到授权与入账。
    """
    if config.provider == "fake":
        return FakeSceneProvider(
            model=config.model,
            behaviour=config.fake_behaviour,
            clock=clock,
            ledger=ledger,
            accounting=accounting,
        )
    if config.provider == "openai_compat":
        source = os.environ if environ is None else environ
        api_key = source.get(config.api_key_env, "").strip()
        if not api_key:
            raise ProviderError(
                "NOT_CONFIGURED",
                f"environment variable {config.api_key_env} is empty or missing",
            )
        return OpenAICompatibleProvider(
            model=config.model,
            api_key=api_key,
            base_url=config.base_url,
            timeout_ms=config.timeout_ms,
            max_retries=config.max_retries,
            backoff_ms=config.backoff_ms,
            temperature=config.temperature,
            max_output_tokens=config.max_output_tokens,
            json_mode=config.json_mode,
            disable_thinking=config.disable_thinking,
            prepare_in_thread=prepare_in_thread,
            send_images=config.send_images,
            price_input_per_mtok_usd=config.price_input_per_mtok_usd,
            price_output_per_mtok_usd=config.price_output_per_mtok_usd,
            price_as_of=config.price_as_of,
            price_currency=config.price_currency,
            clock=clock,
            ledger=ledger,
            accounting=accounting,
            sink=sink,
        )
    raise ValueError(f"unknown provider: {config.provider!r}")


def build_ledger(config: PerceptionConfig) -> UsageLedger:
    return UsageLedger(budget_usd=config.budget_usd)


def describe_provider(
    config: PerceptionConfig,
    *,
    environ: Mapping[str, str] | None = None,
    ledger: UsageLedger | None = None,
) -> dict[str, Any]:
    """供 `doctor` 打印的供应商摘要：**不含密钥**，也不发起任何网络调用。"""
    paid = provider_is_paid(config)
    info: dict[str, Any] = {
        "provider": config.provider,
        "model": config.model,
        "paid": paid,
        "offline": config.provider == "fake",
        "api_key_env": config.api_key_env,
        "api_key_present": api_key_present(config, environ),
        "send_images": config.send_images,
        "budget_usd": config.budget_usd,
        "timeout_ms": config.timeout_ms,
        "max_retries": config.max_retries,
        "max_output_tokens": config.max_output_tokens,
        "disable_thinking": config.disable_thinking,
        "price_configured": config.price_input_per_mtok_usd is not None,
        "price_currency": config.price_currency,
        "price_as_of": config.price_as_of,
    }
    if config.provider == "fake":
        info["fake_behaviour"] = config.fake_behaviour
    else:
        info["base_url"] = config.base_url
    if ledger is not None:
        info["ledger"] = ledger.snapshot()
    return info


__all__ = [
    "PAID_PROVIDERS",
    "PROVIDER_NAMES",
    "api_key_present",
    "build_ledger",
    "build_provider",
    "describe_provider",
    "provider_is_paid",
]
