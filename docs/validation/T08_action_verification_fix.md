# T08 pilot5 动作验证诊断（F-F）与 F-A 评估

负责人：Codex；2026-10-18。实现方离线诊断与交付，不是 ADR-009 独立复核或实机验收。T08 仍 IN_PROGRESS。

## 1. 先复现，再修改

原始本地报告：`.runtime/live/G2-1-pilot5/navigation-live/G2-1-a38cd371b0244584b23aff2475f2718c.json`，SHA256：
`6846d2cedcbba0aa4b24da72b5853c3ac4bcf1a4d9ef1d4acc257bb10063b180`（本轮实算一致）。没有修改/覆盖该报告。

**产品代码修改前**的探针输出保存在 `.runtime/verify_ff_before.log`：从原 `verify()` 的 AST 提取 18 个 AND 条件，独立求值打印，不依赖短路行为；随后调用原方法，复现 unknowns 1/2/3。修正后的可重复入口：

```powershell
.venv/Scripts/python.exe -m pytest -q -s tests/unit/test_navigation_verification_diagnostics.py::test_pilot5_print_every_predicate_and_reproduce_unknowns
```

该测试使用 `tests/fixtures/pilot5_verification.json` 的脱敏结构化元数据，创建 2160×1440 BGRA 合成内存帧。没有恢复原始画面，也没有重跑像素稳定性比较或真实模型。

复建边界：session/epoch 来自动作与稳定性证明；captured_at、实体、整体置信度和危险/不确定性存在性来自报告。可见窗口与 frame_ref 来自已经通过的 `capture()` 前置保证，引用为合成引用；`generated_at_ns` 以紧随构造的 observation 事件 at_ns 作上界代理（生产构造时读取同一单调时钟）。原报告没有保留实际窗口/引用/生成时刻，不能声称逐字恢复这些字段；但结果已含两个证据观测 ID，是原 `valid` 为 true 的直接代码路径证据。危险/不确定性正文用存在性占位符替代。原事件只记录导航相关实体，不是完整模型实体列表。

## 2. 每条布尔条件

三列分别对应动作依据 000005→验证 000011、000011→000017、000017→000032；T/F 为实际离线求值输出。

| 谓词 | 005→011 | 011→017 | 017→032 |
|---|---|---|---|
| result_completed | T | T | T |
| result_ok | T | T | T |
| result_action_matches | T | T | T |
| result_session_matches | T | T | T |
| action_verifiable | T | T | T |
| result_finished | T | T | T |
| observation_after_finish | T | T | T |
| capture_monotonic | T | T | T |
| observation_unseen | T | T | T |
| observation_new | T | T | T |
| window_visible | T | T | T |
| frame_bound | T | T | T |
| session_consistent | T | T | T |
| epoch_consistent | T | T | T |
| scene_observation_bound | T | T | T |
| scene_generated_after_capture | T | T | T |
| action_before_bound | T | T | T |
| scene_before_issue | T | T | T |
| scene_confidence_known | T | T | T |
| **scene_confidence_sufficient** | **F** | **F** | **F** |
| **scene_no_hazards** | **F** | **F** | **F** |
| **scene_no_uncertainties** | **F** | **F** | **F** |
| unique_bound_confident_target | T | T | T |
| target_timestamp_current | T | T | T |

后六项是原来 `target()` 与 last_seen 的隐含门禁，现在显式诊断。场景置信度依次 0.60 / 0.75 / 0.60，均低于目标门槛 0.85；实体置信度 0.90 不等于场景置信度。原结果 evidence_observation_ids 已有前后两个 ID；只有基础 `valid` 为 true 才会追加后一个 ID。因此第86节“其余基础门禁之一失败”的推断被证伪，`valid_target=false` 不等于基础 valid=false。

`build_scene()` 的场景置信度是**所有保留实体**的最小置信度，source 事件却仅展示目标/clearance：低于 0.90 的整体值与还有未展示实体一致。缺少原模型完整载荷，不能断言是哪一实体、哪句危险/不确定性导致，不能声称模型误报。

结论：不是时间顺序/观测派生失效，而是安全采信拒绝被原反馈合并为“目标不可见”，形成可观测性缺口。保留 UNKNOWN→TARGET_UNOBSERVED，不能删除危险/不确定性或把整体置信度替换为目标置信度来让试点通过。

## 3. 修复范围与契约

- 原 18 条 AND 改为具名 bool 字典后 `all()`；finished_at=None 的独立诊断有空值保护。其余条件原样保留。
- `target()` 的实体筛选和场景门禁提取为共享函数；仍要求场景置信度达标、无危险、无不确定性、唯一且证据绑定的高置信目标。
- `NavigationSkill.last_evidence` 新增 `diagnostics_version=1`、`verification_predicates`、`failed_predicates`。沿现有 `status()`→`navigation_feedback`→report.events 路径持久化，每次动作结束都记录，不只记录最终失败。
- 谓词名为代码登记闭集，无动态类名、异常文本、危险正文或模型字段名回显。不是新的结局码；`TARGET_UNOBSERVED` 保持不变。
- 外层 schema9(v6)/10(v7) 不改：增加的是可选嵌套诊断 v1，不是新增执行证据。校验器允许旧反馈缺省，但新诊断必须版本/类型/闭集/false 列表/valid_target 内部一致。它不提供数字签名，也不能证明被整体伪造的 bool 来自现场，不能取代稳定性/动作/到达证据校验。
- 不改到达逻辑：连续两个新观测、相同 session/epoch/track、几何到达才可 SUCCESS；未到达的合格目标仍 UNKNOWN。

