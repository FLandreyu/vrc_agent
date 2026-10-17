"""不阻塞控制路径的最小结构化事件记录。

写入策略（T10；2026-10-16 独立复核 R1、修正件复核第 28 节 R1a/R1b、第三轮复核第 30 节 R1c 修正）：

- **字段白名单 + 逐字段语义**：只有登记过的字段名会落盘，且每个字段有自己的取值语义
  （`FIELD_KINDS`）。语义不是「一串字符」：枚举字段只接受登记值（`ENUM_VALUES`，
  来自 `telemetry.codes` 的闭集与契约里的事件状态字面量），动作标识只接受契约
  `new_id()` 的形状，参数值只接受数值或 OSC 参数地址，事件名只接受小写下划线常量形状。
  未知字段名与未知枚举值都计入 `rejected`，「调用方写了不该写的字段/值」本身就是要
  被看见的信号。
- **原因码是固定的，不回显输入**（复核 R1a/R1c）：`rejected_reasons`/`redacted_reasons` 写进
  结构化日志、并会被 CLI 打印，所以原因里只允许固定枚举（`unknown_field`、
  `unregistered_value`、`non_scalar:dict` 这类）。**未登记字段名不进原因串**，非标量也只回
  **登记过的内建类别**（`NON_SCALAR_KINDS`，其余一律 `other`），**不拼接任意类型名** ——
  否则「诊断」就成了把任意未知 key/类名（可能夹带凭据）搬到报告里的泄露出口。
- **值净化按固定顺序判定**：凭据/媒体标记（命中写 `[redacted]` 并计数）→ 令牌前缀 →
  空白与控制字符（含尾部换行）→ 长度 → 不透明长串 → 字段语义（`fullmatch`，不再允许
  `$` 尾随换行）。不透明长串覆盖 URL-safe base64：`-`、`_` 只是普通字符，也不构成
  「这是标识符」的证明。
- **保证范围（如实限定）**：本模块只保证「不符合字段语义/形状的输入不会整串落盘」，不是
  密码学检测，也不保证识别值的真实来源或信息含义。逐字段边界不同：`operation`/`mode` 是
  词形短串（`WORD_TEXT`），语义上是操作/模式名，**形状合法的短词会被接受**（不保证它是
  可信常量，也不保证它不含信息）；`value` 只接受数值或 OSC 参数地址（`OSC_PATH_TEXT`），
  普通随机字符串会被拒；枚举字段只认登记值；`action_id` 只认契约 `new_id()` 的十六进制
  形状；`pid`/`count` 只认整数。自由文本、转写、媒体协议串都不在这些字段的形状内。
- **不阻塞**：写盘在线程里做，队列满就丢弃并计数（`dropped`）；控制路径永远不等 I/O。
- **可诊断**：`stats()` 给出写入/丢弃/净化/拒绝/失败计数，报告据此说明日志是否完整。
"""

import json
import math
import queue
import re
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args
from uuid import uuid4

ALLOWED_FIELDS = frozenset(
    {
        "action_id",
        "status",
        "state",
        "reason_code",
        "pid",
        "mode",
        "count",
        "operation",
        "value",
        "at_ns",
        "granted_ms",
        "expires_ns",
        "sequence",
    }
)

#: 字段取值语义。字段名和语义一起构成白名单：类型/取值不对的字段同样进 `rejected`。
KIND_EVENT = "event"
KIND_ACTION_ID = "action_id"
KIND_ENUM = "enum"
KIND_WORD = "word"
KIND_INT = "int"
KIND_SCALAR = "scalar"

#: 每个允许字段的语义（复核第 28 节 R1b：不再让「一串字符」通过，逐字段给语义）：
#:
#: - `action_id` 是契约 `ActionRequest.action_id`（`new_id()` = `uuid4().hex`）；
#: - `mode`/`operation` 是代码侧的词形常量或 OSC 参数名（字母开头、词字符与短横线、≤32）；
#: - `status`/`state`/`reason_code` 是闭集（登记码 + 状态字面量），未知值一律拒绝；
#: - `pid`/`count` 与续租的 `at_ns/granted_ms/expires_ns/sequence` 只接受整数；
#:   `value` 只接受数值或 OSC 参数地址（`Scalar` 里没有字符串）。
FIELD_KINDS: dict[str, str] = {
    "action_id": KIND_ACTION_ID,
    "mode": KIND_WORD,
    "operation": KIND_WORD,
    "status": KIND_ENUM,
    "state": KIND_ENUM,
    "reason_code": KIND_ENUM,
    "pid": KIND_INT,
    "count": KIND_INT,
    "value": KIND_SCALAR,
    "at_ns": KIND_INT,
    "granted_ms": KIND_INT,
    "expires_ns": KIND_INT,
    "sequence": KIND_INT,
}


