# T08 稳定性 v9 候选：逐保护区可见面积 ppm 容差

日期：2026-10-18；负责人：Codex；依据：TASKS 第98/99节及用户后续范围更正。

**这是提案，不是冻结，不是实机验收。** v9 为 `REQUIRES_FIELD_CALIBRATION`，独立复核与用户冻结前不得 live。现场配置保持 v8/dry-run。

## 1. 新工件与唯一语义变化

- 策略：`src/vrc_agent/live_navigation/stability_v9.json`。
- SHA256：`990a1d7de902318f4ed04715adaa7bfd71c9c806c30fb90e82ad37a142ceef0e`；sidecar为小写十六进制加CRLF，66字节。
- 版本：`local-rgb-stability-v9-candidate`；装配键：`stable-inference-v9`；报告：独立 schema12。
- 以冻结v8为底，仅改version/status并增加`protected_tolerance`显式声明。加载器在摘要校验后逐项严格比对v8底座与新声明，status采用与v6/v8相同的字符串闭集；状态闭集预留未来冻结入口，不构成当前冻结授权。
- `acceptance_semantics`仍为`target-path-v8`；目标七谓词、置信度作用域、路径许可和到达/人工复核语义不改。共享采信报告校验器只增加固定的v9/schema12配对入口，仍调用原v8采信实现；没有把v9像素容差回填到旧版本。

唯一判据变化为：**原保护区零变化改为每个保护区各自的可见面积预算**；全局≤50,000 ppm、Δ2、黑帧阈值和尺寸校验仍独立执行。

## 2. 明确公式、上下限与选值理由

新声明无隐式默认：

```json
{
  "protected_tolerance": {
    "policy": "per_visible_region_ppm_clamped_v1",
    "max_changed_ppm": 100,
    "min_allowed_px": 1,
    "max_allowed_px": 128,
    "rounding": "ceil",
    "regions": "independent"
  }
}
```

对每个证据实体的外扩保护框分别计算：

```text
V = 该框面积 − 该框与遮罩相交的面积（交集按并集去重）
A = min(V, 128, max(1, ceil(V × 100 / 1_000_000)))
该框通过 ⇔ changed_in_this_region ≤ A
stable ⇔ 非黑帧 ∧ 全局变化≤5% ∧ 所有框分别通过
```

`ceil`通过整数公式`(V * 100 + 999999) // 1000000`计算；未通过条件为`changed > A`，不使用浮点四舍五入。每框至少需有可见像素，V>0至少允许1 px，完全不可见仍沿原`require_visible`/`fully masked evidence`路径拒绝。此取整/下限依据用户末次明确更正，替代本轮未交付的floor/2px草稿。

100 ppm为现场小变化（约7–34 ppm）留出起评余量，仍远小于全局5%。向上取整且至少1 px保留非空小框的单像素噪声余量；128 px封顶使大框绝对允许变化不再随面积无限增加。57,525、126,036、147,096、1,039,360、1,164,640可见像素分别允许6、13、15、104、117 px。

**取整与封顶有实际语义**：ceil最多带来不足1 px的向上余量；V极小时1 px可能覆盖全部可见像素，不能宣传为所有大小框的有效比例都严格≤100 ppm。V>1,280,000时128 px封顶使有效容差低于100 ppm。备选为50 ppm或64 px封顶；这些更保守但可能增加噪声拒绝，当前候选不采用。起评值、小框风险与封顶须由独立复核及现场数据决定，不能凭这一批样本证明安全。

容差无法从像素计数区分“渲染噪声”和“真实的少量语义变化”；目标小位移、细小障碍或可见区域很小的目标都可能落入预算。复验须包含目标/障碍真实移动的对照，不能只拍静止帧。21/44 px多框分布缺失也意味着本提案不能保证现场一定恢复运动。

各框独立计算，重叠像素可以在多个框中分别计入，任何一框超限即拒绝；大地板框不能给目标框“借预算”，同位置的不同实体也不合并额度。保留`protected_changed`并集计数供旧字段对照，它不代替逐框判据。

遮罩按用户后续明确更正，沿用冻结v8的实际`exclude_masked`语义：部分交叠时排除遮罩像素；原实体框或外扩框完全不可见则拒绝。该语义不是v4的`reject_on_overlap`（任何交叠均拒绝）。分母为该框的可见面积，不能用原矩形面积或另一框面积替代；不能借另一可见框掩盖完全遮罩的框。

