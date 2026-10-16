# T08 稳定性 v4 候选（按 v3 独立复核第 7 节修正）

- 日期：2026-10-16
- 作者：实现者（DSH / flash），**非复核者**
- 依据：[v3 独立复核记录](T08_stability_v3_review.md) 第 7 节（F1/F2/N1/N2）、[v2 现场复验记录](T08_stability_v2_field.md)（含第 6 节）、`docs/TASKS.md` 第 46/47/50 节
- 状态：候选，`REQUIRES_FIELD_CALIBRATION`，生产入口与运行时**拒绝**接入 live；**尚未经过独立复核，也尚未现场复验**

## 1. 结论与安全边界

v3 候选被独立复核**退回修正**，其中 F1 是保护语义缺陷：策略把导航证据 bbox 外扩 32 像素后才要求「保护区内零变化」，但当外扩矩形与 UI 遮罩相交时，遮罩先把该区域的像素清零，比较器因此看不到任何变化，进而把「交集里发生了真实变化」判成 stable。复核给出了最小反例，并且该反例在受监管运行时里还能发出一次 dry-run motion（见复核记录第 3 节的 `margin.json` 证据与运行时逐行追踪）。

本轮不改判据阈值（阈值来自第 46/47 节现场证据，见第 3 节），只做两件事：把「外扩保护矩形不得与遮罩相交」变成策略里**显式声明并被三层强制执行**的语义，并把 F2 的版本装配收紧到统一工厂。修正按复核要求走**新版本 + 新摘要**流程：新增 `stability_v4.json`（`protected_mask_policy: reject_on_overlap`），v1/v2/v3 的策略文件与摘要**一字未改**，其既有复核结论仍然锚定在原摘要上。

未声明的旧版本（v1/v2/v3）保持原有行为不变——这不是遗留缺口，而是可追溯性要求：`protected_mask_policy` 是版本化语义，v4 声明它、v3 不声明它。v4 与 v3 的差异**只有** `version` 与 `protected_mask_policy` 两个键（回归用例逐键断言），因此 v4 是 v3 的直接继承者，而不是新判据。

安全边界不变：v4 仍是候选，CLI 与运行时在建立控制器、采集画面、发网络请求之前就以 `field calibration` 拒绝 live；ARM 仍只走本机热键；本轮未启动 live、未 ARM、未付费、未发真实输入。

## 2. 版本、策略值与固定摘要

| 项 | 值 |
| --- | --- |
| 版本名 | `local-rgb-stability-v4-candidate` |
| 文件 | `src/vrc_agent/live_navigation/stability_v4.json`（+ `.sha256`） |
| SHA256 | `e4dcb100c6da68e360199593e90408806aa7753558650e4403e9497b2aefefea` |
| schema | 5 |
| 装配键 | `navigation_design = "stable-inference-v4"` |
| 指标 | `rgb_tolerance_area_guard_v3`（沿用 v3 形态） |
| 保护语义 | `protected_mask_policy: "reject_on_overlap"` |
| 排除区 | `[[1536,0,2048,512]]` |
| 全局面积上限 | `max_changed_ppm = 50000`（5%） |
| 保留像素 | 2160×1440 − 512×512 = 2 848 256；142 412 以内通过、**142 413 起拒绝** |
| 其他判据 | `pixel_tolerance 2`、`protected_margin 32`、`black_threshold 8`、`warmup_pairs 2`/`warmup_max_frames 8`/`warmup_interval_ms 100`、`checkpoints ["after_inference","before_submit"]` |
| 与 v3 的键差异 | 仅 `version`、`protected_mask_policy` |

`load_policy("v4")` 会重算文件字节的 SHA-256 并与 `.sha256` 逐字符比对；摘要侧车与文件都是 CRLF，和 v1–v3 同风格。

## 3. 设计与取舍

**F1（外扩保护区与遮罩交叠必须先拦）**。三层强制执行，判据只有一个实现：

1. **比较器**：`stability_v3.verdict` 在 `rejects_masked_protection(policy)` 为真时，要求证据里的 `protected_rectangles` 是合法的整数矩形列表，并用 `stability.masked_protection_overlap` 判相交；相交即 `ValueError("protected region masked")`，绝不返回 stable。`compare_v3` 因此对 v4 直接拒绝，而不是先被掩码清零再放行。
2. **运行时**：`StableNavigation.reject_masked_protection(entities)` 在 `observe()`（推理返回后、`fresh_proof`/比较之前）与 `before_submit()`（每次尝试的 `fresh_proof` 之前）调用；相交即 `RuntimeHalt("MASKED_SCENE_EVIDENCE")`，**在任何动作提交与派发之前**中止该任务。这与既有 `MASKED_SCENE_EVIDENCE`（实体与 UI 掩码相交）语义一致，不新增结局码。
3. **报告复验**：`validate_stability_report` → `validate_proof` → `policy_verdict` → `verdict`，因此一份 `navigation_design=stable-inference-v4` 的报告若带着与外扩保护区相交的证据，会被复验拒绝，不能靠事后审计补回。

这也是复核给出的两个备选里更保守的一个：不是「保护区优先读未遮罩像素」（那要改比较基准与掩码顺序，判据形态会变），而是**相交即拒绝**，把不可证明的情况明确判为失败。

