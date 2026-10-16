# T08 稳定性 v4 修正件独立复核（ADR-009，2026-10-16）

复核者 Codex / root；实现者 DSH / Flash。对象为 TASKS 第 51/52 节与 [T08_stability_v4.md](T08_stability_v4.md)，策略 `local-rgb-stability-v4-candidate`，SHA256 `e4dcb100c6da68e360199593e90408806aa7753558650e4403e9497b2aefefea`，schema=5。

本次独立编写并实际运行探针，未导入实现者测试、夹具、旧探针；不改产品源码、策略、配置、依赖或实现者测试。采用内存合成图像、自建无网络供应商与真实 `ControllerProcess` dry-run IPC。仅 dry-run 测试核心 ARM，没有启动 live、操作本机热键、调用付费接口、读取密钥或发送真实输入。GraphFlow 本会话没有可调用工具，按项目回退约定定向读取，未伪造 context/plan/index。

## 0. 结论

**F1/F2 修正与原 N1/N2 处置的主体未能证伪，本轮离线修正件独立复核通过；发现两项非阻断校验瑕疵 N3/N4。**

- F1：原反例在 v4 比较器被拒，标准运行时在比较/派发前零动作中止；完整报告复验拒绝相交证据。对边界接触、单像素交叠、空保护区、伪造矩形、删改策略字段、来源实体与矩形不一致等攻击，没有得到新的有效动作绕过。
- F2：原有 `StableNavigation(..., policy_version="v4")` 路径在构造前拒绝；工厂与各代实现类一致，未知版本拒绝。显式调用基类初始化器构造 v4 的补测也没有得到零预热执行：预热阶段异常、零模型调用/动作、控制器 PAUSED。
- 原 N1 文档的 142413 边界和 408 对统计措辞已修正；原 N2 的浮点 retained_pixels 与 null 块字段，在完整报告复验中均拒绝。
- **N3**：底层 `verdict` 检查矩形类型/长度，却不检查有序、非空和画面边界；部分非法几何可直接通过。完整报告的实体框重算仍拒绝这些伪造矩形。
- **N4**：`schema_version=5.0` 可通过报告复验；没有发现 schema 2/3/4 与 v4 的跨版本混用放行。
- **本结论不是实机验收或冻结许可。T08 保持 IN_PROGRESS，v4 保持 REQUIRES_FIELD_CALIBRATION 并拒绝 live。** 五组姿态、遮罩覆盖、真实时延与固定 20 次任务没有执行。

## 1. 逐项对照与对抗结果

