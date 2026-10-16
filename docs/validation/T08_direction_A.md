# T08 方向 A：慢推理与本机稳定性证据（2026-10-16）

实现者 Codex / root。对应 TASKS 第 34 节。代码与离线验证已实现；尚未独立复核、未现场标定、未执行真实 20 次任务。T08 保持 IN_PROGRESS。

## 设计与范围

新增 `StableNavigation`，沿用 `SupervisedNavigation` 的控制进程、权限检查、短动作校验、结果验证和结束暂停。旧路径默认不变：`navigation_design="legacy"`，单次推理仍不得超过 1000ms。`Runtime` 的 ReplayController 检查和控制进程、热键代码没有修改。

新策略是独立配置 `live_navigation/stability_v1.json`，版本 `local-rgb-stability-v1-candidate`，同目录 SHA256 加载校验。世界任务清单、到达阈值和世界摘要不变。选用新路径的配置字段为根级 `navigation_design="stable-inference-v1"`；供应商关闭思考使用 `[perception].disable_thinking=true`，不接受任意 extra_body。实际 `configs/navigation-live.toml` 未改。

候选策略 **REQUIRES_FIELD_CALIBRATION，生产入口在创建控制器/供应商前拒绝**。原因是无法从已有静态截图证明 HUD 排除区在真实运行时始终只包含不参与导航的界面。独立复核后，现场先采集静止序列核对遮罩，再由用户选择新冻结版本；不得直接把本候选状态改成 FROZEN 或原位改摘要冒充新版本。现有 `--confirm-calibration` 不绕过候选策略拒绝。测试直接装配仅使用 dry-run 控制器或内存 core。

## R1：局部一致性与证据链

1. 已归零状态下采集新观测，校验 session/epoch、唯一 ID、单调时间、可见窗口、尺寸和原始 1500ms 时效；IPC 登记后保留不可变帧。
2. 在线程中编码 PNG；异步等待供应商，仍通过 30ms 轮询授权。结果引用必须匹配请求与原观测 ID；经既有 build_scene 严格构造原始场景。
3. 重新采集新观测。比较推理帧与当前帧的全部 RGB（忽略没有视觉意义的 BGRA alpha），不缩图、不用平均差。保留区域一像素变化也拒绝；任一保留区域全黑拒绝。
4. 仅相等时从原始场景派生当前场景。新 ID、新采集时间来自实际新采集，不刷新旧 Observation。报告同时保留 `inference_source`、派生 `observation.derived_from` 和 `stability_proofs`；实体引用的派生关系可复验。
5. 规划前再采一帧复验（`before_submit` 检查点），通过后仍绑定前一步那份**未改时间戳且仍新鲜**的观测；额外帧记录 `validates_observation_id`。然后最多 200ms 本地技能决策、权限检查和 bind_request，控制层继续按原年龄拒绝过期请求。检查点是离散的，不能证明两次采样之间或最后采样之后没有瞬间变化。
6. 任一稳定性拒绝，旧场景不发送动作，最多三次重新采集/推理。执行前复验也最多三轮；整体仍受任务和租约截止期限制。耗尽为 SCENE_UNSTABLE 并暂停。每个动作结束归零后仍须新推理、新观测、独立稳定性证据，连续两次到达方可 SUCCESS。

候选遮罩只排除 2160×1440 图像左下 `[0,1296,86,1440)`。这是**用于离线验证机制的工程候选区域，不是声称真实麦克风/HUD 在此位置**。区域之外的 HUD/时钟动画会保守拒绝；不会自动扩大遮罩以通过测试。任何模型实体框与遮罩重叠也拒绝。遮罩内新物体不可被该指标发现，因此只有现场证实该区域始终为不承载世界内容的 HUD，才可能冻结使用；否则发布新版本或采用其他本地验证器。

