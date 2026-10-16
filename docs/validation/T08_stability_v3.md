# T08 稳定性 v3 候选（2026-10-16）

实现者 DSH / Flash（用户按第 47 节派发）。依据 [稳定性 v2 现场复验与定位](T08_stability_v2_field.md)（含第 6 节定位补拍）与 [现场标定](T08_stability_field.md)。这是新候选实现与离线自测，不是现场验收，也不是 ADR-009 独立复核；复核须另派执行者。T08 保持 IN_PROGRESS。

## 1. 版本与安全边界

- 新文件 `live_navigation/stability_v3.json` / `.sha256`，版本 `local-rgb-stability-v3-candidate`，SHA256 `333739e324ccc30f63e0c1ec533290dbd572090c03ef0d2be85711239d4adb5a`。
- 根级配置增加显式 `navigation_design="stable-inference-v3"`；默认仍 legacy。v1（schema=2）、v2（schema=3）的文件、摘要、语义与报告格式均保留；v3 使用 schema=4。
- v3 状态仍是 `REQUIRES_FIELD_CALIBRATION`，CLI 与运行时在创建控制器/采集/供应商网络请求之前拒绝接入 live（`live_navigation/session.py` 的候选拒绝分支）。实际 live 配置、冻结世界与动作阈值未修改。
- 仍为独立 `ControllerProcess`、显式 live 与本机 ARM、原租约/epoch/session 校验、8000ms 推理、30s 任务、单轴 200ms、结束归零、绑定观测最多 min(1500ms, control 配置)。`max_actions`/`freshness_ms` 的声明项与实际强制点沿用第 38 节说明（N3 未变）。

## 2. 判据与策略值

现场数据给出的可分性：真实静止相邻帧的变化（Δ>2）≤0.56%，真实视角移动 61.6%–93.4%；同时真实渲染输出上存在**无法用零容差或局部面积阈值处理的环境动画**（客户端 UI、镜中角色、画面顶部动画带）。v3 只保留两个现场可分离的量：**全局面积上限**与**导航证据保护区零容差**。

对每个非遮罩像素全量计算 `Δ=max(|ΔR|,|ΔG|,|ΔB|)`，Δ>2 才计为变化；alpha 不参与；无采样、无降分辨率。同时满足以下条件才 `stable=true`：

| 检查 | v3 策略值 | 意义与限界 |
| --- | --- | --- |
| 黑帧 | 任一帧保留区域全部 RGB ≤8 则拒绝 | 与 v1/v2 一致；9 不是黑 |
| 全局 | 变化像素占保留像素 ≤50000 ppm，即 **5%** | 实测环境上限：安静段 0.14–0.17%、活跃段中位至 2.07%（v2 现场口径）。5% 对活跃环境留 ≥2.3 倍余量、对最小移动信号 61.6% 留 ≥12 倍余量 |
| 导航证据 | 目标、`forward_clearance`、`recovery_clearance` 的 bbox 外扩 32 像素并裁剪到画面，区域内变化像素必须为 **0** | 安全相关的局部保护不变；仍不能识别模型未报告的通路/目标，也不能排除 Δ≤2 的真实变化 |
| 局部单块 | **已移除**（无 `tile_size`/`tile_offset`/`max_tile_changed` 三键） | 见第 3 节；v3 证据里若出现 `max_tile_changed` 直接判非法 |

排除区 `[1536,0,2048,512)`：由 v2 的 `[1664,0,1856,512)` 扩大。保留像素 = 2160×1440 − 512×512 = **2 848 256**，5% 上限 = **142 412** 像素（142 413 起拒绝；判据用整数乘法，无浮点边界误差）。全局已拒绝时不再评估保护区，报告写 `protected_changed: null`（未评估），不伪装成零。

## 3. 设计与取舍（为什么删块判据、为什么 5%）

- **遮罩扩大**：定位补拍显示 v2 遮罩小于真实 UI 动画范围——遮罩内 8 块持续动画 265–266/266，紧贴遮罩左侧 x=1600 列与右侧 x=1856/x=1920 列同样持续（Δ 241–255），真实动画 UI 横向约 x≈1600–2000；第 6 节给出块边界候选 `[1536,0,2048,512)`，本轮采用。该矩形须在下一轮现场确认**不遮挡导航目标**（G1/G3 目标是否可能落入此带）。
- **删掉逐块判据**：现场 408 对中仅 2 对 stable 且不连续，无法形成预热所需的连续通过（安静窗口的单块变化也有 25–926/1024，>15 必拒），且环境动画本身可形成同量级的连续变化区。任何局部面积阈值都无法同时容下环境动画与拦住小移动体，因此 v3 不再保留该维，只保留「环境噪声在面积上远小于真实移动」这一条可分性。
- **5% 的来源**：安静 0.14–0.17%、活跃 1.71–2.14% → 5% 仍有 ≥2.3 倍余量；移动 61.6%+ → ≥12 倍余量。选 5% 而不是 1% 是因为 1% 已被现场证明对活跃姿态必拒（第 46 节 408 对中 304 对 global_fail）。
- **已知代价（必须写进复核输入）**：保护区外、5% 以内的稀疏真实变化（小移动体、粒子、UI 碎块、镜面碎块）会被判 `stable`。安全依赖的是「导航证据实体 bbox + 32 像素」的零容差保护，而不是全局面积阈值。这是把「环境噪声必然拒绝」换成「保护区内严格、保护区外按面积」的取舍，不是安全性的证明。
- **未采纳的替代**：把遮罩扩到覆盖顶部带（会被判为遮挡画面/导航目标）、按区域分层阈值（缺少现场分区证据）、保留局部判据但放宽容差（现场显示单块可达 926）。

