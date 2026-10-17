# T08 有界重试（甲）与 v7 逐证据稳定性候选（乙）独立定向复核

- 复核日期：2026-10-18
- 复核者：DSH / Flash（**独立复核角色，只审不改**：未改产品源码、策略文件、世界、提示、配置与测试）
- 复核对象：`45af68f`（甲：v6 有界重试）、`23f92be`（乙：v7 逐证据稳定性候选）与其交付文档 `docs/validation/T08_bounded_retry.md`、`docs/validation/T08_stability_v7_proposal.md`、`docs/TASKS.md` 第 83/84/85/86 节
- 依据：ADR-009（定向复核：只检验被声明的主张，自建探针复现，不复用实现方自测作证据）
- **本记录不是实机验收**：未启动 live、未 ARM、未调用付费模型、未发送真实输入、未读取密钥；全部证据来自离线源码核对与自建探针。

## 1. 结论

| 对象 | 结论 |
| --- | --- |
| 甲 `45af68f`（v6 有界重试） | **声明主体未能证伪、无阻断项** |
| 乙 `23f92be`（v7 逐证据稳定性候选） | **声明主体未能证伪、无阻断项** |

两者的语义边界必须同时保留：

- 甲的结论**只覆盖**「每周期共享 3 次 attempt、证据绑定、预检不授权、不可重试集合、报告可重算、ARM 拒绝提示」这六项。它不构成 v6 稳定性判据本身的复验，也不构成对 pilot5 现场结果（第 86 节 F-F / F-A）的背书。
- 乙仍是**未冻结候选**（`status=REQUIRES_FIELD_CALIBRATION`、拒绝 live、默认配置仍 v6/dry-run）。本复核**不能**被引用为冻结依据、通过验收或实机可用证据。
- 非阻断观察见第 6 节 N1–N10，其中 N3（控制家族码）、N5（现场归因）、N8（报告体积）与 N9（校验分工）涉及报告口径，使用报告/批次汇总时须一并读。

## 2. 复核时的仓库状态（必须声明）

- `HEAD = f9e9c7c`（“v6 重试后试点（第 86 节）”）；`git diff --stat 23f92be..HEAD` 仅 `docs/TASKS.md`，即**被审产品代码等于乙提交**。
- 复核时**工作树脏**，内含另一执行者（Codex）未提交的第 87 节 F-F 修复：`docs/ARCHITECTURE.md`、`docs/TASKS.md`、`src/vrc_agent/live_navigation/stability_report.py`、`src/vrc_agent/planner/__init__.py`、`src/vrc_agent/skills/navigation.py` 已修改，另有 4 个未跟踪文件（`docs/validation/T08_action_verification_fix.md`、`tests/fixtures/pilot5_verification.json`、`tests/integration/test_verification_diagnostics_report.py`、`tests/unit/test_navigation_verification_diagnostics.py`）。
- 因此本轮**探针与四项门禁跑在“HEAD + 未提交第 87 节”上**。对被审对象的唯一触及是 `stability_report.py`：新增 `validate_feedback_diagnostics(report)` 并在 `validate_stability_report` 首行调用，只对含 `diagnostics_version`/`verification_predicates`/`failed_predicates` 的 `navigation_feedback` **增加拒绝**，不改变 attempt/v7 校验路径（`git diff` 逐行核对）。据此 A/B 结论对 `23f92be` 仍成立；本节保留以杜绝“门禁数字对应哪棵树”的歧义。
- 未覆盖、未回滚、未 stage 上述他人改动。

## 3. 探针与运行命令

两个探针自建，只用产品代码（不 import `tests/` 夹具），不 spawn 控制子进程、不注册热键、不 live：内存 `ControllerProcess` 子类 + 真实 `Controller` 内核 + 内存 http 替身 + `RecordingTransport`，帧为合成 2160×1440 BGRA。

