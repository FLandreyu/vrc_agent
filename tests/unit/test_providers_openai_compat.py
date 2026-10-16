"""OpenAI 兼容适配器：请求形状、结构化输出解析、分类重试、用量与费用归属。

全部走注入的假传输层，一个真实请求都不发（默认不计费）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.error
from typing import Any

import pytest

from vrc_agent.clock import FakeClock
from vrc_agent.providers.base import CancelToken, Deadline, ModelInput, UsageLedger
from vrc_agent.providers.errors import ProviderError
from vrc_agent.providers.openai_compat import (
    HttpRequest,
    HttpResponse,
    OpenAICompatibleProvider,
    validate_base_url,
)
from vrc_agent.telemetry import ALLOWED_FIELDS

API_KEY = "sk-test-key-must-not-leak"
MODEL = "vision-test-1"
ENDPOINT = "https://api.example.com/v1/chat/completions"
# 帧字节里放一段可 grep 的标记：用来证明默认情况下它没有被发出去。
FRAME_MARKER = b"MARKER-FRAME-BYTES-MUST-NOT-TRAVEL"
FRAME = FRAME_MARKER + bytes(range(16)) * 4
SCENE = {"entities": [], "hazards": [], "uncertainties": ["看不清"]}


class StubTransport:
    """可编程传输层：按调用序号返回响应，或抛出异常。"""

    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.requests: list[HttpRequest] = []

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self._handler(request, len(self.requests) - 1)

    @property
    def calls(self) -> int:
        return len(self.requests)


def body_of(document: Any) -> bytes:
    return json.dumps(document).encode("utf-8")


def ok_response(
    content: Any = None,
    *,
    finish_reason: Any = "stop",
    usage: Any = None,
    **message_extra: Any,
) -> HttpResponse:
    if content is None:
        content = json.dumps(SCENE)
    message = {"content": content, **message_extra}
    document: dict[str, Any] = {"choices": [{"finish_reason": finish_reason, "message": message}]}
    if usage is not None:
        document["usage"] = usage
    return HttpResponse(status=200, body=body_of(document))


def status_response(status: int, body: bytes = b"{}") -> HttpResponse:
    return HttpResponse(status=status, body=body)


async def no_sleep(seconds: float) -> None:  # 不真的退避，测试里时间由 FakeClock 控制
    return None


def make_provider(
    transport: StubTransport,
    clock: FakeClock,
    *,
    ledger: UsageLedger | None = None,
    sink: Any = None,
    **changes: Any,
) -> OpenAICompatibleProvider:
    options: dict[str, Any] = {
        "model": MODEL,
        "api_key": API_KEY,
        "base_url": "https://api.example.com/v1",
        "transport": transport,
        "clock": clock,
        "ledger": ledger,
        "sink": sink,
        "sleep": no_sleep,
    }
    options.update(changes)
    return OpenAICompatibleProvider(**options)


def model_input(**changes: Any) -> ModelInput:
    options: dict[str, Any] = {
        "prompt": "describe this frame",
        "observation_id": "obs-1",
        "request_id": "req-1",
        "session_id": "sess-1",
        "environment_epoch": 1,
        "created_at_ns": 5_000_000_000,
        "image": FRAME,
        "image_media_type": "image/png",
        "image_ref": "frame-abc",
        "image_size": (96, 72),
    }
    options.update(changes)
    return ModelInput(**options)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class Sink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def text(self) -> str:
        return json.dumps(self.events, ensure_ascii=False)


# --------------------------------------------------------------------------- 成功路径


def test_request_shape_is_a_chat_completion_call() -> None:
    clock = FakeClock(0)
    transport = StubTransport(lambda request, index: ok_response())
    provider = make_provider(transport, clock)

    response = run(provider.infer(model_input(), deadline=Deadline.unlimited()))

    assert transport.calls == 1
    request = transport.requests[0]
    assert request.method == "POST"
    assert request.url == ENDPOINT
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["Content-Type"] == "application/json"
    document = json.loads(request.body.decode("utf-8"))
    assert document["model"] == MODEL
    assert document["temperature"] == 0.0
    assert document["max_tokens"] == 1024
    assert document["response_format"] == {"type": "json_object"}
    system, user = document["messages"]
    assert system["role"] == "system"
    assert "JSON" in system["content"]
    assert user["content"] == [
        {
            "type": "text",
            "text": "describe this frame\nEvidence observation ID for this frame: obs-1",
        }
    ]
    assert response.payload == SCENE
    assert response.provider == "openai_compat"
    assert response.model == MODEL
    assert response.request_id == "req-1"
    assert response.observation_id == "obs-1"
    assert response.attempts == 1
    assert response.finish_reason == "stop"
    assert provider.attempts == 1


def test_frames_are_not_sent_unless_explicitly_enabled() -> None:
    clock = FakeClock(0)
    transport = StubTransport(lambda request, index: ok_response())
    provider = make_provider(transport, clock)

    response = run(provider.infer(model_input(), deadline=Deadline.unlimited()))

    assert FRAME_MARKER not in transport.requests[0].body
    assert response.usage.image_count == 0
    assert response.usage.requests == 1


def test_images_are_attached_with_a_data_url_when_enabled() -> None:
    clock = FakeClock(0)
    transport = StubTransport(lambda request, index: ok_response())
    ledger = UsageLedger(budget_usd=1.0)
    provider = make_provider(transport, clock, ledger=ledger, send_images=True)

    response = run(provider.infer(model_input(), deadline=Deadline.unlimited()))

    document = json.loads(transport.requests[0].body.decode("utf-8"))
    parts = document["messages"][1]["content"]
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    data_url = parts[1]["image_url"]["url"]
    prefix = "data:image/png;base64,"
    assert data_url.startswith(prefix)
    assert base64.b64decode(data_url[len(prefix) :]) == FRAME
    assert response.usage.image_count == 1


def test_usage_without_a_price_table_stays_unknown() -> None:
    clock = FakeClock(0)
    transport = StubTransport(lambda request, index: ok_response(usage={"prompt_tokens": 10}))
    ledger = UsageLedger(budget_usd=1.0)
    provider = make_provider(transport, clock, ledger=ledger)

    response = run(provider.infer(model_input(), deadline=Deadline.unlimited()))

    assert response.usage.prompt_tokens == 10
    assert response.usage.completion_tokens is None
    assert response.usage.total_tokens is None
    assert response.usage.cost_usd is None  # 未知费用不是 0
    assert response.usage.cost_estimated is False
    assert response.usage.cost_known is False
    assert ledger.calls == 1
    assert ledger.unknown_cost_calls == 1
    assert ledger.spent_usd_known == 0.0
    assert ledger.cost_complete is False


def test_total_tokens_is_summed_when_the_provider_omits_it() -> None:
    clock = FakeClock(0)
    transport = StubTransport(
        lambda request, index: ok_response(usage={"prompt_tokens": 10, "completion_tokens": 4})
    )
    provider = make_provider(transport, clock)

    response = run(provider.infer(model_input(), deadline=Deadline.unlimited()))

    assert response.usage.total_tokens == 14


def test_configured_prices_produce_an_estimated_cost() -> None:
    clock = FakeClock(0)
    transport = StubTransport(
        lambda request, index: ok_response(usage={"prompt_tokens": 10, "completion_tokens": 4})
    )
    ledger = UsageLedger(budget_usd=1.0)
    provider = make_provider(
        transport,
        clock,
        ledger=ledger,
        price_input_per_mtok_usd=3.0,
        price_output_per_mtok_usd=15.0,
        price_as_of="2026-01-01",
    )

    response = run(provider.infer(model_input(), deadline=Deadline.unlimited()))

    expected = (10 * 3.0 + 4 * 15.0) / 1_000_000
    assert response.usage.cost_usd == pytest.approx(expected)
    assert response.usage.cost_estimated is True
    assert ledger.spent_usd_known == pytest.approx(expected)
    assert ledger.unknown_cost_calls == 0
    assert ledger.cost_complete is True


# --------------------------------------------------------------------------- 失败分类


@pytest.mark.parametrize(
    ("status", "expected_attempts", "retryable"),
    [
        (400, 1, False),
        (404, 1, False),
        (401, 1, False),
        (429, 2, True),
        (500, 2, True),
        (503, 2, True),
    ],
)
def test_http_errors_are_classified_and_only_429_or_5xx_are_retried(
    status: int, expected_attempts: int, retryable: bool
) -> None:
    clock = FakeClock(0)
    transport = StubTransport(lambda request, index: status_response(status))
    ledger = UsageLedger(budget_usd=1.0)
    sink = Sink()
    provider = make_provider(transport, clock, ledger=ledger, sink=sink)

    with pytest.raises(ProviderError) as info:
        run(provider.infer(model_input(), deadline=Deadline.unlimited()))

    error = info.value
    assert error.failure == "HTTP_ERROR"
    assert error.status == status
    assert error.retryable is retryable
    assert error.attempts == expected_attempts
    assert transport.calls == expected_attempts
    # 复核 R2：**每一次发出的尝试**独立入账（429/5xx 重试后是两个可能已被计费的请求），
    # 不能把两次 HTTP 记成一次。失败尝试未知计费，成功费用一分没记。
    assert ledger.calls == expected_attempts
    assert ledger.spent_usd_known == 0.0
    assert ledger.unknown_cost_calls == expected_attempts
    assert ledger.snapshot()["unknown_token_calls"] == expected_attempts
    assert ledger.cost_complete is False
    assert sink.names() == ["provider_attempt"] * expected_attempts + ["provider_failure"]
    assert sink.events[-1][1]["status"] == "HTTP_ERROR"


def test_transport_errors_from_the_network_are_retryable() -> None:
    clock = FakeClock(0)
    ledger = UsageLedger(budget_usd=1.0)

    def handler(request: HttpRequest, index: int) -> HttpResponse:
        raise urllib.error.URLError("name resolution failed")

    transport = StubTransport(handler)
    provider = make_provider(transport, clock, ledger=ledger)

    with pytest.raises(ProviderError) as info:
        run(provider.infer(model_input(), deadline=Deadline.unlimited()))

    error = info.value
    assert error.failure == "TRANSPORT_ERROR"
    assert error.retryable is True  # 网络抖动正是有限重试要救的情况
    assert error.attempts == 2
    assert transport.calls == 2
    assert "name resolution failed" in error.detail
    # 两次网络失败都发出去了：各自计一次未知计费（复核 R2）。
    assert ledger.calls == 2
    assert ledger.unknown_cost_calls == 2


def test_a_read_timeout_is_classified_as_timeout_but_still_retryable() -> None:
    clock = FakeClock(0)

    def handler(request: HttpRequest, index: int) -> HttpResponse:
        raise TimeoutError("read timed out")

    transport = StubTransport(handler)
    provider = make_provider(transport, clock)

    with pytest.raises(ProviderError) as info:
        run(provider.infer(model_input(), deadline=Deadline.unlimited()))

    assert info.value.failure == "TIMEOUT"
    assert info.value.retryable is True
    assert info.value.attempts == 2


@pytest.mark.parametrize(
    ("label", "response", "failure", "fragment"),
    [
        (
            "body-not-json",
            HttpResponse(status=200, body=b"<html>nope</html>"),
            "MALFORMED_RESULT",
            "not JSON",
        ),
        (
            "body-not-object",
            HttpResponse(status=200, body=b"[1, 2]"),
            "MALFORMED_RESULT",
            "not a JSON object",
        ),
        (
            "no-choices",
            HttpResponse(status=200, body=body_of({"choices": []})),
            "MALFORMED_RESULT",
            "no choices",
        ),
        (
            "choices-not-list",
            HttpResponse(status=200, body=body_of({"choices": {}})),
            "MALFORMED_RESULT",
            "no choices",
        ),
        (
            "choice-not-object",
            HttpResponse(status=200, body=body_of({"choices": ["x"]})),
            "MALFORMED_RESULT",
            "not an object",
        ),
        (
            "no-message",
            HttpResponse(status=200, body=body_of({"choices": [{}]})),
            "MALFORMED_RESULT",
            "no message",
        ),
        ("content-not-string", ok_response(12345), "MALFORMED_RESULT", "not a string"),
        ("content-not-json", ok_response("hello"), "MALFORMED_RESULT", "content is not JSON"),
        ("content-not-object", ok_response("[1, 2]"), "MALFORMED_RESULT", "not a JSON object"),
        (
            "truncated",
            ok_response(json.dumps(SCENE), finish_reason="length"),
            "MALFORMED_RESULT",
            "truncated",
        ),
        ("empty-content", ok_response("", finish_reason=None), "MALFORMED_RESULT", "empty content"),
        (
            "content-filtered",
            HttpResponse(
                status=200,
                body=body_of(
                    {"choices": [{"finish_reason": "content_filter", "message": {"content": None}}]}
                ),
            ),
            "REFUSED",
            "empty content",
        ),
        (
            "refusal",
            ok_response("", refusal="I cannot describe that"),
            "REFUSED",
            "cannot describe",
        ),
    ],
)
def test_malformed_or_refused_results_are_rejected_without_retrying(
    label: str, response: HttpResponse, failure: str, fragment: str
) -> None:
    clock = FakeClock(0)
    transport = StubTransport(lambda request, index: response)
    ledger = UsageLedger(budget_usd=1.0)
    provider = make_provider(transport, clock, ledger=ledger)

    with pytest.raises(ProviderError) as info:
        run(provider.infer(model_input(), deadline=Deadline.unlimited()))

    assert info.value.failure == failure, label
    assert fragment in info.value.detail, label
    assert transport.calls == 1  # 畸形结果不重试：同样的请求只会再畸形一次
    assert ledger.unknown_cost_calls == 1


def test_a_retry_never_outlives_the_deadline() -> None:
    """第一次尝试把时间用光后，第二次尝试根本不会发出。"""
    clock = FakeClock(0)

    def handler(request: HttpRequest, index: int) -> HttpResponse:
        clock.advance_ms(500)  # 这一步就吃掉了整个预算
        return status_response(503)

    transport = StubTransport(handler)
    provider = make_provider(transport, clock)
    deadline = Deadline.from_now(clock, 400)

    with pytest.raises(ProviderError) as info:
        run(provider.infer(model_input(), deadline=deadline))

    assert transport.calls == 1
    assert info.value.failure == "TIMEOUT"


def test_an_expired_deadline_fails_before_any_request() -> None:
    clock = FakeClock(0)
    transport = StubTransport(lambda request, index: ok_response())
    provider = make_provider(transport, clock)
    deadline = Deadline.from_now(clock, 100)
    clock.advance_ms(200)

    with pytest.raises(ProviderError) as info:
        run(provider.infer(model_input(), deadline=deadline))

    assert info.value.failure == "TIMEOUT"
    assert transport.calls == 0


def test_a_cancelled_token_fails_before_any_request() -> None:
    clock = FakeClock(0)
    transport = StubTransport(lambda request, index: ok_response())
    provider = make_provider(transport, clock)
    cancel = CancelToken()
    cancel.cancel()

    with pytest.raises(ProviderError) as info:
        run(provider.infer(model_input(), deadline=Deadline.unlimited(), cancel=cancel))

    assert info.value.failure == "CANCELLED"
    assert transport.calls == 0


def test_the_budget_gate_blocks_the_call_before_the_request() -> None:
    clock = FakeClock(0)
    transport = StubTransport(lambda request, index: ok_response())
    ledger = UsageLedger(budget_usd=0.0)  # 付费调用必须显式配预算
    provider = make_provider(transport, clock, ledger=ledger)

    with pytest.raises(ProviderError) as info:
        run(provider.infer(model_input(), deadline=Deadline.unlimited()))

    assert info.value.failure == "BUDGET_EXCEEDED"
    assert transport.calls == 0
    assert ledger.calls == 0


def test_the_request_timeout_follows_the_remaining_budget() -> None:
    clock = FakeClock(0)
    transport = StubTransport(lambda request, index: ok_response())
    provider = make_provider(transport, clock)

    run(provider.infer(model_input(), deadline=Deadline.unlimited()))
    assert transport.requests[0].timeout_s == pytest.approx(20.0)

    transport2 = StubTransport(lambda request, index: ok_response())
    provider2 = make_provider(transport2, clock)
    run(provider2.infer(model_input(), deadline=Deadline.from_now(clock, 250)))
    assert transport2.requests[0].timeout_s == pytest.approx(0.25)

    transport3 = StubTransport(lambda request, index: ok_response())
    provider3 = make_provider(transport3, clock)
    run(provider3.infer(model_input(), deadline=Deadline.from_now(clock, 1)))
    assert transport3.requests[0].timeout_s == pytest.approx(0.001)  # 有下限，不会退化成 0


# --------------------------------------------------------------------------- 隐私与配置


def test_the_api_key_never_leaks_into_repr_logs_or_telemetry() -> None:
    clock = FakeClock(0)
    transport = StubTransport(lambda request, index: status_response(403))
    ledger = UsageLedger(budget_usd=1.0)
    sink = Sink()
    provider = make_provider(transport, clock, ledger=ledger, sink=sink)

    with pytest.raises(ProviderError) as info:
        run(provider.infer(model_input(), deadline=Deadline.unlimited()))

    assert API_KEY not in repr(provider)
    assert API_KEY not in str(info.value)
    assert API_KEY not in sink.text()
    assert API_KEY not in json.dumps(ledger.snapshot())
    assert "Authorization" not in sink.text()


def test_telemetry_uses_only_whitelisted_fields() -> None:
    clock = FakeClock(0)
    transport = StubTransport(lambda request, index: ok_response(usage={"prompt_tokens": 1}))
    sink = Sink()
    provider = make_provider(transport, clock, sink=sink)

    run(provider.infer(model_input(), deadline=Deadline.unlimited()))

    assert sink.names() == ["provider_attempt", "provider_ok"]
    for name, fields in sink.events:
        assert set(fields) <= ALLOWED_FIELDS, name
        assert "prompt" not in fields
    assert sink.events[-1][1]["count"] == 1


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"api_key": ""}, "NOT_CONFIGURED"),
        ({"model": ""}, "ValueError"),
        ({"max_retries": 4}, "ValueError"),
        ({"max_retries": -1}, "ValueError"),
        ({"timeout_ms": 0}, "ValueError"),
        ({"max_output_tokens": 0}, "ValueError"),
        ({"price_input_per_mtok_usd": 1.0}, "ValueError"),
        ({"base_url": "http://api.example.com/v1"}, "ValueError"),
    ],
)
def test_the_constructor_fails_fast_on_bad_configuration(
    changes: dict[str, Any], expected: str
) -> None:
    clock = FakeClock(0)
    transport = StubTransport(lambda request, index: ok_response())

    if expected == "ValueError":
        with pytest.raises(ValueError):
            make_provider(transport, clock, **changes)
    else:
        with pytest.raises(ProviderError) as info:
            make_provider(transport, clock, **changes)
        assert info.value.failure == expected


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.example.com/v1",
        "https://api.example.com/v1/",
        "http://127.0.0.1:8080/v1",
        "http://localhost:11434/v1",
        "http://[::1]:8080/v1",
    ],
)
def test_allowed_base_urls(base_url: str) -> None:
    assert validate_base_url(base_url) == base_url.rstrip("/")


@pytest.mark.parametrize(
    "base_url",
    ["http://api.example.com/v1", "ftp://api.example.com/v1", "not-a-url", "", "/v1"],
)
def test_rejected_base_urls(base_url: str) -> None:
    with pytest.raises(ValueError):
        validate_base_url(base_url)
