# T08 v8 运动作用域修订（`c1af37e`）——ADR-009 定向复核记录

- 复核对象：提交 `c1af37e`（父提交 `508d518`），即 TASKS 第 94 节的 v8 未冻结候选运动作用域修订。
- 策略摘要：`98bf0aa7986f8349c678f6196e307239ad02df5945004c23266e9b740082e2ef`（`stability_v8.json` 字节 SHA-256 与 `stability_v8.sha256` sidecar 一致）。
- 复核角色：独立复核者（ADR-009），**只审不改**。本轮未修改任何产品代码、策略、阈值、配置、世界或提示；仅新增本记录、`docs/TASKS.md` 第 95 节与 `AGENTS.md` 顶部一条状态胶囊。
- 边界：未启动 live、未 ARM、未调用付费模型、未发送真实输入、未读取密钥；所有结论来自离线代码审查与自建探针合成数据。**本记录不是实机验收**，也不能替代现场复验或用户冻结。
- 历史口径：第 90 节初版摘要 `908af1df0eb214af1e26312d1eb308a1094feb96f37a478136894364e7bfc0bc` 属于父提交 `508d518` 的历史初版，本记录不据其结论评判本次修订；v8 仍为 `REQUIRES_FIELD_CALIBRATION` 候选。

## 1. 复核结论

**声明主体未能证伪、无阻断项。**

第 94 节对 `c1af37e` 的可验证声明（运动按目标识别＋对应路径证据/包络/预算许可、整场危险信号降为记录、到达语义分离且不自动成功、恢复许可只看 `recovery_clearance`、不变项未动）在逐项复核中均未找到反例；四项门禁与只读计划入口在原提交上复跑通过。

同时必须明确：

- v8 仍是**未冻结候选**，状态 `REQUIRES_FIELD_CALIBRATION`，在创建任何采集/推理/控制资源之前拒绝 live；本结论不构成冻结授权，也不构成实机验收。
- 本轮复核**不改变第 93 节的到达口径**：20 次任务仍以报告 `arrival_geometry_met` ＋人工逐条确认验收，不要求也不宣称自动 SUCCESS；人工确认率与机器自动 SUCCESS 率必须分开汇总。
- 新风险仍然成立：模型误报 clearance 时，整场 `has_hazards`/`has_uncertainties` 不再否决运动；**允许运动 ≠ 物理路径安全**（见第 5 节与 N4）。

## 2. 方法与可复现证据

- 探针：`.runtime/verify/t08_v8_review_probe.py`，SHA256 `5f368ad4c275e663a7772a844b005912f893c9aa631bc5bcb3f6406e233154ce`。
- 运行命令：

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -u .runtime/verify/t08_v8_review_probe.py \
  | tee .runtime/verify/t08_v8_review_probe.log
