# T08 v8「目标识别与路径安全分离」候选提案

负责人 Codex，2026-10-18；依据 TASKS 第87/89节。**这是提案，不是冻结，不是实机验收。** 仅交付离线候选实现及回归；T08 仍 IN_PROGRESS，固定20次任务未恢复。必须独立复核、用户授权冻结、现场复验后再决定是否接入实机。

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
- SHA256：`908af1df0eb214af1e26312d1eb308a1094feb96f37a478136894364e7bfc0bc`；按工作树原字节计算，sidecar 为小写摘要+CRLF（66字节），Git `-text` 保持字节。
- 以冻结 v6 为底：所有原数值、metric、mask、motion_profiles、checkpoints 逐项保持。没有继承 v7 的非目标框1000ppm预算；所有导航证据保护区仍按 v6 零容差。
- 新增声明全部写在 JSON，loader 与 `DECLARATION` 严格比较，缺项、额外项、数值/语义变化均拒绝；不以隐式默认解释。候选状态不可通过改名或只换摘要冒充已冻结。

| 语义 | v6 / v7 | v8 候选 |
|---|---|---|
| 目标是否可采信 | 场景整体 confidence 达标、无任何 hazards/uncertainties，再筛唯一合格目标 | 独立 target_recognition：唯一同标签实体（先按标签计数）、实体 confidence≥goal.min_confidence、当前单观测绑定、合法bbox、可见且绑定帧、目标不完全遮罩 |
| confidence 作用域 | 所有保留实体最小值（报告只展示导航实体） | **固定 target_entity**；唯一目标的实体 confidence，无唯一目标为 None；source_confidence 仅保留旧整体值作诊断，绝不用于授权 |
| 危险/不确定性 | 否决目标识别，也阻断规划/验证 | 不否决识别；按整场存在性继续否决运动与自动 SUCCESS，必须记录 |
| 路径 | clearance 与场景采信耦合 | 独立 path_safety，正面通道证据必需，不能由目标可见/没有危险推断 |
| 到达 | 连续两个新观测，同session/epoch/track及世界几何 | 全部不变；几何满足但有场景信号时记 arrival_review_required=true，清空连续到达证据，仍 UNKNOWN |
| 报告 | v6 schema9 / v7 schema10 | 独立 schema11；不得混用 |

选择 target_entity 的理由：目标实体的识别置信度应回答“是否看到了这个目标”，不能因另一个不相关实体的0.60置信度消失。另一选项“目标+导航证据最小值”仍会让低置信路径证据污染目标识别，故本候选不采用。风险是目标看得清而路径看不清；路径实体仍必须独立通过0.90、几何和帧绑定，不能借用目标置信度。

## 3. 两组谓词与动作授权

`target_recognition` 逐项记录：unique_label、entity_confident、evidence_bound、bbox_legal、frame_visible、target_frame_bound、target_not_fully_masked。

唯一按**同标签全部实体**计数，不因另一个低置信实体被筛走就当成唯一。bbox 必须有限且有序、归一化；当前场景必须只有一个观测ID；帧引用/可见性/session/epoch 绑定；遮罩沿 v6 可见部分语义，完全不可见拒绝，部分交叠不增加任何像素保证。目标 last_seen 及动作后时间顺序仍由原验证链检查。

`path_safety` 逐项记录：scene_no_hazards、scene_no_uncertainties、forward_clearance、recovery_clearance、motion_envelope、max_actions。

- 前进：目标识别全部通过 + 无整场危险/不确定性 + forward_clearance + 包络 + 动作预算。
- 恢复侧移：上述场景条件 + recovery_clearance + 包络 + 预算；forward_clearance 不能替代。recovery 标签仍按原提示定义要求双侧短步可见安全空间，并非新增地图能力。
- 转向：目标识别 + 无整场危险/不确定性 + 原转向包络 + 预算，不要求前进通道。
- 等待仅100ms并有动作预算；stop 始终可停止。proposal_permitted 是对该动作种类的判定，某个“不适用”的 clearance=false 不等于 wait/stop 不可执行。
- 前进/恢复 clearance 原几何不变：x1≤0.35、x2≥0.65、y1≤0.7、y2≥0.9、实体confidence≥0.9，唯一实体、当前观测与last_seen绑定。任一缺失或不合格拒绝；不得扩框、推测或由没有hazards推断通道。
- 选定 motion_profiles 的单轴速度/时长闭集与40步不变；运行时提交前独立复算 v8 授权，替换decide不能绕过。
- 场景信号持续存在或路径阻断时有限等待，连续3次暂停 PATH_UNSAFE；不是靠刷新识别状态无限等待/恢复。每次动作结束仍经原控制层到期归零。