def _literal_values(annotation: object) -> frozenset[str]:
    """取 `Literal[...]` 里登记的字符串取值（契约是闭集的唯一来源）。"""
    return frozenset(value for value in get_args(annotation) if isinstance(value, str))


#: 控制状态机的取值。`control/core.py` 直接写字符串字面量、没有导出常量，这里显式登记
#: （telemetry 不能反过来 import control：control 依赖本模块，反过来会成环）。
CONTROL_STATES: frozenset[str] = frozenset(
    {"DISARMED", "ARMED_IDLE", "EXECUTING", "PAUSED", "ESTOP"}
)

_ENUM_VALUES: frozenset[str] | None = None


def enum_values() -> frozenset[str]:
    """枚举字段的闭集：登记码表（各层 `Literal` 的汇总）+ 契约状态字面量 + 控制/半双工状态
    + 供应商层的非码状态字。**不在这个集合里的字符串一律拒绝**，不再「按形状放行」。

    懒加载是不得已：`telemetry` 被 `control.service`（进而 `dialogue.chatbox`）反向依赖，
    导入期去 import 这些模块会成环（实测 `ImportError: cannot import name 'EventLog' from
    partially initialized module 'vrc_agent.telemetry'`）。首次用到枚举字段时它们都已加载完。
    """
    global _ENUM_VALUES
    if _ENUM_VALUES is None:
        from vrc_agent.contracts import Status
        from vrc_agent.dialogue.halfduplex import HalfDuplexState

        from .codes import KNOWN_CODES

        _ENUM_VALUES = (
            frozenset(KNOWN_CODES)
            | _literal_values(Status)
            | _literal_values(HalfDuplexState)
            | CONTROL_STATES
            | frozenset({"ok", "partial", "error", "failed"})
        )
    return _ENUM_VALUES


#: 疑似凭据或原始媒体的片段；命中即净化（大小写不敏感）。这一层故意「宁可多报」：
#: 命中写占位符而不是丢字段，留下「有人试图写敏感值」的痕迹。
SENSITIVE_MARKERS: tuple[str, ...] = (
    "sk-",
    "bearer ",
    "bearer:",
    "authorization",
    "api_key",
    "apikey",
    "x-api-key",
    "data:image/",
    "data:audio/",
    "-----begin",
    "password",
    "passwd",
    "secret",
    "credential",
    "private_key",
    "client_secret",
    "access_token",
    "refresh_token",
    "id_token",
    "session_token",
    "cookie",
)

#: 常见令牌前缀（大小写不敏感，按开头匹配）：`ghp_…` 这类合成令牌没有元数据可依赖，
#: 只能按前缀识别 —— 旧版本不认识它们，令牌因此原样落盘（复核 R1 反例）。
TOKEN_PREFIXES: tuple[str, ...] = (
    "sk-",
    "sk_live_",
    "sk_test_",
    "rk_live_",
    "pk_live_",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "glpat-",
    "xoxa-",
    "xoxb-",
    "xoxp-",
    "xoxr-",
    "xoxs-",
    "akia",
    "asia",
    "aiza",
    "ya29.",
    "eyj",
    "hf_",
    "npm_",
    "pypi-",
    "dop_v1_",
)

#: 字符串字段的最大长度；超过视为可能是密文/媒体/base64，宁可丢也不写。
MAX_FIELD_TEXT = 64

#: 整数字段的上限：超过 2^53 的值在 JSON 往返里不再精确，宁可不写。
MAX_INT_VALUE = 2**53

