# T08 v10 有界原地扫描候选（`336fb50`＋`d7b8070`）与冻结前预算装配修正（`5ae2a68`）——ADR-009 定向复核记录

- 复核对象：`336fb50`（v10 主体，22 文件 `+1135/−42`）、`d7b8070`（交付核对，仅 `docs/TASKS.md +11/−?`）、`5ae2a68`（第116节预算装配修正，`control/service.py` +1 行 + 测试/文档）。复核时 `HEAD=5ae2a680f642651a0a04614070435a40b16076ea`，`git status --porcelain` 为空。
- 策略摘要：v10 `437849cfee57cc157cc01cb76a0bc56eb8ab5eade7510a31bff47d4a5c14bff3`；v9 仍 `c9fb45ee3295766e62f3ceda2c4a744aab9dd45e580d36385168a59f97e700a2`；world v3 仍 `1da677140aad5a76920a7ad5cd88a5ffc1899300062cb852392b9483067f19f7`。
- 复核角色：独立复核者（ADR-009），**只审不改产品代码**。本轮未修改任何策略/阈值/配置/世界/提示/`control/`/`configs/`；只新增本记录、`docs/TASKS.md` 第117节与 `AGENTS.md` 顶部状态胶囊。
- 边界：未启动 live、未 ARM、未调用付费模型、未发送真实输入、未读取密钥。自建探针为纯内存 + 真实 `ControllerProcess`（dry-run，全程 `DISARMED`、零发送）。**本记录不是冻结授权，也不是实机验收。**

## 1. 复核结论

**声明主体未能证伪，无阻断项。** 第115节记录（`docs/validation/T08_path_scan_v10.md` 第69–73行）自陈的“预算装配阻断”**已由 `5ae2a68` 关闭**，本轮实测确认。

- 自建探针 **61 项断言 0 失败**（A(a)–(f)、B(g)(h)(i)、C(j) 逐项覆盖，见第3节）。
- 四项门禁我在 `5ae2a68` 工作树复跑了**两次独立进程**：`1503 passed in 610.02s`（`.runtime/verify/review_v10_gates.log`）与 `1503 passed in 603.36s`（`.runtime/verify/t08v10_gates_pytest.log`，同日同树另一进程），两次 `ruff check`=`All checks passed!`、`ruff format --check`=`166 files already formatted`、`pip check`=`No broken requirements found.`，全部 exit 0。交付方自报 `1503 passed in 566.82s` 与之同量级；`1503 = 第115.3节 1501 + 2` 两个新增参数化预算用例，数字可追溯。
- 真实 `ControllerProcess`（本轮实测，非内存 core）：**v10=180000 ms、v9=180000、v5=30000、legacy=30000**，`base_ms=30000`、`state=DISARMED`、`sent_count=0`、`recorded_messages=[]`、`extend`/`renew` 抛 `ValueError`。
- 冻结前仍需用户授权的事项见第5节（状态闭集、sidecar 重生成、现场 20 次）——这些是流程前置，**不是本轮发现的缺陷**。

## 2. 方法与可复现证据

- 探针：`.runtime/verify/t08v10_review_probe.py`（自建，61 用例；不在 `tests/` 下，默认 `testpaths` 不收集）。运行：`.venv/Scripts/python.exe -m pytest .runtime/verify/t08v10_review_probe.py -p no:cacheprovider -q` → `61 passed`。
- 探针复用产品测试夹具（`tests/integration/test_navigation_v6.py:39 assembly(profile="field")`、`tests/integration/test_acceptance_v8_runtime.py:21 assemble_v8`、`tests/integration/test_stability_v9_runtime.py:19 assemble_v9`、`tests/unit/test_world_v3_confidence.py:25 observed`），但**断言全部自写**，不复用交付方 `tests/integration/test_path_scan_v10_runtime.py` 的断言。
- 调试中出现的 6 次探针失败全部是探针自身缺陷（`audit()` 返回键是 `path_safety` 而非 `path`；`compare_v9` 是函数内局部导入；`MotionEnvelope` 是 dataclass、`MOTION_PROFILES` 闭集；f3 假设被产品更强的校验推翻），逐条纠正后全绿；**未因探针不改而放过任何产品行为**。
- 逐字节不变性：`.runtime/verify/t08v10_invariance.log`（`git diff --name-status 336fb50^..HEAD`、10 个 sidecar 的 `sha256sum`、`configs/`/世界/`skills/`/`acceptance_v8.py` 零改动核对）。