```

- 结果：**18 项，0 失败**（分组 P1 摘要/版本纪律 4 项、P2 运动作用域 6 项、P3 到达语义 5 项、P4 不变项 2 项、P5 新风险专项 1 项）。
- 探针不复用 `tests/` 夹具，只调用产品代码：内存 `ControllerProcess` 子类（不 spawn 子进程）、真实 `Controller` 控制内核与 `RecordingTransport`、合成 2160×1440 BGRA 帧、内存 HTTP 替身充当供应商、真实 `OpenAICompatibleProvider`/`Perception`/`StableNavigationV8` 与报告校验器。探针通过 `importlib` 复用本人 v7 复核探针（`.runtime/verify/t08_v7_review_jia_probe.py`）的内存控制层与帧源。
- 为拿"真实报告"作篡改用例，探针实际跑通两条闭环：清洁到达闭环（2 个动作、自动 SUCCESS）与有信号到达闭环（2 个等待动作、机器 outcome `MANUAL_STOP`、无自动 SUCCESS）。为避免真实配置下多次 `wait` 撞 180s/40 步，探针在结果返回后主动 `MANUAL_STOP`，并**不覆盖 `skill.limits`**，报告 `run_parameters.max_actions` 仍为策略值 40。
- GraphFlow 记录：`graphflow_context` 本次可用，但只返回符号/文件级锚点（`acceptance_v8.py`、`acceptance_report_v8.py`、`stability_v8.json`、`skill.py`）与历史对话条目，**未返回可引用的代码正文**；因此本记录的所有代码结论均来自 `read`/`grep` 直接读取，未据锚点推断行为。

## 3. 逐项证据

### 3.1 P1 摘要与版本纪律

| 项 | 断言 | 实测 |
| --- | --- | --- |
| P1-1 | 字节摘要 / sidecar / loader / schema 一致；旧初版字节不同 | `sha256(stability_v8.json)==98bf0aa7…e2ef`、sidecar 文本 strip 后相同、`load_policy("v8")[1]==98bf0aa7…e2ef`、`SCHEMAS["v8"]==11`；`508d518:src/vrc_agent/live_navigation/stability_v8.json` 的 git blob SHA-256 `==908af1df…c0bc` 且 != 新摘要 |
| P1-2 | v1–v7 仍可装载；v8 相对 v6 只增采信声明 | `load_policy` v1…v8 全部通过；`set(v8)-set(v6)` 恰为 `DECLARATION` 的 9 个键（`acceptance_semantics`、`arrival_acceptance`、`path_safety`、`scene_confidence_scope`、`scene_signal_arrival_effect`、`scene_signal_motion_effect`、`scene_signals`、`target_recognition`、`target_recognition_rules`），无删键；v6 的 18 个像素/阈值/包络键（`frame_size`、`metric`、`protected_mask_policy`、`excluded_rectangles`、`inference_ms=8000`、`task_timeout_s=180.0`、`max_actions=40`、`max_inference_attempts=3`、`checkpoints`、`freshness_ms=4000`、`pixel_tolerance=2`、`max_changed_ppm=50000`、`protected_margin=32`、`black_threshold=8`、`warmup_pairs=2`、`warmup_max_frames=8`、`warmup_interval_ms=100`、`motion_profiles`）逐项相等，只有 `version`/`status` 不同；`STABLE_NAVIGATION_CLASSES["v8"] is StableNavigationV8` |
| P1-3 | loader 严格比较：旧声明/旧摘要/篡改声明被拒 | 用临时目录替换 `importlib.resources.files` 后：新声明（按原始字节复制）可装载且摘要相符；**旧初版声明**（`veto_if_any` + 旧 `requires`）自洽摘要仍 `ValueError`；新声明配旧 sidecar `ValueError("stability policy digest mismatch")`；`status=FROZEN`、把 `scene_no_hazards`/`scene_no_uncertainties` 加回 `forward_requires`、删 `scene_signal_motion_effect`、把 `scene_signal_arrival_effect` 改回 `veto_if_any` 均 `ValueError("invalid v8 acceptance candidate declaration")`；把真实报告的 `stability_policy.digest` 改成旧值后 `validate_stability_report` 拒绝 |
| P1-4 | v8 在任何资源前拒绝 live；v6 放行到哨兵 | 对 `session.navigation_entry` 打桩 `load_config`、`build_provider`、`ControllerProcess`、`live_capture`、`Perception`（后四者抛 `Sentinel`）：v8 在创建资源前抛 `ValueError("stability policy requires field calibration and a newly frozen version; candidate policies cannot enable live navigation")`；v6 走到 `Sentinel` |

旧摘要报告不可按新语义解释这一点由 P1-3 的"报告 digest 改旧值即拒绝"覆盖：报告必须精确匹配当前 loader 的摘要与策略对象，旧摘要没有等价解释路径。

### 3.2 P2 运动作用域

| 项 | 断言 | 实测 |
| --- | --- | --- |
| P2-1 | 有信号时 forward/yaw 仍许可，信号只记录 | `path_safety.has_hazards=True`、`has_uncertainties=True`，`scene_no_hazards/scene_no_uncertainties=False`；有 `forward_clearance` 时 forward 与 yaw 的 `proposal_permitted=True`；`scope.motion_effect=="record_only"`；策略三轴 `requires` 恰为 forward=[`forward_clearance`,`motion_envelope`,`max_actions`]、strafe=[`recovery_clearance`,…]、yaw=[`motion_envelope`,`max_actions`] |
| P2-2 | 无 clearance 时相对运动不许可、闭环无 motion | 场景无 `forward_clearance` 时 forward/strafe `proposal_permitted=False`、yaw 仍 True；运行时首条 `acceptance_decision` 的 forward 不许可，派发 `wait(100)`，整个闭环 0 个 motion 动作（outcome `MANUAL_STOP`） |
| P2-3 | 目标识别任一谓词失败即不许可运动 | `entity_confidence=0.1`（`scene_confidence_scope=target_entity` 下 `confidence=0.1`）→ `target_recognition.entity_confident=False`；用 `model_construct` 造非法 bbox `(0.5,0.5,0.4,0.4)` → `bbox_legal=False`；同标签两个实体 → `unique_label=False`；三者各自都使 motion `proposal_permitted=False` |
| P2-4 | clearance 几何/置信度/绑定边界精确 | `(0,0,0.65,0.9)` 与 `(0.35,0.7,1,1)` 许可；`x1=0.3501`、`x2=0.6499`、`y1=0.7001`、`y2=0.8999`、`confidence=0.8999` 拒绝，`confidence=0.9` 许可；`last_seen_ns` 与观测 `captured_at_ns` 差 1ns 拒绝；同标签两个 clearance 实体拒绝；`evidence_observation_id` 不在 `scene.observation_ids` 拒绝 |
| P2-5 | decide/recovery/runtime 无整场否决残留 | `AcceptanceSkillV8.decide` 与 `recovery_allowed` 源码/字节码中不出现 `arrival_permitted`；`recovery_allowed` 只按 `clearance(scene,"recovery_clearance")` 判定；`runtime.run` 的提交前分支含 `select_target` 与 `"v8"` 分支标志；`acceptance_v8.py` 中 `arrival_permitted` 的唯一调用点是 `audit()` 的 `automatic_success_allowed`（仅报告字段），不参与运动或恢复许可 |
| P2-6 | 有信号运行时确实执行运动且审计可重算 | 有信号 + 合成 clearance 的闭环：首个动作为 motion（`forward 0.4/500`，field 包络），首条 `acceptance_decision.audit.proposal_permitted=True`、`has_hazards=True`、`scope.motion_effect=="record_only"`，报告通过 `validate_stability_report`（`acceptance_decision` 的 audit 被校验器按观测/请求重算并逐字段比对） |

结论：运动作用域从"整场信号否决"改为"目标七谓词＋按轴证据/包络/预算"，代码、策略与报告校验器三处一致；没有发现"有信号即整场不许动"或"无 clearance 仍前进"的残留路径。恢复许可放宽到只看 `recovery_clearance` 属声明行为，其残余风险见 N4。

### 3.3 P3 到达语义

| 项 | 断言 | 实测 |
| --- | --- | --- |
| P3-1 | 到达标记与自动成功许可按信号分离 | 干净场景到达：`arrival_geometry_met=True`、`arrival_review_required=False`、`scope.automatic_success_allowed=True`；有信号到达：`geometry=True`、`review_required=True`、`auto_success=False`、`arrival_effect=="record_review_required"`、`field_acceptance=="arrival_geometry_met_and_manual_confirmation"`；未到达：`geometry=False`、`review=False` |
| P3-2 | 有信号到达运行不自动成功、review 可见 | 闭环 2 个等待动作、`outcome=="MANUAL_STOP"`、两条 result 均非 SUCCESS；`navigation_feedback` 的 `evidence.acceptance.arrival_geometry_met=True`、`arrival_review_required=True`、`scope.automatic_success_allowed=False`；`valid_target=True`（只表示动作后仍识别到目标）；`evidence` 至少含 9 个键（`acceptance`、`centre_error`、`diagnostics_version=1`、`failed_predicates=[]`、`observation_id`、`target_area`、`track_id`、`valid_target`、`verification_predicates`） |
| P3-3 | 清洁场景两帧仍可自动 SUCCESS | 闭环 2 个等待动作后第二次 `verification=="SUCCESS"`、`outcome=="SUCCESS"`、报告通过校验；两次 feedback 的 `acceptance` 均 geometry=True/auto=True/review=False（与 `arrival_acceptance.consecutive_new_observations=2` 一致） |
| P3-4 | 伪造 SUCCESS / 抹 review / 缺作用域 / 旧摘要均拒 | 对真实报告做 11 类篡改全部 `ValueError`，起因为：有信号 result 改 `SUCCESS`、有信号 `outcome` 改 `SUCCESS`、抹掉 `arrival_review_required`、抹掉 `scene_signal_motion_effect`、伪造 `automatic_success_allowed=True`、抹掉 `scope.has_hazards`、清洁报告 verification 降为 `UNKNOWN`、清洁 feedback 删 `acceptance`、`stability_policy.digest` 改旧值、`schema_version=9`、`navigation_design` 改 `stable-inference-v6` |
| P3-5 | 人工确认不由模型生成、无新控制能力 | 报告中不出现 `manual_confirmed`/`人工确认` 之类字段；`acceptance` 键集恰为 7 键（`acceptance_semantics`、`target_recognition`、`path_safety`、`proposal_permitted`、`scope`、`arrival_geometry_met`、`arrival_review_required`）；`control/service.py` 与 `control/core.py` 相对父提交逐字节一致 |

机器 `outcome`/`verification` 原样保留：探针未发现任何把人工确认写入机器结局的路径（`acceptance_v8.py`/`runtime.py` 无确认状态机，`stable_runtime.py` 只把 audit 放进 `navigation_feedback.evidence`）。

### 3.4 P4 不变项

| 项 | 断言 | 实测 |
| --- | --- | --- |
| P4-1 | v1–v7 策略、世界、提示、控制内核未动 | `git diff --name-only 508d518..c1af37e` 恰为 10 个文件：`AGENTS.md`、`docs/ARCHITECTURE.md`、`docs/TASKS.md`、`docs/validation/T08_v8_acceptance_proposal.md`、`src/vrc_agent/live_navigation/{acceptance_v8.py,runtime.py,stability_v8.json,stability_v8.sha256}`、`tests/integration/test_acceptance_v8_runtime.py`、`tests/unit/test_acceptance_v8.py`；其中源码+配置改动只有 `acceptance_v8.py`、`runtime.py`、`stability_v8.json`、`stability_v8.sha256`；v1–v7 的 `stability_v*.json`/`.sha256`、`live_world*`/提示文件、`skills/navigation.py`、`live_navigation/skill.py` 在两提交间 git blob 完全一致 |
| P4-2 | 默认 legacy/dry-run、现场配置未切 v8、只读计划 | `AppConfig()` 默认 `navigation_design="legacy"`、`mode="dry-run"`、`navigation_motion_profile="cautious"`；`configs/navigation-live.toml` 仍 `mode="dry-run"` + `stable-inference-v6`，不含 v8；`python -m vrc_agent navigate-live --task G2-1` 输出 plan-only：`mode="plan-only"`、`sends_live_input=False`、exit 0，默认世界 v1 的 `suite_digest==dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335`；探针另以 `world_version="v2"` 调用 `navigation_entry`，`suite_digest==load_live_world("v2").digest()==b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0` |

5%/Δ2/遮罩/保护区零容差/2 对预热/4000ms/180s/40 步/3 attempt/运动包络与 30s 基础租约、急停/epoch/TTL/到期归零的代码路径未被本提交触及（策略 18 键相等 + 控制内核逐字节一致）。

### 3.5 P5 新风险专项

- 用"有 hazards + 有 uncertainties + 伪造 `forward_clearance`（几何合法、置信度 0.96）"构造场景：`proposal_permitted=True`，即**运动确实被允许**——这是第 94 节声明的设计行为，不是缺陷，但意味着整场危险信号已不构成运动否决。
- 同一场景的到达：`arrival_geometry_met=True` 而 `automatic_success_allowed=False`，即**危险场景不会直接自动成功**；有信号时自动成功恒为 false，只能靠人工确认。
- 证据中不含任何"物理安全/已证明/`proven_safe`/`path_is_safe`"字样，`motion_effect=="record_only"` 在源码与策略声明中均显式出现。
- 残余风险边界：运动许可仍受短动作（field forward 0.4/500ms、strafe 0.2/200ms、yaw 1.0/200ms）、`max_actions=40`、动作新鲜度 4000ms、3 次 attempt、30s 基础租约、急停与人工在场约束；但这些约束证明的是"动作受限"，不是"路径安全"。**模型误报 clearance 时，本修订不提供额外拦截。**

## 4. 四项门禁原始输出（`c1af37e`，离线）

```text
$ .venv/Scripts/python.exe -m pytest -q
1308 passed in 444.16s (0:07:24)