#: 未指定语义时的宽松形状（公开辅助函数 `sanitize_value` 用）：ASCII 标识符/短名字/路径。
#: 事件名不走这里——`EventLog.emit` 用 `KIND_EVENT`（更窄的常量形状）。
IDENTIFIER_TEXT = re.compile(r"[A-Za-z0-9_./\\:+-][A-Za-z0-9_.:/\\+-]{0,%d}" % (MAX_FIELD_TEXT - 1))

#: 枚举形状：字母开头、只含字母数字与下划线，没有路径分隔符。
ENUM_TEXT = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,%d}" % (MAX_FIELD_TEXT - 1))

#: 事件名：代码侧常量，小写下划线、≤40 字符（`session_start`、`provider_attempt`…）。
#: 事件名不是自由文本入口：任意编码块（含大小写混用/`-`/`+`/`/`/`=`/超长）都进不来。
EVENT_TEXT = re.compile(r"[a-z][a-z0-9_]{0,39}")

#: 词形标识符：`mode`/`operation`（`offline`、`supervised-dry-run`、`GestureLeft`…）。
#: ≤32 字符，字母开头，只含词字符与短横线。
WORD_TEXT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")

#: 动作标识：契约 `new_id()` = `uuid4().hex`（32 位小写十六进制）。
ACTION_ID_TEXT = re.compile(r"[0-9a-f]{32}")

#: OSC 参数地址：`/avatar/parameters/xxx`（参数样本落盘时只接受这个形状）。
OSC_PATH_TEXT = re.compile(r"/avatar/parameters/[A-Za-z0-9_]{1,48}")

#: 字段形状表；枚举字段没有形状正则（走 `enum_values()` 闭集判定）。
PATTERNS_BY_KIND = {
    KIND_EVENT: EVENT_TEXT,
    KIND_ACTION_ID: ACTION_ID_TEXT,
    KIND_WORD: WORD_TEXT,
    KIND_ENUM: ENUM_TEXT,
}

#: 只接受字符串的字段语义：整数/浮点出现在这些字段里是调用方写错了。
TEXT_KINDS: frozenset[str] = frozenset({KIND_EVENT, KIND_ACTION_ID, KIND_ENUM, KIND_WORD})

#: 非标量拒绝原因只回**登记过的内建容器类别**（复核第 30 节 R1c）：任意对象与其子类
#: 一律 `other`，绝不拼接 `type(value).__name__` —— 否则诊断计数会变成任意类名（可能
#: 夹带合成令牌）的出口，类别集合也会无界扩张。
NON_SCALAR_KINDS: dict[type, str] = {
    dict: "dict",
    list: "list",
    tuple: "tuple",
    set: "set",
    frozenset: "frozenset",
    bytes: "bytes",
    bytearray: "bytearray",
    memoryview: "memoryview",
}

#: 长串（base64/hex/URL-safe base64）里的「一段不透明字符」：`-`、`_` 与 `+`、`/`、`=`
#: 同等对待，不能因为出现分隔符就认为它是标识符。
OPAQUE_RUN = re.compile(r"[A-Za-z0-9+/=_-]{32,}")

#: 混合字符类（大写+小写+数字）且达到这个长度时按编码块处理：这是唯一不依赖分隔符的
#: 不透明判据，覆盖「全小写/全大写但有分隔符」之外的随机串（复核第 28 节 R1b）。
MIXED_CLASS_MIN_LENGTH = 24

#: 整串都是 base64 字符表（没有分隔符）时的长度阈值。阈值必须**高于**合法的 32 位
#: uuid4().hex —— 契约里的 `action_id` 就是这种形状，误判会把控制路径的关键字段丢掉
#: （曾把 `action_result.action_id` 整列过滤掉）。64 位 sha256 摘要这种真摘要仍然会被丢。
OPAQUE_TEXT = re.compile(r"[A-Za-z0-9+/=]+")
OPAQUE_MIN_LENGTH = 40

REDACTED = "[redacted]"

KEEP = "keep"
REDACT = "redact"
DROP = "drop"


def _is_forbidden_character(character: str) -> bool:
    """空白与控制字符（含换行）：任何字段值里出现都视为不是标识符。"""
    return character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F


