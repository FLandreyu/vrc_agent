# T08 稳定性 v5 候选独立复核（ADR-009，2026-10-17）

复核者：DSH / Flash；v5 实现者：Codex / root。对象为 TASKS 第 56/57 节与 [T08_stability_v5.md](T08_stability_v5.md)，策略 `local-rgb-stability-v5-candidate`，SHA256 `1cc4287a4c944ae898012e016ee21b591041f1eb7000041844312c3ec02de21c`。基线提交 `3ae0e40`（工作区干净）。

本轮独立编写并运行探针 `.runtime/verify/v_t08_stability_v5_review.py`，**不导入实现者的 tests/夹具或自测脚本，不修改产品源码、策略、配置与实现者测试**。采用内存合成 BGRA、自建供应商桩与真实 `ControllerProcess` **dry-run** 子进程；仅 dry-run 控制器使用测试 ARM，没有启动 live、没有付费请求、没有读取密钥、没有采集现场图像或发送真实输入。GraphFlow 本会话可调用但只返回 symbol anchor，未作为证据，也未伪造 context/plan/index。

## 0. 结论

**第 56/57 节与 v5 文档的声明主体本轮未能证伪；没有发现阻断项，也没有发现需要退回修正的 F 级问题。** 本轮同样**不是**“独立复核通过”式的实机验收：v5 仍是候选，`REQUIRES_FIELD_CALIBRATION` 未变，T08 保持 IN_PROGRESS。

- 108 项独立检查 0 失败（日志 `.runtime/verify/t08v5_review_final.log`，结构见第 4 节）。覆盖：版本与不变量、保护语义三层（比较器/运行时/报告复验）、来源与报告篡改矩阵、旧版隔离、配置传播、提案隔离、第 54 节模型框表逐格重算。
- 第 54 节 6 条现场模型框的整数审计表（`entity_masked_px` / `protected_masked_px` / `protected_visible_px`）由本探针走产品比较器逐格重算，与文档一致；G3 到达仍为 `NO_TARGET`，未被合成框填补。
- 少数值得记录的观察（非阻断，见第 2 节）：保护矩形外扩压入遮罩的实体按声明放行（审计暴露其被遮罩面积）；v4 与现场到达框的结构性冲突被独立复现（不得回退 v4 用于现场）；面积盲区在极端稀疏情形（1 像素 Δ3）的表现。
- 现场五组静止/移动、真实时延、G3/G5 重拍、用户选版与固定 20 次任务均未执行；本复核不能替代它们（第 5 节）。

## 1. 逐项对照