| 探针 | SHA256 | 运行命令 | 结果 |
| --- | --- | --- | --- |
| `.runtime/verify/t08_v7_review_jia_probe.py` | `385295f60d37531679a5403a25e13147db138c148601b5020b291fdf285d9c69` | `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe .runtime/verify/t08_v7_review_jia_probe.py \| tee .runtime/verify/t08_jia_probe.log` | **12 项，0 失败** |
| `.runtime/verify/t08_v7_review_yi_probe.py` | `8e56e6efcb82bd280c81716c61b91bddf2c2c4630ea9963c7bd073843aac8d1b` | `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe .runtime/verify/t08_v7_review_yi_probe.py \| tee .runtime/verify/t08_yi_probe.log` | **9 项，0 失败** |

探针日志与工件（全部 `.runtime/verify/`）：

| 文件 | SHA256 |
| --- | --- |
| `t08_jia_probe.log` | `e4d11aa714895bd8133cdd0272c92fb8ea401a17d8182b0cb8eff94e6060c052` |
| `t08_yi_probe.log` | `d01bad51437242ed71d5e9f1b25e438cf224446dc42e5b9d42337faf2a3a0c9d` |
| `t08_jia_a1_precheck_all.json`（三次预检全失败） | `fc055be421b77022282914e5f0b8002ee9dbcda6146a186266509db4492c1139` |
| `t08_jia_a1_nested.json`（三次 before_submit 全失败） | `82fe4dc94dc6ac7b7fc526d330e3ef35dbeaa9b26269bf45affe6d4cf46ac52d` |
| `t08_jia_a5_historical.json`（退化到历史 schema9 形态） | `b17d669aac4d78f7c5fea56491263f430e9c91cf4a3273ef74a8dcba319a61e5` |
| `t08_yi_v6_report.json` | `8511f8c593266b1ba7d14d4afef45aa9664922adb37a6584e71f5f0bc117aaa3` |
| `t08_yi_v7_report.json` | `e2942ff02e6e9dbd2ac1040d5a9045b441a3d56a1fec9016e65aab843664844e` |

## 4. 甲 `45af68f`：六项逐条证据

### A1 attempt 预算：3 次共享、不放大、第 4 次被拒 —— 未能证伪

| 场景 | 实测 |
| --- | --- |
| 每次预检都被拒（预检帧被污染） | `outcome=SCENE_UNSTABLE`，`stability_attempt` 事件恰 `[(1,1),(1,2),(1,3)]`，预检 `[(1,1),(1,2),(1,3)]`，**模型推理 0 次**，无动作 |
| after_inference 第 1 次被拒 | 周期 1 的 attempts `[1,2]`，推理 2 次，第 2 次恢复并完成 1 个动作（`MANUAL_STOP` 收尾） |
| before_submit 第 1 次被拒 | 周期 1 的 attempts `[1,2]`，未放大 |
| 三次 before_submit 全失败 | **推理 3 次（不是 9 次）**，`SCENE_UNSTABLE`，无动作 |
| 直接越过守卫再调一次 `reference_capture` | `RuntimeHalt("SCENE_UNSTABLE")`，`attempt` 仍为 3、无新事件 —— 第 4 次被拒 |

机制核对（`src/vrc_agent/live_navigation/stable_runtime.py`）：预算只有一个计数器 `self.attempt`（`V6.__init__`），`V6.reference_capture` 先 `if self.attempt >= limit: raise` 再 `self.attempt += 1`；`V6.observe` 的 `while self.attempt < limit` 与基类 `observe`/`before_submit` 内部的 `for _ in range(max_inference_attempts)` 共用同一计数器，故基类循环在 V6 下不会独立再放大一次。`t08_jia_a1_nested.json` 的 `run_parameters` 与事件可逐条对上：`{"motion_profile":"field",…,"freshness_ms":4000,"task_timeout_s":180.0,"max_actions":40,"max_inference_attempts":3}`。

