# T10 验证记录：结构化事件、用量与预算（2026-10-15）

> 最新结论：第四轮离线独立复核通过（38 项未能证伪，843 项测试通过），详见 [第四轮复核记录](T10_round4_review.md) 与 TASKS 第 32 节。T10 仍为 IN_PROGRESS，仅待补真实实机折算等验收证据；以下历次退回/修正记录保留，不代表末次复核仍待执行。

> 末次复核见 [第三轮独立复核记录](T10_round3_review.md) 与 TASKS 第 30 节：原反例通过，但 R4c（缺金额补零）、R1c（动态类名进入拒绝原因）仍未通过，T10 保留 IN_PROGRESS。以下保留历次实现/修正记录。

> 最新复核（2026-10-16）：第 7 节修正件已由 Codex / root 独立复核，**再次退回**。R2/R3 在本轮范围内未能证伪；R1/R4/R5 尚有 8 项反例，见 [修正件复核记录](T10_revision_review.md) 与 TASKS 第 28 节。以下原始交付/修正记录保留，不代表已通过末次复核。

> 2026-10-16 独立复核更新：本页以下保留实现者交付时的声明；Codex / root 已发现 R1–R5 反例，**复核退回、T10 保留 IN_PROGRESS**，不能将下文自测结论视作已通过独立验证。详见 [T10 复核记录](T10_review.md) 与 TASKS 第 26 节。

- 负责人：DSH / Flash（实现与自测）。**未做 ADR-009 独立交叉复核**，本记录是实现者的自测证据，不等于已复核结论。
- 任务定义（`docs/TASKS.md` 第 3 节 T10）：建立结构化事件、错误码、延迟和 usage 汇总；价格表带日期/币种；支持会话预算和耗尽暂停；未知价格/usage 明确标记，不以 0 替代。
- 完成条件：① 一份离线报告可贯穿各模块；② 异常日志无 key/授权头/原始媒体；③ 预算到限不再发起付费调用；④ 实机报告能折算每小时估计用量。
- 边界：本轮**没有**真实付费供应商调用（无密钥）、**没有**实机操作、**没有**启动 live 控制或发送输入；所有数字来自离线回放与假供应商。

## 1. 交付物

| 文件 | 作用 |
| --- | --- |
| `src/vrc_agent/telemetry/__init__.py` | `EventLog`（后台线程 JSONL、有界队列 1024、`stats()`）+ 值净化与字段白名单 |
| `src/vrc_agent/telemetry/codes.py` | 错误码登记表（9 个家族、72 个码）；`codes_of()` 从报告结构化字段捞码；`tally()` 把未知码单列 |
| `src/vrc_agent/telemetry/metrics.py` | 延迟统计（count/min/mean/p50/p95/max/sum，最近秩）与日志回读（坏行计数） |
| `src/vrc_agent/telemetry/cost.py` | `PriceTable`（币种 + 生效日期）、`CostEstimate` 三态、`price_table_of()`、`estimate_cost()` |
| `src/vrc_agent/telemetry/budget.py` | `SessionBudget`：调用前授权、按价格表入账、耗尽即暂停标记 |
| `src/vrc_agent/telemetry/report.py` | `SessionRecorder`（五阶段计时/码/事件/用量/每小时折算/warnings）、`aggregate_usage`、`hourly_*` |
| `src/vrc_agent/telemetry/retention.py` | 日志保留策略：默认干跑，只处理目标目录里的 `.jsonl` 普通文件 |
| `src/vrc_agent/cli.py` | 新子命令 `report`、`logs`（`report_run()`、`logs_run()`） |
| `src/vrc_agent/config.py` | `price_as_of` 必填（配了价格就必须给生效日期）、必须是真实日期 |
| `src/vrc_agent/providers/{base,openai_compat,factory}.py` | `Usage.cost_as_of`/`cost_currency`；价格日期缺失即拒绝构造；工厂透传 |
| `src/vrc_agent/runtime.py`、`live_navigation/runtime.py` | 预算耗尽 → `outcome=BUDGET_EXCEEDED` + `controller.stop("BUDGET_EXCEEDED")`（PAUSED），不算运行时错误 |
| `src/vrc_agent/live_navigation/session.py` | live 报告写 `usage` 与 `hourly`；`summarize_reports()` 折叠整批用量 |
| `src/vrc_agent/contracts.py` | 新增顺序稳定的 `REASONS` 元组（登记表枚举 `Reason`） |