## 3. 三份现场工件核对

报告位于`.runtime/live/<任务>-v8pilot1/navigation-live/`，不提交原始本地报告：

| 任务 | 报告文件名 | 原始字节 SHA256 |
| --- | --- | --- |
| G2-1 | `G2-1-d5794dc1904d4c1b87cafb993a3763e2.json` | `3447a7750ffc9472cb8fee6f8758308875b881a4cd994f2871bc6f43f22ca1d0` |
| G2-2 | `G2-2-adea3555261743d0ba4dde2ee294ea03.json` | `09d4853625e382fa260578acfe3e7490234a212c6a34e98fcd1717b5ec1b60da` |
| G4-1 | `G4-1-3c167621c40b4730b83b218a3a8723e9.json` | `13a97ed2a27030291c39f6df451cf9a28e7dc9b99db0dde429ea0c4ef38aca30` |

| 任务 | 执行证明通过/拒绝 | 预检通过/拒绝 | 预热通过/拒绝 |
| --- | --- | --- | --- |
| G2-1 | 3 / 4 | 5 / 1 | 12 / 0 |
| G2-2 | 4 / 4 | 6 / 2 | 16 / 0 |
| G4-1 | 1 / 1 | 1 / 2 | 6 / 0 |

原始工件为**9次执行证明拒绝＋5次预检拒绝＝14次**；第99节“10次拒绝”的汇总与工件不符。本节列出全部原始拒绝，不拼造“10个样本”，也不覆盖历史记录。

### 3.1 逐样本反事实对照

P表示`stability_proofs`，C表示`stability_prechecks`，索引从0计。各框面积/允许值一一对应，原始拒绝均为黑帧False。结论仅指像素稳定性，并非完整闭环重放或运动许可。

| 任务/索引 | 全局变化px | 保护区并集变化px | 各框可见px | 各框允许px | v8→v9草案判定 |
| --- | ---: | ---: | --- | --- | --- |
| G2-1 P0 | 7 | 4 | 1,039,360 / 147,096 | 104 / 15 | 拒绝→通过；任一框变化≤并集4 |
| G2-1 P1 | 51 | 21 | 1,164,640 / 147,096 | 117 / 15 | 无法确定；目标框>15则仍拒绝 |
| G2-1 P4 | 109 | 44 | 1,164,640 / 147,096 | 117 / 15 | 无法确定；目标框>15则仍拒绝 |
| G2-1 P6 | 4 | 2 | 1,039,360 / 147,096 | 104 / 15 | 拒绝→通过 |
| G2-2 P0 | 4,233 | 5 | 126,036 | 13 | 拒绝→通过 |
| G2-2 P1 | 9 | 1 | 126,036 | 13 | 拒绝→通过 |
| G2-2 P6 | 4,178 | 1 | 126,036 | 13 | 拒绝→通过 |
| G2-2 P7 | 1,202,965 | 未计算（null） | 126,036 | 13 | 拒绝→拒绝；全局42%超过5% |
| G4-1 P1 | 41,811 | 1 | 57,525 | 6 | **判定变化：拒绝→通过**；框内仅1 px（17.4 ppm） |
| G2-1 C5 | 8 | 3 | 1,039,360 / 147,096 | 104 / 15 | 拒绝→通过 |
| G2-2 C3 | 5 | 1 | 726,160 / 126,036 | 73 / 13 | 拒绝→通过 |
| G2-2 C5 | 2 | 1 | 126,036 | 13 | 拒绝→通过 |
| G4-1 C1 | 32,715 | 1 | 57,525 | 6 | 拒绝→通过；框外变化仍受原5%约束 |
| G4-1 C2 | 38,224 | 2 | 57,525 | 6 | 拒绝→通过；框外变化仍受原5%约束 |

G4-1 P1按用户现场更正：目标未移动，目标保护框内仅1 px变化，作为单像素渲染噪声处理；原始报告只能确认计数，不能从未保存的BGRA帧独立复核“物理目标未移动”。1,202,965 px样本继续由全局门槛拒绝，测试保留同量级、同绝对计数的合成内存帧回归。

这三份旧报告不含原始BGRA或逐框变化位置。多框21/44 px究竟落在目标还是地板不能还原；不能把并集ppm当逐框ppm，不能宣称14项全部已有真实像素重放。v9新报告才记录逐框见证，现场复验应使用新报告收集实际分布。

