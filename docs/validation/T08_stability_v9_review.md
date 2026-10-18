# T08 v9 逐保护区 ppm 候选（`ab37394`）——ADR-009 定向复核记录

- 复核对象：提交 `ab37394`（父提交 `a6a30c3`），即 TASKS 第 100 节的 v9 逐保护区 ppm 候选；20 个文件 `+1248/−42`。
- 策略摘要：`990a1d7de902318f4ed04715adaa7bfd71c9c806c30fb90e82ad37a142ceef0e`（`stability_v9.json` 字节 SHA-256、`stability_v9.sha256` sidecar 与加载器三者一致）。
- 复核角色：独立复核者（ADR-009），**只审不改**。本轮未修改任何产品代码、策略、阈值、配置、世界或提示；只新增本记录、`docs/TASKS.md` 第 101 节与 `AGENTS.md` 顶部一条状态胶囊。
- 边界：未启动 live、未 ARM、未调用付费模型、未发送真实输入、未读取密钥；全部结论来自离线代码审查、自建探针与已保存的现场报告工件。**本记录不是冻结授权，也不是实机验收。**
- 历史口径：v9 是 `REQUIRES_FIELD_CALIBRATION` 候选，在任何采集/供应商/控制器资源创建之前拒绝 live；现场配置仍是 `stable-inference-v8` + `dry-run`。

## 1. 复核结论

**声明主体未能证伪，但该候选存在 1 项冻结阻断（B1），因此本轮不能给出“可冻结”结论。**

- 逐项复核（摘要纪律、公式与边界、遮罩语义、报告/校验器、不变项、现场 14 条对照、新风险）**均未找到反例**；自建探针 80 项断言 0 失败。
- **B1（阻断，冻结前必修）**：`src/vrc_agent/control/service.py:166-176` 的 `task_timeout_s` 设计枚举只含 `stable-inference-v6`/`-v7`/`-v8`，**缺 `stable-inference-v9`**。冻结并把现场配置切到 v9 后，控制器进程会静默回落到默认 `30.0` 秒任务预算，而不是 v9 声明的 180 秒。可验证后果（探针 S7 已复现，未启动 live）：
  - 控制器 `lease_budget_ms=30,000`；F9 人工续租的 `granted = min(extension_ms, lease_started + 30,000 − lease_expires)`，首租 30 秒后剩余额度为 **0 ms**，续租实际不生效，30 秒到期即按到期归零。
  - 报告侧 `live_navigation/leases.py:33` 要求 `control_budget.task_timeout_ms == policy["task_timeout_s"]*1000`；v9 现场报告会携带 `30,000` 而声明要求 `180,000`，直接抛 `ValueError: lease configuration exceeds task timeout`，**schema12 报告无法通过复验**。
  - 同一探针确认把 `task_timeout_ms` 换成 `180,000` 后租约复验通过，说明缺陷只在装配枚举，不在策略或判据。
- 因此：**v9 现在不能冻结、不能切现场**。B1 修复并补回归后需要重新复核；本轮不构成“通过”，也不能被引用为实机验收。
- 其余部分（尤其“21/44 px 两项 UNKNOWN 不是通过”“41,811 px 不等于物理目标未移动”）与提案一致，未发现夸大。

## 2. 方法与可复现证据