def _looks_opaque(value: str) -> bool:
    """不透明长串判定：URL-safe base64 的 `-`/`_` 不再是豁免理由（复核 R1）。

    第三种判据不依赖分隔符：**大小写+数字混合且足够长**的串按编码块处理。这条是
    「未知字符串按标识符形状放行」的补丁（复核第 28 节 R1b：`-`/`_` 把字符段切碎后，
    前面的字符段规则一条都不命中）。
    """
    for run in OPAQUE_RUN.findall(value):
        if any(character in "+/=" for character in run):
            return True
        has_upper = any(character.isupper() for character in run)
        has_lower = any(character.islower() for character in run)
        has_digit = any(character.isdigit() for character in run)
        # 大小写混用且带数字（或足够长）的字符段：base64/令牌的典型形态。
        if has_upper and has_lower and (has_digit or len(run) >= OPAQUE_MIN_LENGTH):
            return True
    if (
        len(value) >= MIXED_CLASS_MIN_LENGTH
        and any(character.isupper() for character in value)
        and any(character.islower() for character in value)
        and any(character.isdigit() for character in value)
    ):
        return True
    return len(value) >= OPAQUE_MIN_LENGTH and OPAQUE_TEXT.fullmatch(value) is not None


def _classify(value: object, kind: str | None) -> tuple[str, object, str]:
    """返回 `(处置, 值, 原因)`；原因用于 `stats()['rejected_reasons']`，便于定位调用方。

    判定顺序：类型（按 `kind` 限形）→ 凭据/媒体标记 → 空白与控制字符 → 长度 →
    不透明长串 → 字段形状。先看标记是故意的：命中标记要留下「有人试图写敏感值」的
    痕迹（`[redacted]`），而不是静默丢弃。
    """
    if value is None:
        return KEEP, None, "null"
    if isinstance(value, bool):
        # 布尔字段不是计数值：`count=True` 这种写法在旧实现里会被原样写进日志。
        if kind == KIND_INT:
            return DROP, None, "bool_for_int"
        return KEEP, value, "bool"
    if isinstance(value, int):
        if abs(value) > MAX_INT_VALUE:
            return DROP, None, "int_too_large"
        if kind in TEXT_KINDS:
            return DROP, None, "int_for_text"
        return KEEP, value, "int"
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return DROP, None, "float_not_finite"
        if kind in TEXT_KINDS or kind == KIND_INT:
            return DROP, None, "float_for_text"
        return KEEP, value, "float"
    if isinstance(value, str):
        if kind == KIND_INT:
            # `pid="42"` / `count="3"` 这类字符串计数不是计数：类型限形要真的限住。
            return DROP, None, "text_for_int"
        lowered = value.lower()
        if any(marker in lowered for marker in SENSITIVE_MARKERS):
            return REDACT, REDACTED, "sensitive_marker"
        if lowered.startswith(TOKEN_PREFIXES):
            return REDACT, REDACTED, "token_prefix"
        if not value or any(_is_forbidden_character(character) for character in value):
            return DROP, None, "empty_or_control_character"
        if len(value) > MAX_FIELD_TEXT:
            return DROP, None, "too_long"
        if _looks_opaque(value):
            return DROP, None, "opaque_blob"
        pattern = PATTERNS_BY_KIND.get(kind or "")
        if pattern is not None and pattern.fullmatch(value) is None:
            return DROP, None, f"not_{kind}"
        if kind == KIND_ENUM:
            if value not in enum_values():
                # 原因串只写固定码，不回显被拒的值（复核 R1a：诊断不是泄露出口）。
                return DROP, None, "unregistered_value"
            return KEEP, value, "enum"
        if kind == KIND_SCALAR and OSC_PATH_TEXT.fullmatch(value) is None:
            # 契约 `Scalar = StrictBool | StrictInt | StrictFloat`：字符串不是参数值。
            # 参数样本落盘只接受 OSC 参数地址这一种字符串。
            return DROP, None, "text_for_scalar"
        if IDENTIFIER_TEXT.fullmatch(value) is None:
            return DROP, None, "not_an_identifier"
        return KEEP, value, "text"
    # dict / list / bytes / 任意对象：可能夹带媒体或整段不可信文本，一律不写。
    # 原因只回登记类别或 `other`，不拼接任意类型名（复核第 30 节 R1c）。
    return DROP, None, f"non_scalar:{NON_SCALAR_KINDS.get(type(value), 'other')}"