| 复核点 | 独立证据 | 结果 |
| --- | --- | --- |
| 版本、摘要与不变量 | A01–A04：v5 策略摘要按字节重算与 sidecar/文档一致；v4→v5、v3→v5 的键差异恰为 `version`/`metric`/`protected_mask_policy`；A06 全部版本状态仍 `REQUIRES_FIELD_CALIBRATION`；A07 未知版本拒绝；A09/A10 schema 表 6、预热版本集含 v5 | 未能证伪 |
| 旧版隔离 | A05：v1–v4 策略/ sidecar 与 `stability_v2.py`/`stability_v3.py` 相对 HEAD 无改动（`git diff` 判定，规避 CRLF 归一化误报）；A11b/A11c 世界 v1 与 v5 文件均已提交；D10 v4 部分交叠仍中止；D11 v1/v2/v3 未声明新语义、行为不变 | 未能证伪 |
| 遮罩外扩与保护承诺范围 | B05 完全被遮罩实体拒绝；B06–B08 部分交叠放行且审计含可见/被遮罩两部分；B09 被遮罩像素变化不可见（声明放行）；B10 同一变化移到可见保护区即拒绝；B16/B17 1 像素级可见缝隙放行；B21 完全遮罩实体不被另一实体可见面积掩盖；D07–D09 运行时同判据；B22 实体贴遮罩边界时保护矩形按 margin 压入遮罩（见 N1） | 未能证伪；N1 属声明范围 |
| 并集审计与几何 | B18–B20 两个交叠实体的并集不重复计数且小于逐框面积和；B25/B26 与独立 oracle 在含交叠矩形上逐格一致；B27/B28 零宽与超界矩形拒绝 | 未能证伪 |
| 面积与颜色阈值 | B23 实造 142412 通过、142413 拒绝（边界与文档一致）；B03 Δ2 不计变化；B24 黑帧拒绝；B01 单像素 Δ3 只计入面积（见 N3） | 未能证伪 |
| 报告与来源篡改 | C01/C02 交付工件摘要与完整复验；C03/C04b 工件的 5 项审计齐备、2 动作/5 证据/6 预热比较/schema 6/dry-run；C05–C10 审计字段增删改与浮点化全部拒绝（含五项字段各自缺失）；C11/C12 只改来源事件审计或把来源实体改成完全被遮罩均拒绝；C13–C18 schema/设计/策略摘要/`stable=` 翻转拒绝；C19 预热链、C20/C21 动作新鲜度拒绝；C22 dry-run 工件不按 live 计入；C24 审计矩形重复列出拒绝；C25 被篡改报告整份进 `invalid_files` | 未能证伪 |
| 已知自证边界 | C23 只改 RGB 摘要仍被接受（报告复验不校验像素，文档第 3 节已声明） | 已声明限界 |
| 运行时接线 | D01–D04 工厂 v1–v5、未知版本拒绝、基类只接受自身版本且在任何采集/模型/动作前拒绝、v2 类不得装配 v5；D05/D06 运行时审计与独立 oracle 一致；D12 observe/before_submit 均在取样前检查；D13 `derive` 在场景构造前检查；D14 生产入口经统一工厂；D15 候选状态检查先于装配 | 未能证伪 |
| 结局码表 | D16 扫描 `stability_v5.py`/`stable_runtime.py`/`session.py` 的大写字面量：未引入未登记结局码，沿用的 9 个码（含 `MASKED_SCENE_EVIDENCE`/`WARMUP_UNSTABLE`/`SCENE_UNSTABLE`/`STABILITY_TIMEOUT` 等）均在 `KNOWN_CODES` | 未能证伪 |
| 配置传播 | A12/A15 默认仍 legacy、live 配置未声明导航设计；A13 配置字面量接受 `stable-inference-v5`；A14/E01–E03 `disable_thinking`/`max_output_tokens=1024`/预算生效且请求体含 `thinking:{type:disabled}`、无图像字段 | 未能证伪 |
| 提案隔离（B 项） | E04/E05 合法提案可加载且状态为 `PROPOSAL_NOT_APPROVED`、不声称已拍参考图；E06 来源世界不符拒绝；E07 未重拍却填参考摘要拒绝；E08/E08b 只覆盖一组或重复组拒绝；E08c 状态不能声明为已批准；E09 提案模块无写文件调用；E10 生产入口不导入提案模块；E11 冻结世界文件相对 HEAD 未改 | 未能证伪 |
| 第 54 节模型框表 | F01 fixture 摘要 `260085330ad827eec33a187fa5ed2ad21924a27da5a199e2899118c84353fbe6` 一致；F02 记录来源探针摘要且声明仅回放模型框；F03 6 条记录的三元组逐格与文档一致（G1 到达 122368/138752/1586800，G2 到达 0/0/304180，G4 到达 0/0/53766，G5 到达 129536/145920/2447474，G1 起点 0/208/406922，G3 起点 0/0/442682）；F04 G3 到达 `NO_TARGET`；F05 部分交叠记录 v5 全部可接纳、v4 全部被拒（见 N2） | 未能证伪 |
| 四项门禁（本复核独立复跑） | `.runtime/verify/t08v5_gates.log`：`python -m pytest -q` → **972 passed in 133.27s**；`ruff check src tests` → All checks passed!；`ruff format --check src tests` → 131 files already formatted；`pip check` → No broken requirements found.（四项 rc=0） | 与实现方声明一致 |
| 交付工件 | `.runtime/verify/t08_stability_v5-0f9340fd159f432ea9a3e0c35059989f.json`，SHA256 `dba289b00c0d2bf6400c88e750d92de4c8c52a53e86c29d944186d83e91c8644`，通过完整复验 | 未能证伪 |

## 2. 观察与最小反例（均非阻断）

