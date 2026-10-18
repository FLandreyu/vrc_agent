# T08 v8「目标识别与路径安全分离」候选提案

负责人 Codex，2026-10-18；依据 TASKS 第87/89节。**这是提案，不是冻结，不是实机验收。** 仅交付离线候选实现及回归；T08 仍 IN_PROGRESS，固定20次任务未恢复。必须独立复核、用户授权冻结、现场复验后再决定是否接入实机。

本页已按TASKS第91/92/93节用户决定修订；当前语义以第8节及新摘要为准。初版交付门禁保留在第6节，不能替代本次修订的门禁与独立复核。

## 1. 现场依据与样本口径

pilot5 本地报告 SHA256 `6846d2cedcbba0aa4b24da72b5853c3ac4bcf1a4d9ef1d4acc257bb10063b180`：5次 inference_source，场景 confidence 0.60/0.60/0.75/0.60/0.60，均有 hazards 和 uncertainties；3次动作反馈的18个基础验证条件通过，失败来自场景采信。原观察事件未保留全部实体和危险正文，第87节通过原代码路径及合成元数据复建定位。

pilot6 本地报告 SHA256 `c9101e3314a70fc7d1b483b70b41eea2276708b5628ede3521908e47d6f10bb7`：4次模型调用/4次 inference_source，其中3次进入动作后的验证反馈。三次验证场景 confidence **0.85/0.85/0.75**，失败谓词依次为：

1. scene_no_uncertainties；
2. scene_no_hazards、scene_no_uncertainties；
3. scene_confidence_sufficient、scene_no_hazards、scene_no_uncertainties。

两次报告的摘要在本轮实算一致，没有修改原报告。第89节“8/8”的口径是 pilot5 五次推理来源 + pilot6 三次验证场景，全部至少有一类危险/不确定信号。若计全部 inference_source，则是9/9（pilot6另一次 confidence=0.85，也有两类信号）。**不能写成全部场景置信度都在0.60–0.75或全部低于0.85**。系统性的是危险/不确定信号存在与目标采信耦合，不是每次都低置信。两次试点均未产生位移验收证据。

## 2. 显式候选声明

- 文件：`src/vrc_agent/live_navigation/stability_v8.json` / `.sha256`。
- version=`local-rgb-stability-v8-candidate`，status=`REQUIRES_FIELD_CALIBRATION`；设计键 `stable-inference-v8`。
- 当前SHA256：`98bf0aa7986f8349c678f6196e307239ad02df5945004c23266e9b740082e2ef`；按工作树原字节计算，sidecar 为小写摘要+CRLF（66字节），Git `-text` 保持字节。初版`908af1df…`→本摘要是未冻结候选修订，不是v9或新的像素判据版本。
- 以冻结 v6 为底：所有原数值、metric、mask、motion_profiles、checkpoints 逐项保持。没有继承 v7 的非目标框1000ppm预算；所有导航证据保护区仍按 v6 零容差。
- 新增声明全部写在 JSON，loader 与 `DECLARATION` 严格比较，缺项、额外项、数值/语义变化均拒绝；不以隐式默认解释。候选状态不可通过改名或只换摘要冒充已冻结。

| 语义 | v6 / v7 | v8 候选 |
|---|---|---|
| 目标是否可采信 | 场景整体 confidence 达标、无任何 hazards/uncertainties，再筛唯一合格目标 | 独立 target_recognition：唯一同标签实体（先按标签计数）、实体 confidence≥goal.min_confidence、当前单观测绑定、合法bbox、可见且绑定帧、目标不完全遮罩 |
| confidence 作用域 | 所有保留实体最小值（报告只展示导航实体） | **固定 target_entity**；唯一目标的实体 confidence，无唯一目标为 None；source_confidence 仅保留旧整体值作诊断，绝不用于授权 |
| 危险/不确定性 | 否决目标识别，也阻断规划/验证 | 不否决识别/运动，继续记录；有信号不自动SUCCESS，几何满足时标人工review |
| 路径 | clearance 与场景采信耦合 | 独立 path_safety，正面通道证据必需，不能由目标可见/没有危险推断 |
| 到达 | 连续两个新观测，同session/epoch/track及世界几何 | 自动确认约束不变；有信号仍UNKNOWN且标review。现场20任务改为几何证据＋人工确认，不要求自动SUCCESS |
| 报告 | v6 schema9 / v7 schema10 | 独立 schema11；不得混用 |

