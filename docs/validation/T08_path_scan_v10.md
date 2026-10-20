# T08 stability v10：缺前进clearance时的有界原地扫描候选

日期：2026-10-20；实现：Codex；交接：TASKS第115节。

**这是候选提案，不是冻结，不是实机验收。** `status=REQUIRES_FIELD_CALIBRATION`，schema13独立；任何live采集、供应商或控制器资源前拒绝。本轮没有启动live、ARM、付费推理、真实输入或密钥读取，现场仍稳定性v9＋世界v3＋dry-run。

## 1. 现场依据与范围

TASKS第113节：world v3后G2-2/G4-1目标置信度0.72、七个识别谓词全真，稳定性6/6通过；各3个决策都请求前进，但没有`forward_clearance`，连续等待后约19.7s因`PATH_UNSAFE`结束。识别门槛和像素噪声已不是这两个样本的主瓶颈。

第114节的clearance提示路线不采用：G2-2输出改善、G4无效，障碍/遮挡帧引入误报。该节表述有统计不一致：清单列clearance率10.7%、几何合格率7.1%，后文又写3/28几何合格；本提案不把它们当成相同的已核实比例，也未重新调用模型。但任一非零危险误报已足以支持不采用该提示，本轮完全不改提示或clearance规则。

v10只增加一项行为：目标已可信且合法前进只因clearance被拒时，有限地执行已有yaw短动作，换视角后重新取证。它不允许无clearance平移，不把yaw不需要clearance解释为物理安全保证，不新增自动续租或ARM。

## 2. 显式策略与摘要

新增`stability_v10.json/.sha256`，版本`local-rgb-stability-v10-candidate`。相对冻结v9仅修改version/status并增加以下完整块；其余键和值逐项相同：

```json
{
  "enabled": true,
  "trigger_denied_attempts": 3,
  "max_scans_per_task": 4,
  "yaw_step": 1.0,
  "duration_ms": 200,
  "alternate_direction": true,
  "initial_direction": 1,
  "reset_path_waits_per_scan": true,
  "post_scan_requires_new_observation": true,
  "trigger": "recognized_forward_denied_only_by_forward_clearance",
  "exhaustion": "unchanged_path_wait_limit_PATH_UNSAFE",
  "post_scan_evidence": "new_inference_source_after_finished_with_both_checkpoints"
}
```

新文件字节SHA256：`437849cfee57cc157cc01cb76a0bc56eb8ab5eade7510a31bff47d4a5c14bff3`。sidecar使用小写摘要＋CRLF（66字节），`.gitattributes -text`固定字节口径。加载器严格比对v9主体与整个显式块；缺字段、额外字段、类型伪造、私改阈值/幅度/上限/状态均拒绝。当前只接受候选状态，不预先开放冻结入口。

yaw步长±1.0、200ms必须等于已有cautious/field运动包络；没有新幅度或混轴。初始方向+1，随后-1、+1、-1；四次总yaw执行时间最多800ms，仍各自到期归零。

## 3. 触发、上限与耗尽流程

1. 沿用原规划器产生请求；目标七谓词全真，请求是纯正向前进，原包络与max_actions均通过，只有`forward_clearance=false`导致审计拒绝，才算一次合格拒绝。无目标、低置信、侧移/recovery缺证、混轴/越界或动作预算耗尽不触发。
   “唯一失败项”按该轴的requires判定：前进不额外要求recovery_clearance；侧移请求因recovery_clearance缺失被拒时绝不转入扫描。
2. 每次计数绑定不同的当前观测，重复同一观测不能凑够次数；非合格决策中断连续计数。前两次合格拒绝仍派发100ms wait。
3. 第三次合格拒绝且扫描额度剩余时，替换为一个独立纯yaw动作。该yaw重新走原`audit()`，要求识别通过及`yaw_requires=[motion_envelope,max_actions]`；运行时还会再次审计、校验运动包络、检查权限、绑定请求，才经原IPC提交。
4. 扫描占用一次正常动作名额，不叠加平移。将path_waits和本轮连续拒绝计数清零，但不清零全任务扫描次数、租约/epoch、unknowns、恢复预算或任何控制状态。
5. yaw完成后调用原`observe(after_ns=finished_at_ns)`：严格更新的采集、两对预热、准入预检、编码/推理、after_inference；下一次提交前仍有before_submit。旧参考可参与只增加拒绝的预检，不能提供扫描后的语义/clearance。新模型推理源本身也须晚于yaw完成。
6. 新观测验证后记录是否取得clearance。取得并不保证下一步一定前进，仍按新场景及完整门禁决策；未取得则重新累计最多三次合格拒绝。新的预算/租约/急停/失焦失败仍立即停止，不能为了完成扫描次数忽略它们。
7. 四次扫描耗尽后不再扫描，回到既有path_waits逻辑；继续三次拒绝达到原limit=3，以`PATH_UNSAFE`结束。理想化全缺证闭环的扫描决策号为3/6/9/12，第15次拒绝终止；最后一次终止决策不派发动作。中途也可能先撞30s租约、180s任务、40动作或其它原门禁，四次不是保证可执行完的额度。

扫描技能标签为`scan`：衡量的是重新取证，不冒充前进或朝目标居中进展，避免把主动换视角误判成原纠偏技能的无进展。原目标识别、到达几何/连续性与自动SUCCESS语义不变；有hazards/uncertainties仍记录且不自动SUCCESS。

## 4. 报告与重算

