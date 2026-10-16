# T10 独立交叉复核（2026-10-16）

复核者：Codex / root；实现者：DSH / Flash。对象为 TASKS 第 23 节及 `T10_telemetry.md` 的四项声明。**结论：发现可复现反例，退回修正；T10 保留 IN_PROGRESS。** 现有门禁通过不代表本次复核通过。本轮未修改产品代码、项目测试、配置或依赖；未使用真实密钥、付费服务、live 控制或真实媒体。

## 方法与证据

先核对当前源码，再自建探针 `.runtime/verify/v_t10_review.py`；不导入实现者测试、fixture 或期望值。仅用合成数据、临时目录和内存 HTTP transport，直接调用生产 `EventLog`、成本/汇总函数及真实供应商适配器。运行：

```powershell
.\.venv\Scripts\python.exe .runtime/verify/v_t10_review.py
```

输出归档 `.runtime/verify/v_t10_review.json`。23 项检查，7 项符合预期、16 项发现反例；这不是 16 个互不相关的缺陷，归并如下。脚本退出码 0 只表示证据收集完成，应看 `failed=16`；不能作为通过门禁。首轮运行即复现，未修正探针后选择性隐藏结果。

## R1：P1，字段白名单后的值净化可绕过

位置：`src/vrc_agent/telemetry/__init__.py:56`、`:61`、`:97`、`:101`、`:135`。

- 向允许字段 `value` 写入合成 `ghp_` + 40 字符令牌，日志原样保存。它不含现有敏感标记，且 `_` 使不透明长串检测失效。
- 30 字节合成数据的 URL-safe base64 `4OHi4-Tl5ufo6err7O3u7_Dx8vP09fb3-Pn6-_z9` 也原样保存；`OPAQUE_TEXT` 不识别 `-`/`_`，而标识符规则允许它们。这里只证明编码字节块可过闸，不声称测试使用了真实图像或音频。
- `PRIVATE_SENTINEL\n` 原样保存：正则 `.match()` 的 `$` 允许末尾换行，违反“不含空白”声明。JSON 会转义换行，因此这是净化失效，不是 JSONL 换行注入。
- 未知字段确实丢弃，但不计入 `rejected`。六条探针事件中一个嵌套值加一个未知字段应至少拒绝两次，实际 `rejected=1`；验证记录所说“其他 kwarg 丢弃并计入 rejected”不成立。

已通过对照：`Bearer ...` 被替换为 `[redacted]`，dict 载荷被丢弃。

修正要求：按字段定义允许值/类型，控制 reason/state/mode/operation 等枚举及标识符来源，不把“含分隔符”作为安全证明；标识符用全串匹配，拒绝换行。补充 URL-safe 编码、常见前缀令牌、未知字段计数回归。不能仅增加一个密钥前缀就继续宣称任意密钥/媒体都无法落盘。

## R2：P1，HTTP 重试漏计潜在费用，且不重新过预算闸门

位置：`src/vrc_agent/providers/openai_compat.py:219`（仅 infer 开始检查）、`:226`（attempt）、`:267`（最终失败才入账）、`:279`（最终成功入账）。

反例 A：第一次 transport 抛 TimeoutError、第二次成功且有已知价格/usage。实际 HTTP 调用两次，但台账 `calls=1`、`unknown_cost_calls=0`、`cost_complete=true`。第一次可能已经计费的超时被最终成功覆盖，导致每小时费用和剩余额度虚假完整。

反例 B：第一次 HTTP 期间通过公开 `ledger.record()` 模拟共享台账入账至预算上限，随后超时。调用顺序为 `ensure_affordable → http → http`，第二次仍成功，未出现 BUDGET_EXCEEDED。此用例是预算在重试间变化的故障注入，不声称当前 CLI 正在并发调用；它证明“每次付费调用前都检查”不成立。

边界：当前 T08 live 强制 `max_retries=0`，不会触发此重试路径；通用适配器允许重试，因此离线/通用路径与 live 的保护不能笼统说一致。普通串行下一次 infer 在余额耗尽后确实会被阻止（探针对照只有一次 HTTP）。

修正要求：每次实际 HTTP 尝试前重新授权；每次已发出且可能计费的失败独立记未知费用/usage，重试成功不能覆盖。预调用拒绝不计作已发送/计费请求。补充超时后成功、全部重试失败、重试间余额耗尽、无发送拒绝的时序断言。

## R3：P2，SessionBudget 未接入生产调用，报告中的预算状态失真

位置：`src/vrc_agent/cli.py:619`、`src/vrc_agent/live_navigation/session.py:63`；两处都传 `ledger=budget.ledger`。全源码检索 `authorize(` 只有方法定义，没有生产调用。

