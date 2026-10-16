# T08 世界 v2 / 到达与提示调整提案（2026-10-17，未定案）

状态：**PROPOSAL_NOT_APPROVED**。依据 TASKS 第 54/55 节。本轮没有发布世界 v2，没有修改冻结世界 v1、当前提示词或生产到达规则；本文数值和提示片段仅供独立复核、现场重拍和用户决策。v5 保护语义实现见 [v5 验证记录](T08_stability_v5.md)。

## 1. G3：在镜面激活前识别物理木框

现场激活后的到达参考图中模型报告木框不可见、goal_found=False。新保护语义不能补回缺失实体，也不能把未检测到目标解释为到达。建议下一现场会话先重拍**未激活、正面居中、距离足够近但尚未进入激活区**的物理面板；若没有可靠的激活前停留位置，应重新设计此任务，而不是降低尺寸判据来冒充成功。

建议到达规则（**待验证，未启用**）：`kind="box"`，`min_width=0.62`、`min_height=0.65`、`centre_tolerance=0.12`。依据唯一已报告的未激活近距框宽高 0.654×0.685，建议尺寸较其分别小 0.034/0.035（约 5%），为初始工程试验值；单样本不能证明鲁棒性。原规则 0.7×0.7 无法由该样本满足。居中与尺寸需同时成立，宽高本身不证明位置/朝向安全。

v5 下部分遮罩交叠不再结构性中止，但必须有可见木框实体、未遮罩保护区保持稳定、场景满足可信度及无 hazard/uncertainty 等原有动作/结果校验。模型仍需报告可见证据，不能为 UI 背后的边界补全或造框。未来若要强制区分“激活前/后”，需新增经现场验证的阶段证据契约；本轮只准备提案，不增加未经验证的激活检测器。

建议提示追加片段（未接入 `calibrated_prompt`）：

> For Avatar Mirror, report only the physical wooden panel and its visible wooden edges, never the avatar reflection or the whole screen. Before activation, use the visible physical frame. After activation, still attempt to locate visible wooden frame edges; if they cannot be seen, omit the target and state that the frame is not visible. Do not infer an occluded frame boundary or claim arrival.

这段提示只能改善输出意图，不保证模型服从或识别成功。应把激活前、激活后有边缘、激活后无边缘三类图分别验证。

## 2. G5：排除池面的大框

第 54 节模型给出的 `[0.02,0.18,0.98,0.99]` 是接近全屏的池面/图案大框。v5 可接纳它的几何形状，却不能证明标签或边界正确；当前尺寸判据可能因此虚假满足。这是剩余的语义问题，需要新参考图与提示联合复验。

建议提示追加片段（未接入生产）：

> For home_floor_diamond, bound only the visible white-outlined nested diamond motif itself. Exclude the pool surface, water, surrounding floor and unrelated borders. Do not use the pool or the whole image as the target box. If the white outline cannot be distinguished, omit the target and describe the uncertainty. Never claim arrival.

建议先保留 `kind="lower_half"` 的解释及现有数值作为**待重拍验证基线**：width 0.6、height 0.25、lower coverage 0.3、centre tolerance 0.12，不把它们宣布为世界 v2 定值。现有 `lower_half` 的 coverage = `bbox宽 × bbox与画面下半部交集高度 / 0.5`，不是“目标像素分割面积”。收紧 bbox 后 coverage 和宽高通常下降，既可能消除错误到达，也可能令真实到达不可达；不能未经新参考图测量就补偿性降低阈值。

保护区跟随较小的正确 bbox 外扩 32px，可减少错误大框保护的无关动画，但同样减少覆盖范围。必须验证菱形前方通路、`forward_clearance` 与恢复区域证据仍存在，不用提示变化代替通路安全验证。

## 3. 世界 v2 变更清单（待批准、待现场取证）

| 项 | 提议 | 尚缺证据 |
| --- | --- | --- |
| G1 | 世界 v1 标定、到达与提示不变；v5 声明接纳部分交叠 | 仍需 v5 配对静止/移动复验，不能直接宣称四次任务成功 |
| G3 | 新的激活前到达参考；候选 box 0.62×0.65；提示如第 1 节 | 新图路径与 SHA256 **null / 待重拍**；位置、朝向、是否稳定停留、激活前后对照和误判率 |
| G5 | 新的白描边菱形参考；提示收紧；lower_half 数值重新测量后定夺 | 新图路径与 SHA256 **null / 待重拍**；正确 bbox、coverage 分布、可达性、池面大框负例 |
| G2/G4 | 暂无世界规则变更提议 | 仍需五组统一复验，不能沿用几何干净就当导航成功 |
| 20 项清单 | 评估 G3/G5 四次各任务的出发/朝向/到达引用是否需更新；不得静默重排或删除失败任务 | 最终完整任务清单、标定源摘要、新世界摘要、用户批准 |

参考图摘要不填零、不借用旧激活态图的哈希冒充新图。将来发布须新版本、完整契约/清单和新摘要，不能只替换世界 v1 的某个图或字段。

## 4. 本轮已准备的离线路径

`live_navigation/world_proposal.py` 提供 `WorldV2Proposal` 与 `load_world_proposal(path)`；仅接受 `version="vrchat-home-live-v2-proposal"`、`status="PROPOSAL_NOT_APPROVED"`、当前世界 v1 的 source digest，且只涵盖 G3/G5。当前草案要求 `reference_state="PENDING_FIELD_CAPTURE"`、`reference_sha256=null`；有真实新参考图后须进入下一步发布契约，不能在此类型里伪装“已完成取证”。

`evaluate_box(group,bbox)` 只计算草案的尺寸/居中条件，返回布尔值供离线比较；不创建可交给生产入口的世界对象、不生成 SUCCESS、不控制角色。单元测试通过临时文件验证提案加载、观测宽高与负例；生产 `load_live_world()` 仍只接收并校验世界 v1。当前没有生产世界 v2 加载开关，也没有新的世界文件落地。

下一步：独立复核提案逻辑及 v5 → 现场重拍 G3/G5、收集正负样本 → 用户决定规则/提示 → 单独发布世界 v2 与契约/摘要 → 再验证延迟与固定 20 次任务。不得将本提案的建议数值写入已验收能力。