### A2 证据绑定：只由同一 attempt 的链支撑 —— 未能证伪

`t08_yi_v6_report.json`/恢复场景下动作只绑定在 `before_submit` 证明上（`attempt=2`，`reference_observation_id` 等于第 2 次 `inference_source` 的观测 ID）。以下伪造全部被 `ValueError` 拒绝（探针逐条）:

- `proof.attempt ∈ {1(与 source 不符), True, 4, None}`、删 `attempt`、改 `source.attempt`、`precheck.attempt=3`（超出上限）、`attempt_cycle=0`；
- 重复的 `stability_attempt` 事件（同 (cycle,attempt) 第二次出现）；
- 动作改绑预检观测 ID（“action missing stability checkpoint”）、改绑不存在的观测 ID；
- proof 的 `reference_captured_at_ns` 前移 5 s（与 source/capture 不符）。

机制：`attempts.py` 强制 `proof.(cycle,attempt) == inference_source.(cycle,attempt)` 且每个 source 必须有同 `reference_observation_id` 的 **accepted** 预检；`stability_report.py` 强制动作 → `validates_observation_id` → accepted proof → `derived_from` → accepted proof → source → precheck 全链一致，且 `checked_at_ns <= issued_at_ns < observation.captured_at_ns + freshness_ms`。

### A3 预检不作动作证据、两检查点每 attempt 实评 —— 未能证伪

- 全部预检记录**不含** `validates_observation_id`（校验器另有硬拒绝：“precheck cannot authorize actions”）；
- 由 `proofs.pop()` + `prechecks.append()` 可见预检证明不会留在 `stability_proofs` 里参与动作绑定；
- `stability_precheck` 事件在 attempt 1、2 均为 `accepted=True`，且第 2 个 attempt 的 after_inference（`inference_source` 的 `(1,2)`）与 before_submit（proof 的 `(1,2)`）都实际出现，说明两检查点每 attempt 都重评。

### A4 不可重试集合与优先级 —— 未能证伪

| 触发 | 实测 |
| --- | --- |
| 人工急停 | `MANUAL_STOP`，attempts `[1]`，不重试 |
| 预检帧过期（captured_at 老 5000 ms） | `STALE_OBSERVATION`，attempts `[1]`，**推理 0 次** |
| 预热期间帧持续变化 | `WARMUP_UNSTABLE`，attempts `[1]`，推理 0 次 |
| 租约到期 | `EXPIRED_LEASE`，attempts `[1]` |
| epoch 变化 | `EPOCH_MISMATCH`，attempts `[1]` |

源码：`V6.observe` 的 `except RuntimeHalt as exc: if str(exc) != "SCENE_UNSTABLE": raise` —— 只有 `SCENE_UNSTABLE`（稳定性判据不通过）进入重试；权限/焦点/epoch/租约/急停码全部直抛。4000 ms 新鲜度、`task_timeout_s=180`、`max_actions=40`、30 s 基础租约与续租上限、急停/归零优先级均未被甲触及（`git diff 45af68f^..45af68f` 不含 `leases.py`、`control/core.py`、配置默认值）。

注：预检新鲜度失败从 `SCENE_UNSTABLE` 改为 `STALE_OBSERVATION` 是甲的行为变化（见 N3、N4）。

### A5 报告可重算 —— 未能证伪

- `run_parameters.max_inference_attempts == 3 == policy["max_inference_attempts"]`；`parameters.py` 只在该键存在时把它纳入严格 JSON 相等比较（否则历史报告无此键也通过），不回显载荷。
- `stability_attempt` 事件的 `(cycle,attempt)` 每周期从 1 连续、`attempt == counts[cycle]+1`；所有 precheck/proof/`inference_source`/`observation` 事件的 `(cycle,attempt)` 必须已在事件里声明（“undeclared attempt evidence”）。
- 历史兼容：把预算与全部 attempt 元数据一并删除后，`validate_stability_report` 仍通过（`t08_jia_a5_historical.json`）—— 历史 schema9 可读。
- 伪造被拒：只删预算（残留 attempt 元数据）、补回预算但删 `stability_attempt` 事件、预算取 `{1,2,4,True,"3",3.0}` 全部 `ValueError`。