| 复核项 | 自建反例/证据 | 结果 |
| --- | --- | --- |
| 版本与摘要 | A01–A04：v4 完整摘要与声明一致；v3 完整摘要仍 `333739e324ccc30f63e0c1ec533290dbd572090c03ef0d2be85711239d4adb5a`；v1/v2 摘要前缀符合既有记录，完整字节摘要存 manifest；v3/v4 字典差异恰为 version、protected_mask_policy；世界完整摘要 `dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335` | 符合声明；默认仍 legacy |
| F1 比较器 | A05：原目标 `[1400,100,1520,200]` 的外扩 `[1368,68,1552,232]` 与遮罩交叠；无像素变化和 `(1540,150)` 高对比变化两种输入均 `protected region masked`；A06 相同输入在 v3 仍 stable | v4 不依赖观察到变化才拒绝；旧版本行为没有被偷偷修成 v4 |
| F1 边界/随机矩形 | A07：遮罩左侧、右侧、下侧分别构造“外扩刚接触”和“交叠 1px”；A12 用独立整数区间 oracle 对 30 组随机合法矩形比较 | 半开区间接触允许、实际交叠拒绝，全部符合独立期望。遮罩顶部位于 y=0，不存在画面内从其上方接近的情形 |
| F1 标准运行时 | B_runtime_margin：真实 dry-run IPC，1 次模型调用、3 帧/2 次预热、0 动作、MASKED_SCENE_EVIDENCE/PAUSED；B_runtime_touch：外扩刚接触遮罩的目标允许一次 motion，报告通过 | 原漏判路径关闭，干净边界没有被一律误拒 |
| F1 before_submit | B_before_submit_F1：在已构造运行时中放入相交的原始参考场景，并将 fresh_proof 换成不应到达的哨兵 | 比较前即 MASKED_SCENE_EVIDENCE，哨兵未调用；这是检查点单独注入测试，不冒充第二次完整导航 |
| F1 完整报告 | C01/C02：换相交矩形、隐藏矩形、反向矩形、删/改 protected_mask_policy；另同时改 inference_source 的目标 bbox 和 proof 矩形 | 完整复验全部拒绝；相交情形明确 `protected region masked`，隐藏/非法矩形由来源实体重算拒绝 |
| F2 基类/工厂 | D01–D05：四代类映射、所有 12 个类/显式版本错配、6 个未知版本值；检查 v4 实际继承 v2 预热方法 | 工厂一致；错配在缺少 client 参数也先抛版本错误，证明先于采集/模型/动作；未知版本均 ValueError |
| F2 显式基类初始化绕行 | 补充探针创建未初始化 V4 实例，显式调用 StableNavigation.__init__(..., policy_version="v4")，没有替换类的方法 | 动态方法仍进入 v2 预热；2 帧后缺 warmup_checks 而报错，0 模型调用、0 动作、source 关闭、PAUSED。非正常装配会报错，但没有得到原 F2 的执行绕过 |
| 原 N1 | 定向核对 TASKS 第 48 节、AGENTS v3 条目及 v3 文档第 2/3 节；A10 全量帧边界 142412/142413 | 边界与统计措辞已改；142412 通过、142413 拒绝 |
| 原 N2 | A08 覆盖 retained_pixels 浮点/bool/string/null，块键 null/0/false；C01 在实际运行报告上只改单字段再复验 | 均拒绝。changed/protected 的 bool、负数、浮点和越界计数也拒绝 |
| 预热回归 | B_runtime_unstable、transition、estop | 持续不稳：8 帧/7 对、零模型调用/动作、WARMUP_UNSTABLE；初帧过渡后恢复，报告通过；第二帧急停：零模型调用/动作、MANUAL_STOP/ESTOP。source 均关闭 |
| schema 隔离 | C03：四代各自的空动作正例 × schema 1–5（20 个组合）；C04 将真实 v4 报告冒充 v1/v2/v3；C01 缺预热、空引用、断链 | 只有各自 2/3/4/5 对应组合通过；真实 v4 跨版本均拒绝，预热链破坏拒绝。空动作正例只验证分派，不算导航成功 |
| 候选 live 拒绝 | E：四代全授权 live 参数下 provider/controller/capture 哨兵；D03：四个类的 live 标签构造检查（对象未启动进程） | 入口都在资源创建前因候选状态拒绝；四个类均 candidate stability policy 拒绝；没有实际启动 live |

## 2. 非阻断问题

### N3：底层保护矩形“合法”检查只覆盖形状和类型

- 位置：`src/vrc_agent/live_navigation/stability_v3.py:49` 起。当前要求 list、4 个严格 int，但未要求 `0 <= left < right <= width`、`0 <= top < bottom <= height`。
- 直接 `verdict` 最小反例：正常全零变化计数，分别换 `protected_rectangles` 为 `[[10,10,5,5]]`（反向）、`[[-10,0,10,10]]`（越画面）、`[[1,1,1,2]]`（零宽），均返回 true。覆盖整个画面的越界框则因碰遮罩被拒，不能据此声称几何校验完整。
- **完整报告对照**：把正常 v4 报告的 proof 矩形改为反向矩形，仍被 `protected evidence regions mismatch` 拒绝；实体本身受严格 bbox 契约约束，重算不会生成这些非法矩形。没有发现标准运行时/完整报告通过此路径产生权限绕过，因此列非阻断。
- 建议补齐几何检查，使比较器、直接 verdict 调用和完整报告的“合法矩形”契约一致；不要仅依赖调用方从 Entity 重算。

### N4：schema 5 的数值相等代替严格整数检查

- 位置：`src/vrc_agent/live_navigation/stability_report.py:121`。比较 `report.get("schema_version") != SCHEMAS[version]`，Python 中 `5.0 == 5`。
- 实测在正常完整 v4 报告上仅改 `schema_version=5.0`，复验通过；None、True、字符串 `"5"` 被拒。四代 schema 互换仍全部拒绝，不是跨版本证据隔离失效。
- 建议用严格 int 校验后再做版本映射，避免消费者对 JSON 类型的理解不同。本项不修改动作或来源证据，列非阻断。

补充说明：手工绕过子类初始化得到的 AttributeError 是错误装配的可诊断性问题，不等于模型能控制 Python 类。任意修改 POLICY_VERSION、替换工厂字典、覆盖 reference_capture 或删改策略对象属于可信代码改写，不在“模型仅输出 Proposal”的输入面内；本轮不将其冒充模型攻击或已找到的绕过。

## 3. 仍存在的限界与独立性