选择 target_entity 的理由：目标实体的识别置信度应回答“是否看到了这个目标”，不能因另一个不相关实体的0.60置信度消失。另一选项“目标+导航证据最小值”仍会让低置信路径证据污染目标识别，故本候选不采用。风险是目标看得清而路径看不清；路径实体仍必须独立通过0.90、几何和帧绑定，不能借用目标置信度。

## 3. 两组谓词与动作授权

`target_recognition` 逐项记录：unique_label、entity_confident、evidence_bound、bbox_legal、frame_visible、target_frame_bound、target_not_fully_masked。

唯一按**同标签全部实体**计数，不因另一个低置信实体被筛走就当成唯一。bbox 必须有限且有序、归一化；当前场景必须只有一个观测ID；帧引用/可见性/session/epoch 绑定；遮罩沿 v6 可见部分语义，完全不可见拒绝，部分交叠不增加任何像素保证。目标 last_seen 及动作后时间顺序仍由原验证链检查。

`path_safety` 逐项记录：has_hazards、has_uncertainties、scene_no_hazards、scene_no_uncertainties、forward_clearance、recovery_clearance、motion_envelope、max_actions。场景信号字段是观测记录，不在各轴requires内。

- 前进：目标识别全部通过 + forward_clearance + 包络 + 动作预算。
- 恢复侧移：目标识别 + recovery_clearance + 包络 + 预算；forward_clearance不能替代。recovery标签仍按原提示定义要求双侧短步可见安全空间，并非新增地图能力。
- 转向：目标识别 + 原转向包络 + 预算，不要求前进通道。
- 等待仅100ms并有动作预算；stop 始终可停止。proposal_permitted 是对该动作种类的判定，某个“不适用”的 clearance=false 不等于 wait/stop 不可执行。
- 前进/恢复 clearance 原几何不变：x1≤0.35、x2≥0.65、y1≤0.7、y2≥0.9、实体confidence≥0.9，唯一实体、当前观测与last_seen绑定。任一缺失或不合格拒绝；不得扩框、推测或由没有hazards推断通道。
- 选定 motion_profiles 的单轴速度/时长闭集与40步不变；运行时提交前独立复算 v8 授权，替换decide不能绕过。
- 路径证据不足/包络或预算拒绝时有限等待，连续3次暂停PATH_UNSAFE。单独存在场景信号不触发该暂停；几何已到达后的等待仍受租约/180s/40步限制，不会无限运行。每次动作结束仍经原控制层到期归零。

v8不清除或按模型自然语言筛掉危险信号，记录仍是整场存在性；它不再额外硬否决运动。能否前进依赖正面的路径实体，不能从目标可见或没有危险推断安全，亦不能保证模型clearance判断正确。

## 4. 到达安全与报告重算

几何到达继续使用所选世界原定义；自动SUCCESS仍需连续两次动作后的干净新观测、同session/epoch/track及连续证据链。有信号时只记录arrival_geometry_met与arrival_review_required，不进入自动成功序列。按第93节，现场人工验收不再等待自动SUCCESS；机器结局与人工结论分开保存，见第8节。

schema11 顶层必需：acceptance_semantics=`target-path-v8`、acceptance_observations（全部采集元数据，包括预热/预检/两个原检查点）。沿用原稳定性证明、attempt、timings、lease账本。

