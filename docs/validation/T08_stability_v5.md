# T08 v5 可见保护语义与 live 配置修复（2026-10-17）

负责人：Codex / root，实现与自测。依据 TASKS 第 54/55 节现场证据及第 50/53 节复核。本次是**新候选实现，尚待另一执行者 ADR-009 独立复核和现场复验**。T08 保持 IN_PROGRESS；未启动 live、付费调用、操作本机 ARM 热键或发送真实输入。G3/G5 到达/提示仅为[未批准提案](T08_world_v2_proposal.md)，未改生产提示或世界 v1。

## 1. 新版本与不变量

| 项 | v5 值 |
| --- | --- |
| 文件 | `src/vrc_agent/live_navigation/stability_v5.json` + `.sha256` |
| 版本 | `local-rgb-stability-v5-candidate` |
| SHA256 | `1cc4287a4c944ae898012e016ee21b591041f1eb7000041844312c3ec02de21c` |
| 状态、装配 | REQUIRES_FIELD_CALIBRATION；`stable-inference-v5`；schema **6** |
| metric、保护语义 | `rgb_tolerance_visible_guard_v5`；`protected_mask_policy="exclude_masked"` |
| 相对 v4 的键差异 | 仅 version、metric、protected_mask_policy；新 metric 隔离可见保护证据契约 |
| 阈值与遮罩 | 5%（142412 个保留像素变化可通过，142413 拒绝）；Δ≤2；黑帧≤8；外扩32；遮罩仍 `[1536,0,2048,512)` |
| 预热、预算 | 连续2对、最多8帧、间隔100ms；推理8000ms、任务30s、重试3，均未变 |

v1–v4 的策略文件、sidecar 和 `stability_v2.py`/`stability_v3.py` 判据源码按交付前清单逐字节复算保持不变；世界 v1 JSON 与 sidecar 均未改，语义摘要仍 `dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335`。旧版行为由原分派保留，v4 仍相交即拒绝。共享装配只增加 v5、默认仍 legacy/dry-run，原 ReplayController 类型检查、独立控制进程与本机 ARM、lease/epoch/急停/归零不变。

## 2. 保护承诺的明确改变

v3 复核 F1 指出：在“整个外扩保护区必须零变化”的承诺下，先清零遮罩会漏判。v4 用相交即拒绝修复该承诺，但第 54 节证明它与现行到达图存在结构性冲突。**v5 改变承诺范围：只证明外扩保护区中未被遮罩的像素满足 Δ≤2；交集不可见，不声称已验证稳定。**

- 部分交叠：允许比较/派生；遮罩内变化不计入全局或保护变化，这是**声明放行、不是漏判**。
- 可见部分任一个 Δ>2 像素：保护变化非零、stable=false，重新感知；持续变化则有界重试后暂停，不派发动作。
- 每个保护框完全被遮罩覆盖：拒绝。每个原实体 bbox 完全被遮罩覆盖时，即便外扩有少量可见背景，也拒绝。不能靠其他干净实体补偿一个完全不可见目标。
- 运行时 observe 与 before_submit 在比较前执行同一个可见性审计；derive 对全部实体的原 bbox 检查完全遮罩（不只检查导航标签）。模型仍不能 ARM、修改策略或接触 transport。

未采用可选的“遮罩比例≥50%拒绝”：现场尚无可用于确定该阈值的正负例分布，本轮严格实现第 55 节的非空可见部分规则，不擅自添加一个未经定夺的产品阈值。**代价：只剩很小可见片段也可能通过几何门槛；部分遮挡下的错误 bbox、漏报或不确定边界仍需原场景置信度/hazard/uncertainty 链路及现场验证约束，不能视为已经解决。**

## 3. 三层实现与报告契约

新增 `stability_v5.py`，复用已存在的 RGB 容差、黑帧函数及 bbox 外扩算法，不修改旧版判据。矩形使用向外取整的像素半开区间；严格检查整数、有序、非空、画面边界。并集面积算法避免多个实体或遮罩重叠时重复计数。

每份 v5 proof（含预热）及 `inference_source` 导航证据事件附：

| 字段 | 含义 |
| --- | --- |
| entity_rectangles | 原始导航实体 bbox 像素矩形，margin=0 |
| protected_rectangles | bbox 外扩32并裁剪后的去重矩形 |
| entity_masked_px | 原始矩形并集与遮罩并集的交集面积 |
| protected_masked_px | 外扩矩形并集与遮罩并集的交集面积 |
| protected_visible_px | 外扩矩形并集面积减去交集面积 |
| protected_changed | 未遮罩保护区变化像素的并集计数；全局已超限时为 null（未评估） |