**F2（版本装配走统一工厂）**。`StableNavigation.POLICY_VERSION = "v1"`，构造函数在 `policy_version` 与 `self.POLICY_VERSION` 不一致时立刻 `ValueError("stability policy version must match the navigation class")`——发生在任何采集、模型调用、动作之前；`StableNavigationV2/V3/V4` 各自只声明自己的 `POLICY_VERSION`。新增 `STABLE_NAVIGATION_CLASSES` 与 `stable_navigation_class(policy_version)`（未知版本报 `unknown stability policy version`），`session.py` 只经由工厂选类，不再用 if/elif 手工拼装。

**N1（文档更正）**：拒绝边界写作 143 413 的两处（`docs/TASKS.md` 第 48 节、`AGENTS.md` v3 条目、`T08_stability_v3.md` 第 2 节）已改为 **142 413**；`T08_stability_v3.md` 第 3 节「408 对中没有一对通过局部判据」已改为现场记录能支持的口径——「408 对中仅 2 对 stable 且不连续，无法形成预热所需的连续通过，可用性极差」。

**N2（报告校验严格化）**：`verdict` 对 `retained_pixels` 改用 `type(...) is int`（`2848256.0`、`True` 都被拒），对已删除的块字段改用**键存在性**判断（`max_tile_changed: null` 也被拒）。这两条是校验契约收紧，不改变任何已通过证据的判定结果，对 v3/v4 同时生效。

## 4. 实施与隔离

- 新增：`src/vrc_agent/live_navigation/stability_v4.json`、`.sha256`。
- 修改：`stability.py`（`load_policy` 允许 v4；新增 `rejects_masked_protection`、`masked_protection_overlap`）、`stability_v3.py`（verdict 的 F1 与 N2 分支）、`stable_runtime.py`（基类只允许 v1、工厂、`StableNavigationV4`、`reject_masked_protection` 调用点）、`stability_report.py`（`SCHEMAS` 加 v4=5、`WARMUP_VERSIONS` 加 v4、`validate_stability_report` 版本表加 `stable-inference-v4`）、`session.py`（版本映射加 v4、改用工厂）、`config.py`（`navigation_design` Literal 加 `stable-inference-v4`）。默认仍是 `legacy`。
- 未改：v1/v2/v3 的策略文件与摘要、世界摘要、live 配置、依赖锁、`CONTROL_UNAVAILABLE` 等既有结局码语义。

## 5. 证据与汇总

- 回归：`tests/unit/test_stability_v3_review_regressions.py`（5 项）——v4 是 v3 的直接继承者且差异仅两个键；F1 最小反例在 v4 下被拒（`protected region masked`）而 v3 保持既有行为；`verdict` 对相交证据/非法矩形结构拒绝、对干净证据通过；基类只接受 v1、工厂与 `POLICY_VERSION` 一致、未知版本拒绝；N2 的类型与块键严格性。
- 集成：`tests/integration/test_stability_v4_runtime.py`（6 个用例，含参数化）——v4 成功路径用 schema 5 且保留面积策略；**遮罩保护相交在任何动作前中止**；四类失败零动作；报告篡改与整文件拒绝；候选 live 硬拒绝；写 `.runtime/verify/t08_stability_v4-{session_id}.json`。
- 定向实测（本轮复跑）：同帧 stable；Δ2 stable；Δ3 拒；保护区内单像素拒；保护区外单像素放行；新遮罩内 `(1600,384)`/`(1536,511)` 计 0、`x=1535` 计 1；142 412 通过、142 413 拒；**F1 反例（bbox `[1400,100,1520,200]` → 外扩 `[1368,68,1552,232]`，变化点 `(1540,150)`）在 v4 下报 `protected region masked`**。

## 6. 验证与结果

四项门禁在本机项目虚拟环境（`.venv/Scripts/python.exe`，Python 3.14.6）独立复跑，日志 `.runtime/verify/t08v4_gates.log`：

- `python -m pytest -q` → **937 passed in 200.36s**（v3 的 923 基线上新增 14 项：5 项回归 + 9 项集成用例）
- `python -m ruff check src tests` → All checks passed!
- `python -m ruff format --check src tests` → 126 files already formatted
- `python -m pip check` → No broken requirements found.

门禁全绿只说明离线测试通过，**不构成独立复核，也不构成实机验收**。

## 7. 明确未证明

- 本版本**没有做过现场复验**：五组静止、运动、遮罩覆盖、预热可用性都未在真实客户端上验证；扩充后的遮罩 `[1536,0,2048,512)` 是否会遮挡导航目标仍未确认（第 47 节第 6 节列为现场必检项）。
- 判据的可分性仍受 v3 声明限界：面积 <5% 的小移动体、Δ≤2 的真实变化、遮罩覆盖到的画面内容都不可检出；保护区内零容差只在「外扩矩形与遮罩不相交」时成立，相交时按 F1 直接拒绝，不做证明。
- v1/v2/v3 保持既有行为：它们仍可声明接入离线报告路径，但仍拒绝 live；其复核结论钉在原摘要上，不因本次修正而「顺带通过」。v3 已被 v4 取代为待现场复核的候选。
- 本轮未启动 live、未 ARM、未付费、未发真实输入；20 次任务与延迟复测继续顺延。

## 8. 复现命令与下一步

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m pytest -q tests/unit/test_stability_v3_review_regressions.py tests/integration/test_stability_v4_runtime.py
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
.venv/Scripts/python.exe -m pip check
```

下一步（顺序不可省）：① 由**另一执行者**按 ADR-009 独立复核本修正件（F1 三层强制、F2 工厂、N1/N2）；② 复核通过后再做现场复验，现场必须先确认新遮罩不遮挡导航目标、保护区与遮罩不再相交；③ 由用户选择是否冻结；④ 之后才谈 20 次任务成功率。不得用本轮门禁数字替代①②。