### A6 ARM 拒绝提示只走非阻塞 outbox —— 未能证伪

AST 与源码核对（`src/vrc_agent/live_navigation/control/service.py`）：

- `_worker` 的 arm 分支在 `core.arm(...)` 抛 `ValueError` 时先 `respond({"notification":"arm_refused","message":"ARM refused: …"})` 再 `raise`，外层 `except ValueError` 仍 `log.emit("human_command_rejected", operation=event)` —— 安全判定与提示互不影响；
- `respond` 的实现是 `outbox.put_nowait(...)`，`queue.Full` → `disconnected.set()`（失败即断开，不会阻塞控制循环）；
- 主代理侧 `ControllerProcess.request` 只对 `notification in {"lease_extended","arm_refused"}` 打印 `message` 后 `continue`，不返回、不改状态、不授予任何能力；
- IPC 命令路径仍拒绝 `extend/renew` 与 live `arm/reset`；`op` 比较集合未超出已知 IPC 操作；`control/core.py`、`transport/osc.py` 未被甲修改。
- 交付回归 `test_arm_refused_notice_without_permission_change` 亦复跑通过（见门禁）。

## 5. 乙 `23f92be`：八项逐条证据

### B1 字节/摘要与不变项 —— 未能证伪

- 工作树 v6 文件 SHA256 = `e46e0c2816c6f512fbd4fd5e5c60ff1ef3fc4d0560e0e8b29d3b6a6315caa513`；v7 = `fbebd9cbfd6d11988dd783ea83b7a4e8ef38c1b3ce236e435dcd20617cb2caf7`，两 `.sha256` sidecar `strip()` 后相同；`load_policy("v1".."v7")` 全部通过。
- `git diff --name-only 45af68f 23f92be` 恰 19 个文件，**不含** v1–v6 策略 JSON/sidecar、`world*.json`、提示/技能文件、`configs/`；v7 键集 = v6 键集 ∪ `{zero_tolerance_labels, evidence_changed_ppm}`，取值差异仅 `{version, status}`，`metric` 仍为 `rgb_tolerance_visible_guard_v5`。

### B2 候选门禁 —— 未能证伪

- monkeypatch `session.load_config`、`session.build_provider`、`session.ControllerProcess` 为哨兵：v7 live 在**创建任何资源之前**抛 `ValueError("… requires field calibration and a newly frozen version …")`，哨兵从未被触碰；同法 v6 live 能通过该门禁抵达哨兵（未被候选门禁拦下）。
- `AppConfig()` 默认 `legacy` + `dry-run`；`configs/navigation-live.toml` 仍 `mode="dry-run"`、`navigation_design="stable-inference-v6"`。默认未被改成 v7。

### B3 规则与实现逐字一致 —— 未能证伪

- `status=REQUIRES_FIELD_CALIBRATION`；`zero_tolerance_labels == ["home_raccoon"]`（恰一项）；`evidence_changed_ppm` 为 `int 1000`；`max_changed_ppm=50000`、`pixel_tolerance=2`、`margin=32`。
- 非目标证据框预算（整数比较，不向上取整）：`visible=174592 px` → 允许 174 px，174 通过、175 拒绝；等价于 `allowed = visible*1000//1_000_000` 且 `allowed*1e6 <= visible*1000 < (allowed+1)*1e6`。
- 全局 5%：`retained=2848256`，变化 142412 通过、142413 拒绝。
- 无隐式默认：改 `evidence_changed_ppm ∈ {1001, 999, True, 1000.0}`、扩展/清空/字符串化零容差清单、`version` 改 v6、**删除** `evidence_changed_ppm` 键 —— 全部 `ValueError`/`KeyError`（`verdict` 与 loader 两处都拒）。