新装配键`stable-inference-v10`，独立schema13；采信仍`target-path-v8`，像素证明复用v9逐框100ppm实现。schema12及更旧版本拒绝扫描证据，不能把v10改个schema号当成旧版本。

- `acceptance_decision.audit.path_scan`：eligible、新观测标记、连续拒绝数、triggered、固定触发原因、序号、方向、累计次数、耗尽标志、完整yaw审计。原audit仍保留“请求前进不许可”，实际`dispatched`记录纯yaw，不能把前进假写成被放行。
- `navigation_feedback.evidence.path_scan`：序号/方向、action_id、扫描前后观测ID、fresh_observation、clearance_acquired。
- 对应事件`path_scan_triggered`、`path_scan_observed`，与上述决策/反馈时间及字段逐项一致；没有新增控制能力或控制结局码。

若yaw后因急停、租约失效或推理失败而未形成新观测，报告保留触发事件及实际动作结局，不伪造`path_scan_observed`或把未知写成`clearance_acquired=false`。只有完成新观测验证后，该布尔值才表示本次观测是否提供合格clearance；缺少反馈时不能继续决策/平移。

校验器从世界/策略、按时间登记的观测、实际请求、动作与结果重放同一有限状态机；不相信报告计数器或布尔值。重算触发、交替、名额、path_waits/停止状态、yaw审计、新观测验证及clearance；缺事件、额外事件、缺字段、提前扫描、伪造方向/次数、混轴动作、错误schema/声明live均拒绝。还核对扫描反馈对应的推理源严格晚于yaw完成，随后由通用校验器继续复验两个检查点、预热与逐框像素证据。预检不能替代执行证据。

这些是结构一致性重放，不是报告数字签名，也不能证明真实画面中的地板安全。

## 5. 冻结前装配阻断：控制预算映射（本轮不修）

`control/service.py`的180s映射枚举目前只列v6/v7/v8/v9，未列v10。依据本轮`control/`字节不动的约束，**没有顺带修改它**。真实dry-run控制子进程（始终DISARMED、无消息）回归明确揭示：v10会走既有30s兜底，`control_budget.task_timeout_ms=30000`；不能声称它已接入180s，也不能用内存core默认180s掩盖。

候选运行时/策略仍严格180s，schema13报告仍要求180s且拒绝把30s账本冒充完整报告；本轮内存闭环直接使用既有180s core构造预算，保留30s基础租约，无续租。入口与直接运行时构造均拒绝v10 live，候选loader也拒绝FROZEN状态。

因此**冻结前必须另案授权预算装配修正、实进程回归与独立复核**；仅改status/sidecar不足以启用v10。当前未切配置，现场v9的180s映射及world v3完全不变。这是明确的候选后续阻断，不是可忽略的实机验收缺口。

## 6. 风险与现场复验清单

原地yaw可能改变身体/头部朝向，引入遮挡或失去目标；它不等于安全路径探索。+/-交替可能在两个视角间来回，而非覆盖更广视野；4次上限防止无限循环，但不能保证取得clearance。每次重新推理增加成本和延迟；现场约3–4s推理时可能先耗尽30s基础租约，只有操作者本机Ctrl+F9可续租，绝不自动续租。

模型仍可能误报clearance；hazards不否决运动的既有风险没有消失。本提案保持原几何与conf≥0.9，不采用第114节的提示。识别门槛仍来自world v3（G2/G4=0.7，其余0.85），0.55不采信；yaw后目标丢失仍消耗原未知预算，不能盲扫。

另案解决预算映射并经独立复核、用户冻结后，才可人在场验证G2-2/G4-1换视角是否改善clearance、转向是否造成目标丢失、交替是否反复回到死角、G1/G3/G5遮罩/到达交互、真实耗时与人工续租/急停优先。固定20任务与人工到达确认未启动，本轮不能证明≥16/20。

## 7. 验证记录

新增纯内存边界、闭环与报告伪造回归；未知设计/工厂拒绝用例从新占用的v10顺延到v11，原拒绝语义不变。v1–v9策略/sidecar、世界、提示、control/和configs/做开工SHA256对照。四项门禁、只读计划、具体文件清单及不变项结果完成后写入TASKS第115节。

最终四项门禁均exit 0（项目虚拟环境，日志`.runtime/verify/t08_v10_{pytest,ruff_check,ruff_format,pip_check}.log`）：

```text
1501 passed in 551.89s (0:09:11)
All checks passed!
166 files already formatted
No broken requirements found.
```

定向回归59项通过；首轮全量唯一失败是旧schema/预热闭集断言未同步v10（1500项通过），同步后单项通过，再完整复跑得到上述1501项全绿；首轮日志另存`pytest_initial.log`。没有修改生产行为来迁就测试。

默认只读计划：`.venv/Scripts/python.exe -m vrc_agent navigate-live --task G2-2 --config configs/navigation-live.toml --world-version v3`，exit 0、`mode=plan-only`、`sends_live_input=false`、世界摘要`1da677140aad5a76920a7ad5cd88a5ffc1899300062cb852392b9483067f19f7`；完整输出同前缀`plan.log`。

116个既有生产/config文件对照中，仅10个授权源码变化；冻结策略18文件、全部世界、原skill/七谓词/clearance/提示、control/及configs/逐字节未改。新策略/sidecar摘要核对通过，现场仍v9＋world v3＋dry-run。`git diff --check`通过；22个改动文件与验证记录见TASKS第115节及同前缀`manifest.json`、`invariants.json`。GraphFlow当前不可调用，未伪称已更新图谱。第5节冻结前预算装配阻断仍然存在，离线门禁全绿不改变该结论。
