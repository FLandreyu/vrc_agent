# T08 稳定性 v2 候选独立复核（ADR-009，2026-10-16）

复核者：独立执行会话（DSH / Flash，非实现者 Codex / root）。对象为 TASKS 第 40 节交付与 [T08_stability_v2.md](T08_stability_v2.md)（`local-rgb-stability-v2-candidate`，SHA256 `5487965f…`）。本复核只读源码、策略文件与工件：未启动 live、未 ARM、未付费、未发送真实输入、未读密钥。

- 自建探针：`.runtime/verify/v_t08_stability_v2_review.py`（不 import 实现者测试或夹具），**71 项断言 0 失败**；完整输出 `.runtime/verify/t08v2_review_final.log`。
- 独立复跑四项门禁：`893 passed in 136.58s`；`ruff check` All checks passed!；`ruff format --check` 119 files already formatted；`pip check` No broken requirements found.。日志 `.runtime/verify/t08v2_gates.log`。
- 独立复算摘要：v2 `5487965fead36853397440d43a8f09ec11aaf05b90e4b452739f35487ff3084d`（与 sidecar 一致）、v1 `e9350468…` 未变、交付工件 `89faa511…` 逐位一致、世界摘要 `dda1d3b1…` 未变。

## 0. 结论

- 第 40 节的可验证声明**主体未能证伪**：指标边界、区域策略、预热链、证据复验、安全边界与 schema 隔离均有独立断言通过。
- 发现 **3 项非阻断问题**：N1 新结局码未登记 T10 码表（写事件时 `reason_code` 被整字段拒绝）；N2 交付目录遗留 3 份旧格式 v1 工件已不再通过现行复验；N3 策略 `max_actions`/`freshness_ms` 仍是声明项（第 38 节既定方案，本轮实测复现）。
- 交付工件是 **dry-run** 工件：汇总入口按来源整份拒收、不计成功，只改来源字段也仍判 `UNVERIFIED`。**本复核不构成实机验收**：五组现场静止、真实时延与 20 次任务均未执行。T08 保持 IN_PROGRESS，v2 保持 `REQUIRES_FIELD_CALIBRATION`，不得原位改状态或放宽阈值。

## 1. 逐项对照