## 4. v9 未扩大的容忍度

**保护区外≤5%的真实局部变化仍会被视为稳定；该容忍度在v8已经存在。** G4-1 P1的41,811 px中，41,810 px位于目标保护区外，全局约1.47%，本来就通过v8冻结的全局门槛；v8拒绝它的唯一像素原因是保护区内1 px。v9只消除这一零容差拒绝，不是把原本超过全局预算的变化放行。

这意味着局部障碍、框外物体甚至某些有语义的移动，若未进入保护框且总量≤5%，像素稳定性仍可能通过。目标七谓词、clearance门槛和控制保护仍需分别满足，但不能把它们解释为完全覆盖这一视觉盲区。进一步收紧框外变化属于另案新增判据，不在v9夹带。不得靠降低全局上限、改变遮罩或新增块判据使这条样本重新失败。

## 5. 证明、报告重算与隔离

每次预热、预检和两次执行证明都包含：

- `tolerance_policy`：固定`per_visible_region_ppm_clamped_v1`。
- `changed_runs`：Δ2后变化像素的行优先线性索引区间，半开、排序、互不相接、无遮罩像素；用于重新计算全局数和逐框数。
- `protected_regions`：按track/label/原框排序，每行记录track_id、label、原框、外扩框、`protected_visible_px`、`protected_changed_px`、`protected_allowed_px`、`protected_changed_ppm`。
- 原有全局计数、保护区并集计数、掩码审计、原始/掩码RGB摘要、观测ID、session/epoch和时间字段继续保留。

校验器从绑定观测的实体重建各框，按变化位置见证重算交集面积、计数、允许值与ppm，严格核对字段类型与数值；最后重新得出stable，拒绝不一致布尔值。预检的框还与当时最近已接受场景重绑定；首次无既往场景时不得凭空添加框。预热必须无实体保护框。旧schema不得容纳v9字段，v9不得标schema9/10/11或12.0；候选报告不得声称live或发送真实输入。

重算是结构/算术/绑定的一致性校验，**不是原始像素的密码学真实性证明**。报告未保留原始帧，攻击者若同时伪造全部见证和摘要，不能仅靠离线JSON证明真实画面；不把自洽见证称为可信采集签名。位置见证也会增大报告并暴露粗粒度变化位置，仍限本地，不记录原始图像或场景私密文本。高频交替变化可能增加比较耗时和报告体积；原1000ms比较超时保持不变，超时仍安全暂停，性能待现场复验。

## 6. 运行时与安全不变项

- `StableNavigationV9`继承v8采信和v6有界重试：预检只增加拒绝，不能作为动作证据；after_inference与before_submit仍逐次实际评估；每周期共享最多3次attempt。
- 预热2对、4000ms新鲜度、180s任务预算、40步、运动闭集不改；4000ms边界仍严格拒绝，不刷新旧观测时间。
- 新设计在session资源创建前按候选状态拒绝；直接构造live运行时也拒绝。未自动ARM/续租，模型/供应商没有控制授权能力。
- 30s基础租约、人工续租、epoch、TTL、pending_releases、急停/到期归零实现不动；第100节原候选交付未改`control/`，第102节仅追加下述预算枚举，原安全机制不变。
- 第100节原交付时control进程的180s预算枚举仅含v6/v7/v8，按当时边界未改控制层；第101节将其确认为冻结阻断B1。**第102节已按用户另行授权仅补入v9枚举行**，并补真实dry-run进程预算/本地续租/schema12报告回归，详见`T08_v9_budget_fix.md`；修正件仍待独立复核。v9仍在资源前硬拒绝live，不能仅把status改FROZEN就宣称现场已验收。
- v1–v8策略/sidecar逐字节不变，世界v1/v2、提示、`configs/`不变。现场仍`stable-inference-v8`与`dry-run`。

## 7. 验证与后续

已补合成内存帧边界、逐框不共享预算、1/2 px放行、精确ppm边界、全局1,202,965 px拒绝、41,811/1 px更正回归、遮罩/黑帧/尺寸/Δ2、候选资源前拒绝、schema隔离、证明伪造/缺字段、两检查点与三次预算、4000ms边界和v8采信/到达语义回归。