对抗范围：合成单像素物体/角色进入、大片镜面变化均拒绝；遮罩内图标变化可接受；黑帧拒绝。相同像素不证明模型语义正确、世界没有画外移动、捕获源没有冻结，也不证明截图内容具备真实物理含义。已有 confidence/uncertainties/hazards、目标/通路证据继续有效；稳定但错误的高置信模型描述仍是既有信任边界。

## R2–R4：时间、取消与权限

冻结策略显式记录：推理预算 **8000ms**、任务 **30s**、动作 **40**、每轮最多 **3** 次推理、观测时效 **1500ms**。8 秒覆盖第 33 节所述秒级延迟的工程预算，不是本轮真实延迟测量。推理超预算为新结局 `INFERENCE_TIMEOUT`；旧路径 `MODEL_LATE` 不改语义。编码/稳定性计算各有 1000ms 截止期，分别为 IMAGE_ENCODING_TIMEOUT/STABILITY_TIMEOUT。

推理预算只允许在停止状态等待，不延长运动、租约或观测时效；动作仍为白名单单轴 200ms。30 秒租约下以 3.5 秒一次推理计算，初次加每步后推理使动作容量约为 7 次（还要扣采集/动作/比较开销）；40 是上限，不能承诺会用满。现有标定没有可靠的单步世界位移数据，因而不能推算 16/20 现场成功率。租约不足按失败处理，本轮未增加租约或续租；若现场确需改变该边界，返回用户决策。

取消保证沿用 CancelToken 的协作语义：下一次授权轮询发现急停/暂停/epoch/lease 变化即取消协程等待，迟到返回不进入控制链。**标准库 urllib 阻塞线程不能被 asyncio.cancel 强行终止，不能承诺远端停止运算或停止计费**；线程由 HTTP timeout 收尾。取消中的已发送调用现在计一次未知费用，迟到返回不重复入账。报告不能声称这些请求是已知零费用。30ms 是轮询间隔，不是 Windows/Python/IPC 的硬实时上限；独立控制进程继续提供热键和 watchdog 保障。

关闭思考只发固定 `thinking:{"type":"disabled"}`；False 时没有该字段。空内容和 finish_reason=length 仍为 MODEL_MALFORMED_RESULT。没有自动 ARM/RESET，没有新动作，没有修改传输出口。

## R5：报告兼容

新报告 schema_version=2，附 navigation_design、完整策略及摘要、逐次 proof。单份汇总先校验新证据的来源、时间、摘要格式、黑帧/相等判定、派生实体关系和动作绑定；缺字段、冲突或损坏整份计 invalid_files，不进入使用量或成功率。旧 schema=1 报告维持旧规则；不能仅携带新证据而省略设计声明。

usage/hourly 仍走 T10 原函数；来源为 false、缺金额、未知 token/价格、缺失时间等口径未放宽。未知价格的内存 HTTP 示例费用保持 unknown。摘要是本地一致性检查，不是数字签名；没有保存原始图片，汇总不能独立重算像素。真实 usage/hourly 和实际账单核对仍待现场。

## R6：复现与后续

`python -m pytest tests/integration/test_stable_navigation.py -q`：可控时钟 + 内存 HTTP + 真实 control core 验证 3.5 秒路径；另有真实 spawn 子进程/IPC 的 supervised-dry-run 报告。合成画面和模型输出不是实机导航成功证据。测试输出独占 `.runtime/verify/t08_direction_a-<session>.json`，不覆盖前次报告。

下一步由不同执行者按 ADR-009 复核本实现，重点证伪遮罩盲区、离散检查窗口、报告派生链、取消费用、租约边界和新旧路径隔离。之后才进入现场稳定性标定与用户选版；再做真实延迟复测和固定 20 次任务。本轮没有读取密钥、付费调用、live、人工 ARM 或真实输入。

GraphFlow 工具本会话不可调用，context/plan/index 未执行。四项门禁实跑结果及最新报告路径见 TASKS 新交接节。