| 第 40 节声明 | 复核断言（探针编号） | 结果 |
| --- | --- | --- |
| 新文件/摘要/状态、显式 `stable-inference-v2`、默认 legacy、v1 与 schema=2 保留 | A1–A3、B1–B4、B18–B22、D1–D3 | 通过。v2 摘要可由字节重算并与 sidecar 一致；schema=3 配 v2、schema=2 配 v1 之外一律 `unknown navigation design`；声明 schema=1 却带证据被拒（`undeclared stability evidence`），真 legacy 报告仍通过 |
| 全量 Δ≤2 容差、全局 ≤1%、整数计数、全局拒绝时局部写 null | A4–A10、A23–A26 | 通过。`changed_flags` 与标量 oracle 在随机数据上逐像素一致（mismatch=0）；Δ=2 不算变化、Δ=3 算；648/664 像素在 256×256 上给出 stable/拒绝边界；全局已拒绝时 `max_tile_changed`/`protected_changed` 为 null，携带局部计数即 `unexpected partial comparison details`；五类伪造计数全部报错 |
| 32×32 网格 × 四组偏移、每格 ≤15，跨单格边界不漏 | A11–A13 | 通过。单格 15 通过、16 拒绝；完整 4×4 高对比块在 8 个位置（含跨单一网格边界与角点）全部拒绝；3×3（9 像素）通过，与文档「不是所有形状的小目标检测器」一致 |
| 目标/通路/恢复 bbox 外扩 32 像素、区域内变化必须为 0 | A14–A18、B10 | 通过。保护区内单个 Δ>2 像素即拒绝（`protected_changed=1`）；外扩与画面裁剪、去重排序符合声明；proof 里的保护区与源实体框不一致被拒（不能伪造放宽） |
| 仅右上 UI `[1664,0,1856,512)` 为候选排除区、取消左下遮罩、`MASKED_SCENE_EVIDENCE` 不变 | A19–A22、C4、D2 | 通过。掩码内变化被忽略且保留像素数=2160×1440−192×512=3012096；旧左下遮罩处变化重新计入计数与 RGB 摘要；源场景实体框与排除区相交即 `MASKED_SCENE_EVIDENCE` 且零动作；黑帧判据 ≤8 与单通道 9 的边界符合声明 |
| 预热：至少三帧成两对、间隔 ≥100ms、最多 8 帧、失败清空计数、WARMUP_UNSTABLE 且零模型调用 | C1–C3、C7、C12、B11 | 通过。稳定源 1 动作产出 schema=3 报告并通过复验；预热链结构（连续两环稳定、链尾即推理来源观测）自证；持续变化源与 Δ=3/帧漂移都在预热阶段 `WARMUP_UNSTABLE` 且 0 次模型调用；推理后始终不稳定时 `SCENE_UNSTABLE` 且模型调用恰好 3 次（`max_inference_attempts`）；九类预热证据篡改全部被拒 |
| 固定参考、不链式滚动、不跨动作复用；继承控制/租约/时效/归零 | C5、C9–C11、B12–B17 | 通过。推理在途急停 → `MANUAL_STOP`、0 动作、推理已发出 1 次；未 ARM → `ARM_TIMEOUT` 且 0 次模型调用；500ms 租约到期在推理在途生效 → `EXPIRED_LEASE`；推理超预算 → `INFERENCE_TIMEOUT`；派生观测/实体、推理来源、动作绑定时序的 6 类篡改全部被拒 |
| 证据语义：记 `stable` 不冒充 `equal`、报告只做内部一致性复验 | B19–B20、A27 | 通过（含已声明限界）。`equal` 字段在 v2 不可冒充；**自洽**改写计数与 RGB 摘要（保持 `stable` 与计数一致、摘要置零）仍被接受——与文档「计数和哈希不是签名」一致 |
| 汇总重算判定与坏文件整份拒收、T10 口径不改 | D4–D6 | 通过。dry-run 工件被汇总整份计入 `invalid_files`（`report provenance mismatch`）、成功率 0；只翻转 `mode`/`sends_live_input` 后证据链通过但 `UNVERIFIED`；新码写事件被显式拒绝而非静默落盘（见 N1） |
| 候选拒绝 live、门禁先于控制器/采集/网络、无新依赖 | D7–D9、C8 | 通过。计划模式仍只读且世界摘要未变；完整 live 授权下 v2 候选配置仍 exit 2（`field calibration` 消息在建控制器前给出）；交付工件无内联图像/凭据；`requirements.lock`、`pyproject.toml` 自初始提交未变；`src/vrc_agent/runtime.py:42` 的 `ReplayController` 类型检查保留 |
| 「仍未证明」清单如实（不宣称五组静止或 16/20） | 文档与工件一致性、C11 | 通过。工件 `usage.calls=3`、`unknown_cost_calls=3`、`cost_complete=false`，与第 40 节一致；未见任何把合成/离线结果写成实机验收的表述 |

## 2. 非阻断问题

- **N1（码表缺口，承接方向 A 的 F3）**：`INFERENCE_TIMEOUT`、`STABILITY_TIMEOUT`、`IMAGE_ENCODING_TIMEOUT`、`SCENE_UNSTABLE`、`WARMUP_UNSTABLE`、`MASKED_SCENE_EVIDENCE` 六个新结局码均不在 `KNOWN_CODES`。写结构化事件时 `reason_code` 会被整字段丢弃（`log.stats()["rejected_reasons"] == {"reason_code:unregistered_value": 1}`），而报告 JSON 仍保留该码。影响：T10 的事件侧看不到 v2 的失败原因，只能从报告侧读。建议登记这六个码或明确记录「不进事件码表」。**（2026-10-16 处置：实际 26 个层内码全部登记，见第 6 节。）**
- **N2（可追溯性）**：`.runtime/verify/` 内 3 份旧格式 v1 工件（`1ca695c4…`、`6b87de59…`、`a57083a7…`）的 proof 缺少 `session_id`，被现行验证器判 `stability authority mismatch`；它们不是交付物也未在任何文档中被引用。另有 12 份现行格式 v1 报告全部通过复验。建议把旧工件移出交付目录，避免汇总时被误读成回归。**（2026-10-16 处置：已移出至 `.runtime/archive/verify-legacy-v1/`，见第 6 节。）**
- **N3（声明项，第 38 节既定方案）**：把运行时策略 patch 成 `max_actions=1`、`freshness_ms=1` 后，端到端仍执行 2 个动作，报告则声明被改写的值——实测复现 F1。当前实际强制点（技能 `NavigationLimits.max_actions` 默认 40、`min(1500, control.observation_max_age_ms)`）与策略今天的取值一致，运行时行为无缺口；仅在将来改策略文件时会再次出现「声明与强制点不同源」。