- inference_source / observation：confidence_scope、按目标作用域重算的confidence、旧source_confidence、scene_generated_at_ns、observation_metadata（ID、capture时间、frame_ref/size、window_state、session/epoch）。不保存原始图像、危险/不确定性正文。
- acceptance_decision：观测ID、连续decision_index、requested/dispatched Proposal、audit。audit 必须完整包含两组谓词、作用域/信号存在性/对识别、运动、到达的影响、proposal_permitted、两项到达标记。
- navigation_feedback.evidence.acceptance：同样两组逐项结果和作用域；原18条基础门禁及新目标谓词仍列在 verification_predicates / failed_predicates 中。

校验器从严格契约实体、观测元数据、动作、可信世界与策略重算目标置信度/几何/遮罩/两组谓词，校验决策序号、实际动作与dispatched一致、全检查点session/epoch/时间绑定；按顺序重放verify，重算连续到达结果。嵌套字段缺失、伪造bool、作用域/到达标记不符、缓存观测缺字段、虚假SUCCESS、混用旧schema、候选宣称live均拒绝。

限制：这是本地结构一致性审计，不是数字签名；若整份实体/画面/信号一并伪造，离线报告无法认证实机真实性。source_confidence 是旧全体最小值的记录，报告不保存无关实体，不能从导航实体重新证明这个历史值；它不参与 v8 采信。危险信号只保留存在性，人工判断危险正文和实际场景仍需用户在场或另行授权的固定帧评测。

## 5. 新风险与约束

| 新风险 | 候选约束与剩余限制 |
|---|---|
| 在危险场景中声称到达 | 几何标记与SUCCESS分离；信号否决自动成功并清空连续证据，报告显式要求人工复核；单帧不得自动SUCCESS |
| 移除整场信号的运动否决后误走 | 信号仍记录，运动只由对应clearance、包络与预算许可；如果模型同时误报clearance，整场信号将不再拦住它，须现场监督/急停与专项复核，不能宣称风险已消除 |
| 忽略低置信无关实体后误走 | 目标置信度不授权路径；通道证据仍0.9及原几何。人工到达确认不能补救运动过程中的错误路径判断 |
| 模型给出高置信幻觉或伪造安全地板 | 帧绑定、唯一实体、稳定性、几何等不能证明模型语义正确；必须固定帧人工标注及现场对抗验证，不能据离线通过冻结 |
| 遮罩遮住部分目标/路径 | 沿 v6 可见部分保护语义，不保证遮罩内像素；完全遮罩拒绝，G1/G3/G5交互仍须现场复验 |
| 新契约允许目标confidence=None但保留其它实体 | 仅 ScopedSceneV8；无唯一目标不授权运动。旧 SceneState 契约、v1–v7语义均不变 |

## 6. 初版离线验证及待复验（908af1df…历史）

针对性候选测试：`43 passed in 12.87s`；此前 v8+v6/F-F联合回归：`90 passed in 38.13s`。包含唯一性/低置信/绑定/bbox/可见性/完全遮罩拒绝；clearance五条边界；无关实体低置信；未知目标为None；危险场景等待/暂停；危险到达不得成功；两帧干净到达；原窗口/时间/epoch/session/绑定门禁；候选资源前拒绝与基类绕行；schema11伪造/缺字段/混用/非法历史置信度拒绝。

最终完整四项门禁（均使用项目 `.venv/Scripts/python.exe -m`）：

```text
pytest -q
1292 passed in 393.50s (0:06:33)

ruff check src tests
All checks passed!

ruff format --check src tests
157 files already formatted

pip check
No broken requirements found.
```

原始输出 `.runtime/v8_{pytest,ruff,format,pip}.log`。固定CRLF摘要工件使用 `git -c core.whitespace=cr-at-eol diff --cached --check`，无错误；没有为了行尾警告改策略字节。

失败记录不覆盖：首轮完整门禁 `3 failed, 1288 passed in 469.56s (0:07:49)`（`.runtime/v8_pytest_first_full.log`）。两个失败是新v8注册后未同步的旧版本闭集断言，已同步schema/工厂/未知v9断言，保持旧版本数值与基类拒绝语义；定向 `2 passed in 0.38s`。另一个是旧 `test_v2_spawn_report` 的真实时钟路径触发 STALE_OBSERVATION，**未改时限或代码**原样单跑 `1 passed in 9.03s`（`.runtime/v8_v2_isolated.log`），不凭单次重跑断言其瞬态来源。最终结果以上述完整1292项通过为准，不以局部重跑替代。

