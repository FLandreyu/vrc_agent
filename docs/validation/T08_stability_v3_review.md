# T08 稳定性 v3 候选独立复核（ADR-009，2026-10-16）

复核者：Codex / root；v3 实现者：DSH / Flash。对象为 TASKS 第 48/49 节与 [T08_stability_v3.md](T08_stability_v3.md)，策略 `local-rgb-stability-v3-candidate`，SHA256 `333739e324ccc30f63e0c1ec533290dbd572090c03ef0d2be85711239d4adb5a`。

本轮独立编写并运行探针，**不导入实现者的 tests/夹具或旧复核脚本，不修改产品源码、策略、配置、实现者测试**。采用内存合成 BGRA、自建供应商桩与真实 `ControllerProcess` **dry-run** 子进程；仅 dry-run 控制器使用测试 ARM，没有启动 live、付费请求、读取密钥、采集现场图像或发送真实输入。GraphFlow 本会话无可调用工具，未伪造 context/plan/index。

## 0. 结论

**退回修正，不能写“主体未能证伪”或“独立复核通过”。**

- **F1（高，冻结前阻断）：导航证据的 32px 外扩保护区与遮罩交叠时，保护区内变化被遮罩清零；原 bbox 不交叠就不会触发 `MASKED_SCENE_EVIDENCE`。** 自建真实 dry-run IPC 闭环发出一次 `motion`，报告复验接受。
- **F2（中，冻结前需封闭）：`StableNavigation(..., policy_version="v3")` 可选择 v3 判据却绕过预热。** 实测零预热检查、一次 `motion`；报告事后拒绝 `missing warmup evidence`，不能撤销已发动作。生产 CLI 正常选择 `StableNavigationV3`，候选 live 闸门未被绕过。
- **N1/N2（低）：文档存在面积拒绝边界及现场统计误述；报告校验未完全兑现严格类型/禁用字段的声明。** 详见第 2 节。
- 标准装配的预热失败、采集超时、失焦、旧时间、epoch 变化、租约耗尽和急停路径均有零调用/零动作证据；schema 主版本与候选 live 拒绝未能证伪。它们不能抵消 F1/F2。
- **T08 保持 IN_PROGRESS，v3 保持 REQUIRES_FIELD_CALIBRATION。** 五组静止、真实时延、20 次任务没有执行；本复核不是实机验收。

## 1. 逐项对照

| 复核点 | 独立证据 | 结论 |
| --- | --- | --- |
| 版本与冻结边界 | A01–A03：v3 完整摘要符合用户指定；v1/v2 摘要前缀符合既有记录；世界完整摘要仍 `dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335`。完整文件摘要另存 manifest | 未发现本轮改写；默认配置仍 legacy |
| 5% 精确边界 | A05：实造 142412、142413、143412、143413 个变化像素，只有 142412 通过；其余全局拒绝、`protected_changed=null` | 整数判据正确；文档“143413 起拒绝”错误，实际为 **142413** |
| 删除逐块判据的新盲区 | A06/A07：保护区外连续 **300×300=90000** 个高对比像素（约保留区 3.16%）v3 stable、v2 拒绝 | 属已声明代价，但并不只限于“稀疏小变化”；连续大块同样可通过 |
| 比较器与颜色边界 | A04 独立随机标量 oracle 对 20000 像素逐项一致；A08 全屏 Δ=2 通过、Δ=3 拒绝；A12 黑帧 0/8 拒绝、9 通过，alpha 不影响 | 未能证伪；容差不能等同于真实画面静止 |
| 保护区内单像素 | A09 未遮罩保护区一个变化像素拒绝；A11 遮罩边缘外侧像素拒绝；A10 原 bbox 相交边界；B04 原 bbox 实际进入遮罩时零动作 `MASKED_SCENE_EVIDENCE` | 常规分支有效；**外扩保护区交叠缺口 F1 已证伪** |
| 预热与异常 | B01 正常发一次 motion；B03 初帧过渡失败后成功引用 `[1,2]`；持续不稳 8 帧/7 对 → WARMUP_UNSTABLE；旧时间、epoch 变化、失焦、采集超时、500ms 租约到期、急停分别得到对应中止码 | 后七条均零模型调用、零动作且 source 关闭；急停 ESTOP，其余 PAUSED。标准 v3 类未能证伪；直接基类入口见 F2 |
| 证据复验与 schema 隔离 | C01/C02：标准报告通过；缺预热、空引用、错摘要、过短间隔、断链、改保护区、非空块计数、错 epoch、过期动作均拒绝；C03：拿 v3 报告冒充 schema 1/2/3 或 v1/v2 均拒绝 | 未能证伪主要链路；N2 仍可接受两类不合声明的字段 |
| 旧 schema 正例 | 补充探针自建无动作的 v1/v2/v3 报告，只有各自 schema 2/3/4 通过，其余组合拒绝 | 只证明分派正例及错配拒绝，不把空报告当成功导航 |
| 候选 live 门槛 | D：全授权参数下，三代配置均在 provider/controller/capture 哨兵之前因候选状态拒绝；补充探针对三个运行时构造器验证 live 标签拒绝（没有启动进程） | 显式 live 与本机人工 ARM 门槛未改，未实际启动 live |