## 3. 逐项证据

### 3.1 A(a) 不变项与 v9 语义

- 策略文件层：`git diff --name-only 336fb50^..HEAD -- 'stability*.json' 'stability*.sha256'` 只回 `stability_v10.json`/`stability_v10.sha256`；v1–v9 共 18 个 json/sidecar 逐字节未改，10 个 sidecar 的 `sha256sum` 与文件名逐一匹配（v1 `e9350468…`、v2 `5487965f…`、v3 `333739e3…`、v4 `e4dcb100…`、v5 `2e21c734…`、v6 `e46e0c28…`、v7 `fbebd9cb…`、v8 `11b98adf…`、v9 `c9fb45ee…`、v10 `437849cf…`）。
- `src/vrc_agent/live_navigation/acceptance_v8.py`（七谓词/`clearance`/`arrived`/`arrival_permitted`）、`src/vrc_agent/skills/`、`src/vrc_agent/live_navigation/skill.py`、`configs/`、世界文件与 sidecar：`git diff` **零改动**。
- v10 声明 = v9 主体 + 精确 3 键：探针断言 `{k for k in v10 if v10[k] != v9.get(k)} == {"version","status","path_scan"}`，`path_scan` 与模块常量 `PATH_SCAN` 逐键相等。
- 加载器防篡改：6 种私改（`max_changed_ppm+1`、`pixel_tolerance=3`、`max_scans_per_task=5`、`yaw_step=2.0`、`task_timeout_s=170`、`status=FROZEN`）经 `monkeypatch.context()` 换 `files` 后 `load_policy("v10")` 全部 `ValueError`（`stability.py:27` 的 `expected_v10` 严格相等）。
- `stability_v9.py` 的唯一改动是 `allowed_pixels` 的版本门从 `!= v9` 改为 `not in {v9, v10}`（3 行）；该文件再无其它 `version` 分支（`grep -n "v9-candidate\|v10-candidate"` 只命中第 32–33 行），且 `stability_v9.json`/sidecar 未变 → **对 v9 的像素判定/容差/遮罩语义零影响**；v10 与 v9 共用同一 ppm 比较器（探针用哨兵法证明 `stability.compare(None,None,v9)` 与 `(…,v10)` 都落到 `stability_v9.compare_v9`）。结论：不构成 v9 语义变化。

### 3.2 A(b) 触发纯性

- `eligible`（`path_scan.py:53-63`）= `proposal.kind=="motion" and args.forward>0 and args.strafe==args.yaw==0 and all(audit["target_recognition"].values()) and path_safety["motion_envelope"] and path_safety["max_actions"] and not path_safety["forward_clearance"]`。四类破坏各自实测：给出 `forward_clearance`／目标置信度 0.1／包络外前进 `0.9`／计数 41，先由 `agent.audit` 独立证目标键为假，再证不触发。
- “唯一失败项”定位准确：探针断言 `audit["path_safety"][axis+"_requires"] == ["forward_clearance","motion_envelope","max_actions"]`，可触发时 `[k for k in required if not path_safety[k]] == ["forward_clearance"]`，同时 `recovery_clearance is False` 但**不属**前进许可项。hazards/uncertainties 按 v8 既定语义 `motion_effect: record_only` 只记录不否决（探针 b5 正向断言：有 hazards 仍能触发扫描，但 `scope.automatic_success_allowed` 为 False）。
- 七谓词无空字典恒真路径：`acceptance_v8.py:164-183` 用 `zip(RECOGNITION, (…), strict=True)` 恒返回 7 键，无目标时逐键 False。
- 重复观测不能凑数：`denied_streak` 仅对**连续新鲜** `scene.observation_ids[0]` 递增，重复 id 立即归零。实测序列 `(obs1,+1)`→`(obs1,0)`→`(obs1,0)`→`(obs2,+1)`→`(obs2,0)`→`(obs3,+1)` 只有 1，不触发；连续 obs1/obs2/obs3 → 3，触发。中间某次取得 clearance → 计数归零（探针 b4）。

### 3.3 A(c) 动作安全