测试：`tests/unit/test_telemetry_{codes,metrics,cost,budget,report,retention}.py`、`tests/unit/test_t10_privacy.py`、`tests/integration/test_telemetry_cli.py`，以及 `tests/unit/test_live_navigation.py` 新增 1 项实机报告口径用例。共 **85 + 1 = 86 项**（逐文件：codes 8、metrics 8、cost 13、budget 9、report 17、retention 7、privacy 16、CLI 集成 7，live 报告 1）。

## 2. 完成条件逐条证据

### ① 一份离线报告贯穿各模块

命令（exit 0）：

```bash
python -m vrc_agent report --scenario estop --turns 2 --max-steps 2 --state-dir .runtime/t10
```

实测要点：`elapsed_ms=52`；五个阶段都有样本——`capture_replay` 1 样本成功（p95 7ms）、`perception` 2 样本成功（p95 2ms）、`planning` 1 样本成功（p95 9ms）、`actuation` 1 样本**失败**（p95 100ms，急停场景的动作被判 CANCELLED）、`dialogue` 1 样本成功（p95 18ms）；`codes.by_code={MANUAL_STOP:8, OK:5, UNKNOWN:1}`、`by_family={control:13, verification:1}`、`unknown_codes={}`；`counters={actions:1, scenes:2, dialogue_records:4}`；`runtime={outcome:MANUAL_STOP, final_state:ESTOP, actions:1, sequence_digest:ed2cdeb2…}`；`dialogue={records:4, total_ms:1480, audio.playback_records:2, audio.listen_records:2}`；报告自带 `offline=true`、`sends_live_input=false`、`writes_media=false`。同一份报告里同时含回放、感知、规划/执行与交流四段，证明汇总可跨模块。

`navigation-report`（T08 汇总）同样输出 `usage`/`hourly` 两个字段块，与 §④ 共用同一套折算代码。

### ② 异常日志无 key/授权头/原始媒体

三道闸门，全部有测试：

