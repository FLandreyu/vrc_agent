# T10 修正件独立复核（2026-10-16）

复核者 Codex / root；修正者 DSH / Flash。对照 TASKS 第 26/27 节、`T10_review.md` 与 `T10_telemetry.md` 第 7 节。**结论：部分修正有效，但 R1/R4/R5 仍有反例，退回；T10 保留 IN_PROGRESS。** 本轮不修改产品代码、项目测试、配置或锁文件，不调用网络、付费服务或 live 控制。

## 独立证据与已通过部分

自写 `.runtime/verify/v_t10_revision.py`，输出同名 `.json`；不导入实现者测试/fixture。26 项检查，18 项符合要求、8 项发现反例。用内存 HTTP、合成载荷和临时文件直接调用生产对象。脚本退出 0 表示采集完成，判断结论须看 `failed=8`。没有覆盖旧探针/旧输出，历史证据保留。

```powershell
.\.venv\Scripts\python.exe .runtime/verify/v_t10_revision.py
```

| 条目 | 独立检查结果 |
| --- | --- |
| R1 原载荷 | ghp_ 前缀被净化；原 URL-safe base64 被丢弃；尾换行及 NUL 被拒；普通未知字段增加计数并给出原因。新变体见下文 |
| R2 超时后成功 | `authorize → http → authorize → http`，calls=2、unknown_cost_calls=1、cost_complete=false |
| R2 重试间耗尽 | 注入共享台账入账至上限后超时，下一次授权拒绝；仅一次 HTTP。calls=2 对应一次外部入账和一次超时，不把拒绝额外计费 |
| R2 预调用拒绝 | 零预算→BUDGET_EXCEEDED，HTTP=0、calls=0 |
| R3 接线与快照 | 通过 accounting=SessionBudget 耗尽 0.0002 USD，下一次拒绝且 HTTP 总数仍为 1；exhausted=true、pause_reason 非空、estimated_calls=1 |
| R4 基础缺失 | SessionRecorder 无台账、聚合缺失/空/畸形台账均无虚假零费率，NO_DATA 路径通过 |
| R5 常见损坏 | 无时区、倒序、不可解析时间、字符串调用数及 NaN 金额均逐文件进入 invalid_files/invalid_reasons；有效文件仍汇总，coverage 不完整 |

R3 另核对源码：CLI report 与 live 组合根均传 `accounting=budget`。本轮动态探针验证供应商/预算链路；没有独立重建两条控制循环，受监管 dry-run/离线 Runtime 暂停回归随全量门禁执行，不能称为独立实机验证。

## R1a / P1：拒绝原因成为新的敏感值出口

`telemetry/__init__.py:238` 把未知字段名拼入 `unknown_field:{key}`，`:285` 再把原始 key 拼入计数器键。调用 `log.emit("unknown_key", **{synthetic_ghp_token: 1})` 后，JSONL 的载荷字段被丢弃，但 `log.stats()["rejected_reasons"]` 原样包含令牌两次。

这是实际报告出口：`cli.py:732` 将 `log.stats()` 放入 `event_log.writer`。因此“拒绝并记录原因”并未保证报告不泄露；字段名同样可能承载不可信信息。探针只用合成令牌，没有真实凭据。

要求：拒绝原因使用固定码，未知字段名不得未经净化拼入原因/键；计数器类别有界，不能以任意外部字段名无限扩张。回归须检查完整报告/统计，而不只检查 JSONL。

## R1b / P2：URL-safe base64 判别仍依赖大小写启发式

`telemetry/__init__.py:161` 的 `_looks_opaque` 对只有小写字母与 `_` 的 44 字符串（`"a" * 31 + "_" + "b" * 12`）返回非不透明，`value` 原样落盘。该串是可解码的 URL-safe base64 字符串；它没有大写/数字，绕过本次新启发式。此为合成编码块，不声称它是真实媒体文件。

要求：不能继续把某种字符熵/大小写特征当作安全证明；按字段收紧允许值及来源，或明确、可验证地限定净化保证范围。原 ghp_ 反例被修复不等于任意密钥/编码载荷不会进入标量字段。

## R4 / P1：完整台账形状下仍覆盖来源的不完整标志

位置：`telemetry/report.py:255`、`:272`、`:275` 及 `usage_defect`。

用完整合法形状台账（calls=1、image_count=1、tokens_known=100、spent_usd_known=0.1、两个 unknown 计数为 0），分别置 `cost_complete=false` 或 `tokens_complete=false`，聚合结果均恢复成 true。验证形状并没有保留来源标志，原 R4 未修完。

再删除 `unknown_cost_calls` 与 `unknown_token_calls`，仍通过形状验证；聚合补 0 并声明 cost/tokens 完整。缺少“未知计数”不能证明没有未知。

要求：对来源完整性做保守合并（任何 false 不能变 true）；缺少相关字段时拒绝或明确不完整，不能默认 0。补充“字段齐全但标志 false”和“只缺 unknown 计数”两组回归。

## R5a / P1：缺失/零时长报告仍造成分子分母不同源

位置：`live_navigation/session.py:208` 的 `_aware_stamp(None)` 接受缺失；`telemetry/report.py:255–266` 先加用量再检查时长。

混合两份报告：每份 1 次调用、0.1 USD；第一份有 60 秒有效时长，第二份缺 started_at，或开始结束相等（0 秒）。实际结果：

- invalid_files=[]；duration_complete=false；usage.calls=2、spent_usd_known=0.2；
- basis_ms_with_usage=60000；calls_per_hour=120、cost_per_hour_usd=12；
- **hourly.coverage.complete=true，cost_projection_complete=true，reason=null**。

有效配对样本本应仅支持 60 次/小时、6 USD/小时，或整批明确不给费率；当前用两份分子除一份分母。顶层 duration_complete=false 不能修复错误数字及相反的 hourly 完整性声明。

要求：汇总用量总数可以保留，但费率必须另用有效“台账+正时长”配对集合；缺失/零时长明确记 coverage 和原因。禁止 `basis_ms_with_usage=0` 时静默借其他样本时长替代。

## R5b / P2：超大 JSON 整数使整批报告崩溃

`telemetry/report.py:209` 的 `_nonneg_usd` 对 `spent_usd_known=10**400` 执行 float 转换，抛 OverflowError；`live_navigation/session.py:154` 未捕获该类型，整批汇总中断，无 invalid_files 记录。这是可解析的合成 JSON 数字，不是绕过 JSON 语法。

要求：金额转换检查可表示性/有限性并捕获溢出；损坏文件在文件边界隔离。补充超大整数和混合有效批次回归。

## 四项门禁与范围

项目 `.venv` 本轮实跑，全部退出 0：

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q` | **816 passed in 39.87s** |
| `python -m ruff check src tests` | All checks passed! |
| `python -m ruff format --check src tests` | 112 files already formatted |
| `python -m pip check` | No broken requirements found. |

产品门禁未覆盖独立探针中的 8 项反例，因此不能据此通过复核。下一步修正 R1a/R1b/R4/R5a/R5b 并再复核；R2/R3 在本轮测试范围内未能证伪。即使修复后离线复核通过，T10 转 DONE 仍需真实实机 usage/hourly 折算证据。

独立性：同机同工作树；复核者曾实现 T08 基础代码，但 T10 修正由 DSH / Flash 完成。本轮未复用实现者测试桩。GraphFlow 当前无可调用工具，context/plan/index 未执行。首轮自建探针直接运行成功，没有修掉探针后隐藏原始失败的情况。