## 4. 实施与隔离

- 新模块 `live_navigation/stability_v3.py`：复用 v2 已审计的比较器（`changed_flags` 整数通道槽、`protected_rectangles`、`masked_rgb`），只改写判据与计数；`verdict()` 由报告计数复验，拒绝越界/负数/非整数计数、非 bool 黑帧，以及**策略无块判据时夹带的块计数**。
- `stability.py`：`load_policy` 支持 `v3`；`compare()` 按 `metric` 分派（`rgb_tolerance_area_guard_v3`）。
- `stability_report.py`：复验分派 `policy_verdict`（未知 metric 显式报错，不再静默走 v2），`SCHEMAS = {v1:2, v2:3, v3:4}`，预热链校验对 v2/v3 生效。
- `stable_runtime.py`：新增 `StableNavigationV3(StableNavigationV2)`，仅换策略版本与 schema；预热、检查点（`after_inference`/`before_submit`）、固定参考、不跨动作复用等行为与 v2 一致。
- `session.py`：`stable-inference-v3` → 策略 v3 → `StableNavigationV3`；`config.py` 的 `navigation_design` 字面量加入新值。

## 5. 证据与汇总

报告 `navigation_design="stable-inference-v3"`、`schema_version=4`，写 `stability_policy`（含新摘要）、`stability_proofs`（全量计数、保护区、保护区矩形）、`warmup_checks` 与 `warmup_references`。汇总重算判据并校验来源、预热连续链、时序/epoch、实体与保护区矩形、动作绑定时效；坏文件整份进 `invalid_files`。`legacy` 默认、v1 与 v2 报告语义不变。

## 6. 验证与结果（离线，实现方自测）

- 新增 25 项测试：`tests/unit/test_stability_v3.py`（17 项，指标/边界）与 `tests/integration/test_stability_v3_runtime.py`（8 项，闭环/报告）。
  - 指标：摘要与 sidecar 逐位一致、5% 边界的 142 412/142 413 像素、Δ≤2 容差与累积漂移 22/23、保护区内单像素拒绝与保护区外同像素放行、外扩 32 像素的矩形 `[97,97,205,205]`、**局部 4×4 变化在无实体时放行/有实体时拒绝**（v3 与 v2 的关键差异）、遮罩 `[1536,0,2048,512)` 覆盖与 x=1535 边界、黑帧 8/9、篡改计数矩阵（负数/非整数/越界/夹带块计数/全局拒绝却带局部计数）。
  - 闭环：焦点过渡预热（索引 `[1,2]`）、3.5s 内存 HTTP 三动作成功、预热耗尽/推理超时/租约到期/急停四类失败零动作、报告变异（含 `schema_version` 与 `navigation_design` 错配）整份拒绝、候选拒绝 live、真实 `ControllerProcess` dry-run 子进程产出并写出交付工件。
- 四项门禁（实现方自测，日志 `.runtime/verify/t08v3_gates.log`）：`python -m pytest -q` → **923 passed in 182.79s**（898 基线＋25 新用例）；`python -m ruff check src tests` → All checks passed!；`python -m ruff format --check src tests` → 124 files already formatted；`python -m pip check` → No broken requirements found.。
- 旧文件与摘要未改：v1 `e9350468…`、v2 `5487965f…`；世界摘要 `dda1d3b1…` 未改；未新增依赖、未改锁文件。

## 7. 明确未证明（复核与现场复验的对抗输入）

- 5% 面积上限对**稀疏真实小变化**（保护区外小移动体、粒子、镜面碎块）无区分能力；保护区内严格依赖模型正确报告实体 bbox。
- Δ≤2 的真实变化仍不可检测；低对比/透明目标、帧间瞬变、UI 遮罩盲区（x≈1600–2000 之外的 UI 碎块）未覆盖。
- 顶部环境动画带身份未确认；`[1536,0,2048,512)` 是否遮挡导航目标未现场确认。
- 五组姿态静止是否全部通过、16/20 是否可达：**未验证**。本候选只把「环境噪声导致必然拒绝」这一条去掉；现场数据（408 + 266 对）在 5% 口径下需要重新采集才能给结论，不得沿用 v2 现场数字或合成回归当现场结论。
- 现场复验若仍拒绝，不得现场直接扩大阈值或就地改策略文件；须发布新版本 + 新摘要并重新走复核。

## 8. 复现命令与下一步

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_stability_v3.py tests/integration/test_stability_v3_runtime.py -q
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -c "from vrc_agent.live_navigation.stability import load_policy; print(load_policy('v3')[1])"
```

下一步：① 另派执行者按 ADR-009 独立复核第 48 节（含本记录）；② 通过后由用户在场做新版本现场复验（五组姿态、动静切换、麦克风、镜面、UI/目标交叠、预热与比较耗时），特别确认 `[1536,0,2048,512)` 不遮挡目标；③ 用户选版 → 延迟复测 → 固定 20 次任务。