默认 dry-run 原始输出摘要（完整 `.runtime/v8_dry_run.log`）：

```json
{
  "mode": "plan-only",
  "sends_live_input": false,
  "suite_digest": "b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0"
}
```

命令：`.venv/Scripts/python.exe -X utf8 -m vrc_agent navigate-live --task G2-1 --config configs/navigation-live.toml --world-version v2`。这仍是现场 v6 配置的只读计划，不是 v8 live。

待独立复核：作用域/基类隔离、所有运动前守卫、危险到达反例、报告缺字段/重放/跨schema、策略不变项。待用户另行授权的现场：目标与路径分组对真人可见证据是否忠实；hazards/uncertainties的真实性与持续阻断比例；G1/G3/G5遮罩交互；未知/重名目标、遮挡/水池/家具、错误clearance的误放行；真实延迟、稳定性通过率与人工接管。任何门槛调整必须新版本/摘要/复核，不能现场改v8数字过演示；本候选不恢复20任务批次。

## 7. 初版交付文件与未改清单（历史）

新增：acceptance_v8.py、acceptance_report_v8.py、stability_v8.json/.sha256、tests/unit/test_acceptance_v8.py、tests/integration/test_acceptance_v8_runtime.py、本提案。

接线：.gitattributes、config.py、control/service.py（仅新设计预算分派）、live_navigation/{stability,parameters,attempts,stable_runtime,runtime,session,stability_report}.py、skills/navigation.py（默认行为不变的目标/到达hook）、AGENTS.md、ARCHITECTURE.md、TASKS.md第90节。交付只提交本轮改动，保留进入会话时已存在的TASKS编辑。

完整改动文件清单（23个；另两处仅同步版本闭集断言）：

```text
.gitattributes
AGENTS.md
docs/ARCHITECTURE.md
docs/TASKS.md
docs/validation/T08_v8_acceptance_proposal.md
src/vrc_agent/config.py
src/vrc_agent/control/service.py
src/vrc_agent/live_navigation/acceptance_report_v8.py
src/vrc_agent/live_navigation/acceptance_v8.py
src/vrc_agent/live_navigation/attempts.py
src/vrc_agent/live_navigation/parameters.py
src/vrc_agent/live_navigation/runtime.py
src/vrc_agent/live_navigation/session.py
src/vrc_agent/live_navigation/stability.py
src/vrc_agent/live_navigation/stability_report.py
src/vrc_agent/live_navigation/stability_v8.json
src/vrc_agent/live_navigation/stability_v8.sha256
src/vrc_agent/live_navigation/stable_runtime.py
src/vrc_agent/skills/navigation.py
tests/integration/test_acceptance_v8_runtime.py
tests/unit/test_acceptance_v8.py
tests/unit/test_stability_v3.py
tests/unit/test_stability_v3_review_regressions.py
```

字节自查 `.runtime/v8_protected_before.json` 对比结果 `protected_changes []`：v1–v7所有JSON/sidecar、所有现场世界文件、navigation-live.toml及原提示模块均不变。v6摘要仍 `e46e0c2816c6f512fbd4fd5e5c60ff1ef3fc4d0560e0e8b29d3b6a6315caa513`，v7仍 `fbebd9cbfd6d11988dd783ea83b7a4e8ef38c1b3ce236e435dcd20617cb2caf7`。世界v1/v2语义摘要仍 `dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335` / `b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0`。

判据5%/Δ2/保护区零容差/原遮罩、预热2对、4000ms/180s/40步/3attempt、运动包络、30秒基础租约、人工续租门槛、急停/epoch/TTL/pending_releases/到期归零不变。配置保持v6+dry-run。无live、ARM、付费模型、真实输入、密钥读取；未改世界/提示与T13提案。GraphFlow skill已应用，但工具不可用，context/plan/index未执行，不伪造结果。