- 4 次扫描的 `proposal.args.model_dump()` 精确为 `{"forward":0.0,"strafe":0.0,"yaw":±1.0,"duration_ms":200}`，方向 `[1,-1,1,-1]`，与 `MotionEnvelope.accepts()` 的 expected 完全一致（无混轴、无夹带 forward/strafe）。
- 重走原审计：扫描 yaw 在派发前过 `self.audit(scene, dispatched, count)` 并要求 `proposal_permitted`，4 次扫描的 `yaw_audit.path_safety.motion_envelope` 全 True；把 skill 的 `MotionEnvelope.yaw` 私改为 0.5 后，第 3 次合格拒绝触发 `ValueError("scan yaw rejected by unchanged motion audit")`，且 `scan_count==0`、`pending_scan is None` → 该分支非死代码。
- 控制面：全流程结束后 `assert_zero`（`core.active is None`、`AXES` 全 0）；未新增结局码、未新增 IPC 能力（`extend`/`renew`/live ARM/RESET 仍被拒）。

### 3.4 A(d) 扫描后证据与报告校验

- `pending_scan` 非空时再决策即 `ValueError("scan requires a fresh verified observation before another decision")`。
- 新鲜度 16 项谓词实测拒绝三类跨动作复用：`captured_at_ns == finished_at_ns`、`captured_at_ns < action issued`、复用扫描前 `observation_id` 但换新时间戳——三种都 `fresh_observation=False` 且 `halt_reason="PATH_UNSAFE"`。
- 缓存实体（`evidence_observation_id` 绑首帧、`last_seen_ns` 未更新）→ `PATH_UNSAFE`，且 4 次 `path_scan_observed` 的 `clearance_acquired` 全 False（不伪造成功）。
- 真新鲜 clearance → `outcome="SUCCESS"`、`success=True`、`field_acceptance="REQUIRES_HUMAN_REVIEW"`、动作 `["wait","wait","motion","motion","wait"]`（5 决策/5 动作/6 次推理），且完整 schema13 报告通过 `validate_leases` + `validate_stability_report`。
- “重新采集/预热/推理”是运行时结构保证：`runtime.py:151-215` 的 `capture(after_ns)` 要求 `captured_at_ns > after_ns` 严格更新且 `observation_id not in self.seen`（竞态窗口 `min(1500, action_freshness_ms)`、≤50 次×30ms），随后 `observe()` 走 `perception.interpret`（v6+ 预热对）。实测无 clearance 场景 15 决策 = 15 次推理 = 15 个 `inference_source` = 15 个 `stability_prechecks` = 90 条观测，证明每次扫描后确有新采集+新推理，且该新鲜观测同时充当下一决策的 before 观测。

### 3.5 A(e) 耗尽语义与时限先后

- 无 clearance 场景实测：**15 决策 / 14 动作 / 4 扫描**，扫描决策号 `[3,6,9,12]`、`scans_used [1,2,3,4]`、第 4 次 `exhausted=true`，末次决策只 halt 不派发（`len(decisions)==len(actions)+1`），`outcome="PATH_UNSAFE"`。与提案第49行“扫描决策号3/6/9/12、第15次拒绝终止、最后一次不派发动作”逐项一致。
- 与 `path_wait_limit=3` 的交互无无限循环：扫描后 `path_waits=denied_streak=0`；耗尽后不再扫描，回到原路径等待逻辑，连续 3 次拒绝即 `PATH_UNSAFE`。代码上界为 `4×(3+1)+3=19` 决策（未实测），仍 < `max_actions=40`；同时每次迭代 `check_authority` 先查 epoch/租约/`ends_at`，故 30s/180s 可先行中断。
- 急停/租约先于扫描：第 2 次推理前置急停 → 零扫描、`calls≤3`、outcome 非 `SUCCESS`/`PATH_UNSAFE`；同样把租约推到 30000ms → 零扫描。即扫描不是“必须执行完的额度”。

### 3.6 A(f) 报告重算与 schema 隔离