### B4 逐框几何 —— 未能证伪

- `region_shapes`：raw = bbox 按 `floor/ceil` 落到像素；protected = raw 每边外扩 32 px 并裁剪到帧。实测 bbox `(0.70,0,0.72,0.05)` → raw 宽 44、protected `(1480,0,1588,104)`，`visible_px == 5824 == 108*104 - 52*104`（减去与 `[1536,0,2048,512)` 的 52 px 交叠）。
- 遮罩内 3 px 变化 → `changed_pixels == 0`、`runs == []`、`stable=True`；把落在遮罩内的区间塞进 `changed_runs` → `validate_runs` 抛错（“masked pixels in v7 witness”）。
- 两实体保护框重叠处的 1 px 变化 → 两个 region 各 `changed=1`，`protected_changed=1`（并集，不双计）。
- 目标零容差：`home_raccoon` 框内 1 px → `zero_tolerance=True`、`budget_ppm=0`、`changed=1`、`stable=False`；同位置改标签为 `home_blue_ad` → `budget_ppm=1000`、`stable=True`。
- 原框完全落在遮罩内（如 `(0.72,0.10,0.73,0.20)`）→ `compare_v7` 抛 `ValueError`（“完全不可见”）而非静默清零。

### B5 计数可重算、校验器不只信自报 —— 未能证伪

- 独立逐像素枚举（`masked_rgb` + 容差 2）得到 3 个变化像素，与报告的 `changed_pixels`、`changed_runs`、`count_rect` 逐框计数、`count_union` 的 `protected_changed`、`retained_pixels==2848256` 完全一致。
- `validate_runs` 拒绝：乱序、重叠、**相邻**（`start <= previous_end`）、越界（`end > w*h`、负数）、空区间、`bool` 位置、非列表、长度不等于 2、遮罩内区间。
- 报告级伪造全拒：`changed_pixels+1`、`region.changed+1`、`visible_px=1`、`budget_ppm=5000`、`zero_tolerance=True`、`protected_changed+1`、删 `changed_runs`、删 `budget_ppm`、改 `label=home_vase`、改 `entity_rectangle`、删 `track_id`、反转 region 顺序。
- 逐框四字段（`visible_px`/`changed`/`zero_tolerance`/`budget_ppm`）由 `verdict` 用几何与 runs 重算后**精确相等**比较，不存在“只信自报 changed”的路径。

### B6 schema10 隔离 —— 未能证伪

- v6/schema9 报告的 `stability_proofs`/`stability_prechecks`/`warmup_checks` 夹带 `changed_runs` 或 `evidence_regions` → `ValueError("v7 evidence requires schema10")`；v6 报告 `schema_version=10` → 拒。
- v7 报告 `schema_version=9`、`schema_version=7`、或把 `navigation_design` 改成 `stable-inference-v6` → 全部拒；v7 删 `max_inference_attempts` → 拒（schema10 不接受历史预算缺省）。
- 用 `tempfile` + monkeypatch `files()` 重算 sidecar 后篡改 v7 声明（`status=FROZEN`、`ppm=2000`、清单加 `home_vase`），以及把 v6 字节冒充 v7 → 全部 `ValueError`（摘要或声明不符）。

### B7 隐私面 —— 未能证伪

- 实测 v7 proof 的 `changed_runs` 为全 `int` 的半开区间（如 `[[2489400, 2489401]]`），报告无 `frame`/`image`/`rgb`/`pixels`/`frame_data`/`data`/`base64`/`screenshot` 键；本轮合成报告 < 2 MB、最长字符串 < 512。
- 提案文档自述“不得宣传为匿名或无隐私风险”“本地敏感几何证据”“不具密码学认证”，未出现相反口径。

### B8 断言边界 —— 未能证伪

