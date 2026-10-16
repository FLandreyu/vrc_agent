"""模型供应商边界。

- `providers/fake.py`：离线假模型（免费、确定性、不出网）。
- `providers/openai_compat.py`：可配置的真实供应商适配器（OpenAI 兼容 HTTP）。
- `providers/base.py`：截止期、取消、用量、预算台账与有限重试。
- `providers/factory.py`：按配置构造实例；**唯一**读密钥的地方。

业务层只依赖这里的协议与契约，不直接依赖任何供应商 SDK。
"""

from __future__ import annotations

from vrc_agent.providers.base import (
    CancelToken,
    Deadline,
    ModelInput,
    ModelResponse,
    Usage,
    UsageLedger,
    run_attempts,
)
from vrc_agent.providers.errors import (
    PROVIDER_FAILURES,
    RETRYABLE_FAILURES,
    ProviderError,
    ProviderFailure,
)
from vrc_agent.providers.fake import (
    FAKE_BEHAVIOURS,
    FakeBehaviour,
    FakeProvider,
    FakeSceneProvider,
    default_scene_payload,
)
from vrc_agent.providers.openai_compat import (
    DEFAULT_SYSTEM_PROMPT,
    HttpRequest,
    HttpResponse,
    HttpTransport,
    OpenAICompatibleProvider,
    urllib_transport,
    validate_base_url,
)
from vrc_agent.providers.protocol import ModelProvider, ProviderSink

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "FAKE_BEHAVIOURS",
    "PROVIDER_FAILURES",
    "RETRYABLE_FAILURES",
    "CancelToken",
    "Deadline",
    "FakeBehaviour",
    "FakeProvider",
    "FakeSceneProvider",
    "HttpRequest",
    "HttpResponse",
    "HttpTransport",
    "ModelInput",
    "ModelProvider",
    "ModelResponse",
    "OpenAICompatibleProvider",
    "ProviderError",
    "ProviderFailure",
    "ProviderSink",
    "Usage",
    "UsageLedger",
    "default_scene_payload",
    "run_attempts",
    "urllib_transport",
    "validate_base_url",
]