## 8. 未冻结候选修订：运动按路径证据、现场到达人工确认

2026-10-18，负责人Codex，依据TASKS第91/92/93节。用户已决定继续使用deepseek-flash，不换模型；现场20项任务不再要求自动SUCCESS。本次仅修订未冻结候选，不启动live/ARM/付费模型，不读取密钥，不改现场配置、世界或提示。

### 8.1 摘要与版本纪律

- 旧摘要：`908af1df0eb214af1e26312d1eb308a1094feb96f37a478136894364e7bfc0bc`。
- 新摘要：`98bf0aa7986f8349c678f6196e307239ad02df5945004c23266e9b740082e2ef`。
- version仍`local-rgb-stability-v8-candidate`，status仍`REQUIRES_FIELD_CALIBRATION`，schema仍11；新旧草案由策略摘要严格区分，旧摘要报告不能套用新语义。loader完整比较显式声明，未增加缺省兼容路径。
- 这是**未冻结候选修订**，不是v9或新的像素判据版本：v8尚未冻结或获准live，用户已明确授权修订；像素判据、七项目标识别、阈值/几何/预算均不变。运动许可语义确实发生变化，必须重新复核，不能沿用初版测试/复核结论。

### 8.2 明确的运动与到达作用域

各轴requires现在分别为：forward=`forward_clearance/motion_envelope/max_actions`，strafe=`recovery_clearance/motion_envelope/max_actions`，yaw=`motion_envelope/max_actions`；运动仍要求目标七谓词全部通过。

新增顶层声明scene_signal_motion_effect=`record_only`、scene_signal_arrival_effect=`record_review_required`，同时同步scene_signals，避免相互冲突的声明。arrival_acceptance显式写明field_acceptance=`arrival_geometry_met_and_manual_confirmation`及automatic_success=`clean_scene_two_new_observations_only`，有信号时只记录review、不自动成功。

path_safety继续记录has_hazards/has_uncertainties，保留scene_no_hazards/scene_no_uncertainties用于读历史对照，但四项都不进入轴requires。audit.scope记录motion_effect、arrival_effect、field_acceptance、automatic_success_allowed，以下两种情形可直接读取并重算：

| 情形 | 路径/作用域 | 到达标记 |
|---|---|---|
| 场景有信号，clearance/目标/包络/预算满足 | has_hazards或has_uncertainties=true，proposal_permitted=true，motion_effect=record_only | 尚未几何到达时geometry/review都false，不自动声称到达 |
| 场景有信号且几何已到达 | 自动成功许可false，arrival_effect=record_review_required | arrival_geometry_met=true、arrival_review_required=true，结果仍UNKNOWN，由人工确认 |

删除了三个旧作用域残留：decide中额外调用arrival_permitted否决运动；recovery_allowed将自动到达许可当作恢复许可；runtime提交前调用旧target整场否决。现在arrival_permitted只影响自动SUCCESS；runtime仅在v8分支使用v8识别器，其独立audit、白名单、包络、clearance、授权/新鲜度检查保留。v1–v7执行行为不变。

报告校验器沿现有“绑定观测→共享audit重算→verify重放”实现验证新作用域，不信任报告内的许可bool。新增反例覆盖旧motion_effect、缺arrival_effect、缺信号字段、用0伪造bool、旧摘要、几何review被抹去以及有信号伪造SUCCESS，均须拒绝。

### 8.3 20次任务的人工确认口径

引用TASKS第93节用户决定：每项任务以**有效证据中的arrival_geometry_met＋人工逐条确认**验收，不要求机器自动SUCCESS。arrival_review_required和危险/不确定性存在性必须可见；机器outcome/verification不改写，人工结论单独保存。禁止让模型生成“人工已确认”，也不新增任何可触发ARM/RESET/续租的IPC能力。