- 探针：`.runtime/verify/t08_v9_review_probe.py`，SHA256 `c297e0736e9e5c3316f270db0d0940caf8ed8de04b416eb011ce8099faf0a9fd`；输出 `.runtime/verify/t08_v9_review_probe.json`（80 项，0 失败）与 `.runtime/verify/t08_v9_review_probe.log`。
- 探针不复用实现方测试：自带独立实现的 `ceil(V×100ppm)`、矩形并集/遮罩交点、逐框分类与伪造注入；只从产品模块取 `load_policy`/`compare`/`validate_proof`/`validate_stability_report`/`validate_leases` 等公开入口，不调用实现方测试辅助。
- 运行命令（只读）：

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe .runtime/verify/t08_v9_review_probe.py
```

- 现场工件按字节摘要核对：G2-1 `3447a7750ffc9472cb8fee6f8758308875b881a4cd994f2871bc6f43f22ca1d0`、G2-2 `09d4853625e382fa260578acfe3e7490234a212c6a34e98fcd1717b5ec1b60da`、G4-1 `13a97ed2a27030291c39f6df451cf9a28e7dc9b99db0dde429ea0c4ef38aca30`，与提案第 3 节一致。
- GraphFlow 在本会话只返回符号锚点与未填写的对话占位，没有可复核的上下文摘要；本轮结论不依赖它，未据其生成 context/plan/index 结论。

## 3. 逐项证据

### 3.1 摘要与版本纪律（要求 1）

- `stability_v9.json` 字节 SHA-256 = sidecar sidecar（66 字节，小写 hex+CRLF）= `load_policy("v9")` 校验的摘要 = `990a1d7d…`；`status` 为 `REQUIRES_FIELD_CALIBRATION`，版本名 `local-rgb-stability-v9-candidate`。
- v9 与 v8 的 JSON 对称键差**只有** `protected_tolerance`；共有键中只有 `version`、`status` 的值不同，其余键值逐项相等（探针 S1）。加载器用 `json.dumps(sort_keys=True)` 严格比对声明，因此不存在“只改 version 名就换判据”的路径。
- v1–v8 的 16 份 `stability_v*.json`/`.sha256` 在 `a6a30c3..ab37394` 区间内逐字节未变；v8 摘要仍为 `11b98adff4003ee62d40f3a6c96960f50d0d5c2d743a264f2f9dd14af2680cae`。
- `.gitattributes` 只对 v9 的 `.json`/`.sha256` 加 `-text`（旧版本的 CRLF 口径问题见 5.N6）。

### 3.2 公式与边界（要求 2）

独立实现 `A = min(V, 128, max(1, (V*100 + 999999) // 1000000))`，与 `stability_v9.allowed_pixels` 在 23 个采样点逐一相等（另在可见面积 4,636 px 的极小框用例中验证 A=1），含：

| V（可见 px） | 1 | 127 | 128 | 9,999 | 10,000 | 10,001 | 20,001 | 57,525 | 126,036 | 147,096 | 153,900 | 726,160 | 838,192 | 1,039,360 | 1,156,946 | 1,164,640 | 1,280,000 | 1,280,001 | 1,282,226 | 1,400,000 | 2,848,256 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A（allowed px） | 1 | 1 | 1 | 1 | 1 | 2 | 3 | 6 | 13 | 15 | 16 | 73 | 84 | 104 | 116 | 117 | 128 | 128 | 128 | 128 | 128 |

- 判定两侧：`changed == A` 通过、`changed == A+1` 拒绝；`changed == 0` 通过。
- 恰好 ppm 边界（全局）：`retained=2,848,256`，`changed=142,412` 通过、`142,413` 拒绝（5% 边界实现为 `changed*1e6 <= retained*50000`）。
- `V=1,280,000` 恰好 128；`V=1,280,001` 及以上由 128 封顶（不再线性增长）。
- `V<=0`、`type(V) is not int`（含 `True`、`1.0`）一律拒绝；`protected_tolerance` 声明被 `allowed_pixels(1, policy)` 以严格 JSON 比对校验（多键/少键/改值都拒绝）。
- 不共享额度：独立构造双框见证，`12+10`（并集 22 ≤ 11+13=24，看似宽松）被拒绝、`7+10` 通过，证明实现按逐框预算而非并集预算判定（探针 S5）。
- 完全遮罩框不可被另一可见框“借面积”绕过：`region_counts` 先 `audit_regions([raw],[box])`，`require_visible` 直接拒绝（见 3.3）。

### 3.3 遮罩语义（要求 3）

- v9 `protected_mask_policy` 固定 `exclude_masked`；`rejects_masked_protection()` 在 v9 为 `False`，与 v4 的 `reject_on_overlap` 路径明确分叉：同一掩码交叠用例在 v4 抛 `protected region masked`，在 v9 按“部分交叠扣除”计算。
- 分母是**可见面积**：`visible = (x2-x1)*(y2-y1) - masked_area([box])`。独立构造 `<box 原始 174,592 px、可见 125,312 px>`，若用原始面积会得 `allowed=18`，实现得 `13` 并被拒绝；`protected_changed_ppm = count*1e6/visible`。
- 完全遮罩：`require_visible` 抛 `fully masked evidence`；即使另有一个完全可见的保护框，被完全遮罩的框仍单独拒绝（探针 S3 双框用例）。
- `validate_runs` 拒绝落在掩码内的 changed 像素（`masked pixels in v7 witness`），因此可见分母与逐框计数口径一致，掩码内像素不会被计入任何框。

### 3.4 报告与校验器（要求 4）

- schema 隔离：v9 用 schema 12，v1–v8 旧 schema 拒绝 `protected_regions`/`tolerance_policy`（`v9 evidence requires schema12`）；v8 报告带 v9 键或 v9 报告用 schema 11 均被拒绝；`12.0` 因 `type(schema) is not int` 被拒；`changed_runs` 只在 v7/v9 被接受（v8 触发 `v7 evidence requires schema10`）。
- 逐框字段从观测实体与位置见证重算：`verdict` 用 `region_shapes(entities)` 生成身份/几何，`region_counts` 从 `changed_runs` 重算 `protected_visible_px`、`protected_changed_px`、`protected_allowed_px`、`protected_changed_ppm`，逐键用 `type(row[key]) is not type(value) or row[key] != value` 严格比对；`verify_regions` 再把身份/几何与实体见证逐项对齐。我独立构造的一致见证被 `validate_proof` 接受，且我的独立逐框计数与实现值完全相等。
- 类型伪造：`stable` 由重算决定而非报告布尔（flip 即 `contradictory stability verdict`）；`stable=1` 被 bool 类型检查拒绝；`changed/A/ppm` 的 `+1`、`+1.0`、`visible` 浮点化、缺键、多键、`protected_changed+1`、`tolerance_policy` 改串、掩码内 run 共 10 类伪造全部拒绝；`stability_policy` 用 `json.dumps` 严格比对，`True==1`、`100.0==100` 无法蒙混。
- 预检不是动作证据：precheck 带 `validates_observation_id` 直接 `precheck cannot authorize actions`；动作只能绑定被接受的执行证明派生的 observation；被拒绝的证明若被用作动作证据报 `rejected proof used for action`。
- 现场基线：三份 v8 旧报告用 v8 政策仍全部通过 `validate_stability_report`（证明校验器没有因 v9 改动而误伤旧报告）。

### 3.5 不变项（要求 5）

| 不变项 | 复核结果 |
| --- | --- |
| 全局 5% / Δ2 | 保留；边界 142,412/142,413 与 `changed_flags` 的 `abs(diff) > 2` 行为一致 |
| 黑帧阈值 8 | 未变（`black_threshold=8`） |
| 帧尺寸 2160×1440 / retained 2,848,256 | 未变 |
| session/epoch 绑定 | 证明/来源/动作三段均有绑定（见 3.4 与 5.N2 的预检边界） |
| after_inference 与 before_submit 两检查点 | 保留，候选在任何采集/供应商/控制器资源前仍拒绝 live |
| 3 次 attempt 共享预算 | `V6.observe`/`V6.before_submit` 共用同一 `self.attempt`，无嵌套放大；`validate_run_parameters` 在 v9 下拒绝 `task_timeout_s=30.0` |
| 预热 2 对 | `warmup_pairs=2`，`validate_warmup` 要求连续、稳定链与来源绑定，预热保护框必须为空 |
| 4000ms / 180s / 40 步 | 未变；运动包络仍为 field/cautious 双闭集 |
| 30s 租约与急停/到期归零 | `control/` 未改动，急停/epoch/TTL/到期归零逻辑不变（但见 B1：v9 装配预算缺 180s 枚举） |
| control/、configs/、世界、提示 | `git diff a6a30c3 ab37394` 在上述路径为空；世界 v1 `dda1d3b1…`、v2 `b65b3dab…` 摘要未变；现场仍 `stable-inference-v8` + `dry-run` |

### 3.6 现场反事实对照（要求 6）

从三份已保存报告逐框重算（保护框可见面积 = 各框 `protected_rectangles` 并集去重后扣除掩码；逐框预算用 3.2 公式；`protected_changed` 是报告中的并集计数，已由 v8 报告校验器复验过，但**无原始 BGRA 帧、v8 schema 无 `changed_runs`，并集计数不能进一步还原逐框归属**）：

| 现场行 | changed_pixels | protected_changed | 保护框可见面积 | 逐框 allowed | 独立判定 | 提案判定 |
| --- | --- | --- | --- | --- | --- | --- |
| G2-1 P0 | 7 | 4 | 1,039,360 / 147,096 | 104 / 15 | 通过 | 拒绝→通过 |
| G2-1 P1 | 51 | 21 | 1,164,640 / 147,096 | 117 / 15 | 无法确定（21>15） | 拒绝→无法确定 |
| G2-1 P4 | 109 | 44 | 1,164,640 / 147,096 | 117 / 15 | 无法确定（44>15） | 拒绝→无法确定 |
| G2-1 P6 | 4 | 2 | 1,039,360 / 147,096 | 104 / 15 | 通过 | 拒绝→通过 |
| G2-2 P0 | 4,233 | 5 | 126,036 | 13 | 通过 | 拒绝→通过 |
| G2-2 P1 | 9 | 1 | 126,036 | 13 | 通过 | 拒绝→通过 |
| G2-2 P6 | 4,178 | 1 | 126,036 | 13 | 通过 | 拒绝→通过 |
| G2-2 P7 | 1,202,965 | 未产生（全局先拒） | 126,036 | 13 | 全局拒绝（42.23%） | 拒绝→拒绝 |
| G4-1 P1 | 41,811 | 1 | 57,525 | 6 | 通过（1 px = 17.38 ppm） | 拒绝→通过 |
| G2-1 C5（预检） | 8 | 3 | 1,039,360 / 147,096 | 104 / 15 | 通过 | 拒绝→通过 |
| G2-2 C3（预检） | 5 | 1 | 726,160 / 126,036 | 73 / 13 | 通过 | 拒绝→通过 |
| G2-2 C5（预检） | 2 | 1 | 126,036 | 13 | 通过 | 拒绝→通过 |
| G4-1 C1（预检） | 32,715 | 1 | 57,525 | 6 | 通过 | 拒绝→通过 |
| G4-1 C2（预检） | 38,224 | 2 | 57,525 | 6 | 通过 | 拒绝→通过 |

- 汇总与 §99 一致：9 次执行证据拒绝（G2-1 4、G2-2 4、G4-1 1）＋5 次预检拒绝＝14；合计 **11 通过 / 2 无法确定 / 1 全局拒绝**，与提案一致。
- 多框行的“无法确定”是诚实的：并集计数 21/44 大于最小逐框预算 15，逐框归属未知，既不能保证通过也不能断言拒绝；提案第 3.1 节明确写“无法确定；目标框>15则仍拒绝”，**没有把 UNKNOWN 升级为通过**。
- `G4-1 P1` 一行：目标保护框 1 px/57,525 可见 px = 17.38 ppm，框外 41,810 px＝1.4679% 保留像素，仍在 v8 全局 5% 门槛内；提案第 3 节写明“原始报告只能确认计数，不能从未保存的BGRA帧独立复核‘物理目标未移动’”，**没有把该帧说成物理目标未移动的证明**。
- 我按“并集计数 ≤ 各框预算最小值”给出保证性判定；这与 v9 逐框判定等价（并集计数 ≥ 任一框计数），因此上表 11 个“通过”是保证而非估计。

### 3.7 装配点与阻断项 B1（要求 7）

逐点核对“v9 是否漏接”：

| 装配点 | v9 是否接入 | 证据 |
| --- | --- | --- |
| `config.AppConfig.navigation_design` 闭集 | 是 | `config.py:214-222` |
| 租约扩展/包络/预算校验（v6..v9 集合） | 是 | `config.py:238-241`、`256-259`、`266-269`；`config.py:247` 的租约上限按设计名动态 `load_policy` |
| `session` 策略映射与资源前拒绝 | 是 | `session.py:71-79`，FROZEN 检查先于 Controller/供应商创建 |
| `stable_navigation_class("v9")` 工厂 | 是 | 返回 `StableNavigationV9`，MRO 父类 `StableNavigationV8`，`run()` 写 schema 12 |
| `runtime.SupervisedNavigation` v8/v9 后缀匹配 | 是 | `runtime.py:42` |
| `parameters` 运行参数复验 | 是 | `validate_run_parameters` 在 v9 下拒绝 `task_timeout_s=30.0` |
| `attempts` 三次共享预算 | 是 | v9 在要求 `max_inference_attempts` 的集合内 |
| `stability_report` 设计→版本/schema 映射 | 是 | `stability_report.py:130-131`、`179-180` |
| **`control/service.py` 任务预算** | **否（B1）** | `service.py:166-176` 只含 v6/v7/v8，默认 `30.0` |

- B1 的定性：**冻结前必修，不是可忽略项**。它不是文案问题，而是“冻结并切现场后 180s 任务预算与 30s 租约/续租、报告复验同时失效”的装配缺陷；本轮按边界未改 `control/`，修复需在 `service.py` 枚举补 v9 并补一条“v9→180s”的回归。
- 修复方向（仅建议，不实施）：把该枚举改为从 `load_policy` 自动取值的集合（或至少补 `stable-inference-v9`），并加测试断言 `ControllerProcess` 的 `initial_status`/`control_budget.task_timeout_ms == policy["task_timeout_s"]*1000`；该测试必须能在 `dry-run` 下跑，不依赖 live。
- 除 `control/service.py` 外，`src/` 内没有其它 `stable-inference-v*` 枚举漏 v9（探针 S6 全量扫描断言 offenders 恰为该一个文件）。

### 3.8 新风险专项（要求 8）

1. **框外 ≤5% 的真实局部变化仍判稳定（G4-1 1.47%）**：v9 不改全局门槛，这是 v8 既有容忍度；用户在 §99/§100 已明确接受，提案也在第 4 节记录。它**不单独阻断冻结**，但必须写进现场复验清单：用真实目标移动/遮挡对照确认“像素稳定 ≠ 目标未移动”，且不得把通过稳定性当作物理安全证据。
2. **小可见面积相对容忍度偏高**：`allowed = ceil(V×100ppm)` 至少 1 px，因此 `V<10,000` 时 1 px 就 ≥100 ppm（如 V=4,636 → 215.7 ppm）；`V=57,525` 时 6 px 对应 104.3 ppm。方向是“极小框更宽”，属 ceil 语义的必然结果，**不单独阻断**；现场清单应记录每个保护框的可见面积，对 `<10,000 px` 的区域单独标注。
3. **128 px 封顶使大框有效容差低于 100 ppm**：`V>1,280,000` 后 A 固定在 128，如 V=2,848,256 → 44.9 ppm，V=1,282,226 → 99.8 ppm。方向是收紧，**不阻断**；只需在清单中注明大框实际容差。

结论：三条均不构成独立阻断；**B1 才是阻断项**。三条风险应全部进入现场复验清单（与 `G4-1` 单像素样本、21/44 未知归属一并）。

## 4. 四项门禁原始输出（`ab37394`，离线，本复核者复跑）

```text
1394 passed in 625.83s (0:10:25)
All checks passed!
160 files already formatted
No broken requirements found.
```

- 全量 pytest 日志：`.runtime/verify/t08_v9_review_pytest.log`（`EXIT=0`）。
- `ruff check src tests`、`ruff format --check src tests`、`pip check` 三项均 exit 0（本轮工作树在跑门禁时干净，仅 `.runtime/` 下探针工件）。
- 门禁全绿**不抵消** B1：四项门禁不覆盖“`control/service.py` 是否给 v9 180s 预算”，故必须单独补回归（见 3.7）。

## 5. 非阻断观察

- **N1（ceil 抬升）**：`allowed = ceil(V×100ppm)` 使实际有效容差落在 `(A-1)/V·1e6 < ppm ≤ A/V·1e6`，小框可略高于 100 ppm（V=57,525 → 6 px = 104.3 ppm）。这是声明公式的固有语义，与 3.8-2 同源，非缺陷。
- **N2（预检重绑的 epoch 边界）**：`stability_v9.verify_prechecks` 只按 `captured_at_ns`/摘要把预检重绑到更早的观测，报告层对 precheck→source 也只要求同一 `reference_observation_id`、`stable`、`checked_at ≤ source.at_ns`，**没有显式比较 epoch**。跨 attempt 时同 reference id 的旧 epoch 预检可能满足“inference source missing accepted precheck”的反面。继承自 v7/v8 路径，非 v9 新增；建议冻结前补 epoch 一致性或写入现场清单。
- **N3（文案）**：候选拒绝 live 的报错仍是 `candidate v6 cannot claim live execution`（`stability_report.py:240`），对 v9 报告同样触发，文案误导但不影响拒绝行为。
- **N4（文案）**：`acceptance_report_v8._validate_acceptance` 在 v9 报告 schema 不符时统一报 `v8 acceptance requires schema11 and semantics`；配对检查本身只在调用方显式传入错配的 `policy_version`/`schema_version` 时才可达，报告校验器内部总传 `SCHEMAS[version]`。
- **N5（现场工件不可再深挖）**：v8 schema 11 无 `changed_runs`、现场未保存 BGRA 帧，因此 21/44 两项无法还原逐框归属；这是 §3.6 标 UNKNOWN 的唯一原因。若要在现场把这些行变成可判定，必须直接用 v9 重跑（v9 报告带逐框见证），不能用旧工件补算。
- **N6（旧版本 CRLF 口径）**：`.gitattributes` 只对 v9 加 `-text`，v1–v8 仍按工作树 CRLF 计算摘要（v5 复核 N1 已记录的口径问题），本轮未引入新变化。
- **N7（工件不在版本控制内）**：探针与日志位于 `.runtime/`，可复现性依赖本机文件；已在上文记录探针 SHA256。
- **N8（预检拒绝不构成执行证据）**：v9 报告里预检仍会记录逐框字段，但 `validates_observation_id` 被禁止；现场 5 次预检拒绝只影响“该帧能否作为动作前置”，不进入动作绑定，符合设计。

## 6. 未覆盖与后续

- 本轮未运行 live、未调用模型、未验证物理安全；不能据此宣称 v9 的现场可用性或 20 次任务通过。
- 21/44 px 的逐框归属、G4-1 单像素样本的物理判读，均需现场用 v9 报告（含 `changed_runs`）重新取证。
- B1 修复后需另做一次定向复核（至少覆盖 `control/service.py` 枚举、`ControllerProcess.initial_status` 的 180s、`leases` 复验与现有 `dry-run` 计划入口）。
- 之后仍是既定顺序：用户选版/冻结 → 现场复验（含 G1/G3/G5 遮罩交互、目标/障碍真实移动对照）→ 20 次任务与人工逐条确认；T08 保持 `IN_PROGRESS`。

## 7. 边界声明

本记录只审不改；未冻结 v9、未切换现场配置、未修改任何策略/阈值/世界/提示/控制代码。结论仅供用户决定下一步（先修 B1，再考虑冻结与现场复验）。