最终ceil/1px规则定向回归：`82 passed in 68.72s (0:01:08)`，日志`.runtime/verify/t08_v9_targeted.log`。只读现场审计脚本`.runtime/verify/t08_v9_field_audit.py`输出到同目录`t08_v9_field_audit.json`，逐条复算14份旧证明结构/时间与草案预算，包含41,811→通过、1,202,965→拒绝的断言；未保存的逐框位置仍明确UNKNOWN。

最终四项门禁均exit 0，使用项目虚拟环境：

```text
.venv/Scripts/python.exe -m pytest -q
1394 passed in 446.95s (0:07:26)
.venv/Scripts/python.exe -m ruff check src tests
All checks passed!
.venv/Scripts/python.exe -m ruff format --check src tests
160 files already formatted
.venv/Scripts/python.exe -m pip check
No broken requirements found.
```

完整原始日志：`.runtime/verify/t08_v9_{pytest,ruff_check,ruff_format,pip_check}.log`。收到用户取整更正时，首轮floor草稿的全量pytest被主动中止（日志`t08_v9_floor_aborted.log`），该中止不算最终门禁结果；最终门禁以ceil策略及新摘要重新执行。

另一次全量在补严schema12报告声明的`True==1`/`100.0==100`类型伪造前主动中止（`t08_v9_before_strict_audit_aborted.log`），也不计入最终门禁。新增三条定向回归为`3 passed, 50 deselected in 5.44s`；旧schema的声明校验语义不改。

默认只读命令与输出摘录（完整JSON为`.runtime/verify/t08_v9_dry_run.log`，exit 0）：

```text
.venv/Scripts/python.exe -m vrc_agent navigate-live --task G2-1 --config configs/navigation-live.toml --world-version v2
  "mode": "plan-only",
  "sends_live_input": false,
  "suite_digest": "b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0",
```

此命令仅返回计划，不创建live资源，也不能替代v9候选门禁测试。自查工件`.runtime/verify/t08_v9_invariants.json`确认29个基线文件一致（含16份v1–v8策略/sidecar原始字节）；世界摘要为v1 `dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335`、v2 `b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0`。世界文件/提示/configs/control的Git差异为空；现场仍v8/dry-run、基础租约30000ms。

本轮没有真实采集/模型/控制输入；T08保持IN_PROGRESS。下一步是另一执行者按ADR-009独立复核→用户决定→再安排现场逐框抖动、G1/G3/G5遮罩交互、延迟/报告体积复验；固定20任务及人工到达确认未验收。

## 8. 改动文件清单

| 文件 | 用途 |
| --- | --- |
| `src/vrc_agent/live_navigation/stability_v9.json` | 新候选声明 |
| `src/vrc_agent/live_navigation/stability_v9.sha256` | 新字节摘要 |
| `src/vrc_agent/live_navigation/stability_v9.py` | 逐框预算、差分见证、重算及绑定 |
| `src/vrc_agent/live_navigation/stability.py` | v9严格加载与比较分派 |
| `src/vrc_agent/live_navigation/stability_report.py` | schema12、旧格式隔离及逐框验证 |
| `src/vrc_agent/live_navigation/acceptance_report_v8.py` | 复用原采信语义的固定v9/schema12入口 |
| `src/vrc_agent/live_navigation/stable_runtime.py` | 新类与统一工厂装配 |
| `src/vrc_agent/live_navigation/runtime.py` | v9继续执行原v8采信/路径校验 |
| `src/vrc_agent/live_navigation/session.py` | 新设计资源前候选门禁 |
| `src/vrc_agent/live_navigation/parameters.py` | v9接续4000ms预算 |
| `src/vrc_agent/live_navigation/attempts.py` | v9显式三次attempt预算校验 |
| `src/vrc_agent/config.py` | 允许新设计键，默认及现场配置不改 |
| `tests/unit/test_stability_v9.py` | 新判据/取整/遮罩/严格声明测试 |
| `tests/integration/test_stability_v9_runtime.py` | 候选门禁、闭环、证明及审计伪造回归 |
| `tests/unit/test_stability_v3.py` | 统一schema/预热登记断言增v9 |
| `tests/unit/test_stability_v3_review_regressions.py` | 工厂登记与未知版本断言同步 |
| `.gitattributes` | 固定新JSON/sidecar字节，旧条目不改 |
| `AGENTS.md` | 最新候选交接入口 |
| `docs/TASKS.md` | 第100节实现交接 |
| `docs/validation/T08_stability_v9.md` | 本提案、证据表、风险与验证 |