- **N1（声明范围内，建议写入现场口径）：b box 贴近遮罩的实体，其保护矩形有 `margin × 高度` 的部分落在遮罩内且不被验证，但实体仍被放行。** 探针构造 `bbox=(1400,100,1536,200)`（右边界恰在遮罩左界 1536，实体矩形本身不交叠）：实体被遮罩面积 0、放行；保护矩形为 `[[1368,68,1568,232]]`，其中 **5248 = 32×164** 像素在遮罩内。这与 v5 “只证明外扩保护区中未被遮罩像素”的承诺一致，且审计字段（`protected_masked_px`）如实暴露，但对现场核对的含义是：**贴着 UI 区 32px 内的目标，其有效保护范围小于名义 margin**。建议在修订任务书里明确“目标外扩保护矩形与 UI 区的交集不可验证”，现场放置目标时避免把到达参考框贴在遮罩边界。
- **N2（支持 v5 决策，同时限定回退空间）：v4 与现场到达框的结构性冲突被独立复现。** 同一批第 54 节模型框，v4（`reject_on_overlap`）在 G1 到达、G5 到达、G1 起点三条上抛 `protected region masked` 而拒绝，v5 三条全部接纳；G2 到达、G4 到达、G3 起点两版都接纳。因此：v5 的“改变承诺范围”不是文字妥协，而是现场几何的必要条件；**任何后续回退都必须同时改回遮罩或到达框，不能只把版本名切回 v4**。
- **N3（已声明盲区的极端表现）：零（或极少）实体时，少量高对比变化只按面积判定。** 单像素 Δ3 → `changed_pixels=1`、`protected_changed=0`、`stable=true`。文档第 8 节已把“旧的面积阈值盲区”列为遗留风险；本项给出该盲区在极化输入下的下界（1 像素），供现场把“面积阈值能拦住的移动体”写清为“面积 ≥5% 的移动体”。
- **非问题但需与实现方口径一致**：`protected_changed` 在全局超限时为 `null`（未评估），在未超限时为整数（B02/B23）；五项审计字段缺一即拒，且空 `protected_rectangles=[]` 是合法值（无实体），不是缺字段。

## 3. 已声明限界与独立性折扣

- v5 声明放行但本轮只能“未证伪”的：保护区外 <5% 面积的稀疏/小块真实变化、Δ≤2 的真实画面变化、遮罩内内容不可见、完全被遮罩外的极窄可见缝隙、RGB 摘要为自证字段。
- 第 54 节表来自**模型框回放**（`replay_scope=model_boxes_only_not_image_replay`），不是图像级重放；本轮重算只证明几何与审计整数口径自洽，不证明现场像素结论。
- 实现方自测数字（972 passed 等）本轮虽独立复跑一致，但**不能替代**现场证据；本轮所有检查都是离线、dry-run 或内存合成输入。

## 4. 探针、工件与门禁

- 探针：`.runtime/verify/v_t08_stability_v5_review.py`（自建，108 项：A 17 / B 28 / C 26 / D 18 / E 14 / F 5）。
- 结果：`.runtime/verify/t08v5_review_final.log`（`== 108 checks, 0 failed`）、`.runtime/verify/t08v5_review_results.json`。
- 门禁：`.runtime/verify/t08v5_gates.log`（972 passed in 133.27s / ruff / 131 files / pip check）。
- 策略与 sidecar 摘要：v1 `e9350468…`、v2 `5487965f…`、v3 `333739e3…`、v4 `e4dcb100…`、v5 `1cc4287a…` 全部按字节复算一致；世界语义摘要仍 `dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335`。

## 5. 未覆盖（不构成验收）

- 现场五组静止、移动配对、五组姿态保持、真实时延与帧率（v5 未做现场测量）。
- G3/G5 重拍、修订任务书（第 55 节）在现场的执行结果、用户选版与冻结。
- 固定 20 次真实任务与 ≥16/20 判据、接管/超时/卡住计数。
- T10 实机 usage/hourly 折算；v5 的观察链路在真实模型错误/漏报下的表现。
- 图像级回放与真实帧下的审计一致性（本轮为合成帧与模型框）。

## 6. 复现

```bash
.venv/Scripts/python.exe -X utf8 .runtime/verify/v_t08_stability_v5_review.py
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
.venv/Scripts/python.exe -m pip check
```

探针只读产品源码、策略、报告工件与 fixture；不写产品文件（仅写 `.runtime/verify/` 下的日志与结果）。

## 7. 下一步

1. 现场侧按第 55 节修订任务书重拍 G3/G5 参考图并做配对静止/移动复验与性能测量；把 N1 的“保护矩形与 UI 区交集不可验证”写入现场核对清单。
2. 现场数据回来后由用户选择冻结版本；**不得**在没有现场证据时把 v5 标为 FROZEN，也不得只改版本名回退到 v4（见 N2）。
3. 选版后执行延迟复测与固定 20 次任务（接管/超时/卡住计失败，至少 16/20），T08 方可考虑 DONE；T10 实机用量折算仍另案。
4. 本复核记录只覆盖 v5 候选与第 56/57 节声明；若实现方后续修改判据、遮罩或保护语义，须再次独立复核。
