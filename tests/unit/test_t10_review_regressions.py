"""T10 独立复核（`docs/validation/T10_review.md`）与修正件复核（第 28 节）的产品回归。

一条复核意见一段，测试名以 `r<n>_` 开头，复核者可逐项对照：

- R1 净化可绕过：凭据前缀、URL-safe base64、尾部换行、字段类型与未知字段计数；
- R2 重试计费/闸门：每次实际 HTTP 前重新授权，每次「可能已发出」的失败独立入账；
- R3 预算包装层接线：组合根传 `SessionBudget` 本体，快照与报告的预算标志来自真实授权/入账；
- R4 未知不折零：没有台账是 `NO_DATA`，合法零调用才是零；损坏台账（含缺完整性标志）同样
  只能 `NO_DATA`，来源自称不完整时合并与折算都不升级成完整；
- R5 逐文件校验与覆盖一致：坏文件计数不入分子，速率分子/分母同源（无时长的报告既不进
  分子也不进分母），巨数/超长载荷在文件边界被拒且不打断整批。

全部离线：假传输层、内存台账、回放素材，不发网络请求、不发 live 输入。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from vrc_agent.clock import FakeClock
from vrc_agent.config import PerceptionConfig
from vrc_agent.perception import Perception
from vrc_agent.providers.base import ModelInput, Usage, UsageLedger
from vrc_agent.providers.errors import ProviderError
from vrc_agent.providers.openai_compat import (
    HttpRequest,
    HttpResponse,
    OpenAICompatibleProvider,
)
from vrc_agent.telemetry import ALLOWED_FIELDS, EventLog
from vrc_agent.telemetry.budget import SessionBudget
from vrc_agent.telemetry.report import (
    SessionRecorder,
    aggregate_usage,
    hourly_from_usage,
    usage_defect,
)

API_KEY = "sk-review-regression-key"
MODEL = "vision-test-1"
FRAME = bytes([10, 20, 30, 255]) * 64
SCENE = {"entities": [], "hazards": [], "uncertainties": ["看不清"]}
#: 汇总原因里的会话标签只认契约 `new_id()` 的十六进制形状（复核第 30 节 R1c）。
INVALID_SESSION = "0123456789abcdef0123456789abcdef"


# --------------------------------------------------------------------------- 公共桩


class StubTransport:
    """可编程传输层：按调用序号返回响应或抛异常；只记请求，不发网络。"""

    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.requests: list[HttpRequest] = []

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self._handler(request, len(self.requests) - 1)

    @property
    def calls(self) -> int:
        return len(self.requests)


def ok_response(usage: Any = None, content: Any = None) -> HttpResponse:
    document: dict[str, Any] = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": content or json.dumps(SCENE)},
            }
        ]
    }
    if usage is not None:
        document["usage"] = usage
    return HttpResponse(status=200, body=json.dumps(document).encode("utf-8"))


def failed_response(status: int = 500) -> HttpResponse:
    return HttpResponse(status=status, body=b'{"error": "upstream"}')


async def no_sleep(seconds: float) -> None:
    return None


def model_input(**changes: Any) -> ModelInput:
    options: dict[str, Any] = {
        "prompt": "describe this frame",
        "observation_id": "obs-1",
        "request_id": "req-1",
        "session_id": "sess-1",
        "environment_epoch": 1,
        "created_at_ns": 1_000_000_000,
        "image": FRAME,
        "image_media_type": "image/png",
        "image_ref": "frame-abc",
        "image_size": (8, 8),
    }
    options.update(changes)
    return ModelInput(**options)


def make_provider(transport: StubTransport, **changes: Any) -> OpenAICompatibleProvider:
    options: dict[str, Any] = {
        "model": MODEL,
        "api_key": API_KEY,
        "base_url": "https://api.example.com/v1",
        "transport": transport,
        "clock": FakeClock(0),
        "sleep": no_sleep,
        "max_retries": 0,
        "price_input_per_mtok_usd": 3.0,
        "price_output_per_mtok_usd": 15.0,
        "price_as_of": "2026-01-01",
    }
    options.update(changes)
    return OpenAICompatibleProvider(**options)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class RecordingAccounting:
    """记录授权/入账次序的最小 `Accounting` 桩，可让第 N 次授权失败。"""

    def __init__(self, *, fail_on: int | None = None) -> None:
        self.authorized = 0
        self.recorded = 0
        self.failures: list[str] = []
        self._fail_on = fail_on

    def authorize(self, *, paid: bool, provider: str | None = None) -> None:
        self.authorized += 1
        if self._fail_on is not None and self.authorized >= self._fail_on:
            raise ProviderError("BUDGET_EXCEEDED", "budget exhausted before the request")

    def record(self, response: Any, *, paid: bool | None = None) -> None:
        self.recorded += 1

    def record_failure(self, *, provider: str, failure: str, paid: bool) -> None:
        self.failures.append(failure)


def budget_config(**changes: Any) -> PerceptionConfig:
    options: dict[str, Any] = {
        "provider": "openai_compat",
        "budget_usd": 0.0001,
        "price_input_per_mtok_usd": 3.0,
        "price_output_per_mtok_usd": 15.0,
        "price_as_of": "2026-01-01",
    }
    options.update(changes)
    return PerceptionConfig(**options)


def log_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# --------------------------------------------------------------------------- R1 净化


def test_r1_a_github_token_prefix_in_an_allowed_field_is_redacted(tmp_path):
    """反例原文：`ghp_` + 40 字符令牌曾原样落盘（既不含既有敏感标记，`_` 又让不透明检测失效）。"""
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    path = tmp_path / "r1.jsonl"
    log = EventLog(path, "s-r1")
    log.emit("provider_note", value=token)
    log.close()

    written = log_lines(path)[0]
    assert written["value"] == "[redacted]"
    assert token not in path.read_text(encoding="utf-8")
    stats = log.stats()
    assert stats["redacted"] == 1
    assert stats["redacted_reasons"] == {"value:token_prefix": 1}
    assert stats["rejected"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        # 复核反例：30 字节 URL-safe base64，含 `-`/`_`，旧实现原样落盘。
        "4OHi4-Tl5ufo6err7O3u7_Dx8vP09fb3-Pn6-_z9",
        # 标准 base64：带 `+`/`/`/`=` 填充。
        "sDk3fJ2nQw8Zx1Lp0Vb6Yu4Rc7Tg5Hm2Nk9Wq3As6Df8Gh1Jk4=",
    ],
)
def test_r1_urlsafe_and_standard_base64_blobs_are_dropped(tmp_path, payload):
    path = tmp_path / "r1-blob.jsonl"
    log = EventLog(path, "s-r1-blob")
    log.emit("provider_note", value=payload)
    log.close()

    assert "value" not in log_lines(path)[0]
    assert payload not in path.read_text(encoding="utf-8")
    assert log.stats()["rejected_reasons"] == {"value:opaque_blob": 1}


def test_r1_control_characters_and_trailing_newlines_are_rejected(tmp_path):
    """反例原文：`PRIVATE_SENTINEL\\n` 被保存——`.match()` 的 `$` 允许尾部换行。"""
    cases = {
        "status": "COMPLETED\n",
        "state": "ARMED_IDLE\u0000",
        "mode": "live\r\n",
        "operation": "inf er",
    }
    path = tmp_path / "r1-control.jsonl"
    log = EventLog(path, "s-r1-control")
    log.emit("provider_note", **cases)
    log.close()

    written = log_lines(path)[0]
    assert not set(cases) & set(written)
    text = path.read_text(encoding="utf-8")
    assert "PRIVATE_SENTINEL" not in text
    stats = log.stats()
    assert stats["rejected_reasons"] == {f"{key}:empty_or_control_character": 1 for key in cases}


def test_r1_field_types_are_limited_to_their_declared_shape(tmp_path):
    """按字段定义限形：计数字段不接受布尔、枚举/标识符字段不接受数字。"""
    path = tmp_path / "r1-types.jsonl"
    log = EventLog(path, "s-r1-types")
    log.emit(
        "provider_attempt",
        count=True,  # 布尔不是计数
        pid="42",  # 整数标识符不接字符串
        status=5,  # 枚举不接整数
        state="ARMED_IDLE",  # 合法
        count_extra=None,
    )
    log.close()

    written = log_lines(path)[0]
    assert written["state"] == "ARMED_IDLE"
    assert "count" not in written and "pid" not in written and "status" not in written
    assert log.stats()["rejected_reasons"] == {
        "count:bool_for_int": 1,
        "pid:text_for_int": 1,
        "status:int_for_text": 1,
        "unregistered_field:unknown_field": 1,
    }


def test_r1_unknown_fields_are_counted_as_rejected(tmp_path):
    """复核要求：未登记字段被丢弃时必须增加 `rejected`，不能静默跳过。

    第 28 节 R1a 之后原因码固定为 `unregistered_field:unknown_field`：既保留「有人写了
    越界字段」的信号，又不把未知 key 原样写进会被 CLI 打印的原因串。
    """
    path = tmp_path / "r1-unknown.jsonl"
    log = EventLog(path, "s-r1-unknown")
    log.emit("runtime_outcome", status="COMPLETED", text="hello", api_note="world")
    log.close()

    written = log_lines(path)[0]
    assert written["status"] == "COMPLETED"
    assert log.stats()["rejected"] == 2
    assert log.stats()["rejected_reasons"] == {"unregistered_field:unknown_field": 2}
    # 未知 key 绝不进日志正文或原因串（它们是外部输入，可能夹带凭据/自由文本）。
    assert "text" not in written and "api_note" not in written


# ------------------------------------------------------- R1a 修正件复核（第 28 节）

#: 修正件复核反例：44 字符、全小写+下划线/数字的 URL-safe base64 —— 按旧规则它是
#: 「标识符形状」，`value`/`operation` 都原样落盘。
R1B_PAYLOAD = "zm9vymfyc2vjcmv0x3rva2vu9fzgvulxzly3jlda1234"


def test_r1a_unknown_field_names_never_reach_the_reason_codes(tmp_path):
    """反例原文：`rejected_reasons` 保留原始未知 key，CLI 打印报告时把它带出去。"""
    path = tmp_path / "r1a.jsonl"
    log = EventLog(path, "s-r1a")
    # 未知 key 本身就是外部输入：可能是凭据、转写或任意自由文本。
    log.emit("provider_note", ghp_0123456789abcdefghijklmnopqrstuvwxyz="v", transcript="v")
    log.close()

    stats = log.stats()
    dumped = json.dumps(stats, ensure_ascii=False)
    assert stats["rejected"] == 2
    assert stats["rejected_reasons"] == {"unregistered_field:unknown_field": 2}
    # 未知 key 不得出现在日志正文、原因码或任何会被 CLI 打印的统计字段里。
    assert "ghp_" not in dumped and "transcript" not in dumped
    assert "ghp_" not in path.read_text(encoding="utf-8")
    assert "unregistered_field" in dumped  # 但「有人写了越界字段」这件事必须留下


@pytest.mark.parametrize(
    "field",
    sorted(ALLOWED_FIELDS),
)
def test_r1b_the_reviewer_payload_cannot_land_in_any_allowed_field(tmp_path, field):
    """修正件复核 R1b：44 字符 base64 串（含 `-`/`_`）在每个允许字段里都必须被拒。

    逐字段语义之后没有任何字段接受「任意字符串」：枚举是闭集、动作标识是 uuid hex、
    词形字段 ≤32 字符、数值字段只接受数字、参数值只接受数值或 OSC 地址。
    """
    path = tmp_path / f"r1b-{field}.jsonl"
    log = EventLog(path, f"s-r1b-{field}")
    log.emit("provider_note", **{field: R1B_PAYLOAD})
    log.close()

    written = log_lines(path)[0]
    assert field not in written
    assert log.stats()["rejected"] == 1
    assert R1B_PAYLOAD not in path.read_text(encoding="utf-8")


def test_r1b_the_reviewer_payload_is_not_a_valid_event_name(tmp_path):
    """事件名同理：它也是代码侧常量，不接受任意串（旧实现会把它当合法事件名写入）。"""
    path = tmp_path / "r1b-event.jsonl"
    log = EventLog(path, "s-r1b-event")
    log.emit(R1B_PAYLOAD, mode="offline")
    log.close()

    written = log_lines(path)[0]
    assert written["event"] == "invalid_event"
    assert written["mode"] == "offline"  # 同一次调用里的合法字段不受影响
    assert R1B_PAYLOAD not in path.read_text(encoding="utf-8")
    assert log.stats()["rejected_reasons"] == {"event_name:opaque_blob": 1}


def test_r1b_enum_fields_accept_only_registered_values(tmp_path):
    """枚举字段是闭集：形状合法但未登记的值同样拒绝（原因码 `unregistered_value`）。"""
    path = tmp_path / "r1b-enum.jsonl"
    log = EventLog(path, "s-r1b-enum")
    log.emit(
        "action_result",
        status="COMPLETED",  # 契约 Status 字面量
        state="PAUSED",  # 控制状态机取值
        reason_code="BUDGET_EXCEEDED",  # 登记码
        action_id="0" * 32,  # 契约 new_id() 形状
    )
    log.emit("action_result", status="SOMETHING_ELSE", state="ARMED", reason_code="NOT_A_CODE")
    log.close()

    written = log_lines(path)
    assert written[0]["status"] == "COMPLETED" and written[0]["state"] == "PAUSED"
    assert written[0]["reason_code"] == "BUDGET_EXCEEDED" and written[0]["action_id"] == "0" * 32
    assert not {"status", "state", "reason_code"} & set(written[1])
    assert log.stats()["rejected_reasons"] == {
        "status:unregistered_value": 1,
        "state:unregistered_value": 1,
        "reason_code:unregistered_value": 1,
    }


def test_r1b_action_id_must_be_the_contract_identifier_shape(tmp_path):
    """动作标识只接受 `ActionRequest.action_id`（`uuid4().hex`），不接受任意短名字。"""
    path = tmp_path / "r1b-action.jsonl"
    log = EventLog(path, "s-r1b-action")
    log.emit("action_result", action_id="action-0001", status="COMPLETED")
    log.emit("action_result", action_id="A1B2C3D4E5F6A7B8C9D0E1F2A3B4C5D6", status="COMPLETED")
    log.emit("action_result", action_id="a" * 32, status="COMPLETED")
    log.close()

    written = log_lines(path)
    assert "action_id" not in written[0] and "action_id" not in written[1]
    assert written[2]["action_id"] == "a" * 32


def test_r1b_values_accept_numbers_and_osc_parameter_addresses_only(tmp_path):
    """`value` 只放参数值：契约 `Scalar` 是 `StrictBool|StrictInt|StrictFloat`，没有字符串。"""
    path = tmp_path / "r1b-value.jsonl"
    log = EventLog(path, "s-r1b-value")
    log.emit("parameter_received", operation="GestureLeft", value=3)
    log.emit("parameter_received", operation="VelocityX", value=0.5)
    log.emit("parameter_received", operation="Voice", value=True)
    log.emit("parameter_received", operation="GestureLeft", value="/avatar/parameters/GestureLeft")
    log.emit("parameter_received", operation="GestureLeft", value="hello")
    log.emit("parameter_received", operation="GestureLeft", value="hello world")
    log.close()

    written = log_lines(path)
    assert [entry.get("value") for entry in written[:4]] == [
        3,
        0.5,
        True,
        "/avatar/parameters/GestureLeft",
    ]
    assert "value" not in written[4] and "value" not in written[5]
    assert log.stats()["rejected_reasons"] == {
        "value:text_for_scalar": 1,
        # 含空白的自由文本更早一步就被控制字符规则挡下（顺序是有意的：先挡最脏的）。
        "value:empty_or_control_character": 1,
    }


# --------------------------------------------------------------------------- R2 重试


def test_r2_a_timeout_then_success_is_two_independent_entries():
    """反例 A：首次超时后成功，旧实现只记一次且 `cost_complete=true`。"""

    def handler(request: HttpRequest, index: int) -> HttpResponse:
        if index == 0:
            raise TimeoutError("read timeout after the request left the process")
        return ok_response(usage={"prompt_tokens": 10, "completion_tokens": 4})

    ledger = UsageLedger(budget_usd=1.0)
    provider = make_provider(StubTransport(handler), ledger=ledger, max_retries=1)

    response = run(provider.infer(model_input()))

    assert response.attempts == 2
    assert ledger.calls == 2  # 两次都可能已经计费
    assert ledger.unknown_cost_calls == 1  # 超时那次未知，绝不当 0
    assert ledger.cost_complete is False
    assert ledger.spent_usd_known > 0  # 成功那次的已知成本仍然计入


def test_r2_every_attempt_passes_the_budget_gate_again():
    """反例 B：重试间余额耗尽时旧顺序是 `确保 → http → http`，第二次没有再检查。"""
    transport = StubTransport(lambda request, index: failed_response(500))
    accounting = RecordingAccounting(fail_on=2)
    provider = make_provider(transport, accounting=accounting, max_retries=1)

    with pytest.raises(ProviderError) as info:
        run(provider.infer(model_input()))

    assert info.value.failure == "BUDGET_EXCEEDED"
    assert transport.calls == 1  # 第二次尝试在发出前就被闸门拒绝
    assert accounting.authorized == 2  # 每次尝试都重新授权
    assert accounting.failures == ["HTTP_ERROR"]  # 已发出的那次独立入账


def test_r2_a_refused_attempt_is_not_billed_or_sent():
    transport = StubTransport(lambda request, index: ok_response())
    accounting = RecordingAccounting(fail_on=1)
    provider = make_provider(transport, accounting=accounting, max_retries=2)

    with pytest.raises(ProviderError) as info:
        run(provider.infer(model_input()))

    assert info.value.failure == "BUDGET_EXCEEDED"
    assert transport.calls == 0
    assert accounting.recorded == 0 and accounting.failures == []


# --------------------------------------------------------------------------- R3 接线


def test_r3_the_budget_wrapper_records_and_pauses_and_blocks_the_next_call():
    """复核 R3：旧实现只传裸台账，耗尽后 `budget.snapshot()` 仍是「没发生过」。"""
    budget = SessionBudget.from_config(budget_config(budget_usd=0.0001))
    transport = StubTransport(
        lambda request, index: ok_response(
            usage={"prompt_tokens": 1_000, "completion_tokens": 1_000}
        )
    )
    provider = make_provider(transport, accounting=budget, max_retries=0)

    run(provider.infer(model_input()))

    snapshot = budget.snapshot()
    assert transport.calls == 1
    assert snapshot["calls"] == 1
    assert snapshot["exhausted"] is True
    assert str(snapshot["pause_reason"]).startswith("BUDGET_EXCEEDED")
    assert snapshot["estimated_calls"] == 1

    with pytest.raises(ProviderError) as info:
        run(provider.infer(model_input()))

    assert info.value.failure == "BUDGET_EXCEEDED"
    assert transport.calls == 1  # 没有下一次 HTTP


class _BudgetedVision:
    """把真实离线感知结果包一层「授权 → 调用 → 按价入账」，模拟付费供应商。"""

    def __init__(self, inner: Any, budget: SessionBudget, *, tokens: int) -> None:
        self.inner = inner
        self.budget = budget
        self.tokens = tokens
        self.calls = 0

    async def infer(self, model_input: Any, *, deadline: Any, cancel: Any) -> Any:
        self.budget.authorize(paid=True, provider="review_stub")
        self.calls += 1
        response = await self.inner.infer(model_input, deadline=deadline, cancel=cancel)
        usage = Usage(
            prompt_tokens=self.tokens,
            completion_tokens=self.tokens,
            total_tokens=2 * self.tokens,
            cost_estimated=True,
            cost_as_of="2026-01-01",
            requests=1,
        )
        response = response.model_copy(update={"usage": usage})
        # 组合根的完整接线：发出前 authorize、回来后按价格表入账（R3 的核心）。
        self.budget.record(response, paid=True)
        return response


def test_r3_the_offline_loop_pauses_and_sends_no_second_request():
    """离线 Runtime：预算耗尽 → 控制器 PAUSED、无第二次请求、报告带预算暂停标志。"""
    from vrc_agent.simulation import SyntheticVision, build_simulation, load_scenario

    # 1000/1000 token 按 $3/$15 每 Mtok ≈ $0.018，一次就超过 $0.01 的会话预算。
    budget = SessionBudget.from_config(budget_config(budget_usd=0.01))
    runtime = build_simulation(load_scenario("navigation"))
    vision = _BudgetedVision(SyntheticVision(runtime.controller, []), budget, tokens=1_000)
    runtime.perception = Perception(vision, clock=runtime.clock)

    view = asyncio.run(runtime.run())

    assert view["outcome"] == "BUDGET_EXCEEDED"
    assert runtime.controller.snapshot()["state"] == "PAUSED"
    assert vision.calls == 1  # 第一次调用后耗尽，第二次在发出前就停止
    snapshot = budget.snapshot()
    assert snapshot["exhausted"] is True
    assert str(snapshot["pause_reason"]).startswith("BUDGET_EXCEEDED")

    recorder = SessionRecorder(session_id="s-offline-budget", mode="offline", budget=budget)
    recorder.stage_ms("perception", 5)
    report = recorder.build()
    assert report["usage"]["exhausted"] is True
    assert str(report["usage"]["pause_reason"]).startswith("BUDGET_EXCEEDED")
    assert report["usage"]["estimated_calls"] == 1


# --------------------------------------------------------------------------- R3 组合根


def test_r3_the_cli_report_root_hands_the_budget_to_the_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """R3：CLI 的 report 组合根把 `SessionBudget` 本体交给供应商，而不是 `budget.ledger`。"""
    import vrc_agent.cli as cli
    from vrc_agent.providers import factory

    seen: dict = {}
    real_build_provider = factory.build_provider

    def spy(config, **kwargs):
        seen.update(kwargs)
        return real_build_provider(config, **kwargs)

    # `report` 在函数内 `from ... import build_provider`，所以要打工厂模块的属性。
    monkeypatch.setattr(factory, "build_provider", spy)
    code = cli.main(
        [
            "report",
            "--scenario",
            "estop",
            "--turns",
            "2",
            "--max-steps",
            "2",
            "--state-dir",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert isinstance(seen.get("accounting"), SessionBudget), seen
    assert "ledger" not in seen


# --------------------------------------------------------------------------- R4 未知


def test_r4_a_session_without_a_ledger_is_no_data_not_zero_rates():
    recorder = SessionRecorder(session_id="s-no-ledger", mode="offline")
    recorder.stage_ms("perception", 12)

    report = recorder.build()

    assert report["usage"] is None
    assert report["hourly"]["calls_per_hour"] is None
    assert report["hourly"]["cost_per_hour_usd"] is None
    assert report["hourly"]["tokens_projection_complete"] is False
    assert str(report["hourly"]["reason"]).startswith("NO_DATA")
    assert any("usage ledger" in warning for warning in report["warnings"])


def test_r4_an_empty_usage_snapshot_is_not_a_complete_zero_ledger():
    """反例原文：`aggregate_usage([{"usage": {}}])` 曾得到「全 0 且完整」。"""
    aggregated = aggregate_usage([{"session_id": "a", "usage": {}}])

    assert aggregated["reports_with_usage"] == 0
    assert aggregated["reports_without_usage"] == 1
    assert aggregated["complete"] is False
    assert aggregated["cost_complete"] is False
    assert aggregated["tokens_complete"] is False
    assert aggregated["calls"] == 0  # 0 只是「没有可用台账」，不是「用量为 0」

    hourly = hourly_from_usage(60_000, aggregated)
    assert hourly["calls_per_hour"] is None
    assert hourly["cost_per_hour_usd"] is None
    assert str(hourly["reason"]).startswith("NO_DATA")
    assert hourly["coverage"]["reports_without_usage"] == 1


def test_r4_a_ledger_missing_a_count_key_is_invalid_not_zero():
    """缺调用数/图片数的台账不是「0 次调用、0 张图片」，而是不可用台账（R4）。"""
    partial = {
        "session_id": "a",
        "started_at": "2026-10-15T10:00:00+00:00",
        "finished_at": "2026-10-15T10:01:00+00:00",
        "usage": {
            "tokens_known": 100,
            "unknown_cost_calls": 0,
            "unknown_token_calls": 0,
            "spent_usd_known": 0.0,
        },
    }
    assert usage_defect(partial["usage"]) == "usage.calls is missing"

    aggregated = aggregate_usage([partial])

    assert aggregated["reports_with_usage"] == 0
    assert aggregated["reports_invalid_usage"] == 1
    assert any("calls is missing" in reason for reason in aggregated["invalid_usage_reasons"])
    assert aggregated["complete"] is False

    hourly = hourly_from_usage(60_000, aggregated)
    assert hourly["calls_per_hour"] is None
    assert hourly["images_per_hour"] is None
    assert str(hourly["reason"]).startswith("NO_DATA")
    assert hourly["coverage"]["reports_invalid_usage"] == 1


def test_r4_an_explicitly_incomplete_ledger_stays_incomplete_after_aggregation():
    partial = {
        "session_id": "a",
        "started_at": "2026-10-15T10:00:00+00:00",
        "finished_at": "2026-10-15T10:01:00+00:00",
        "usage": {
            "calls": 2,
            "tokens_known": 100,
            "unknown_token_calls": 1,
            "image_count": 2,
            "spent_usd_known": 0.2,
            "unknown_cost_calls": 1,
            "cost_complete": False,
            "tokens_complete": False,
        },
    }

    aggregated = aggregate_usage([partial])

    assert aggregated["calls"] == 2
    assert aggregated["cost_complete"] is False
    assert aggregated["tokens_complete"] is False

    hourly = hourly_from_usage(aggregated["basis_ms_with_usage"], aggregated)
    assert hourly["cost_per_hour_usd"] is None
    assert hourly["cost_projection_complete"] is False
    assert hourly["tokens_projection_complete"] is False
    assert str(hourly["reason"]).startswith("COST_INCOMPLETE")
    assert hourly["basis_ms"] == 60_000  # 分子与分母同源


def test_r4_missing_usage_keeps_token_completeness_false():
    complete = {
        "session_id": "a",
        "started_at": "2026-10-15T10:00:00+00:00",
        "finished_at": "2026-10-15T10:01:00+00:00",
        "usage": {
            "calls": 1,
            "tokens_known": 50,
            "unknown_token_calls": 0,
            "image_count": 1,
            "spent_usd_known": 0.1,
            "unknown_cost_calls": 0,
            "cost_complete": True,
            "tokens_complete": True,
        },
    }

    aggregated = aggregate_usage([complete, {"session_id": "b"}])

    assert aggregated["tokens_known"] == 50
    assert aggregated["tokens_complete"] is False  # 缺一份台账就不能称完整
    assert aggregated["cost_complete"] is False
    hourly = hourly_from_usage(60_000, aggregated)
    assert hourly["tokens_projection_complete"] is False
    assert hourly["coverage"]["complete"] is False


def test_r4_a_legal_zero_call_ledger_reports_zero_not_unknown():
    """与「缺台账」相反的分支：真的没调用过时，0 是事实而不是未知。"""
    budget = SessionBudget(budget_usd=1.0, provider="openai_compat")
    snapshot = budget.snapshot()

    assert snapshot["calls"] == 0
    assert snapshot["cost_complete"] is True

    hourly = hourly_from_usage(60_000, snapshot)

    assert hourly["calls_per_hour"] == 0.0
    assert hourly["cost_per_hour_usd"] == 0.0
    assert hourly["cost_projection_complete"] is True
    assert hourly["tokens_projection_complete"] is True
    assert hourly["coverage"]["source"] == "single_session"


def test_r4_a_defective_aggregate_is_never_called_complete():
    # 合法台账：本版本快照会写出的计数字段齐全，by_provider 是「供应商 → 次数」映射。
    complete = {
        "calls": 3,
        "image_count": 1,
        "tokens_known": 10,
        "unknown_cost_calls": 0,
        "unknown_token_calls": 0,
        "spent_usd_known": 0.0,
        "cost_complete": True,
        "tokens_complete": True,
        "by_provider": {"openai": 2},
    }
    assert usage_defect(complete) is None
    assert usage_defect({**complete, "calls": -1}) is not None
    assert usage_defect({**complete, "calls": True}) is not None
    assert usage_defect({**complete, "spent_usd_known": float("nan")}) is not None
    assert usage_defect({**complete, "calls": "3"}) is not None
    assert usage_defect({**complete, "cost_complete": "yes"}) is not None
    assert usage_defect({**complete, "by_provider": {"openai": -1}}) is not None

    aggregated = aggregate_usage([{"session_id": INVALID_SESSION, "usage": {"calls": -1}}])

    assert aggregated["reports_invalid_usage"] == 1
    assert aggregated["complete"] is False
    assert aggregated["invalid_usage_reasons"] == [
        f"{INVALID_SESSION}: usage.calls is not a non-negative integer"
    ]


# --------------------------------------------------------------------------- R5 逐文件


def live_report(world: Any, task_id: str, **changes: Any) -> dict:
    document: dict[str, Any] = {
        "mode": "live",
        "sends_live_input": True,
        "suite_digest": world.digest(),
        "task_id": task_id,
        "outcome": "MODEL_TIMEOUT",
        "started_at": "2026-10-15T10:00:00+00:00",
        "finished_at": "2026-10-15T10:01:00+00:00",
    }
    document.update(changes)
    return document


def write_report(directory: Path, name: str, document: dict) -> None:
    (directory / name).write_text(json.dumps(document), encoding="utf-8")


def usage_snapshot(**changes: Any) -> dict:
    snapshot: dict[str, Any] = {
        "calls": 3,
        "tokens_known": 300,
        "unknown_token_calls": 0,
        "image_count": 3,
        "spent_usd_known": 0.3,
        "unknown_cost_calls": 0,
        # 第 28 节 R5b：缺完整性标志的台账无法判断「未知」还是「没花钱」，算损坏。
        "cost_complete": True,
        "tokens_complete": True,
        "by_provider": {"openai_compat": 3},
    }
    snapshot.update(changes)
    return snapshot


def test_r5_a_malformed_usage_value_is_an_invalid_file_not_an_exception(tmp_path):
    """反例原文：`usage.calls="not-a-number"` 在文件级 try 之外抛 ValueError。"""
    from vrc_agent.live_navigation.session import summarize_reports
    from vrc_agent.live_navigation.world import load_live_world

    world = load_live_world()
    first = world.tasks[0].task_id
    write_report(
        tmp_path,
        "broken.json",
        live_report(world, first, usage=usage_snapshot(calls="not-a-number")),
    )
    write_report(tmp_path, "good.json", live_report(world, first, usage=usage_snapshot()))

    summary = summarize_reports(tmp_path)  # 单个坏文件不得打断整批

    assert summary["invalid_files"] == ["broken.json"]
    assert "usage.calls is not a non-negative integer" in summary["invalid_reasons"][0]
    assert summary["usage"]["calls"] == 3
    assert summary["usage"]["reports_invalid_files"] == 1
    assert summary["usage"]["complete"] is False
    assert summary["hourly"]["coverage"]["reports_invalid_files"] == 1
    assert summary["hourly"]["coverage"]["complete"] is False


def test_r5_mixed_timezone_timestamps_are_invalid_instead_of_raising(tmp_path):
    """反例原文：开始带时区、结束不带 → 未处理 `TypeError`。"""
    from vrc_agent.live_navigation.session import summarize_reports
    from vrc_agent.live_navigation.world import load_live_world

    world = load_live_world()
    first = world.tasks[0].task_id
    write_report(
        tmp_path,
        "naive-end.json",
        live_report(world, first, finished_at="2026-10-15T10:01:00"),
    )

    summary = summarize_reports(tmp_path)

    assert summary["invalid_files"] == ["naive-end.json"]
    assert "timezone" in summary["invalid_reasons"][0]
    assert summary["hourly"]["reason"].startswith("NO_DATA")


def test_r5_an_unparseable_start_time_is_invalid_and_cannot_inflate_the_rate(tmp_path):
    """反例原文：坏开始时间被静默剔出时长，调用数却仍进分子。"""
    from vrc_agent.live_navigation.session import summarize_reports
    from vrc_agent.live_navigation.world import load_live_world

    world = load_live_world()
    first, second = (task.task_id for task in world.tasks[:2])
    write_report(tmp_path, "good.json", live_report(world, first, usage=usage_snapshot()))
    write_report(
        tmp_path,
        "no-start.json",
        live_report(
            world,
            second,
            started_at="not-a-timestamp",
            usage=usage_snapshot(calls=100, tokens_known=10_000, image_count=100),
        ),
    )

    summary = summarize_reports(tmp_path)

    assert summary["invalid_files"] == ["no-start.json"]
    assert summary["usage"]["calls"] == 3  # 坏文件的 100 次调用不入分子
    assert summary["hourly"]["basis_ms"] == 60_000  # 分母只算有效的那一份
    assert summary["hourly"]["calls_per_hour"] == 180.0
    assert summary["duration_complete"] is True
    assert summary["elapsed_ms"] == 60_000


def test_r5_a_missing_usage_file_makes_the_batch_partial(tmp_path):
    """合法 + 缺台账混合批次：用量只报已覆盖的部分，并标出不完整。"""
    from vrc_agent.live_navigation.session import summarize_reports
    from vrc_agent.live_navigation.world import load_live_world

    world = load_live_world()
    first, second = (task.task_id for task in world.tasks[:2])
    write_report(tmp_path, "good.json", live_report(world, first, usage=usage_snapshot()))
    write_report(tmp_path, "no-usage.json", live_report(world, second))

    summary = summarize_reports(tmp_path)

    assert summary["invalid_files"] == []
    assert summary["usage"]["calls"] == 3
    assert summary["usage"]["reports_with_usage"] == 1
    assert summary["usage"]["reports_without_usage"] == 1
    assert summary["usage"]["complete"] is False
    assert summary["usage"]["cost_complete"] is False
    assert summary["usage"]["tokens_complete"] is False
    hourly = summary["hourly"]
    assert hourly["basis_ms"] == 60_000  # 只有带台账的那份进分母
    assert hourly["calls_per_hour"] == 180.0
    assert hourly["tokens_projection_complete"] is False
    assert hourly["coverage"] == {
        "source": "aggregated_reports",
        "reports_with_usage": 1,
        "reports_without_usage": 1,
        "reports_invalid_usage": 0,
        "reports_invalid_files": 0,
        "reports_paired": 1,
        "reports_unpaired_duration": 0,
        "basis_ms_with_usage": 60_000,
        "session_elapsed_ms": 120_000,
        "complete": False,
    }


def test_r5_a_provenance_mismatch_is_still_counted_as_invalid(tmp_path):
    """对照项：dry-run 来源的报告不能混进实机成功率（复核已通过的边界不能回退）。"""
    from vrc_agent.live_navigation.session import summarize_reports
    from vrc_agent.live_navigation.world import load_live_world

    world = load_live_world()
    write_report(
        tmp_path,
        "preview.json",
        live_report(
            world,
            world.tasks[0].task_id,
            mode="supervised-dry-run",
            sends_live_input=False,
        ),
    )

    summary = summarize_reports(tmp_path)

    assert summary["invalid_files"] == ["preview.json"]
    assert summary["successes"] == 0
    assert summary["threshold_met"] is False


# ------------------------------------------------- 第 28 节补充：R4b / R5a / R5b 残余反例


class _SnapshotBudget:
    """只提供 `snapshot()` 的台账替身：用来把「损坏台账」喂进报告层。"""

    def __init__(self, snapshot: dict) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> dict:
        return self._snapshot


def test_r4_b_a_defective_ledger_is_unknown_not_a_complete_zero() -> None:
    """第 28 节 R4b：有台账对象但快照损坏时报告不能给出「零用量、零费用」的完整折算。"""
    broken = usage_snapshot(calls=-1)
    recorder = SessionRecorder(
        session_id="s-broken", mode="offline", budget=_SnapshotBudget(broken)
    )

    report = recorder.build()

    assert report["usage"]["calls"] == -1  # 原样保留嫌疑值，不修不折
    assert report["hourly"]["calls_per_hour"] is None
    assert report["hourly"]["cost_per_hour_usd"] is None
    assert str(report["hourly"]["reason"]).startswith("NO_DATA")
    assert any("unusable" in item for item in report["warnings"])


def test_r4_b_a_ledger_missing_the_completeness_flag_is_unknown_not_zero() -> None:
    """缺 `cost_complete` 的台账无法判断「没有未知计费」还是「没记录」，同样只能 NO_DATA。"""
    legacy = usage_snapshot()
    legacy.pop("cost_complete")
    recorder = SessionRecorder(
        session_id="s-legacy", mode="offline", budget=_SnapshotBudget(legacy)
    )

    report = recorder.build()

    assert report["hourly"]["calls_per_hour"] is None
    assert str(report["hourly"]["reason"]).startswith("NO_DATA")
    assert any("usage.cost_complete is missing" in item for item in report["warnings"])


def test_r4_a_a_source_that_says_incomplete_is_never_upgraded_by_the_merge() -> None:
    """数值上都是「已知计费」，但来源自称不完整时，合并与折算都不许把它升级成完整。"""
    windows = {
        "started_at": "2026-10-15T10:00:00+00:00",
        "finished_at": "2026-10-15T10:01:00+00:00",
    }
    merged = aggregate_usage(
        [
            {"session_id": "a", **windows, "usage": usage_snapshot()},
            {"session_id": "b", **windows, "usage": usage_snapshot(cost_complete=False)},
        ]
    )

    assert merged["unknown_cost_calls"] == 0  # 数值层面没有「可能计费但算不出价」的调用
    assert merged["cost_complete"] is False  # 但完整性标志来自来源，不能被合并抹平
    assert merged["reports_paired"] == 2

    hourly = hourly_from_usage(120_000, merged)

    assert hourly["cost_per_hour_usd"] is None
    assert hourly["cost_projection_complete"] is False
    assert str(hourly["reason"]).startswith("COST_INCOMPLETE")


def test_r5_b_a_huge_count_is_invalid_without_breaking_the_batch(tmp_path):
    """第 28 节 R5b：`10**400` 这种巨数在文件边界被拒，整批汇总不受影响。"""
    from vrc_agent.live_navigation.session import summarize_reports
    from vrc_agent.live_navigation.world import load_live_world

    world = load_live_world()
    first, second = (task.task_id for task in world.tasks[:2])
    write_report(tmp_path, "good.json", live_report(world, first, usage=usage_snapshot()))
    write_report(
        tmp_path, "huge.json", live_report(world, second, usage=usage_snapshot(calls=10**400))
    )

    summary = summarize_reports(tmp_path)

    assert summary["invalid_files"] == ["huge.json"]
    assert summary["usage"]["calls"] == 3  # 嫌疑值不进分子
    assert summary["usage"]["reports_invalid_files"] == 1
    assert summary["usage"]["complete"] is False
    assert summary["hourly"]["calls_per_hour"] == 180.0
    assert any("huge.json" in reason for reason in summary["invalid_reasons"])


def test_r5_b_a_huge_amount_is_invalid_not_an_overflow(tmp_path):
    """巨数金额走的是「无效文件」而不是 `OverflowError` 打断整批。"""
    from vrc_agent.live_navigation.session import summarize_reports
    from vrc_agent.live_navigation.world import load_live_world

    world = load_live_world()
    first, second = (task.task_id for task in world.tasks[:2])
    write_report(tmp_path, "good.json", live_report(world, first, usage=usage_snapshot()))
    write_report(
        tmp_path,
        "overflow.json",
        live_report(world, second, usage=usage_snapshot(spent_usd_known=10**400)),
    )

    summary = summarize_reports(tmp_path)

    assert summary["invalid_files"] == ["overflow.json"]
    assert summary["usage"]["spent_usd_known"] == 0.3
    assert "overflow.json" in summary["usage"]["invalid_files"]
    # 坏文件会拉低整体完整性，费用折算因此不给数（未知不折价）；速率仍按好文件折算。
    assert summary["hourly"]["cost_per_hour_usd"] is None
    assert summary["hourly"]["calls_per_hour"] == 180.0


def test_r5_b_a_file_boundary_reason_is_truncated_and_single_line(tmp_path):
    """坏文件原因要单行且截断：汇总输出不能把整份载荷（可能含敏感串）回显出去。"""
    from vrc_agent.live_navigation.session import summarize_reports
    from vrc_agent.live_navigation.world import load_live_world

    world = load_live_world()
    payload = "x" * 500 + "\n" + "y" * 50
    write_report(tmp_path, "noisy.json", live_report(world, payload))

    summary = summarize_reports(tmp_path)

    assert summary["invalid_files"] == ["noisy.json"]
    reason = summary["invalid_reasons"][0]
    assert "\n" not in reason
    assert len(reason) <= 240
    assert payload not in json.dumps(summary)  # 原始超长载荷不被回显


def test_r5_a_reports_without_duration_are_excluded_from_rate_and_basis(tmp_path):
    """第 28 节 R5a：没有可测时长的报告，其调用数既不进分子也不进分母。"""
    from vrc_agent.live_navigation.session import summarize_reports
    from vrc_agent.live_navigation.world import load_live_world

    world = load_live_world()
    first, second = (task.task_id for task in world.tasks[:2])
    write_report(
        tmp_path,
        "timed.json",
        live_report(world, first, usage=usage_snapshot(calls=2, tokens_known=20, image_count=2)),
    )
    write_report(
        tmp_path,
        "no-time.json",
        live_report(
            world,
            second,
            started_at=None,
            finished_at=None,
            usage=usage_snapshot(calls=100, tokens_known=10_000, image_count=100),
        ),
    )

    summary = summarize_reports(tmp_path)
    usage = summary["usage"]

    assert usage["reports_with_usage"] == 2  # 两份台账都合法
    assert usage["reports_paired"] == 1  # 但只有一份同时有可测时长
    assert usage["reports_unpaired_duration"] == 1
    assert usage["complete"] is False
    assert usage["calls"] == 102  # 汇总总量仍如实给出 102 次
    assert summary["duration_complete"] is False
    hourly = summary["hourly"]
    assert hourly["basis_ms"] == 60_000  # 分母只算有台账且有计时的那一份
    assert hourly["calls_per_hour"] == 120.0  # 2 次 / 60 秒，而 102 次不能拿来充速率
    assert hourly["coverage"]["reports_unpaired_duration"] == 1
    assert hourly["coverage"]["complete"] is False


# --------------------------------------------- 第三轮复核（TASKS 第 30 节）R1c / R4c


def test_r1c_a_dynamic_class_name_never_reaches_the_reason_codes(tmp_path):
    """反例原文：非标量原因拼 `type(value).__name__`，用合成令牌当类名即可写进报告。

    `rejected_reasons` 会随 `stats()` 进 CLI 报告，所以原因里只允许登记类别或 `other`，
    任何类名（可能夹带凭据形状的串）都不得成为出口。
    """
    token = "ghp_" + "q" * 36
    dynamic = type(token, (), {})
    assert dynamic.__name__ == token  # 类名就是合成令牌本身

    path = tmp_path / "r1c-dynamic.jsonl"
    log = EventLog(path, "s-r1c-dynamic")
    log.emit("action_result", value=dynamic())
    log.close()

    stats = log.stats()
    assert stats["rejected"] == 1
    assert stats["rejected_reasons"] == {"value:non_scalar:other": 1}
    assert token not in json.dumps(stats, ensure_ascii=False)
    assert log_lines(path)[0].get("value") is None  # 实例本身也不落盘


def test_r1c_non_scalar_reasons_are_a_closed_set_of_registered_builtins(tmp_path):
    """只在原因里回登记过的内建容器类别，其余对象（含子类/自定义类）一律 `other`。"""
    from vrc_agent.telemetry import NON_SCALAR_KINDS

    samples = {
        "dict": {},
        "list": [],
        "tuple": (),
        "set": set(),
        "frozenset": frozenset(),
        "bytes": b"x",
        "bytearray": bytearray(b"x"),
        "memoryview": memoryview(b"x"),
    }
    path = tmp_path / "r1c-closed.jsonl"
    log = EventLog(path, "s-r1c-closed")
    for sample in samples.values():
        log.emit("action_result", value=sample)
    for index in range(3):
        token = f"ghp_{index}" + "z" * 32
        log.emit("action_result", value=type(token, (), {})())
    log.close()

    stats = log.stats()
    assert set(stats["rejected_reasons"]) == {
        *(f"value:non_scalar:{name}" for name in samples),
        "value:non_scalar:other",
    }
    # 类别集合有界：任何输入都不能造出登记表之外的原因码。
    categories = {reason.split(":", 2)[2] for reason in stats["rejected_reasons"]}
    assert categories <= set(NON_SCALAR_KINDS.values()) | {"other"}
    assert "ghp_" not in json.dumps(stats, ensure_ascii=False)


def test_r4_c_a_ledger_missing_the_amount_is_invalid_not_zero():
    """反例原文：删掉 `spent_usd_known` 后仍得到 `cost_complete=true`、每小时 0.0 美元。"""
    complete = usage_snapshot()
    stripped = {key: value for key, value in complete.items() if key != "spent_usd_known"}

    assert usage_defect(stripped) == "usage.spent_usd_known is missing"

    aggregated = aggregate_usage([{"session_id": INVALID_SESSION, "usage": stripped}])

    assert aggregated["reports_invalid_usage"] == 1
    assert aggregated["invalid_usage_reasons"] == [
        f"{INVALID_SESSION}: usage.spent_usd_known is missing"
    ]
    assert aggregated["complete"] is False
    assert aggregated["cost_complete"] is False  # 费用完整性不得因缺失而升级
    hourly = hourly_from_usage(60_000, aggregated)
    assert hourly["cost_per_hour_usd"] is None
    assert hourly["cost_projection_complete"] is False
    assert hourly["reason"] is not None


def test_r4_c_a_legitimate_zero_amount_is_still_complete():
    """对照：真正花掉 0 美元的台账仍旧合法，折算出「完整」的零费用。"""
    zero = usage_snapshot(
        calls=0,
        tokens_known=0,
        unknown_token_calls=0,
        image_count=0,
        spent_usd_known=0.0,
        unknown_cost_calls=0,
        by_provider={},
    )
    assert usage_defect(zero) is None

    aggregated = aggregate_usage(
        [
            {
                "session_id": "zero",
                "started_at": "2026-10-15T10:00:00+00:00",
                "finished_at": "2026-10-15T10:01:00+00:00",
                "usage": zero,
            }
        ]
    )

    assert aggregated["reports_invalid_usage"] == 0
    assert aggregated["complete"] is True
    assert aggregated["cost_complete"] is True
    assert aggregated["spent_usd_known"] == 0.0
    hourly = hourly_from_usage(60_000, aggregated)
    assert hourly["calls_per_hour"] == 0.0
    assert hourly["cost_per_hour_usd"] == 0.0
    assert hourly["cost_projection_complete"] is True
    assert hourly["reason"] is None


def test_r4_c_a_mixed_batch_with_one_missing_amount_is_not_complete(tmp_path):
    """混合批次：坏文件既不在分子也不在分母，整体费用完整性如实标为不完整。"""
    from vrc_agent.live_navigation.session import summarize_reports
    from vrc_agent.live_navigation.world import load_live_world

    world = load_live_world()
    first, second = (task.task_id for task in world.tasks[:2])
    good = usage_snapshot(
        calls=1,
        tokens_known=10,
        unknown_token_calls=0,
        image_count=1,
        spent_usd_known=0.1,
        unknown_cost_calls=0,
        by_provider={"openai_compat": 1},
    )
    broken = {key: value for key, value in good.items() if key != "spent_usd_known"}
    write_report(tmp_path, "good.json", live_report(world, first, usage=good))
    write_report(tmp_path, "no-amount.json", live_report(world, second, usage=broken))

    summary = summarize_reports(tmp_path)

    assert summary["invalid_files"] == ["no-amount.json"]
    usage = summary["usage"]
    assert usage["reports_invalid_files"] == 1
    assert usage["reports_invalid_usage"] == 0  # 坏文件在文件边界就被拒，不进聚合
    assert usage["complete"] is False
    assert usage["cost_complete"] is False
    hourly = summary["hourly"]
    assert hourly["calls_per_hour"] == 60.0  # 好文件 1 次 / 60 秒仍如实折算
    assert hourly["cost_per_hour_usd"] is None
    assert hourly["cost_projection_complete"] is False


def test_r1c_report_layer_reasons_do_not_echo_untrusted_names():
    """同一原则适用于报表层：缺陷说明与汇总原因不得回显报告文件里的任意名字。

    报告文件是不可信输入：供应商名与 `session_id` 都可能被写成令牌或超长载荷，而
    `invalid_usage_reasons` 会被 CLI 打印，所以原因里只允许固定码、序号与十六进制会话标签。
    """
    token = "ghp_" + "r" * 36
    hostile = usage_snapshot(by_provider={f"{token}\n\t" + "x" * 200: -1})

    defect = usage_defect(hostile)
    assert defect == "usage.by_provider[0] is not a non-negative integer"
    assert token not in defect and "\n" not in defect

    stripped = {key: value for key, value in usage_snapshot().items() if key != "spent_usd_known"}
    aggregated = aggregate_usage([{"session_id": token, "usage": stripped}])

    assert aggregated["invalid_usage_reasons"] == [
        "unknown: usage.spent_usd_known is missing"  # 非十六进制标签不回显
    ]
    assert token not in json.dumps(aggregated, ensure_ascii=False)