现场确认表至少逐项记录：task_id、所选世界/策略摘要、报告文件SHA256、具体观测ID/captured_at、geometry标记、review标记、信号存在性、机器outcome、人工确认人/时间、确认或否决结论、接管/超时/卡住与失败原因。优先引用动作后feedback中valid_target=true且18条基础门禁全真的观测；不能只摘取一个几何bool跳过时序、session/epoch和帧绑定。

原固定任务清单与≥16/20目标不改；接管、超时、卡住等原始记录与失败分类不隐藏，不能仅因“有人点了确认”就把无到达证据/不安全接管的任务算成功。人工确认与安全结束的操作需在获准现场试点中逐项记录；本轮不新增确认UI/命令，不让确认延长租约，不将EXPIRED_LEASE等机器结局改成SUCCESS。人工确认率与机器自动SUCCESS率必须分开汇总，旧navigation-report的自动成功统计不充当新人工验收台账。

### 8.4 新风险与未解决问题

- **误报clearance的运动风险上升**：如果模型同时给出危险/不确定性与错误高置信clearance，整场信号不再拦住运动。本修订没有通过人工到达确认消除此风险；仍须逐帧路径证据复核、短动作、人在场监督和急停，冻结前专项对抗评估。
- 信号存在性不能被省略、改写成“无危险”，也不能按自然语言任意过滤；报告里“允许运动”不等于“已证明物理路径安全”。
- 有信号的几何到达仍不能自动SUCCESS；人工确认不是模型自我打分，也不能掩盖失控/接管/预算外证据。无信号时原两帧、同session/epoch/track自动确认逻辑仍保留，但不是新20任务的强制条件。
- 几何到达标记不会自动结束任务或续租。若操作者尚未正常结束，租约/180s/40步仍可能先到并停止；这类机器结局必须保留，不能为了验收放宽时限。

### 8.5 本轮验证与改动清单

候选定向回归：`59 passed in 29.51s`，覆盖三种信号组合×三轴、实际恢复分支、有信号但有/无clearance的内存runtime、危险不得假成功、review与作用域伪造/缺字段。首轮“无clearance暂停”测试夹具自动把第二帧目标变成已到达，导致等待确认到租约到期；已固定该夹具目标为未到达尺寸后重跑，未改产品时限或归零语义。

本轮完整四项门禁（项目`.venv/Scripts/python.exe -m`），原始输出：

```text
pytest -q
1308 passed in 398.27s (0:06:38)

ruff check src tests
All checks passed!

ruff format --check src tests
157 files already formatted

pip check
No broken requirements found.
```

原始日志`.runtime/v8r_{pytest,ruff,format,pip}.log`。本轮完整测试一次全绿，未出现第92节提示的v3/v4 spawn偶发STALE；未改测试时限或安全门槛。

未改清单自查：`.runtime/v8r_protected_before.json`逐字节比对`protected_changes []`；recognition_predicates和clearance两个函数源码也与修订前完全一致。v8仅改path_safety、scene_signals、arrival_acceptance及两个新增作用域声明；version/status、5%/Δ2/遮罩/保护区零容差、2对预热、4000ms/180s/40步/3attempt、运动包络不变。v1–v7 JSON/sidecar、全部世界、现场配置、提示、control/core.py与control/service.py均不动。基础30秒租约、急停/epoch/TTL/pending_releases/到期归零不变。

默认只读计划复跑为mode=plan-only、sends_live_input=false，仍为现场v6＋世界v2；完整`.runtime/v8r_dry_run.log`。未启动live、未ARM、未调付费模型、未发真实输入、未读密钥。GraphFlow仍不可用，不伪造context/plan/index。

本轮10个改动文件：AGENTS.md、docs/ARCHITECTURE.md、docs/TASKS.md（第94节）、本提案、src/vrc_agent/live_navigation/{acceptance_v8.py,runtime.py,stability_v8.json,stability_v8.sha256}、tests/unit/test_acceptance_v8.py、tests/integration/test_acceptance_v8_runtime.py。

**仍然只是候选，不是冻结，不是实机验收**。交付后等待另一执行者按新摘要独立复核及用户冻结，再由用户安排现场试点与人工确认批次。