1. **字段白名单**：`ALLOWED_FIELDS` 九个字段（`action_id/status/state/reason_code/pid/mode/count/operation/value`），其他 kwarg 直接丢弃并计入 `rejected`。
2. **值净化**（`sanitize_value`）：非标量（dict/list/bytes）丢弃；字符串必须是指标识符形状（ASCII、≤64 字符、无空白），超长文本丢弃；命中凭据/媒体标记（`sk-`、`bearer `、`authorization`、`api_key`/`apikey`、`data:image/`、`data:audio/`、`-----begin`）写 `[redacted]` 并计数；≥40 字符且完全由 base64/hex 字符组成、又不含 `.`/`:`/`\`/`-`/`_` 分隔符的不透明长串丢弃。
3. **`stats()` 可见**：`written/dropped/redacted/rejected/pending/failed` 六项计数，报告里以 `event_log.writer` 呈现（本次实测 `written=7, dropped=0, redacted=0, rejected=0, failed=false`）。

测试 `tests/unit/test_t10_privacy.py`（16 项）含运行期哨兵：把形如密钥、授权头、`data:image/png;base64,...`、真人转写文本、超长图片摘要的值喂给 `EventLog`，断言写出的 JSONL 里**不出现**这些原文，且计数器如实增长；另有静态检查（遥测包不反向导入 `capture`/`dialogue`/`control`，净化逻辑不依赖调用方自觉）。

### ③ 预算到限不再发起付费调用

- 闸门在**调用前**：`SessionBudget.authorize()` → `UsageLedger.ensure_affordable()`；无预算或已花满即 `ProviderError(failure="BUDGET_EXCEEDED")`，供应商 `infer()` 不会发出请求。
- 运行时语义：`runtime.py` 与 `live_navigation/runtime.py` 都把 `BUDGET_EXCEEDED` 映射成专属结局码并停止控制器（进入 PAUSED），不是 `RUNTIME_ERROR`。
- 测试：`tests/unit/test_telemetry_budget.py`（9 项）覆盖——未付费时付费调用被拒、配了预算时 `remaining_usd` 与价格表视图、按价格表入账后的 `spent_usd_known`/`estimated_calls`、无价格表的付费调用记 `unknown_cost_calls` 且 `remaining_is_upper_bound=true`、免费调用的未知费用不计入、花满后 `exhausted=true` + `pause_reason="BUDGET_EXCEEDED"`、`authorize()` 抛 `ProviderError`、`record_failure` 也计未知费用。
- 本轮**未**用真实付费密钥验证「真的没有发出 HTTP 请求」；该路径由假供应商与桩传输覆盖（见「未验证项」）。

### ④ 实机报告能折算每小时估计用量

- 生产路径：`live_navigation/session.py` 的 `navigation_entry()` 用 `SessionBudget` 作为供应商台账，报告写 `usage=budget.snapshot()`，并写 `hourly=hourly_from_usage(elapsed_ms_between(started_at, finished_at), usage)`。
- 批量汇总：`summarize_reports()` 折叠所有**通过来源校验**的实机报告（`mode="live"` + `sends_live_input=true` + `suite_digest` 匹配冻结世界），输出 `elapsed_ms`、`usage`、`hourly`。
- 诚实性边界（都有测试）：时长为 0 → 速率全 `None` + `NO_DATA`；有未知计费调用 → `cost_per_hour_usd=None` + `COST_INCOMPLETE`；缺 `usage` 的报告不会被当成 0 美元（`reports_without_usage` 计数并使 `cost_complete=false`）；运行时长不足 60 秒时给「这是比例不是实测速率」的 warning。
- 实测（对现场目录 `.runtime/t08_live`，该目录只有 dry-run 预览）：`navigation-report` 如实把 5 个预览报告全部判为 `invalid_files`（来源不匹配：`mode` 不是 `live`），`successes=0`、`usage.calls=0`、`hourly.reason=NO_DATA…`——**预览不计入实机验收，也不用 0 冒充数据**。
- 本段顺带修掉一个缺陷：`summarize_reports()` 在遍历任务时用 `reports = grouped[task.task_id]` 覆盖了外层报告列表，导致 `usage`/`elapsed_ms` 实际只统计最后一个任务（通常为空 → 永远 `NO_DATA`）。改为 `attempts = …` 后，新增用例 `test_supervised_reports_fold_usage_and_hourly`（构造 3 份带 `usage`/时间戳的实机报告 + 1 份 dry-run 预览）断言 `calls=3`、`spent=0.3`、`elapsed_ms=90000`、`calls_per_hour=120.0`、`cost_per_hour_usd=None` + `COST_INCOMPLETE`、预览进入 `invalid_files`。

## 3. 日志保留（`logs`）

```bash
python -m vrc_agent logs --keep-days 0 --state-dir .runtime/t10        # 只列出候选，不删
python -m vrc_agent logs --keep-days 0 --apply --state-dir .runtime/t10
```

实测：干跑列出候选（`age_days` 排序、含大小与修改时间），`--apply` 才删除并返回 `freed_bytes`；默认保留 14 天（`DEFAULT_KEEP_DAYS`），只处理目标目录下的 `.jsonl` 普通文件，不跟随符号链接、不进子目录。测试 `tests/unit/test_telemetry_retention.py`（7 项）。

## 4. 四项门禁（实测原始输出）

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q` | `789 passed in 64.72s` |
| `python -m ruff check src tests` | `All checks passed!` |
| `python -m ruff format --check src tests` | `111 files already formatted` |
| `python -m pip check` | `No broken requirements found.` |

## 5. 未验证项与已知限制

- **没有真实供应商调用**：价格估算、`cost_as_of` 随账单、超预算前的真实拒绝路径只有假供应商与桩传输覆盖；`PRICE_UNKNOWN`/`TOKENS_UNKNOWN` 是当前离线报告的常态。
- **没有实机运行**：`hourly` 折算没在真实 ≥60 秒的实机会话上核对；60 秒以下只作为比例并带 warning。
- **未做 ADR-009 独立交叉复核**：本记录的结论均为实现者自测。2026-10-16 独立复核已执行并于第 26 节退回（[复核记录](T10_review.md)），反例 R1–R5 已按第 7 节修正，**修正件仍待另一执行者复核**。
- 价格为单一币种 `USD`（配置枚举限制），没有汇率换算。
- `EventLog` 队列满时丢弃并计数（1024 条），不阻塞调用方；丢弃不可回溯。

## 6. 复现命令