$ .venv/Scripts/python.exe -m ruff check src tests
All checks passed!

$ .venv/Scripts/python.exe -m ruff format --check src tests
157 files already formatted

$ .venv/Scripts/python.exe -m pip check
No broken requirements found.
```

原始日志：`.runtime/verify/t08_v8_review_{pytest,ruff_check,ruff_format,pip_check}.log`。本轮未出现第 92 节记录的 v3/v4 spawn 偶发 `STALE_OBSERVATION`（批量一次通过）。

`python -m vrc_agent navigate-live --task G2-1` 默认只读计划：`mode="plan-only"`、`sends_live_input=False`、exit 0（默认世界 v1；显式 `--world-version v2` 的摘要见 P4-2）。

## 5. 非阻断观察

**N1（报告口径）** `arrival_review_required=False` 同时覆盖"未到达"和"已干净到达"两种状态，人工确认表不能只看该布尔，必须结合 `arrival_geometry_met` 与机器 `outcome`/`verification` 判读；第 93 节的确认表已要求同时记录几何标记、信号存在性与机器结局，本条只强调实现层面没有第三个"未到达但 review 为真"的取值。

**N2（运行结局）** 有信号场景下 `skill.verify` 的 `near` 依赖 `arrival_permitted`，因此运动过程永远不能自动 SUCCESS；每次动作后 UNKNOWN 场景仍累积 `unknowns`、静止画面还会因 `NO_PROGRESS` 触发恢复队列。真实 40 步配置下，任务仍可能先撞 180s/40 步/租约或 `TARGET_UNOBSERVED`，而不是"几何到达即结束"。人工确认只改变验收口径，不改变运行结局；报告中的 `arrival_geometry_met` 可能来自中途动作而非终态。

**N3（字段语义）** `navigation_feedback.valid_target=True` 只表示"动作后场景仍识别到目标"，**不等于**允许自动成功。P3-2 实测同一反馈中 `valid_target=True` 而 `automatic_success_allowed=False`；汇总脚本不得把 `valid_target` 当到达或成功标志。

**N4（新风险，控制层面）** `has_hazards`/`has_uncertainties` 现仅记录（`record_only`），且 strafe 恢复只需 `recovery_clearance`。若模型把危险场景误报为 clearance 合格，运动仍会执行。建议现场人工确认表把"`has_hazards`/`has_uncertainties` 为真但仍执行了运动"的动作单列复核；这是语义修订的既定代价，不是可在此轮修复的缺陷。

**N5（摘要纪律）** sidecar 与 loader 的一致性属工程完整性校验，不是密码学签名：能同时替换 `stability_v8.json`、`.sha256` 与 loader 预期值的人可以伪造通过（与 v1–v7 同源设计）。旧初版 `908af1df…` 的字节只存在于父提交 `508d518`，当前工作树不可加载（P1-1/P1-3 已证）。

**N6（覆盖缺口）** 仓库内没有 v8 的"旧摘要真实报告"工件（第 90 节未留样），因此"旧摘要报告被拒"只能用构造载荷验证；也没有 v8 现场报告，P3 的到达语义全部用合成场景验证。可复算性证据限于探针与门禁，不含真实采集/模型数据。

**N7（背景，不属本提交）** `pilot5` 的 F-F（`navigation_feedback.valid_target=false`）属第 87 节验证链修复范围，不是 `c1af37e` 的声明对象；本轮门禁包含其测试，但未复评该修复的质量。固定 20 次任务能否恢复，仍取决于第 87 节修复的复核与现场复验，与本轮 v8 结论独立。

## 6. 未覆盖与后续

- 未做 v8 现场任何采集/推理/动作验证；`arrival_geometry_met`、`has_hazards`、clearance 几何在真实场景中的可用性未验证。
- 未评估 `changed_runs`/v7 逐证据路径与 v8 组合（v8 沿用 v6/v5 比较器，本轮未新增组合测试）。
- 未测最坏报告体积（合成报告 <2MB；40 动作的真实报告体积未测）。
- 后续门槛不变：独立复核（本轮）→ 用户选版/冻结（新摘要 `98bf0aa7…`）→ 人在场现场试点 → 才谈 20 次任务与人工确认表。**本轮不解除 v8 的候选状态，不允许 live。**

## 7. 边界声明

本记录为 ADR-009 定向离线复核：不启动 live、不 ARM、不调付费模型、不发真实输入、不读密钥；**不是实机验收**。探针与门禁只能证明代码路径与声明在合成数据上未被证伪，不能证明真实场景的运动安全、到达识别或场景信号可用性。