## 3. 已声明的剩余限界（本轮以实证确认，非新问题）

- 保护区之外、每格 ≤15 且全局 ≤1% 的变化仍可通过：保护区外单像素变化放行（A15），3×3 目标通过（A13）。
- Δ≤2 的真实变化不可见（A16）；单调漂移每帧 +2 级时，动作前观测仍通过，靠**规划前对固定参考的复验**累计暴露（C11：`SCENE_UNSTABLE`、0 动作、但已消耗 4 次推理）——即预热与复验都会挤压 30s 任务预算，与文档「可用动作数可能进一步减少」一致。
- UI 掩码内变化被完全忽略（A20）；掩码是否在各种姿态下都不含世界内容只有现场才能判定，本轮无法验证。
- 报告只做内部一致性复验：自洽的计数/摘要伪造仍被接受（B20、A27）。

## 4. 未覆盖（不构成验收）

现场五组静止姿态、动静切换、麦克风与镜面现场表现、UI/目标交叠的真实比例、真实推理时延与端到端耗时、20 次任务成功率**均未执行**；也没有现场原始帧对的重放验收。必须按第 40/41 节顺序：先按 N1/N2 处置、再现场复验、由用户选版、再做延迟复测与 20 次任务。

## 5. 复现

```powershell
.venv/Scripts/python.exe .runtime/verify/v_t08_stability_v2_review.py        # A/B/C/D 四段，71 项 0 失败
.venv/Scripts/python.exe .runtime/verify/v_t08_stability_v2_review.py A      # 指标与策略边界 27 项
.venv/Scripts/python.exe .runtime/verify/v_t08_stability_v2_review.py B      # 交付工件与证据链 23 项
.venv/Scripts/python.exe .runtime/verify/v_t08_stability_v2_review.py C      # 运行时行为 12 项（真实 ControllerProcess dry-run）
.venv/Scripts/python.exe .runtime/verify/v_t08_stability_v2_review.py D      # 契约与隔离 9 项
.venv/Scripts/python.exe -m pytest -q                                        # 893 passed in 136.58s
```

探针在 `.runtime/verify/t08v2_review/` 下建立自己的 dry-run 状态目录；C 段使用自建信号源与自建记录型 provider（`paid=False`），不产生外部请求与费用。

## 6. 实现方回应与后续处置（2026-10-16，非本复核者）

> 本节由后续执行者追加，不改动上面复核者写下的结论与证据；复核结论以第 0–5 节为准。

- **N1 已登记处置**（`docs/TASKS.md` 第 44 节，实现者 DSH / Flash）：新增 `src/vrc_agent/live_navigation/errors.py` 由该层自声明结局码闭集，`telemetry/codes.py` 新增 `live_navigation` 家族（72→98 码、9→10 家族）。实际缺口比 N1 列的 6 个码更大：层内共 15 个中止码 ＋ 3 个批次汇总码 ＋ 8 个 `MODEL_*` 派生码未登记。回归 `tests/unit/test_telemetry_live_codes.py`（5 项）＋层闭集清单扩充；敏感度证据 `.runtime/verify/n1_regression_sensitivity.py` 显示摘掉修正后 5 项全败（`{'reason_code:unregistered_value': 26}`、报告侧 `unknown error codes present: TASK_TIMEOUT×1`）。门禁 898 passed in 136.49s（`.runtime/verify/n1_gates.log`）。此为**实现方处置，非独立复核**。
- **N2 已归档**（`docs/TASKS.md` 第 43 节，现场会话）：3 份旧格式 v1 工件移出 `.runtime/verify/` 至 `.runtime/archive/verify-legacy-v1/`，文件内容未改。
- **N3 未改**：`max_actions`/`freshness_ms` 按第 38 节既定方案仍是声明项；本轮修正未触碰策略文件与阈值。
- 本轮处置**未改**稳定性策略与摘要、live 配置与世界摘要、控制/运行时行为；**未启动 live、未 ARM、未付费**，也不构成实机验收。下一步仍按第 43 节：五组现场复验 → 用户选版 → 延迟复测 → 固定 20 次任务。