证明 stable 仍须非黑帧、全局变化不超5%、可见保护变化为0。预热时还没有模型实体，两个矩形集合为空、审计面积均0；预热只证明候选像素指标，不是语义静止。

运行时新增 `StableNavigationV5`，工厂按 v5 选该类，基类显式选择 v5 仍立即拒绝；旧类的默认 hook 维持原行为。v5 将新的来源审计附到事件，部分交叠的场景可派生，完全不可见中止为既有 `MASKED_SCENE_EVIDENCE`，**没有新增结局码**。

报告复验按 v5 metric/schema=6 分派；schema 必须严格 int，修正前轮 N4 的等值浮点问题只对 v5 收紧。verdict 重算矩形交集/可见面积并拒绝伪造计数，完整报告再从 source 实体重算原框/外扩框与审计，连尚未生成 proof 的来源事件也核对审计。篡改矩形、面积、来源、预热链、schema 或策略摘要均拒绝，汇总坏报告整份进 invalid_files。摘要和计数仍不是图像签名；自洽伪造整份报告的真实性不在机制保证内。

## 4. 第 54 节模型框重放（不等于图像重放或实机成功）

素材 `tests/fixtures/t08_v5_field_boxes.json` 只提取正式模型探针的7个框记录：五组到达参考＋G1/G3 起点。来源 `.runtime/tmp/t08_v4_model_probe_20261017_001337.json` 的完整 SHA256 `4994e248bdb1a440ab10037f38f620629354a0ff4b449f3fb26f47653cc14b65`；派生 fixture SHA256 `260085330ad827eec33a187fa5ed2ad21924a27da5a199e2899118c84353fbe6`。不复制图像、密钥、对话或供应商响应正文。

重放把记录框置于合成静止像素上，仅检验可见保护几何；没有重新调用模型或重放现场原始帧。第 54 节原探针的交集数与这里向外取整后的整数并集口径不同，不能混用。

| 记录 | 原 bbox 遮罩面积 | 外扩框遮罩面积 | 外扩可见面积 | 结果 |
| --- | ---: | ---: | ---: | --- |
| G1 到达 | 122368 | 138752 | 1586800 | 几何可接纳，不宣称正确到达 |
| G2 到达 | 0 | 0 | 304180 | 几何可接纳 |
| G3 激活态到达 | unknown | unknown | unknown | **NO_TARGET**，仍无木框证据，不能到达 |
| G4 到达 | 0 | 0 | 53766 | 几何可接纳，不清除模型原有不确定性 |
| G5 到达 | 129536 | 145920 | 2447474 | 几何可接纳，池面大框的语义问题仍存在 |
| G1 起点 | 0 | 208 | 406922 | 外扩部分交叠按新语义可接纳 |
| G3 起点 | 0 | 0 | 442682 | 几何可接纳 |

G3 的缺目标回归保留空实体/unknown 场景，不用合成框填补。v5 只移除结构性的相交拒绝，**不能推导 20 项任务已经可成功执行或 ≥16/20**；G3/G5 仍依赖提案决策与现场重拍。

## 5. live 配置修复 D

`configs/navigation-live.toml` 的 `[perception]` 显式设置 `disable_thinking=true`，保留 max_tokens=1024，文件头替换过时的推理说明，引用第54节思考开启时6/7空content的现场证据。选择禁用思考而非提高输出上限，依据同一现场对照禁用后7/7返回；没有为猜测的推理 token 数额外加预算或超时。

配置级回归从真实文件加载，经 provider factory 哨兵验证 disable_thinking 的传递；已有请求体回归仍检查 `thinking={"type":"disabled"}`。本次没有调用真实供应商复测。现场2.6–8.3s中的8.3s可能超8000ms，成功返回并不保证能在候选截止期内完成。文件仍是 dry-run/legacy，未替用户选择 v5 或冻结。

live 配置文件 SHA256 `3cabfa7d7d0e34004383fadd467a831e3a5f9b0bc9b7ae35dc8076859566261d`。

## 6. 到达/提示提案 B 与准备路径