```bash
.venv/Scripts/python.exe -m vrc_agent report --scenario estop --turns 2 --max-steps 2 --state-dir .runtime/t10
.venv/Scripts/python.exe -m vrc_agent report --scenario navigation --turns 3 --max-steps 3 --state-dir .runtime/t10
.venv/Scripts/python.exe -m vrc_agent logs --keep-days 0 --state-dir .runtime/t10
.venv/Scripts/python.exe -m vrc_agent navigation-report --report-dir .runtime/t08_live
.venv/Scripts/python.exe -m pytest tests/unit/test_telemetry_*.py tests/unit/test_t10_privacy.py tests/unit/test_t10_review_regressions.py tests/integration/test_telemetry_cli.py -q
.venv/Scripts/python.exe -m pytest tests/integration/test_supervised_navigation.py -q   # 含受监管 dry-run 的预算耗尽暂停
```

## 7. 复核退回后的修正（R1–R5，2026-10-16）

对照 [T10 独立复核记录](T10_review.md)（第 26 节）逐项修正，回归入口
`tests/unit/test_t10_review_regressions.py`（23 项，测试名以 `r<n>_` 对齐复核条目），
外加 `tests/integration/test_supervised_navigation.py` 的预算耗尽暂停用例与
`tests/unit/test_live_navigation.py` 的 live 组合根接线用例。

| 条目 | 问题 | 修正 | 回归 |
| --- | --- | --- | --- |
| R1 | 允许字段内的 `ghp_` 令牌、URL-safe base64 原样落盘；`re.match` 放行尾部换行；未知字段静默丢弃 | `telemetry/__init__.py` 重写：`FIELD_KINDS` 逐字段限定类型/取值（identifier/enum/int/scalar）、`fullmatch` 全串匹配、拒绝空白与控制字符、`TOKEN_PREFIXES` 常见凭据前缀 → `[redacted]`、`_looks_opaque` 识别 32+ 位不透明串、未知字段计入 `rejected`；`stats()` 新增 `rejected_reasons`/`redacted_reasons` | `test_r1_*`（5 项） |
| R2 | 只在 infer 开始检查一次预算；重试成功只记一次且 `cost_complete=true` | `openai_compat.infer` 的 `attempt()` 内：发出前重新授权、transport/解析失败按「已发出」独立入账、成功入账在 attempt 内；预调用拒绝不计已发送/计费；`Accounting` 协议（`providers/base.py`）统一授权与入账接口 | `test_r2_*`（3 项）+ `test_providers_openai_compat.py` 的 `calls == attempts`/`unknown_cost_calls == attempts` 断言 |
| R3 | 两个组合根只传 `budget.ledger`，`SessionBudget.authorize/record` 无生产调用 | `cli.py` 的 `report` 与 `live_navigation/session.py` 改传 `accounting=budget`；供应商在每次请求前 `authorize`、回来后 `record`，快照的 `exhausted`/`pause_reason`/`estimated_calls` 来自真实授权与入账 | `test_r3_*`（3 项：包装层、离线 Runtime 暂停、CLI 组合根接线）+ 受监管 dry-run 暂停用例 + live 组合根接线用例 |
| R4 | `usage=null` 折算成完整零费率；`{}`、`cost_complete=false` 聚合后变完整；缺一份 usage 时 token 仍称完整 | `SessionRecorder.build` 无台账时输出 `usage=null` + `NO_DATA` 折算与 warning；`aggregate_usage` 区分「合法零台账」与「空/缺失/畸形」，保留来源完整性标志；`usage_defect` 把**缺调用数/图片数**的台账判为损坏（不再 `or 0` 折成 0 次调用） | `test_r4_*`（7 项） |
| R5 | 畸形 usage 数值在文件级 try 之外抛异常；有/无时区混用抛 TypeError；坏开始时间静默漏计时长但保留用量分子 | `summarize_reports` 逐文件严格校验（时间戳必须带时区、结束不早于开始、台账过 `usage_defect`），问题计入 `invalid_files`/`invalid_reasons` 且不打断整批；`hourly_from_usage` 的分子与分母同源（`basis_ms_with_usage`），并给出 `coverage`（含 `reports_invalid_files`）与完整性标志 | `test_r5_*`（5 项） |

- 四项检查（项目 `.venv`，实际执行、退出码均 0）：`python -m pytest -q` → **816 passed in 40.74s**；`python -m ruff check src tests` → All checks passed!；`python -m ruff format --check src tests` → `112 files already formatted`；`python -m pip check` → `No broken requirements found.`。
- 本轮只改实现与回归，未改配置、锁文件、价格表或 live 门禁；未调用付费供应商、未启动 live、未发送输入。
- 仍未验证：真实实机报告的 `usage`/`hourly` 核对；真实账单与 ≥60 秒现场折算。
- 下一条可执行动作：另一执行者复核本次修正（对照第 26 节 R1–R5 与本节回归）。