- 零容差清单恰 `["home_raccoon"]`；世界 v1/v2 的 G1/G3/G4/G5 标签（`home_blue_ad`/`home_mirror_frame`/`home_vase`/`home_floor_diamond`）均不等于 `home_raccoon`，G2 等于 `home_raccoon`。
- 提案明确“不能声称 G1/G3/G4/G5 目标也保留零容差”“当前候选不声明这些目标的零容差覆盖”。

## 6. 非阻断观察（N1–N10）

- **N1（甲·报告字段）** `observation` 事件的 `attempt`/`attempt_cycle` 只校验“已在 `stability_attempt` 中声明”，未与绑定它的 proof 的 attempt 交叉核对；探针把 `observation.attempt` 从 2 改成 1 仍通过校验。动作证据链不受影响（proof→source→precheck 已强制同 attempt），属元数据严格性缺口。
- **N2（甲·报告字段）** “新格式不能删预算冒充历史”只能拦住“只删预算”或“保留 attempt 元数据”的伪造；把预算与全部 attempt 元数据一并删除后，报告与历史 schema9 不可区分（schema9 无版本标记）。全量重写不可检测，与乙提案“全套重写不具密码学认证”同源，属已知边界。
- **N3（甲·控制家族码）** 甲把 V6 预检新鲜度失败从 `SCENE_UNSTABLE` 改为 `STALE_OBSERVATION`。该码在 `contracts.REASONS` 已登记，但 `telemetry.codes.family_of("STALE_OBSERVATION") == "control"`，而 `SCENE_UNSTABLE`/`WARMUP_UNSTABLE` 归 `live_navigation`；且它**不在** `live_navigation/errors.py` 的 15 码 `LIVE_NAVIGATION_FAILURES` 闭集内（v2 预热路径早已抛同码，属既有缺口）。后果：按 `live_navigation` 家族统计失败时会漏记这一类，但因码本身已登记，报告不会出现 “unknown code” 警告。**报告/批次汇总口径须按 control 家族读这一结局。**
- **N4（甲·纵深防御）** `V6.reference_capture` 的新增新鲜度检查位于 `capture()` 之后（`capture` 已有同源检查），属纵深防御；不改变可观测行为，但报告无法证明该分支曾真正触发。
- **N5（甲·现场归因）** pilot5 的 F-F（3 次 `navigation_feedback` 全 `valid_target=false`、`TARGET_UNOBSERVED`）与 F-A（`forward_clearance` 仅 1/5）**不由甲引入**：甲 diff 未触及 `skills/`、`verify()`、提示词。第 87 节（工作树未提交）已把归因从“剩余绑定/时序门禁”更正为场景置信度/危险/不确定性门禁；本轮用合成帧跑同一闭环（4 动作）得 24 个谓词全 true、`valid_target=True`，**F-F 离线不可复现**，故只能由现场数据解释，本轮不证实也不证伪。
- **N6（乙·配置刚性）** `zero_tolerance_labels`/`evidence_changed_ppm` 在 loader 的 v7 分支被**严格等于**校验，任何扩展（改清单、改 ppm、改 status）都必须发新版本。这是有意的刚性且提案已声明，但意味着不存在“用户授权后只改配置”的通路。
- **N7（乙·声明面）** `parameters.action_freshness_ms` 的 5000 ms 上限从“仅 v6”扩到 `{v6, v7}`；v7 实际仍由 `freshness_ms=4000` 与配置 4000 取小，行为不变，属声明面扩展而非阈值放宽。
- **N8（乙·报告体积）** `changed_runs` 是位置证据，体积随变化**段数**增长；本轮合成报告 < 2 MB，但未测最坏情况（全局 5% 上限内若变化像素交替分布，可产生约 14.2 万段）的报告体积与校验耗时。
- **N9（乙·校验分工）** `verify_regions` 只核对 region 的身份/几何投影（raw/protected 与实体一致），逐框 `count`/`budget` 完全依赖 `verdict` 重算；组合仍覆盖，但唯一调用点在 `validate_proof`，未来若新增调用方只调 `verify_regions` 会漏掉计数。
- **N10（乙·依据样本）** 1000 ppm 的依据是 5 对静止样本最大 54 px 的约 21.7 倍（地板外扩框约 1 172 672 px → 预算约 1172 px），提案自述未证明最优/普适。该依据属现场标定范畴，离线复核只能确认“规则与实现一致”，不能证明阈值合适。