## 4. F-A：证据、未知项与可测方案

已有事实：5 次 inference_source，仅 obs-000015 有 forward_clearance，框 `[0.05,0.66,0.95,0.99]` / conf=0.90，**满足几何与实体置信度门槛**。但该场景 confidence=0.75 且 has_hazards/has_uncertainties 都 true，`target()` 仍拒绝。3 次 wait 是目标本身未通过安全采信后直接规划的 wait；path_waits=0 不支持“先提出前进再被缺少 clearance 降级”的解释。

| 方向 | 现有证据 | 当前可得结论 |
|---|---|---|
| 提示词 | runtime-clearance-v1 已要求检查地板、明确几何、正反例；同时目标段有 Find exactly the landmark | 目标偏向可能降低地板召回，尚无配对试验，不能确定因果；不在本轮改提示 |
| 温度 | 当前现场配置未覆盖，PerceptionConfig 默认 0.0，provider 请求显式传 temperature | 没有“温度太高”的证据；当前配置不能替代历史请求快照 |
| 输出上限 | 当前默认 max_output_tokens=1024，思考关闭；适配器遇 finish_reason=length 会拒绝 | 这5个已解析成功载荷不符合已知 length 截断路径；报告只有聚合10931 tokens，缺逐次 output tokens/finish_reason，不能完全排除模型在预算内主动省略 |
| 场景/保守性 | 五次均有危险和不确定性；场景置信度0.60或0.75 | 当前硬阻断已证实；未保存原图/正文，不能判断真实障碍还是过度保守 |

**后续对照提案，未执行、未定案**：用户另行授权保留固定私有帧后，选20张（明确开阔地板/真实障碍/遮挡或深度不明各有覆盖），人工按原几何与可见性标注；固定图像字节、JPEG q85、模型版本、禁用思考、temperature=0、1024 tokens。先做基线提示与“先独立检查目标和通道、仅有真实证据才输出”的候选提示配对，每图每条件3次；不要求模型强行输出地板框，也不要求移除真实危险。

记录：目标召回、clearance 提及率、几何合格率、场景可采信率、**危险场景误放行数**、wait 的首个/全部拒绝谓词、逐次 tokens/finish_reason/延迟。由人工盲评危险与不确定性是否有画面依据。先定位遗漏与保守拒绝各自占比，再决定是否单独测2048 tokens；若仍需测温度，以0/0.2独立配对，不能同时换提示与额度。按固定样本逐项报告，不用少量召回上涨宣称安全等价或实机通过。危险误放行须为0，任一伪造/扩框均退回；所有几何与 conf 门槛保持原值。

不改变整体 scene/hazards/uncertainties 门禁。若未来拟将无关实体或一般描述与导航安全分离，必须新版本显式协议、证据作用域与反例复核，不能在本次诊断暗改。合成安全场景测试已证明现有门禁可满足，并非 live 设计上必然不可达。

## 5. 验证与未改清单

针对性回归：`35 passed in 4.70s`。覆盖24项谓词逐项反例、pilot5三次拒绝、未到达不得成功、两帧到达正例、报告诊断篡改拒绝、旧反馈兼容、内存 runtime→报告事件持久化与结束无活动动作。

完整门禁（依次使用项目 `.venv/Scripts/python.exe -m pytest -q` / `ruff check src tests` / `ruff format --check src tests` / `pip check`），原始输出：

```text
1249 passed in 219.74s (0:03:39)
All checks passed!
153 files already formatted
No broken requirements found.
```

原始文件分别为 `.runtime/ff_pytest.log`、`ff_ruff.log`、`ff_format.log`、`ff_pip.log`。`git diff --check` 通过；配置、控制/传输、世界及 v6/v7 JSON/sidecar 定向 `git diff --exit-code` 无变化。没有修改第82节/T13提案。

默认 dry-run：`navigate-live --task G2-1 --config configs/navigation-live.toml --world-version v2` 输出 `mode=plan-only`、`sends_live_input=false`、suite_digest=`b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0`；完整输出 `.runtime/ff_dry_run.log`。

未改：v1–v7 策略 JSON/sidecar、全部阈值/遮罩/保护语义/预热/检查点；世界 v1/v2/提示词；live 配置仍 dry-run/v6；30秒基础租约、人工续租、急停/epoch/TTL/pending_releases/到期归零。没有启动 live、ARM、付费调用、真实输入或读取密钥。

- v6 JSON SHA256：`e46e0c2816c6f512fbd4fd5e5c60ff1ef3fc4d0560e0e8b29d3b6a6315caa513`。
- v7 JSON SHA256：`fbebd9cbfd6d11988dd783ea83b7a4e8ef38c1b3ce236e435dcd20617cb2caf7`。
- 世界 v1/v2 语义摘要仍 `dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335` / `b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0`。

GraphFlow：应用其 skill，但本会话工具清单无 GraphFlow，context/plan/index 均未执行；依据 AGENTS 的不可用回退约定定点读取，不伪造结果。