## 8. 修正件复核退回后的再次修正（R1a/R1b/R4/R5a/R5b，2026-10-16）

对照 [T10 修正件复核记录](T10_revision_review.md)（`docs/TASKS.md` 第 28 节）逐项修正。该轮已确认 R2/R3 未能证伪，本轮只处理 R1/R4/R5 的残余反例；未改配置、锁文件、价格表与 live 门禁。回归入口仍是 `tests/unit/test_t10_review_regressions.py`（现 36 个测试函数 / 45 个收集项，按 `r1a`/`r1b`/`r2_`/`r3_`/`r4`/`r5` 分组）。

| 条目 | 反例（第 28 节） | 修正 | 回归 |
| --- | --- | --- | --- |
| R1a | 未知字段名本身是合成令牌时，`rejected_reasons` 原样保留该 key，CLI 报告成为新的泄露出口 | `sanitize_field` 对未登记字段名一律返回固定码 `unknown_field`（不带原始 key）；`emit` 把未登记 key 折叠成固定标签 `unregistered_field`；`redacted_reasons` 的 key 侧只可能是已登记字段名 | `test_r1a_unknown_field_names_never_reach_the_reason_codes` |
| R1b | 44 字符全小写 + 下划线的 URL-safe base64 仍原样落盘（大小写启发式不是安全证明） | 逐字段语义表 `FIELD_KINDS`：`event`/`mode`/`operation` 词形 ≤32 字符、`action_id` 必须是 32 位十六进制、`status`/`state`/`reason_code` 是闭集枚举、`pid`/`count` 只接受整数、`value` 只接受数值或 OSC 地址；`_looks_opaque` 补「大小写混用且含数字、长度 ≥24」形状规则；docstring 如实限定「形状与凭据模式净化，不是密码学证明」 | `test_r1b_the_reviewer_payload_cannot_land_in_any_allowed_field`（9 个允许字段的落盘矩阵）+ 事件名/枚举/类型 4 项 |
| R4 | 字段齐全但来源自称 `cost_complete=false` 仍被聚合为 `true`；缺未知计数两项仍默认 0 并称完整 | `aggregate_usage` 用「与」合并来源标志（只降不升）；`usage_defect` 把 `unknown_cost_calls`/`unknown_token_calls`/`cost_complete` 列为必填（缺标志无法判断完整性）；`SessionRecorder.build` 与 `_warnings` 复用同一校验，损坏台账只出 `NO_DATA` 并点名缺陷 | `test_r4_a_a_source_that_says_incomplete_is_never_upgraded_by_the_merge`、`test_r4_b_a_defective_ledger_is_unknown_not_a_complete_zero`、`test_r4_b_a_ledger_missing_the_completeness_flag_is_unknown_not_zero` 等 |
| R5a | 「60 秒/1 次 + 缺失或零时长/1 次」被算成 120 次/小时且 `coverage.complete=true` | 聚合输出配对集合 `paired_*`（台账合法 **且** 时长可测）：`reports_paired`/`reports_unpaired_duration`/`paired_basis_ms`/`paired_calls`/`paired_tokens_known`/`paired_image_count`/`paired_spent_usd_known`；`hourly_from_usage` 的汇总分支只读这一组，缺配对的报告既不进分子也不进分母，`coverage` 如实列出配对/未配对份数 | `test_r5_a_reports_without_duration_are_excluded_from_rate_and_basis`、`test_r5_a_missing_usage_file_makes_the_batch_partial`、`tests/unit/test_live_navigation.py::test_supervised_reports_fold_usage_and_hourly` |
| R5b | 金额 `10**400` 触发 `float()` `OverflowError`，未计 `invalid_files` 且打断整批 | `summarize_reports` 的文件级 `except` 兜住全部异常（原因串单行化并截断到 200 字符）；`_nonneg_int`/`_nonneg_usd` 拒绝超过 2^53 的巨数，`float()` 溢出转无效值 | `test_r5_b_a_huge_count_is_invalid_without_breaking_the_batch`、`test_r5_b_a_huge_amount_is_invalid_not_an_overflow`、`test_r5_b_a_file_boundary_reason_is_truncated_and_single_line` |