- schema 配对：`acceptance_report_v8.py` 的 `{"v8":11,"v9":12,"v10":13}` 严格校验；非 v10 设计携带 `path_scan_triggered`/`path_scan_observed`/`audit.path_scan`/`evidence.path_scan` → `ValueError("path scan evidence requires schema13")`；未知设计/错误 schema → `unknown navigation design`；`stability_policy` 摘要混用拒绝。
- v10 重放路径：逐 `acceptance_decision` 调 `skill.dispatch(scene, requested, index)`，`same(event["audit"], audit)` + `same(dispatched, expected)`；`halted scan decision cannot send an action` 阻断“已停仍发动作”；`navigation_feedback` 逐键 `same`，并对 `path_scan_observed` 追加 `same(event["skill"], "scan")` 与该观测 `inference_source.captured_at_ns > result.finished_at_ns` 检查（`scan reused pre-motion inference source`）。
- 24 类变异全部拒绝：篡改 `audit`/`path_safety`/动作参数/动作顺序/删 `path_scan_observed`/伪造 `fresh_observation`/时间戳倒置/`derived_from` 改写/schema 降级/非 v10 设计夹带扫描证据等；伪造 `outcome="SUCCESS"` 也被 `acceptance_report_v8.py:283`（`v8 success without arrival evidence`）拒绝。
- 报告层亦能查跨动作复用：`stability_report.py:296-375` 要求每个 proof 实体 `evidence_observation_id == proof["reference_observation_id"]` 且 `last_seen_ns == times[0]`，每条 observation 的 `derived_from` 必须等于其 accepted proof 的 `reference_observation_id` —— 与运行时 16 项谓词形成两层独立校验。

### 3.7 B(g)(h)(i) 预算装配修正

- (g) 旁路：`control/service.py:166-186` 子进程第一步 `AppConfig.model_validate_json(config_json)`；随后单一 `if config.navigation_design in {"…-v6"…"-v10"}: task_timeout_s = load_policy(…)[0]["task_timeout_s"]`，否则 `30.0`；`Controller(..., task_timeout_s=task_timeout_s)` 是唯一注入点。全仓多行 `{…"v9"…}` 字面量扫描 29 处，唯一不含 v10 的是 `stability.py:27` 的 v10 分支自身（设计如此）；单行扫描 0 命中。枚举外设计由 `config.py` 的 `Literal` 在构造期拒绝（`AppConfig(navigation_design="stable-inference-v11")` 直接报 `navigation_design`）。
- (h) 断言未放水：`5ae2a68` 把 `test_candidate_budget_mapping_remains_explicit_freeze_blocker`（断言 30000）改写为 `test_candidate_budget_mapping_is_wired_to_180s_and_worker_stays_disarmed`，新断言为 `180000` + `DISARMED` + `sent_count==0` + `recorded_messages==[]` + `extend`/`renew` 抛错——**更强且仍真实起 `ControllerProcess`**。参数化 `test_existing_process_budgets_and_30s_fallback_unchanged` 现覆盖 `v6–v10=180000`、`v5/legacy=30000`，每个组合都启动真实子进程（我在探针 h1 中独立重跑同一矩阵，结果一致）。
- (i) 与 `leases.py` 一致：`validate_leases` 要求 `task_ms == policy["task_timeout_s"]*1000` 且 `base+step*limit <= task_ms`；诚实 schema13 报告通过，5 类预算变异两者都拒（`task_timeout_ms=30000`→`lease configuration exceeds task timeout`；`base_ms=31000`/`extension_max=6`/`extension_ms=40000`→`invalid lease integer`；`total_ms=60000`→`elapsed control exceeds granted lease`）。

### 3.8 C(j) 独立复跑

- 探针 61 项 0 失败（第2节命令）。
- 四项门禁两次独立进程复跑：`1503 passed in 610.02s` 与 `1503 passed in 603.36s`，两次 ruff/format(166)/pip 全绿、exit 0（`.runtime/verify/review_v10_gates.log`、`.runtime/verify/t08v10_gates_pytest.log`）。
- 真实进程：`v10 180000 30000 DISARMED`，`sent_count=0`、`recorded_messages=[]`。

## 4. 非阻断观察