## 2. 问题与最小反例

### F1：保护区被遮罩截断，仍宣称保护区零变化

- 位置：`stability_v3.py:60` 先 `masked_rgb` 再计变化；`stable_runtime.py:67` 的 `intersects_mask` 仅检查原始 bbox；矩形外扩由 `stability_v2.py:45` 计算。
- 2160×1440 下，目标 bbox 像素坐标 `[1400,100,1520,200]`，外扩后 `[1368,68,1552,232]`；v3 遮罩 `[1536,0,2048,512)`。改变 `(1540,150)` 从 RGB 40 到 200：像素属于外扩保护区与遮罩交集，原 bbox 不碰遮罩。
- 比较结果：`changed_pixels=0`、`protected_changed=0`、`stable=true`，归一化 RGB 摘要相同；原始帧摘要不同。这不是保护区外的 5% 已知限界，直接违背“bbox 外扩 32 像素内变化必须为 0”。
- 运行时证据：`margin.json` 使用相同帧变更，自建模型正确引用当前观测并给出目标/floor 实体；标准 `StableNavigationV3` → **1 次 motion（yaw）**，结束 PAUSED，报告通过 `validate_stability_report`。对照 `overlap.json` 把原 bbox 右界改为 1550 时，正确中止 `MASKED_SCENE_EVIDENCE`、零动作。
- 建议修正：在派生/执行前拒绝**外扩保护矩形**与遮罩相交；或明确实现保护区优先、对交集读取未遮罩像素。不要只改报告文字把保护承诺缩小。须补比较器、运行时与报告复验契约，并遵守新版本/摘要流程，保持旧版本可追溯。本缺口来自共享机制，v3 遮罩扩大后仍存在，不声称仅由 v3 新引入。

### F2：公共基类的版本选择可跳过 v3 预热

- 位置：`stable_runtime.py:17` 接受任意已知 `policy_version`；基类 `reference_capture`（39）只取一帧；预热只在 `StableNavigationV2` 的覆盖方法（207）中，v3 子类继承它。
- 反例：直接调用 `StableNavigation(..., policy_version="v3")`。实测 **5 帧、0 warmup_checks、2 次模型调用、1 次 motion**，最终 ACTION_LIMIT/PAUSED；报告声明 schema=4，却缺预热字段，复验才报 `missing warmup evidence`。
- 对照标准 `StableNavigationV3`：相同正常源 9 帧、4 次预热比较、2 次模型调用、1 次 motion，报告通过。测试把技能动作额度收紧为 1，仅为控制探针长度，没有放宽动作、安全或 live 门槛。
- 建议：基类只允许 v1；将版本选择固定在对应实现类中，或用统一工厂按版本强制装配所需预热行为。错误的直接构造应在任何采集/模型/动作前拒绝，不能依赖事后报告审计。这个入口不是正常 CLI 路径，也没有绕过当前候选 live 拒绝；但保留它会让未来调用方悄悄省掉预热。
- 探针中的记录名 `N3_constructor_without_warmup` 为最初发现时编号；最终按本节 **F2** 管理。

### N1：文档边界与现场统计表述

- TASKS 第 48 节、AGENTS v3 条目与 v3 文档第 2 节写“143413 起拒绝”；实际及文档第 6 节测试口径都是 **142413** 起拒绝。142412/142413 两个相邻像素数已独立实测。
- v3 文档第 3 节“408 对中没有一对通过局部判据”与现场记录的 **2 对 stable（不连续）** 不相容。现场能支持“不能形成连续预热通过、可用性极差”，不能支持“零对通过局部判据”。不影响本轮面积计算结果，但应纠正决策依据的措辞。