v8 不清除、重新解释或按模型自然语言筛掉危险信号。记录的是**整场存在性**，不声称已有可靠的“与导航无关危险”分类器。故对 pilot5/6 这类持续有信号的场景，本版本仍可能没有位移；分离不是把失败转成成功。

## 4. 到达安全与报告重算

几何到达继续使用所选世界原定义；连续两次动作后的新观测、同 session/epoch/track、连续观测链全部保留。任何一帧有 hazards/uncertainties 都不能进入成功证据序列。`arrival_geometry_met` 记录“几何已满足”；`arrival_review_required` 标记“存在信号且几何满足”；这两者均不是 SUCCESS，必须再取得两帧干净、连续的新证据。

schema11 顶层必需：acceptance_semantics=`target-path-v8`、acceptance_observations（全部采集元数据，包括预热/预检/两个原检查点）。沿用原稳定性证明、attempt、timings、lease账本。

- inference_source / observation：confidence_scope、按目标作用域重算的confidence、旧source_confidence、scene_generated_at_ns、observation_metadata（ID、capture时间、frame_ref/size、window_state、session/epoch）。不保存原始图像、危险/不确定性正文。
- acceptance_decision：观测ID、连续decision_index、requested/dispatched Proposal、audit。audit 必须完整包含两组谓词、作用域/信号存在性/对识别、运动、到达的影响、proposal_permitted、两项到达标记。
- navigation_feedback.evidence.acceptance：同样两组逐项结果和作用域；原18条基础门禁及新目标谓词仍列在 verification_predicates / failed_predicates 中。

校验器从严格契约实体、观测元数据、动作、可信世界与策略重算目标置信度/几何/遮罩/两组谓词，校验决策序号、实际动作与dispatched一致、全检查点session/epoch/时间绑定；按顺序重放verify，重算连续到达结果。嵌套字段缺失、伪造bool、作用域/到达标记不符、缓存观测缺字段、虚假SUCCESS、混用旧schema、候选宣称live均拒绝。

限制：这是本地结构一致性审计，不是数字签名；若整份实体/画面/信号一并伪造，离线报告无法认证实机真实性。source_confidence 是旧全体最小值的记录，报告不保存无关实体，不能从导航实体重新证明这个历史值；它不参与 v8 采信。危险信号只保留存在性，人工判断危险正文和实际场景仍需用户在场或另行授权的固定帧评测。

## 5. 新风险与约束

| 新风险 | 候选约束与剩余限制 |
|---|---|
| 在危险场景中声称到达 | 几何标记与SUCCESS分离；信号否决成功并清空连续证据，报告显式要求人工复核；单帧不得成功 |
| 分离后危险信号被弱化 | 目标识别只记录信号，但所有运动与自动成功仍为硬否决；提交前独立重算，报告不得省略作用域 |
| 忽略低置信无关实体后误走 | 目标置信度不授权路径；通道证据仍0.9及原几何，并保留整场信号否决；“无关”不是危险豁免类别 |
| 模型给出高置信幻觉或伪造安全地板 | 帧绑定、唯一实体、稳定性、几何等不能证明模型语义正确；必须固定帧人工标注及现场对抗验证，不能据离线通过冻结 |
| 遮罩遮住部分目标/路径 | 沿 v6 可见部分保护语义，不保证遮罩内像素；完全遮罩拒绝，G1/G3/G5交互仍须现场复验 |
| 新契约允许目标confidence=None但保留其它实体 | 仅 ScopedSceneV8；无唯一目标不授权运动。旧 SceneState 契约、v1–v7语义均不变 |

## 6. 离线验证及待复验

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

## 7. 文件与未改清单

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