- **N1（声明与实现不同源，非阻断）**：`PATH_SCAN` 的三个键 `alternate_direction`、`reset_path_waits_per_scan`、`post_scan_requires_new_observation` 在 `src/vrc_agent/live_navigation/path_scan.py` 中**从未被读取**（方向交替硬编码为 `scan_count % 2`，第 107 行无条件清零，`pending_scan` 守卫无条件生效）。因 `stability.py:27` 对整块 `path_scan` 做严格相等校验，任何配置都无法把它们改成 False，所以当前“声明=行为”成立；但若未来把校验放宽为子集校验，这三键会静默失效。建议下一版冻结时要么接线、要么删键。
- **N2（`eligible` 中两项近似恒真，非阻断）**：`path_safety["motion_envelope"]` 与 `["max_actions"]` 判的是**已被 `decide()` 归一化到包络全量级**的提案，因此实际判别力集中在七谓词、`¬forward_clearance` 与“连续 3 次新鲜拒绝”上；包络判据只在 skill 的 `MotionEnvelope` 与提案不一致时才为假（探针 c2 用此构造出拒绝路径）。语义不变，仅说明该条件的冗余性。
- **N3（现场时限顺序，非阻断）**：假时钟下 15 决策在 `elapsed_ms=13390` 结束（< 30s 基础租约）；现场 3–4s 推理时 15 决策约 45–60s，**会先撞 30s 基础租约**，只能靠本机 Ctrl+F9 人工续租（最多 5 次、总 ≤180s）。提案第77行已自陈，属既有 v9 现场约束，不是 v10 新增缺陷；但固定 20 次前应把“扫描可能需要 1–2 次人工续租”写入现场清单。
- **N4（校验器分层，非阻断）**：单独调 `validate_acceptance` 时**不要求**每条 observation 都有 `inference_source`（删掉最后一个 source 仍 ACCEPTED）；对外唯一入口 `validate_stability_report` 会以 `ValueError("cross-attempt action evidence")` 拒绝。且 `acceptance_report_v8.py` 查找 source 用的是 `next(...)`，该 `StopIteration` 不被外层 `except (KeyError, TypeError, IndexError, AttributeError)` 捕获——本轮未能构造出到达该行的报告（前置校验先拒），但建议下一轮改成显式 `ValueError`。
- **N5（防御性异常路径，非阻断）**：`dispatch()` 首行 `pending_scan` 守卫抛 `ValueError`，在运行时会被 `except Exception` 折成 `outcome="RUNTIME_ERROR"` 而非 `PATH_UNSAFE`。本轮未构造到实际触发（`verify()` 总会先于下一次 `dispatch()` 执行），但结局码语义上不够精确。
- **N6（文档过期，需同步）**：`AGENTS.md` 第35行仍写“冻结前装配阻断：control/service.py 预算枚举未列 v10，实 dry-run 控制进程仍 30s 兜底……需另案授权接 180s 与独立复核”，与 `5ae2a68` 及 `docs/TASKS.md` 第116节矛盾——本轮已按复核结论更新该胶囊。
- **N7（历史表述分工，非缺陷）**：`docs/validation/T08_path_scan_v10.md:69-73` 仍写“service.py 未列 v10、冻结前必须另案授权修正”，是该提案提交时的如实自陈（其第100行亦自陈“第5节阻断仍然存在”）；修复在 `5ae2a68` 的独立文档 `T08_v10_budget_fix.md`。两文分工，不需回改前者。

## 5. 未覆盖与后续（冻结前置）

- **v10 状态闭集**：`stability.py:27` 的 `expected_v10` 严格要求 `status="REQUIRES_FIELD_CALIBRATION"`，**仅改 `status` 与重生成 sidecar 不足以启用 v10**（loader 会以 `invalid v10 path scan candidate declaration` 拒绝 FROZEN）。冻结需另案放开状态闭集（对齐 v6/v8/v9 的 `{REQUIRES_FIELD_CALIBRATION, FROZEN}` 做法）并同步摘要。
- 现场：切 `navigation_design` 到 v10 → 人在场复验（含 N3 的续租清单）→ 固定 20 次任务与人工逐条到达确认；T08 仍 IN_PROGRESS。
- 未覆盖：实机 live（未启动）、真实模型遵从性、20 次任务统计、G2-2/G4-1 现场能否借扫描取得 clearance。

## 6. 边界声明

本记录是 ADR-009 定向复核结论，不构成冻结授权、不是实机验收、不能替代现场 20 次任务与人工确认。复核者未修改任何产品代码、策略、控制、配置、世界或提示；门禁全绿不作为结论依据，结论逐项来自第3节的探针断言与工件原文。
