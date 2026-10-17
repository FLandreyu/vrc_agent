"""OpenAI 兼容的真实供应商适配器（`/chat/completions`）。

刻意不引入任何 SDK：HTTP 用标准库 `urllib`，在 `asyncio.to_thread` 里执行，因此
不会阻塞事件循环。适配器只负责「发请求、解析结构化输出、记录 usage、超时与有限重试」，
**不执行动作、不落盘、不写日志正文**。

安全与隐私约定：

- 密钥只从环境变量读入内存（配置里只存变量名），不进配置、不进日志、不进遥测。
- 画面默认**不发送**：只有 `send_images=True` 时才会把内存里的帧以 data URL 附上。
- 未知用量/费用记 `None`，不能用 0 冒充。
"""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from vrc_agent.clock import Clock, SystemClock
from vrc_agent.providers.base import (
    Accounting,
    CancelToken,
    Deadline,
    ModelInput,
    ModelResponse,
    Usage,
    UsageLedger,
    run_attempts,
)
from vrc_agent.providers.errors import ProviderError, ProviderFailure
from vrc_agent.providers.images import encode_bgra, validate_image_encoding
from vrc_agent.providers.protocol import ProviderSink

DEFAULT_SYSTEM_PROMPT = (
    "You describe a single screenshot of a VR application. Reply with a JSON object only: "
    '{"entities": [{"track_id": str, "label": str, "bbox": [x1,y1,x2,y2], "confidence": 0..1, '
    '"evidence_observation_id": str}], "hazards": [str], "uncertainties": [str]}. '
    "bbox is normalized to the full frame with the origin at the top left. "
    "Never invent an object you cannot see: when the image is unclear, return empty entities "
    "and put the reason in uncertainties."
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "[::1]"})


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """一次 HTTP 调用的全部内容，便于测试注入传输层。"""

    method: str
    url: str
    headers: dict[str, str]
    body: bytes
    timeout_s: float


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


HttpTransport = Callable[[HttpRequest], HttpResponse]


def urllib_transport(request: HttpRequest) -> HttpResponse:
    """默认传输。HTTP 错误码也返回响应体，交给适配器分类。"""
    http_request = urllib.request.Request(
        request.url,
        data=request.body,
        method=request.method,
        headers=request.headers,
    )
    try:
        with urllib.request.urlopen(http_request, timeout=request.timeout_s) as response:
            return HttpResponse(
                status=int(response.status),
                body=response.read(),
                headers=dict(response.headers),
            )
    except urllib.error.HTTPError as exc:
        return HttpResponse(
            status=int(exc.code),
            body=exc.read(),
            headers=dict(exc.headers or {}),
        )


@dataclass(frozen=True, slots=True)
class _Parsed:
    scene: dict[str, Any]
    finish_reason: str | None
    usage: dict[str, Any]