- 面积上限、容差、预热参数没有变化。保护区外的连续大块变化只要不超 5% 仍可能通过；Δ≤2 的真实变化、遮罩盲区、模型漏报/错误框与离散采样间瞬变均未解决。不能把本轮保护交叠修正写成通用语义安全保证。
- 报告是内部一致性审计，不是像素重算或数字签名。自洽篡改整份证据的真实性不在本机制保证内；本轮没有用它推断真实客户端成功。
- v1–v3 策略文件/摘要保留；共享 verdict 对 v3/v4 的 N2 严格性收紧已在修正文档中说明，不宣称旧版所有畸形报告仍兼容。
- 本轮复核者未参与 DSH 的 v4 实现；Codex 曾实现方向 A/v2 基底并做 v3 复核，因此独立性针对本次修正件，不宣称对所有共享代码从未参与。仍为同机同工作树，使用文件摘要锚定，没有 Git 提交锚点。
- 核对旧 Runtime 的 ReplayController 类型检查仍保留；本轮没有改控制进程/热键/lease/epoch、默认模式或真实 OSC 传输。

## 4. 工件与门禁

- 主探针 `.runtime/verify/v_t08v4_codex_review.py`（SHA256 `8b487a710aef2fc46fefa08e0c61311733be2bc558015acbf250c7104406e868`），输出 `.runtime/verify/t08v4_codex_probe.log`。148 条记录：**140 个条件检查、8 条诊断事实**，条件检查零失败；N3/N4 的诊断接受事实不计为安全通过。
- 主证据独占目录 `.runtime/verify/t08v4_codex_1792164882442659100/`：六份完整场景报告、`manifest.json`、`results.json`。results SHA256 `6c22a17df8987035c49b910fb6ed9665d54b4fae1c9471be36ac9d6d2bd4b877`。
- F1 `margin.json` SHA256 `63a83c37c34804f1fe5d1b0516c9eb9fff23c5d06209208e7d267fc2f0896891`；正常基线 `normal.json` SHA256 `4d6457671bcb768e7c3a785fcf303d32fed63d53f8cbfd30a095b7f55f492322`。正常基线把技能额度收紧为 1，用于证明 motion 和报告链路，并非导航成功率。
- 补充探针 `.runtime/verify/v_t08v4_codex_constructor.py`，输出 `t08v4_codex_constructor.log`；独占工件 `.runtime/verify/t08v4_base_route_1792164970822130000/result.json`，**1 个综合条件检查通过**（零模型/动作且关闭/暂停）。
- 合计 **149 条记录、141 个条件检查零失败**；未引用实现者测试的门禁绿灯来替代这些反例。manifest 覆盖本轮审查代码、四代策略与 sidecar、配置模型和依赖文件，探针结束及文档写入前复算均一致。

| 四项门禁（复核者实际运行，项目虚拟环境） | 结果 |
| --- | --- |
| `python -m pytest -q` | **937 passed in 180.81s (0:03:00)**；日志 `.runtime/verify/t08v4_codex_pytest.log` |
| `python -m ruff check src tests` | All checks passed! |
| `python -m ruff format --check src tests` | 126 files already formatted |
| `python -m pip check` | No broken requirements found. |

## 5. 未覆盖（不构成验收）

未采集真实客户端图像，未现场复验五组姿态、麦克风、镜面、UI/目标交叠和动静切换；未测真实供应商端到端延迟；没有执行固定 20 次任务。原始现场帧没有在本轮按 v4 重放；没有进行全平台/长期并发性能验收。标准例采用真实时间的 dry-run IPC，不能据此承诺现场每次比较满足 1s。

## 6. 复现

```powershell
.venv/Scripts/python.exe .runtime/verify/v_t08v4_codex_review.py
.venv/Scripts/python.exe .runtime/verify/v_t08v4_codex_constructor.py
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
.venv/Scripts/python.exe -m pip check
```

两条探针每次建立独占目录；重定向日志时使用新文件名，保留本轮证据与 manifest，不覆盖旧复核工件。

## 7. 下一步

1. N3/N4 可作为非阻断校验收紧另行处置，改后附回归与交接；本轮没有替实现方修代码。
2. 本次离线修正件复核门槛已满足，随后由现场会话复验：先确认新遮罩不遮挡导航目标、外扩保护区不与遮罩相交，再检查五组静止/动静切换、预热可用性与耗时。若仍不通过，不得现场直接放宽策略，须新版本/摘要重走复核。
3. 现场通过后由用户选版/决定冻结，再进行延迟复测与固定 20 次真实任务（接管/超时/卡住计失败，至少 16/20）。本轮没有执行或宣称这些结果。