按实际组合方式创建预算与供应商，一次调用耗尽 0.0002 USD 后，第二次被底层 UsageLedger 正确拒绝；然而 `budget.snapshot()` 仍为 `exhausted=false`、`pause_reason=null`、`estimated_calls=0`。因此第 23 节宣称的 `SessionBudget.authorize → ensure_affordable` 并未接线；台账底层保护有效不等于上层预算状态正确。

离线 `runtime.py:285` 与 live `live_navigation/runtime.py:278` 均有 BUDGET_EXCEEDED 结局分支，live 最终统一 pause；本轮通过源码确认，未另做独立控制进程预算耗尽试验，不能将 provider 探针称为两条 runtime 的端到端验证。

修正要求：组合根统一经过预算授权和入账接口，避免重复计费，或让快照从真实台账推导完整状态并更正文档契约。修复后在离线与受监管 dry-run 两条循环中验证耗尽→无下一次 HTTP→PAUSED，以及报告预算标志。

## R4：P1，未知台账被转换为已知零，完整性标志丢失

位置：`src/vrc_agent/telemetry/report.py:98`、`:132`、`:290`。

- `SessionRecorder` 未设置 budget，经过 60 秒后 `usage=null`，但 hourly 四种速率均为 0，`cost_projection_complete=true`、`reason=null`。应是 NO_DATA。
- `aggregate_usage([{"usage": {}}])` 被认作一份完整台账：所有量为 0、`cost_complete=true`；60 秒折算费用为 0。
- 输入明确 `cost_complete=false` 的部分台账，汇总忽略原始标志并变成 true，费用折算 0。
- 一份已知 token 台账加一份缺 usage 报告，成本标志会变不完整，但 `tokens_projection_complete` 仍为 true。未知调用/token 不能从没有数字推成已完整。

已通过对照：底层 `estimate_cost` 缺价格/缺 token 时 `amount_usd=None` 且有原因；`hourly_from_usage(None, None)` 为 NO_DATA。问题集中在报告层，不是这两项底层规则失效。

修正要求：区分合法零调用快照、缺失/空/畸形台账；保留来源完整性标志。没有台账统一 NO_DATA；有部分数据只能报明确的已知部分/下界，不能宣称总费用或 token 完整。调用数/图像数缺失也应有明确完整性口径。

## R5：P2，损坏报告绕过 invalid_files，时长与分子覆盖不一致

位置：`src/vrc_agent/live_navigation/session.py:113`、`:128`、`:144`；`src/vrc_agent/telemetry/report.py:86`。

自建报告包含匹配的 live 来源、当前摘要、合法任务 ID 和失败 outcome，不伪造成功证据：

- `usage.calls="not-a-number"` 通过来源检查，在 try/except 外汇总抛 ValueError，而非进入 invalid_files。
- 开始带时区、结束不带时区时，时间相减抛未处理 TypeError，同样没有记 invalid_files。
- 不可解析开始时间被静默剔除出时长，但该报告的调用数仍纳入分子，`invalid_files=[]`。单份报告虽给 NO_DATA，但混合其他有效时长会放大每小时速率，且没有时长覆盖完整性标志。
- 空 usage 也通过来源检查，形成 R4 的虚假完整零费用。

已通过对照：dry-run 来源被正确列入 invalid_files，不能混入实机验收。

修正要求：逐文件严格验证台账数值/有限性/非负性及时间戳一致性，在文件边界捕获问题；损坏文件计 invalid_files 或明确的遥测无效清单，不能让单个文件打断整批汇总。费率分子与分母必须使用同一覆盖集合，缺失时标不完整/NO_DATA。补充合法文件与损坏文件混合批次用例。

## 门禁与复核范围

项目 `.venv` 实际执行，均退出 0：

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q` | **789 passed in 38.88s** |
| `python -m ruff check src tests` | All checks passed! |
| `python -m ruff format --check src tests` | 111 files already formatted |
| `python -m pip check` | No broken requirements found. |

四项门禁的范围是现有产品与测试；独立探针在 `.runtime/verify/`，其反例并未加入产品测试，故门禁全绿与复核退回并不冲突。真实费用、账单、现场 ≥60 秒报告与 T08 的 20 次任务仍未验证。

独立性限制：与实现者共享机器/工作树；本复核者曾实现 T08 基础装配，但 T10 的变更由另一执行者完成，本轮未复用其测试桩。本轮 GraphFlow 无可调用工具，context/plan/index 未执行；没有宣称索引已更新。

下一步：实现方按 R1–R5 修正并添加产品回归，再由不同执行者复核。未知字段计数作为 R1 一并处理。修复后的独立复核与真实 `usage/hourly` 证据齐备前，不将 T10 转 DONE。