def validate_base_url(base_url: str) -> str:
    """只允许 https，或指向本机回环的 http（本地推理服务）。"""
    from urllib.parse import urlsplit

    parts = urlsplit(base_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"base_url must be an absolute http(s) URL: {base_url!r}")
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK_HOSTS:
        raise ValueError(
            "plain http is only allowed for loopback base_url; "
            f"use https for remote endpoints: {base_url!r}"
        )
    return base_url.rstrip("/")


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class OpenAICompatibleProvider:
    """可配置的真实供应商适配器。

    与供应商无关的部分（截止期、取消、有限重试、用量归属、预算门禁）都由这里实现，
    所以换成兼容 OpenAI 协议的其它服务只需要改配置。
    """

    name = "openai_compat"
    paid = True

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_ms: int = 20000,
        max_retries: int = 1,
        backoff_ms: int = 200,
        temperature: float = 0.0,
        max_output_tokens: int = 1024,
        json_mode: bool = True,
        disable_thinking: bool = False,
        prepare_in_thread: bool = False,
        send_images: bool = False,
        image_format: Literal["png", "jpeg"] = "png",
        jpeg_quality: int = 85,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        price_input_per_mtok_usd: float | None = None,
        price_output_per_mtok_usd: float | None = None,
        price_as_of: str | None = None,
        price_currency: Literal["USD"] = "USD",
        transport: HttpTransport | None = None,
        clock: Clock | None = None,
        ledger: UsageLedger | None = None,
        accounting: Accounting | None = None,
        sink: ProviderSink | None = None,
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError(
                "NOT_CONFIGURED", "empty API key; check the configured environment variable"
            )
        if not model:
            raise ValueError("model must not be empty")
        if not 0 <= max_retries <= 3:
            raise ValueError("max_retries must be between 0 and 3")
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if (price_input_per_mtok_usd is None) != (price_output_per_mtok_usd is None):
            raise ValueError("input and output prices must be configured together")
        if price_input_per_mtok_usd is not None and price_as_of is None:
            # 没有生效日期的价格不算价：宁可在装配时失败，也不要给出一个来路不明的数字。
            raise ValueError("a price table must state the date it was valid: price_as_of")
        self._model = model
        self._api_key = api_key
        self._base_url = validate_base_url(base_url)
        self._endpoint = f"{self._base_url}/chat/completions"
        self._timeout_ms = timeout_ms
        self._max_retries = max_retries
        self._backoff_ms = backoff_ms
        self._temperature = float(temperature)
        self._max_output_tokens = max_output_tokens
        self._json_mode = json_mode
        if type(disable_thinking) is not bool:
            raise ValueError("disable_thinking must be boolean")
        self._disable_thinking = disable_thinking
        self._prepare_in_thread = prepare_in_thread
        self._send_images = send_images
        validate_image_encoding(image_format, jpeg_quality)
        self._image_format = image_format
        self._jpeg_quality = jpeg_quality
        self._system_prompt = system_prompt
        self._price_input = price_input_per_mtok_usd
        self._price_output = price_output_per_mtok_usd
        self._price_as_of = price_as_of
        self._price_currency = price_currency
        self._transport: HttpTransport = transport or urllib_transport
        self._clock: Clock = clock or SystemClock()
        self._ledger = ledger
        # 装配层若给了预算包装层，闸门与入账都走它：否则预算快照看不到任何调用（复核 R3）。
        self._accounting: Accounting | None = accounting if accounting is not None else ledger
        self._sink = sink
        self._sleep = sleep or asyncio.sleep
        self.attempts = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def __repr__(self) -> str:  # 密钥与请求正文都不进 repr
        return (
            f"OpenAICompatibleProvider(model={self._model!r}, endpoint={self._endpoint!r}, "
            f"send_images={self._send_images}, max_retries={self._max_retries})"
        )

    async def infer(
        self,
        model_input: ModelInput,
        *,
        deadline: Deadline = Deadline.unlimited(),
        cancel: CancelToken | None = None,
    ) -> ModelResponse:
        token = cancel or CancelToken()
        token.raise_if_cancelled()
        deadline.raise_if_expired(self._clock.now_ns())
        started = self._clock.now_ns()
        attempts_done = 0

        def authorize() -> None:
            """每次**可能真的发出**的请求前的闸门；重试同样要过闸（复核 R2）。

            预算在第一次调用返回后耗尽时，第二次尝试必须在这里被拒绝，而不是发出去
            再靠事后统计发现超支。
            """
            if self._accounting is not None:
                self._accounting.authorize(paid=self.paid, provider=self.name)

        def account_failure(failure: ProviderFailure) -> None:
            """一次已发出的尝试可能已经被计费：独立入账，不覆盖也不合并（复核 R2）。"""
            if self._accounting is not None:
                self._accounting.record_failure(provider=self.name, failure=failure, paid=self.paid)

        async def attempt(index: int) -> ModelResponse:
            nonlocal attempts_done
            attempts_done = index + 1
            token.raise_if_cancelled()
            deadline.raise_if_expired(self._clock.now_ns())
            # 闸门在构造请求、计数和发请求之前：被预算拒绝的尝试不算「发出过」。
            authorize()
            self.attempts += 1
            if self._prepare_in_thread:
                request, image_count = await asyncio.to_thread(
                    self._build_request, model_input, deadline
                )
                token.raise_if_cancelled()
                deadline.raise_if_expired(self._clock.now_ns())
            else:
                request, image_count = self._build_request(model_input, deadline)
            self._emit("provider_attempt", operation="infer", count=attempts_done)
            try:
                response = await asyncio.to_thread(self._transport, request)
            except asyncio.CancelledError:
                # 取消等待不能撤销远端计费；线程中的 HTTP 由自身 timeout 收尾。
                account_failure("CANCELLED")
                raise
            except TimeoutError as exc:
                # 读超时是「这次可能已经花了钱、但重试还有意义」的典型情况，由适配器显式
                # 标成可重试（策略注释见 errors.RETRYABLE_FAILURES）。
                account_failure("TIMEOUT")
                raise ProviderError(
                    "TIMEOUT", f"transport timed out: {exc}", retryable=True
                ) from exc
            except OSError as exc:
                # 网络层抖动（DNS 失败、连接被拒、连接中断）必须走 ProviderError，否则
                # 有限重试完全用不上，调用方只能看到裸 OSError。
                account_failure("TRANSPORT_ERROR")
                raise ProviderError("TRANSPORT_ERROR", f"transport failed: {exc}") from exc
            try:
                parsed = self._parse(response)
            except ProviderError as exc:
                # 响应已经回来（很可能已被计费）但内容不可用：这一次同样要入账。
                account_failure(exc.failure)
                raise
            result = ModelResponse(
                request_id=model_input.request_id,
                observation_id=model_input.observation_id,
                provider=self.name,
                model=self._model,
                payload=parsed.scene,
                usage=self._usage(parsed.usage, image_count=image_count),
                latency_ms=max(0, (self._clock.now_ns() - started) // 1_000_000),
                attempts=attempts_done,
                finish_reason=parsed.finish_reason,
                created_at_ns=self._clock.now_ns(),
            )
            if self._accounting is not None:
                self._accounting.record(result, paid=self.paid)
            return result

        try:
            result = await run_attempts(
                attempt,
                max_retries=self._max_retries,
                backoff_ms=self._backoff_ms,
                deadline=deadline,
                cancel=token,
                clock=self._clock,
                sleep=self._sleep,
            )
        except ProviderError as exc:
            # 账目在每次已发出的尝试里逐次登记，这里只负责上报（失败被记一次，不重复）。
            self._emit("provider_failure", status=exc.failure, count=max(1, attempts_done))
            raise ProviderError(
                exc.failure,
                exc.detail,
                retryable=exc.retryable,
                status=exc.status,
                attempts=max(1, attempts_done),
            ) from exc
        self._emit("provider_ok", status="ok", count=max(1, attempts_done))
        return result

    def _build_request(
        self, model_input: ModelInput, deadline: Deadline
    ) -> tuple[HttpRequest, int]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": model_input.prompt
                + "\nEvidence observation ID for this frame: "
                + model_input.observation_id,
            }
        ]
        image_count = 0
        if model_input.image is not None and self._send_images:
            media_type, image = model_input.image_media_type, model_input.image
            if media_type == "application/x-bgra":
                image, media_type = encode_bgra(
                    image,
                    model_input.image_size,
                    image_format=self._image_format,
                    jpeg_quality=self._jpeg_quality,
                )
            encoded = base64.b64encode(image).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                }
            )
            image_count = 1
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": content},
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
        }
        if self._json_mode:
            body["response_format"] = {"type": "json_object"}
        if self._disable_thinking:
            body["thinking"] = {"type": "disabled"}
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = HttpRequest(
            method="POST",
            url=self._endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            body=payload,
            timeout_s=self._timeout_s(deadline),
        )
        return request, image_count

    def _timeout_s(self, deadline: Deadline) -> float:
        remaining = deadline.remaining_ms(self._clock.now_ns())
        budget_ms = self._timeout_ms if remaining is None else min(self._timeout_ms, remaining)
        return max(0.001, budget_ms / 1000)

    def _parse(self, response: HttpResponse) -> _Parsed:
        if response.status >= 400:
            retryable = response.status == 429 or response.status >= 500
            raise ProviderError(
                "HTTP_ERROR",
                f"HTTP {response.status}",
                retryable=retryable,
                status=response.status,
            )
        try:
            document = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("MALFORMED_RESULT", f"response body is not JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise ProviderError("MALFORMED_RESULT", "response body is not a JSON object")
        choices = document.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("MALFORMED_RESULT", "response has no choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderError("MALFORMED_RESULT", "choice is not an object")
        finish = choice.get("finish_reason")
        finish_reason = finish if isinstance(finish, str) else None
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderError("MALFORMED_RESULT", "choice has no message object")
        refusal = message.get("refusal")
        if isinstance(refusal, str) and refusal:
            raise ProviderError("REFUSED", refusal[:200])
        content = message.get("content")
        if content is None or (isinstance(content, str) and not content.strip()):
            failure = "REFUSED" if finish_reason == "content_filter" else "MALFORMED_RESULT"
            raise ProviderError(failure, f"empty content (finish_reason={finish_reason})")
        if not isinstance(content, str):
            raise ProviderError("MALFORMED_RESULT", "content is not a string")
        if finish_reason == "length":
            raise ProviderError("MALFORMED_RESULT", "output was truncated by max_tokens")
        try:
            scene = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ProviderError("MALFORMED_RESULT", f"content is not JSON: {exc}") from exc
        if not isinstance(scene, dict):
            raise ProviderError("MALFORMED_RESULT", "content is not a JSON object")
        usage = document.get("usage")
        return _Parsed(
            scene=scene,
            finish_reason=finish_reason,
            usage=usage if isinstance(usage, dict) else {},
        )

    def _usage(self, usage: dict[str, Any], *, image_count: int) -> Usage:
        prompt = _as_int(usage.get("prompt_tokens"))
        completion = _as_int(usage.get("completion_tokens"))
        total = _as_int(usage.get("total_tokens"))
        if total is None and prompt is not None and completion is not None:
            total = prompt + completion
        cost, estimated = self._cost(prompt, completion)
        return Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            image_count=image_count,
            requests=1,
            cost_usd=cost,
            cost_estimated=estimated,
            # 只有本地估算才有价格表的日期与币种；供应商账单值不带这两个字段。
            cost_as_of=self._price_as_of if estimated else None,
            cost_currency=self._price_currency if estimated else None,
        )

    def _cost(self, prompt: int | None, completion: int | None) -> tuple[float | None, bool]:
        """没有价格表就没有费用数字：返回 None（未知），绝不返回 0。"""
        if self._price_input is None or self._price_output is None:
            return None, False
        if prompt is None or completion is None:
            return None, False
        cost = (prompt * self._price_input + completion * self._price_output) / 1_000_000
        return cost, True

    def _emit(self, event: str, **fields: object) -> None:
        if self._sink is not None:
            self._sink.emit(event, **fields)


__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "HttpRequest",
    "HttpResponse",
    "HttpTransport",
    "OpenAICompatibleProvider",
    "urllib_transport",
    "validate_base_url",
]
