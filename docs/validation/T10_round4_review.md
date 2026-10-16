# T10 第四轮修正件独立复核（2026-10-16）

复核者 Codex / root；修正者 DSH / Flash。对照 TASKS 第 30/31 节与 `T10_round3_review.md`，独立重建 R4c/R1c 及上一轮扩展反例。**本轮检查范围内未能证伪，未发现需继续退回的问题；离线修正复核通过。T10 仍为 IN_PROGRESS，真实实机 usage/hourly 折算证据待补。**

本轮只新增复核探针/记录并同步交接，不修改产品源码、项目测试、配置、锁文件或 live 门禁。未出网、未调用付费服务、未运行真实控制输入。

## 独立证据与历史工件

没有读取被覆盖的 `v_t10_round3.json`，也没有执行旧脚本以生成新“历史证据”。第 30 节原始 22 过/2 反例结论仍以当时文档为准，第 31 节已经如实记录修正者覆盖旧结果的情况。

本轮从头编写 `.runtime/verify/v_t10_round4_independent.py`，不导入旧探针、旧 JSON 或实现者测试。使用生产函数、合成数据和临时目录，结果独占创建为同名 `.json`（`open("x")`，不能静默覆盖）。**38 项检查全部通过，failed=0，退出码 0**；发现失败时脚本退出 1。

```powershell
.\.venv\Scripts\python.exe .runtime/verify/v_t10_round4_independent.py
```

再次执行须先另定输出文件或归档现有结果，勿删除原证据以冒充初次运行。

| 工件 | SHA-256 |
| --- | --- |
| `v_t10_round4_independent.py` | `b9359ed31b75270539cd6b6abf782bdcd9beb02602e2c3abd1dea365bd0bb437` |
| `v_t10_round4_independent.json` | `403b9d2bcb6a14218a5fb7deebb1716f20ba7fe4911428f32970e818b8bf2c65` |

## R4c：缺失、零值与混合批次

- 分别删除 `spent_usd_known`、`cost_complete`、`unknown_cost_calls`、`unknown_token_calls`：聚合均 `reports_invalid_usage=1`、费用不完整、hourly 为 NO_DATA，费用不是 0。
- 完整台账的合法零金额仍得到 `cost_complete=true`、`cost_projection_complete=true`、每小时费用 0.0。
- 一份正常 + 一份缺金额：无效计数 1，整体与 coverage 不完整，费用折算为 None。逐文件入口也将缺金额文件记 invalid_files，剩余有效样本的调用率为 60/小时。
- 金额为 None、布尔、字符串、负数、NaN、Infinity、10**400 均判损坏，不能借零默认值绕过。
- 来源 cost_complete=false / tokens_complete=false 保守保留；无台账的 SessionRecorder 仍输出 NO_DATA。

### 零值默认写法静态审查

不能声称仓库已完全没有 `or 0` / `or 0.0`。已核对实际可达语义：

| 位置 | 现状及保护 |
| --- | --- |
| `telemetry/report.py` 聚合金额/主计数 | `usage_defect` 验证必填及类型后直取字段；仍有 `if ... is not None else 0` 防御分支，对普通 JSON 台账经过相同校验后不可用于补缺失值；配对集合直接累加校验值 |
| 聚合 `by_provider` 的 `or 0` | `usage_defect` 已逐项确认是合法非负整数，0 是合法零次数，不会把坏计数接受为零 |
| hourly 的 `reports_*`、时长 `or 0` | 缺省用于覆盖计数/无时长路径；无可配对样本或正时长时给 NO_DATA；金额与用量另有数值校验，不从这些默认值推导已知零费用 |
| hourly 完整性判定中的 unknown 计数默认 | 前面 `_nonneg_int` 已验证缺失/非法计数并返回 NO_DATA；正常来源的 false 不被升级 |
| SessionRecorder warnings 中 unknown 计数默认 | 仅在 `usage_defect` 通过后读取；缺失金额时先走损坏台账警告/NO_DATA |
| `providers/base.py` 台账中的 `cost_usd or 0.0` / `total_tokens or 0` | 分别位于 `usage.cost_known` / `usage.tokens_known` 分支；未知进入独立 unknown 计数，合法零值仍可累计 |

动态缺字段、错类型、来源不完整和混合文件检查与上述静态结论一致。没有通过删除所有零值字面量来替代语义验证。

## R1c：闭集类别与报表原因

独立构造类名含 ghp_ 合成令牌、换行/制表、中文、300 字符及伪装成 `dict` 的普通类；增加真正的 dict 子类，以及通过元类禁止访问 `__name__` 的对象。全部落到 **`value:non_scalar:other`**，未读取异常的 `__name__`，原名称不出现在 stats。

八类登记内建对象（dict/list/tuple/set/frozenset/bytes/bytearray/memoryview）分别只产生登记类别。自定义类型/子类不会因为类名等于内建名而获得登记身份。未知字段名仍折叠为 `unregistered_field:unknown_field`，拒绝计数符合输入数量。

报表层：以合成令牌分别作为非法台账的 provider 名和 session_id，原因只保留 provider 项序号及 `unknown` 会话标签；逐文件 invalid_reasons 同样不回声这些名称。检查范围是错误诊断与统计出口，不声称所有合法 provider 标识都应被删除。

docstring 已分别描述 operation/mode 的词形短串、value 的数值/OSC 地址、以及固定类别/other；“不保证识别真实来源或信息含义”的限定与实现一致。此结论不是密码学信息泄露证明，不扩大为任意合法短字符串都不可能携带信息。

## 上轮扩展反例防回归

- 44 字符全小写 URL-safe base64：九个允许字段逐个注入，九项全拒。
- 来源 false、三个缺失完整性/unknown 字段、缺金额、任意动态类名反例均重新构造，没有复用旧结果。
- 60 秒/1 次 + 零时长/1 次，以及缺时长变体：`reports_paired=1`、`reports_unpaired_duration=1`、coverage 不完整；调用率仅 60/小时，不再翻倍为 120。
- 金额、调用数、token 数、图片数分别为 10**400：逐文件隔离，批次继续处理有效样本。
- 超长异常内容含换行/制表：invalid_reasons 单行，异常 detail ≤200 字符。整条另含文件名与异常类型，不声称整条 ≤200。

## 四项门禁

项目 `.venv` 实际运行，全部退出 0：

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q` | **843 passed in 41.38s** |
| `python -m ruff check src tests` | All checks passed! |
| `python -m ruff format --check src tests` | 112 files already formatted |
| `python -m pip check` | No broken requirements found. |

复核结论来自独立证伪尝试，门禁只提供回归证据。本轮不重复宣称新增 R2/R3 独立验证，它们沿用第二轮复核范围并随全量门禁检查。

## 状态与下一步

R4c/R1c 及本轮重建的扩展反例均未能证伪，可关闭本轮离线修正复核项。**T10 不转 DONE**：仍须在受监管现场会话后核对真实 usage/hourly、计费未知项、时长基数及账单/估算口径；不能用合成报告替代实机证据。

同机同工作树，复核与修正由不同执行者完成。首轮新探针直接运行成功，无更改断言掩盖失败。GraphFlow 当前工具列表无可调用入口，context/plan/index 未执行，未宣称图谱更新。