## 7. 现场使用建议（回答“是否应立即停用”）

- **甲：无须因本次复核停用。** 六项声明全部未能证伪，未发现会放大预算、跨 attempt 复用证据、把异常当抖动重试或绕过 ARM 判定的缺陷。pilot5 中实测到的行为（周期 4 的 attempt 2 被拒、attempt 3 恢复）与本轮探针一致。
- **但固定 20 次任务不应在第 87 节修复经独立复核与现场复验前恢复**：pilot5 的动作验证恒为无效（F-F）属产品级缺陷，虽不由甲引入，却使现场无法产生有效位移与到达证据。这是对“现场可用性”的建议，不是对甲的退回。
- **乙：维持候选。** 不得 live、不得据本复核冻结、不得把本记录当作实机验收或 16/20 依据。下一步按既有流程：现场标定/复验 → 用户选版与冻结 → 延迟复测 → 固定 20 次任务。
- 若后续发现甲存在阻断项，停用条件应为：出现任一 attempt 超出 3 次、动作绑定到跨 attempt/预检证据、或 ARM 拒绝提示改变授权状态 —— 本轮三项均未出现。

## 8. 四项门禁原始输出

工作树（HEAD `f9e9c7c` + 未提交第 87 节，见第 2 节），项目虚拟环境 `.venv/Scripts/python.exe`：

```
$ PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -q
........................................................................ [ 97%]
.........................                                                [100%]
1249 passed in 205.41s (0:03:25)
```

（日志 `.runtime/verify/t08_v7_review_pytest.log`）

```
$ .venv/Scripts/python.exe -m ruff check src tests
All checks passed!
```

（日志 `.runtime/verify/t08_v7_review_ruff_check.log`）

```
$ .venv/Scripts/python.exe -m ruff format --check src tests
153 files already formatted
```

（日志 `.runtime/verify/t08_v7_review_ruff_format.log`）

```
$ .venv/Scripts/python.exe -m pip check
No broken requirements found.
```

（日志 `.runtime/verify/t08_v7_review_pip_check.log`）

## 9. 工件清单与复现

```
.runtime/verify/t08_v7_review_jia_probe.py    385295f60d37531679a5403a25e13147db138c148601b5020b291fdf285d9c69
.runtime/verify/t08_v7_review_yi_probe.py     8e56e6efcb82bd280c81716c61b91bddf2c2c4630ea9963c7bd073843aac8d1b
.runtime/verify/t08_v7_review_sha256.txt      （上述 9 个探针/日志/工件的 sha256sum 原始输出）
```

复现步骤：在仓库根目录执行第 3 节两条命令，应分别输出“甲探针：12 项，0 失败”“乙探针：9 项，0 失败”；再执行第 8 节四条门禁命令。探针不读取 `.runtime/live/` 的现场报告、不访问网络、不写产品文件（只写 `.runtime/verify/` 工件）。

## 10. 边界（重申）

- 本轮**不是实机验收**：未启动 live、未 ARM、未调用付费模型、未发送真实输入、未读取密钥；帧全部为合成像素。
- 未评估：v6 稳定性判据在现场的通过率、v7 的 1000 ppm 是否合适、F-F/F-A 的根因、第 87 节修复的语义等价性（后者另有交接）。
- 甲、乙的结论各自只覆盖本记录第 4、5 节的声明主体；不得外推到冻结、验收、成功率或“可自主游玩”。