### N2：报告字段的严格性弱于声明

- `stability_v3.verdict:31` 只比较数值相等，因此 `retained_pixels=2848256.0` 通过；第 4 节声称拒绝非整数计数并非对所有计数成立。
- `verdict:39` 使用 `.get(...) is not None`，故 `max_tile_changed: null` 通过；“v3 证据若出现块计数直接判非法”若指禁止该键，当前并未实现。非空块计数 0 确实拒绝。
- 两者均以正常运行时报告为底稿，仅改单字段，经完整 `validate_stability_report` 仍接受；不是只在底层函数复现。均未增加动作权限，列非阻断契约瑕疵；建议严格 `type(retained_pixels) is int` 并使用键存在性检查，或明确约定允许 null。不要把本项误称为报告数字签名问题。

## 3. 已声明限界与独立性折扣

- 5% 是面积阈值，不能识别变化的语义。连续 300×300 区块也可放行；保护区外出现新物体/低纹理移动不能由少量现场“大幅视角移动”样本排除。上限与样本间的倍数仅描述样本，不是普适安全余量。
- Δ≤2、遮罩内容、离散采样间出现又消失的瞬变、模型漏报/错误 bbox 仍无法排除。预热时尚无实体，比较只用全局面积和黑帧；它证明的是连续帧对符合候选指标，不是语义静止。
- 报告只能复验内部一致性，没有原始图像或签名；本次不声称发现或解决自洽伪造报告问题。仍沿用第 38 节 max_actions/freshness_ms 声明项限制。
- 实现与复核同机同工作树，无 Git 提交锚点，使用策略与文件 SHA256 留证；Codex 曾实现方向 A/v2 共享基底，**本次未参与 DSH 的 v3 实现**。独立性针对本次 v3 修订，不能宣称对全部共享代码从未参与实现。
- 不使用 923 项门禁代替独立反例，也不把 dry-run 控制器日志视为实机证据。

## 4. 探针、工件与门禁

- 自建主探针：`.runtime/verify/v_t08v3_codex_review.py`；最终输出 `.runtime/verify/t08v3_codex_probe_final.log`，78 条记录。补充探针 `.runtime/verify/v_t08v3_codex_isolation.py`，输出 `t08v3_codex_isolation.log`，16 条记录。合计 **94 条记录（12 条场景事实、82 个条件检查），条件检查 0 失败**。**F/N 条件为“反例被复现”，不能把零失败解释为产品通过。**
- 最终独占目录：`.runtime/verify/t08v3_codex_1792162676538338500/`，包含 12 份各自的运行报告及 `results.json`。主探针 SHA256 `54a59944776db03bd9acfe5a6620a7910630c092f5df7fe46b9e9ce3214b3308`；results SHA256 `39a11eba72e0f39b2a1a1d956898ceca4366171954966d823e33caa6847295c8`。
- F1 工件 `margin.json` SHA256 `5b4f872f31cc3d25b5e036bf126199a1db1030561174c7547faf506bde42fdb6`；F2 工件 `base_constructor.json` SHA256 `3cb91253b83f0e7b7a30dd2b39339bacf51e61117af4bce70b735f52eb93fae9`。
- `.runtime/verify/t08v3_codex_manifest.json` 记录被审查代码/策略/依赖文件摘要，补充探针复核摘要一致。本轮未改这些文件。
- 第一轮探针目录 `t08v3_codex_1792162596927221300/` 与 `t08v3_codex_probe.log` 保留调试历史：自建模型最初漏填 `evidence_observation_id`，实体被正确拒绝，导致 2 条探针期望不符。已修复**探针**并完整重跑；旧轮不能作为 F1 的运动证据或产品缺陷，以上结论只引用最终目录。

| 门禁（项目虚拟环境，复核者实际运行） | 结果 |
| --- | --- |
| `python -m pytest -q` | **923 passed in 158.06s (0:02:38)**；日志 `.runtime/verify/t08v3_codex_pytest.log` |
| `python -m ruff check src tests` | All checks passed! |
| `python -m ruff format --check src tests` | 124 files already formatted |
| `python -m pip check` | No broken requirements found. |