def sanitize_value(value: object, *, kind: str | None = None) -> tuple[str, object]:
    """返回 `(处置, 值)`：`KEEP` 原样写、`REDACT` 写占位符、`DROP` 不写。

    `kind` 来自 `FIELD_KINDS`；`None`（默认）表示「宽松标识符形状」，只给公开辅助函数
    与测试用——`EventLog.emit` 对事件名用 `KIND_EVENT`、对字段用逐字段语义。
    """
    verdict, cleaned, _ = _classify(value, kind)
    return verdict, cleaned


def sanitize_field(key: str, value: object) -> tuple[str, object, str]:
    """按字段名净化：未登记字段名本身就是拒绝原因（复核 R1 的计数要求）。

    原因串固定为 `unknown_field`，**不带原始 key**（复核第 28 节 R1a）：原因码会进
    结构化日志并被 CLI 打印，把未知 key 原样搬过去就是把诊断变成泄露出口。
    """
    if key not in ALLOWED_FIELDS:
        return DROP, None, "unknown_field"
    return _classify(value, FIELD_KINDS[key])


class EventLog:
    def __init__(self, path: Path, session_id: str):
        self.path = path
        self.session_id = session_id
        self.queue: queue.Queue = queue.Queue(maxsize=1024)
        self.dropped = 0
        self.redacted = 0
        self.rejected = 0
        self.written = 0
        self.failed = False
        #: 拒绝原因 → 次数（复核 R1：未知字段被静默丢弃时，报告看不出调用方越界）。
        self.rejected_reasons: Counter[str] = Counter()
        #: 净化原因 → 次数（有人试图写令牌时，「被替换成占位符」也应当可诊断）。
        self.redacted_reasons: Counter[str] = Counter()
        self.thread = threading.Thread(target=self._write, daemon=True)
        self.thread.start()

    def _reject(self, reason: str) -> None:
        self.rejected += 1
        self.rejected_reasons[reason] += 1

    def emit(self, event: str, **fields) -> None:
        verdict, name, reason = _classify(event, KIND_EVENT)
        if verdict != KEEP or not isinstance(name, str) or not name:
            self._reject(f"event_name:{reason}")
            name = "invalid_event"
        record = {
            "schema_version": 1,
            "event_id": uuid4().hex,
            "session_id": self.session_id,
            "event": name,
            "monotonic_ns": time.monotonic_ns(),
            "utc": datetime.now(UTC).isoformat(),
        }
        for key, value in fields.items():
            # 未登记字段名在原因码里一律折叠成同一个固定标签：不能把任意 key 带进日志。
            label = key if key in ALLOWED_FIELDS else "unregistered_field"
            verdict, cleaned, reason = sanitize_field(key, value)
            if verdict == KEEP:
                record[key] = cleaned
            elif verdict == REDACT:
                record[key] = cleaned
                self.redacted += 1
                self.redacted_reasons[f"{key}:{reason}"] += 1
            else:
                self._reject(f"{label}:{reason}")
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.dropped += 1

    def _write(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                while (event := self.queue.get()) is not None:
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
                    stream.flush()
                    self.written += 1
        except OSError:
            self.failed = True

    def stats(self) -> dict[str, object]:
        """日志自身的完整性视图；报告据此说明「这份报告的事件是否齐全」。"""
        return {
            "path": str(self.path),
            "written": self.written,
            "dropped": self.dropped,
            "redacted": self.redacted,
            "rejected": self.rejected,
            "rejected_reasons": dict(sorted(self.rejected_reasons.items())),
            "redacted_reasons": dict(sorted(self.redacted_reasons.items())),
            "pending": self.queue.qsize(),
            "failed": self.failed,
        }

    def close(self) -> None:
        try:
            self.queue.put(None, timeout=0.1)
        except queue.Full:
            return
        self.thread.join(timeout=1.0)


__all__ = [
    "ALLOWED_FIELDS",
    "CONTROL_STATES",
    "DROP",
    "FIELD_KINDS",
    "KEEP",
    "MAX_FIELD_TEXT",
    "NON_SCALAR_KINDS",
    "REDACT",
    "REDACTED",
    "SENSITIVE_MARKERS",
    "TOKEN_PREFIXES",
    "EventLog",
    "enum_values",
    "sanitize_field",
    "sanitize_value",
]