- 四项检查（项目 `.venv`，实际执行、退出码均 0）：`python -m pytest -q` → **837 passed in 43.18s**；`python -m ruff check src tests` → All checks passed!；`python -m ruff format --check src tests` → `112 files already formatted`；`python -m pip check` → `No broken requirements found.`。
- 本轮仍未调用付费供应商、未启动 live、未发送输入，也未改真实账单口径；离线夹具的台账补齐了本版本快照必写的完整性标志（`cost_complete`/`tokens_complete`），因此旧版手工台账会被判为损坏——这是有意的严格化。
- 仍未验证：真实实机报告的 `usage`/`hourly` 核对；真实账单与 ≥60 秒现场折算。
- 下一条可执行动作：另一执行者独立复核本轮修正（对照第 28 节 R1a/R1b/R4/R5a/R5b 与本节回归）。

## 9. 第三轮复核退回后的修正（R4c/R1c，2026-10-16）

对照 [T10 第三轮复核记录](T10_round3_review.md)（`docs/TASKS.md` 第 30 节）逐项修正。该轮确认第 28 节的 8 项反例已通过（R2/R3 亦未能证伪），剩余 R4c/R1c 两项；本轮未改配置、锁文件、价格表与 live 门禁，回归入口仍是 `tests/unit/test_t10_review_regressions.py`（现 **51 个收集项**）。

| 条目 | 反例（第 30 节） | 修正 | 回归 |
| --- | --- | --- | --- |
| R4c | `usage_defect` 必填列表漏 `spent_usd_known`：删掉金额字段仍得到 `reports_invalid_usage=0`、`cost_complete=true`、`cost_per_hour_usd=0.0`（缺失被折成合法零） | 必填列表补 `spent_usd_known`（缺金额 → 判损坏 → `reports_invalid_usage`/文件级 `invalid_files` → 费率与完整性 `NO_DATA`/`false`）；`aggregate_usage` 改为「校验过的必填字段直取」，不再 `or 0`/`or 0.0` 折零；**合法零金额**（`0.0` + `cost_complete=true`）保持完整 | `test_r4_c_a_ledger_missing_the_amount_is_invalid_not_zero`、`test_r4_c_a_legitimate_zero_amount_is_still_complete`、`test_r4_c_a_mixed_batch_with_one_missing_amount_is_not_complete` |
| R1c | `_classify` 非标量分支拼接 `type(value).__name__`：以合成令牌为动态类名可进入 `rejected_reasons`（「只允许固定码」声明不成立） | 新增 `NON_SCALAR_KINDS`（登记 8 种内建容器），未登记者折为 `non_scalar:other`；报表层同步：`by_provider` 缺陷用序号、会话标签须匹配 `SESSION_LABEL`（32 位十六进制，否则 `unknown`）；docstring 三处按实际保证范围改写 | `test_r1c_a_dynamic_class_name_never_reaches_the_reason_codes`、`test_r1c_non_scalar_reasons_are_a_closed_set_of_registered_builtins`、`test_r1c_report_layer_reasons_do_not_echo_untrusted_names` |

- 独立证据重跑：复核者探针 `.runtime/verify/v_t10_round3.py` 修正后重跑 → 24 项全过（`passed: 24 / failed: 0`）。该脚本会把结果写回 `.runtime/verify/v_t10_round3.json`，故该 JSON 现为修正后结果；第 30 节的 22 过 2 反例结论仍以复核记录文档为准。重跑只是佐证，不替代独立复核。
- 四项检查（项目 `.venv`，实际执行、退出码均 0）：`python -m pytest -q` → **843 passed in 40.88s**；`python -m ruff check src tests` → All checks passed!；`python -m ruff format --check src tests` → `112 files already formatted`；`python -m pip check` → `No broken requirements found.`。
- 边界与局限：本轮未调用付费供应商、未启动 live、未发送输入，未改真实账单口径；符号规则、登记类别与形状校验都只是拒绝不符合字段语义的输入，**不保证识别真实来源或信息含义**，也不构成密码学证明。此前的严格化保持不变：更早版本手工写出的台账（缺 `cost_complete`/`tokens_complete`/`spent_usd_known`）会被判损坏。
- 仍未验证：真实实机报告的 `usage`/`hourly` 核对；真实账单与 ≥60 秒现场折算。
- 下一条可执行动作：由**另一执行者**独立复核本轮修正（对照第 30 节 R4c/R1c 与本节回归）；复核前 T10 保持 IN_PROGRESS。