## 5. 未覆盖（不构成验收）

没有重采五组现场姿态、真实麦克风/镜面/UI/目标交叠、动静切换、真实供应商延迟与任务成功率。本轮合成目标和图像只用于反例；没有任何一项 20 次实机任务完成。也未做全平台长时间调度或性能压力验收；主探针正常 runtime 使用真实时间与 dry-run IPC，不能据此承诺现场每次比较都低于 1s。

## 6. 复现

```powershell
.venv/Scripts/python.exe .runtime/verify/v_t08v3_codex_review.py
.venv/Scripts/python.exe .runtime/verify/v_t08v3_codex_isolation.py
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
.venv/Scripts/python.exe -m pip check
```

主探针每次建立新目录，不覆盖既有场景证据；日志重定向应使用新文件名。补充探针摘要核对针对本轮 manifest；修改产品后摘要不同是预期现象，不应覆盖旧 manifest 来伪造版本一致。

## 7. 下一步

1. 实现方修正 **F1/F2**，同步契约和独立可重放回归；N1 文档更正与 N2 校验收紧可一并处理。涉及判据/遮罩/保护语义变更遵守新版本与摘要流程，不原位改候选为 FROZEN。
2. 由另一执行者针对修正件再次复核；不得用重跑本探针、或门禁全绿替代独立结论。
3. 通过后再现场复验 → 用户选版 → 延迟复测 → 固定 20 次真实任务（接管/超时/卡住计失败，至少 16/20）。本轮未授权或执行这些实机步骤。

## 8. 实现方回应（2026-10-16，实现者 DSH / Flash；非复核）

本节是**实现方**对第 7 节第 1 条的处置记录，不改变第 0–7 节的复核结论，也不构成复核或验收。完整证据见 [v4 候选验证记录](T08_stability_v4.md) 与 `docs/TASKS.md` 第 51 节。

| 项 | 处置 | 位置 |
| --- | --- | --- |
| F1（高，冻结前阻断） | 按复核建议走保守方案「派生/执行前拒绝相交」，并补齐三层契约：比较器 `protected region masked`、运行时任何动作前 `RuntimeHalt("MASKED_SCENE_EVIDENCE")`、报告复验同一判据拒绝 | `stability.py`（`masked_protection_overlap`/`rejects_masked_protection`）、`stability_v3.py::verdict`、`stable_runtime.py::reject_masked_protection`（`observe`/`before_submit` 调用点） |
| F1 版本流程 | 保护语义按**新版本＋新摘要**落地：`stability_v4.json`（`e4dcb100…`，schema=5，`stable-inference-v4`，`protected_mask_policy: "reject_on_overlap"`）；v4 与 v3 的键差异只有 `version` 与该字段 | `stability_v4.json`/`.sha256`、`stability_report.py`、`session.py`、`config.py` |
| F2（中，冻结前需封闭） | 基类 `POLICY_VERSION = "v1"`，版本不符在任何采集/模型/动作前 `ValueError`；新增 `STABLE_NAVIGATION_CLASSES` 与 `stable_navigation_class()` 统一工厂，`session.py` 只经工厂选类 | `stable_runtime.py`、`session.py` |
| N1（文档） | 拒绝边界改为 **142 413**（第 48 节、`AGENTS.md` v3 条目、v3 文档第 2 节）；v3 文档第 3 节改为现场记录能支持的口径 | `docs/TASKS.md` 第 48 节、`AGENTS.md`、`T08_stability_v3.md` |
| N2（校验收紧） | `retained_pixels` 用 `type(...) is int`；已删除的块字段按键存在性判非法（`max_tile_changed: null` 亦拒） | `stability_v3.py::verdict` |

回归：`tests/unit/test_stability_v3_review_regressions.py`（5 项，含复核最小反例在 v4 被拒 / v3 保持原行为、工厂与基类约束、N2 严格性）＋`tests/integration/test_stability_v4_runtime.py`（9 个用例）。四项门禁见 `.runtime/verify/t08v4_gates.log`。

边界：v4 仍为 `REQUIRES_FIELD_CALIBRATION` 并拒绝 live；v1/v2/v3 文件与摘要未改，其既有结论仍锚定原摘要；未启动 live、未 ARM、未付费、未发真实输入。**下一步仍按第 7 节第 2 条：由另一执行者复核本修正件。**
