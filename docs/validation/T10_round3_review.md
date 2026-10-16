# T10 第三轮修正件独立复核（2026-10-16）

复核者 Codex / root；修正者 DSH / Flash。对照 TASKS 第 28/29 节与 `T10_revision_review.md`。**指定原反例已通过，但扩展证伪仍发现两处缺陷，不能通过整项复核；T10 保留 IN_PROGRESS。** 未修改产品代码、项目测试、配置和锁文件；无实机输入、付费请求或真实敏感素材。

## 独立验证

探针 `.runtime/verify/v_t10_round3.py`，结果 `.runtime/verify/v_t10_round3.json`。24 项检查，22 项符合要求、2 项反例。只复用上轮复核者自写的数据构造函数，不导入实现者测试。使用生产函数、合成数据、临时目录；脚本退出 0 表示收集结束，结果以 `failed=2` 为准。原历史证据未覆盖。

```powershell
.\.venv\Scripts\python.exe .runtime/verify/v_t10_round3.py
```

| 要求 | 实测结果 |
| --- | --- |
| R1a 未知字段名 | 普通、ghp_、Bearer、带换行、200 字符字段名均不出现在 stats；全部折叠成 `unregistered_field:unknown_field`，计数 5 |
| R1b 九字段 | 44 字符全小写 URL-safe base64 逐个注入 action_id/status/state/reason_code/mode/operation/pid/count/value；九个字段全部拒绝，原文未落盘 |
| R4 来源 false | cost_complete=false、tokens_complete=false 均未被聚合升级 |
| R4 指定缺字段 | 分别缺 cost_complete、unknown_cost_calls、unknown_token_calls → reports_invalid_usage=1，hourly 为 NO_DATA，无零费用 |
| R5a 时长配对 | 60s/1次 + 零时长/1次，以及 60s/1次 + 缺时长/1次：均按 1 份配对样本折算 calls_per_hour=60，paired=1、unpaired=1、coverage.complete=false |
| R5b 巨数 | spent_usd_known/calls/tokens_known/image_count 各注入 10**400，均进入 invalid_files，剩余有效样本仍为 60 次/小时，批次未中断 |
| R5b 原因边界 | 超长 provider 名含换行/制表，生成的原因单行；异常 detail 截断至 200 字符。完整条目额外包含文件名/异常类型，不声称整条 ≤200 |

## 剩余 R4c / P1：缺金额字段仍被补成完整零费用

位置：`src/vrc_agent/telemetry/report.py:408`、`:413`、`:331`。

本轮必填列表补上了 cost_complete 和 unknown 计数，但**漏掉 spent_usd_known**。构造其余字段完整且 cost_complete=true 的台账，只删金额字段，usage_defect 不判损坏；聚合执行 `... or 0.0`，输出：

```text
reports_invalid_usage = 0
spent_usd_known = 0.0
cost_complete = true
hourly.cost_per_hour_usd = 0.0
hourly.cost_projection_complete = true
hourly.reason = null
```

这仍违反“缺失金额不冒充已知零”，也与 usage_defect 的“缺任何一个都不是用量为 0”说明冲突。要求把金额纳入必填校验，保留真正的零金额台账合法；新增单独删金额与混合有效文件的回归，不能仅覆盖本轮列举的三个缺失字段。

## 剩余 R1c / P2：非标量类名仍进入拒绝原因，固定码保证不成立

位置：`src/vrc_agent/telemetry/__init__.py:327`。

未知字段名出口已修复，但任意对象仍返回 `non_scalar:{type(value).__name__}`。用合成令牌作动态类名，将实例放入已登记 value 字段，stats 输出 `value:non_scalar:ghp_qq…`，原令牌仍在拒绝原因里。JSONL 不含该值，但统计可进入 CLI 报告。

边界：这是 Python 调用边界的任意对象测试，不是说远端 JSON 能创建 Python 类，也没有实际凭据泄露。它直接证伪 docstring“原因只允许固定码”的声明，并允许非标量类别无限扩张。要求非标量统一为固定 `non_scalar`，或仅登记有限内建类型，其余统一 `other`；不拼接任意类型名。

## docstring 一致性

已明确声明不是密码学检测，方向正确；但仍需修正文案：

- “原因只允许固定枚举”与 R1c 不符。
- 第 19–22 行以 value/operation 举“8 字符随机串会落盘”的例子不精确：operation 可接受词形短串，value 只允许数值或 OSC 地址，普通随机字符串会拒绝。应分别说明，不能笼统说两者只有长度/字符类边界。
- “任何自由文本都不属于形状”过强：语义上属于自由文本但形状上合法的短词可能进入 operation。应表述为拒绝不符合字段语义/形状的输入，不保证识别其真实来源或信息含义。

以上文案项不算第三个运行时缺陷，和 R1c 一并修正。原 44 字符载荷在九字段全拒的实测结论仍成立。

## 门禁与结论

项目 `.venv` 实跑，均退出 0：

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q` | **837 passed in 42.80s** |
| `python -m ruff check src tests` | All checks passed! |
| `python -m ruff format --check src tests` | 112 files already formatted |
| `python -m pip check` | No broken requirements found. |

门禁全绿不消除上述独立反例。下一步修正 R4c、R1c 及对应文案，补回归再复核；真实实机 usage/hourly 证据仍缺，即使离线复核通过也不能直接标 DONE。

同机同工作树，复核者与 T10 修正者不同；本轮不重做已在上一轮验证的 R2/R3，只随全量门禁检查回归。GraphFlow 当前工具列表无可调用入口，context/plan/index 未执行。探针首轮运行成功，未修改断言以隐藏失败。