详见 [世界 v2 提案](T08_world_v2_proposal.md)：G3 建议激活前 box 0.62×0.65，基于0.654×0.685单样本提出，待正负例校准；G5 建议只框白描边菱形，lower_half 数值留作待测基线；G1 标定与提示不变。新参考图与摘要均待现场取证，不能填旧图或零哈希。

新增 `world_proposal.py` 是独立的离线提案类型与显式路径加载器，验证来源世界摘要、未批准状态和 G3/G5 范围；`evaluate_box` 仅评估草案尺寸/居中，不生成任务成功、不装入生产。当前生产世界加载器未改、没有新增世界文件，提示词未启用。测试在临时目录加载草案，拒绝 FROZEN、假参考摘要、错来源与错组。

## 7. 验证与工件

新增回归覆盖：声明部分交叠放行、可见变化持续拒绝、完全遮罩/外扩可见但原框不可见拒绝、矩形几何/整数计数、并集去重、来源及 proof 篡改、五帧模型框表、预热/超时/租约/急停零动作、五代候选 live 入口哨兵、v5 构造器/基类约束、提案与配置。旧测试中只扩充 schema/工厂枚举断言，未改旧版预期结果。

新增35项回归（972 对比原937）。早期定向运行发现 fixture 目录尚未创建，以及“一次性变化重新感知后可恢复”不符合原负例预期，均已修正测试准备；持续变化负例已通过。首轮全量为971通过、1项未分类提案状态失败，已按项目约定将 PENDING_FIELD_CAPTURE/PROPOSAL_NOT_APPROVED 分类为提案标记（不登记为结局码），再完整重跑最终门禁。不将测试夹具问题或中间结果写成产品能力。

| 最终门禁（项目虚拟环境，实际运行） | 结果 | 日志 |
| --- | --- | --- |
| `python -m pytest -q` | **972 passed in 216.40s (0:03:36)** | `.runtime/verify/t08v5_final_pytest.log` |
| `python -m ruff check src tests` | All checks passed! | `t08v5_final_ruff.log`（同目录） |
| `python -m ruff format --check src tests` | 131 files already formatted | `t08v5_final_format.log` |
| `python -m pip check` | No broken requirements found. | `t08v5_final_pip.log` |

真实 `ControllerProcess` dry-run 工件 `.runtime/verify/t08_stability_v5-0f9340fd159f432ea9a3e0c35059989f.json`：SHA256 `dba289b00c0d2bf6400c88e750d92de4c8c52a53e86c29d944186d83e91c8644`，schema=6、supervised-dry-run、sends_live_input=false、SUCCESS，2个动作（motion、wait）、5份proof、6份预热比较，完整报告复验通过。这是内存模型与合成画面下的离线 IPC 证据，不是实机成功。

只读入口实跑（Windows 子进程用 `python -X utf8 -m vrc_agent navigate-live --task G1-1`）：plan-only、sends_live_input=false、冻结世界摘要未变。此处显式 UTF-8 只是验证命令的输出环境；本轮没有扩大为 CLI 编码功能修订。

交付审计 `.runtime/verify/t08v5_delivery.json`（实现方自测）SHA256 `1fe26f3d184f621a136da2fcff1428670c5255eef628f5f69a699cbced6146b5`，脚本 `.runtime/verify/v_t08v5_delivery.py`、日志 `t08v5_delivery.log`；记录旧文件逐字节清单、世界语义摘要、配置/fixture 摘要与上面的模型框表。不是独立复核结论。

旧文件基线 `.runtime/verify/t08v5_frozen_before.json`、最终代码清单 `t08v5_code_manifest.json`、最终文件清单 `t08v5_final_files.json` 供下一执行者锚定本轮工作树；最终四项门禁结束后重算全部基线保持一致。独立复核必须自行构造反例，不得仅重跑这些实现方自测脚本。

## 8. 下一步与未验证

交付后须另一执行者按 ADR-009 独立复核 v5 的声明范围与三层一致性、完全不可见边界、来源审计、旧版隔离及配置；再由现场会话重拍 G3/G5、配对静止/移动复验和性能测量，用户决定规则与冻结版本后才进行真实时延与固定20次任务。

旧的面积阈值盲区、Δ≤2真实变化、遮罩内容不可见、模型错误/漏报、镜面动态与端到端耗时风险依然存在。没有五组实机 v5 复验，没有真实任务成功率或 T10 实机用量折算证据；不得把提案或静态模型框回放写成已经定案或现场通过。
