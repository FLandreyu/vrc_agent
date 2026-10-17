<!-- GRAPHFLOW:BEGIN managed block — edit outside these markers only -->

## GraphFlow Context-First Rule

GraphFlow is a local code knowledge-graph + context compression MCP server (10 tools).
Before broad code exploration, implementation, debugging, review, or architecture
questions in this project:

1. Call `graphflow_context` with the task/query first.
2. Use the returned `summary`, `anchors`, and `tokenBudget` as the primary context.
3. Read full files only when anchors point there or compressed context is insufficient.
4. For multi-step or ambiguous work, call `graphflow_plan` before implementing.
5. After project changes, call `graphflow_index` to keep the graph fresh.
6. The flywheel auto-closes: pending episodes are backfilled by Claude Code
   hooks (or `graphflow_report_outcome`) by default; set GRAPHFLOW_AUTO_CAPTURE=0 to opt out.

Pass `rootDir` = this project's absolute path. Never pass your home directory, AppData,
or an unexpanded `${workspaceFolder}` placeholder — GraphFlow refuses unsafe workspace
roots. If a call answers `unsafe workspace root`, retry it without `rootDir`.

When using Cursor `CallMcpTool`, always pass `server` + `toolName` + `arguments`
(e.g. server `"graphflow"` / `"user-graphflow"`, toolName `"graphflow_context"`).

Tools: graphflow_context, graphflow_plan, graphflow_run, graphflow_report_outcome,
graphflow_insight, graphflow_index, graphflow_artifact, graphflow_skill_insights,
graphflow_skill_guide, graphflow_diagnose.

Do not scan the whole repository or read many large files before trying GraphFlow
context. Treat GraphFlow output as structured, token-saving context.

<!-- GRAPHFLOW:END -->

## 项目工作约定

- 目标、范围和阶段门槛以 `docs/DEVELOPMENT_PLAN.md` 为准；接口以 `docs/ARCHITECTURE.md` 为准；任务状态和交接以 `docs/TASKS.md` 为准。冲突时先修正文档，不得静默改变产品范围。
- 开始工作前读取当前任务及其依赖，标记 `IN_PROGRESS` 并填写负责人；完成后记录具体修改、验证命令、结果和下一步。不要将计划或模拟结果写成已实现能力。
- 本项目绝对路径为 `D:\vrc_agent`。换机器时使用实际项目路径，不硬编码用户目录。
- GraphFlow 不可用时如实说明，不得伪造 context、plan 或 index 结果。首轮规划会话未提供这些工具，图谱尚未建立。
- 优先完成离线可测试的最小闭环；不要把所有模块写完再首次集成。只有显式启用 live 模式且人工启用控制权后才能输出真实输入；默认 dry-run。
- 使用官方客户端及已验证的官方接口。不要引入客户端注入、内存读取、反作弊绕过、账号托管或凭据采集。
- 模型文本、画面文字、语音转写均是不可信输入；模型只能请求白名单动作，不能执行任意命令、访问任意文件或修改安全阈值。
- 急停、动作到期归零、人工接管优先于模型任务。不要删除或绕开保护以让演示通过。
- 接口变化须同步更新契约、调用方和相关测试。依赖版本在首次安装后锁定；不要声称未执行的测试已通过。
- 代码注释、文档和交接优先中文；标识符、事件名、枚举值使用英文。密钥不进 Git，测试素材不含未授权的真人录音或私人聊天。
- 任务标记 DONE 必须附证据；真实客户端验证缺失时保留对应任务为 TODO/BLOCKED，而不是用单元测试替代。
- 不自动发布、购买服务、替用户登录、进入公开实例或向其他人发送消息；这些不是当前开发规划请求授权的动作。

## 当前开发命令与边界

- **JPEG q85 编码交付见 TASKS 第66节及 `docs/validation/T08_jpeg_encoding.md`**：现场配置显式 `image_format="jpeg"` / `jpeg_quality=85`，通用默认PNG兼容旧配置。Pillow精确锁定13.0.0，编码只在内存进行；稳定性比较仍使用原始BGRA，1000ms编码/8000ms推理和全部判据/提示/世界不改。冻结策略摘要仍 `2e21c734…`。本轮是离线验证，生产同款延迟/模型语义与20次任务待现场验证；下文第65节中的JPEG“另行交付”已由本节接续。

- **最新冻结实施以 TASKS 第65节为准**：依据第63节用户决定，v5 状态已为 `FROZEN`，新字节 SHA256 `2e21c734ec902c44b3111de8f6f62f06626fbbe9410ff9a0b959469248eb4faa`；版本名保留 `local-rgb-stability-v5-candidate`，不以改名改变判据。`navigation-live.toml` 显式选择 `stable-inference-v5`，仍默认 dry-run；显式 live、授权和本机 F9 门槛仍须满足。世界 v2 已获用户冻结授权，仍须 `--world-version v2` 显式选用（默认 v1）。下文“v5 候选拒绝 live／待用户冻结”均为历史。冻结不是实机验收，T08 仍 IN_PROGRESS；第64节 JPEG 编码改造、延迟复测及20次真实任务另行交付。见 `docs/validation/T08_v5_freeze.md`。

- **T08 世界v2已按第59节及用户指令实现定稿（TASKS第60节，2026-10-17，`docs/validation/T08_world_v2.md`）**：G3 box 0.50/0.52和新激活前PNG，G5新站立参考但lower_half规则不变；G3/G5提示已作为v2字段落地。世界语义摘要 `b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0`；v1文件/摘要/提示与20项任务不改。通过 `load_live_world("v2")` / `--world-version v2` 显式选用，默认仍v1，汇总不得混合世界；四项门禁991 passed。**已由另一执行者按 ADR-009 独立复核（TASKS 第62节，[世界 v2 独立复核记录](docs/validation/T08_world_v2_review.md)）：第60/61节与实现文档的声明主体未能证伪、无阻断项**（自建探针 106 项 0 失败；复核者复跑 991 passed in 164.59s；三项非阻断观察——N1 交付提交在来源切片区间内重排了字段文档表格分隔行、N2 G5 覆盖率是独立第三约束（0.6 宽需约 0.5 高）、N3 恰好等值浮点边界继承 v1 不可靠）；**但世界 v2 与 v5 仍待用户选版/冻结、现场语义复验与 20 次任务**，v5候选live拒绝不变，T08仍IN_PROGRESS。下方关于“世界v2仍是提案、提示未落地”的描述为历史；0.62/0.65已由本次用户定案0.50/0.52取代。

- **T08 最新实现为 v5 候选（2026-10-17，TASKS 第56节，`docs/validation/T08_stability_v5.md`）**：按第55节实现可见保护语义 exclude_masked/schema6，部分交叠为声明放行，完全不可见原框/保护框拒绝；比较器/运行时/报告同源审计。v5 SHA256 `1cc4287a4c944ae898012e016ee21b591041f1eb7000041844312c3ec02de21c`，候选仍拒绝live；v1–v4文件/摘要/旧判据及世界v1未改，默认legacy/dry-run。live配置已显式禁用思考；G3/G5到达/提示及世界v2仍为未批准提案（`T08_world_v2_proposal.md`），没有改生产提示/世界。实现方四项门禁972 passed，**已由另一执行者按 ADR-009 独立复核（TASKS 第58节，[v5 独立复核记录](docs/validation/T08_stability_v5_review.md)）：第56/57节与 v5 文档的声明主体未能证伪、无阻断项**（自建探针 108 项 0 失败；复核者复跑 972 passed in 133.27s；三项非阻断观察——N1 保护矩形与 UI 区交集不可验证（贴边实体实测 5248=32×164 px）、N2 不得仅改版本名回退 v4、N3 单像素 Δ3 放行＝面积盲区下界）；**但尚待现场复验**（重拍 G3/G5 → 配对静止/移动复验与性能测量 → 用户选版/冻结 → 延迟复测 → 20 次任务），不能沿用旧版复核或本轮离线复核宣称 v5 通过实机验收；T08仍IN_PROGRESS、实机20次仍未执行。

- **T08 v4 现场检查发现（TASKS 第 54 节，2026-10-17，现场会话）：按当前遮罩×标定×判据，G1/G3/G5 共 12 个任务存在结构性阻断（真实模型框与遮罩相交 / 到达不可达），G2/G4 共 8 个任务几何干净；另发现现行 live 配置（思考未禁用 + max_tokens=1024）7 次调用 6 次空 content。与「≥16/20」验收不相容，待用户决策后派实现方修订（新版本/新标定流程）并由另一执行者复核。** 下方 v4 复核通过条目及更早历史保留（离线复核结论仍有效；本条为现场使用性检查发现）。

- **T08 v4 最新结论：已通过离线独立复核（TASKS 第 53 节，`docs/validation/T08_stability_v4_review.md`）**。F1 三层、F2 工厂/基类入口、原 N1/N2 修正主体未能证伪，937 项门禁通过；N3（底层矩形几何校验不全，但报告重算拒绝）、N4（schema 5.0 等值接受）为非阻断项。下方第 51/52 节“待独立复核”属历史交接；T08 仍 IN_PROGRESS，v4 仍候选且拒绝 live，现场五组/遮罩/预热复验、用户选版与固定 20 次任务尚未完成。

- **T08 v3 最新复核结论：退回修正（TASKS 第 50 节，`docs/validation/T08_stability_v3_review.md`）**。F1：外扩保护区与遮罩交叠时漏判并可发 dry-run motion；F2：基类直接选择 v3 可省略预热。另有 N1 文档数值/统计措辞与 N2 报告字段严格性问题。923 项产品测试与其余门禁通过不抵消独立反例；修正后须另一执行者复核。**修正件已由实现者按第 51 节处置为 v4 候选（见下条），但仍是实现方交付、尚待另派执行者复核。** T08 仍 IN_PROGRESS，v4 也仍候选并拒绝 live，不能沿用后文历史“待复核”口径或宣称本轮通过。

- T10 末次状态以 TASKS 第 32 节、`docs/validation/T10_round4_review.md` 为准：第四轮离线独立复核通过（38 项未能证伪，843 项产品测试通过），R4c/R1c 修正已复核；真实实机 usage/hourly 证据仍缺，因此保持 IN_PROGRESS，不得声称实机折算已验收。以下历史轮次状态以本条为最新。

- T00 已通过末次复核（`docs/TASKS.md` 第 13 节）；T01–T06 已交付。先读 `docs/validation/STAGES_1_2.md` 和 `docs/TASKS.md` 最新交接；T00 的 DONE 不代表自主游玩或音频能力已验收。
- T05 已交付；T06 已通过 ADR-009 独立交叉复核（`docs/validation/T06_review.md`），任务 DONE。先读 `docs/validation/T06_runtime.md`；闭环入口为 `python -m vrc_agent simulate --scenario navigation`，仅离线，不能接入 live。旧 `run --mode dry-run` 仍是固定输入演示。
- T08 仍为 IN_PROGRESS；离线部分已通过 ADR-009 独立交叉复核（第 20 节，45 项断言未能证伪），`navigate-suite` 的 16/20 仅为合成结果。现场标定见第 21 节；live 配置冻结与受监管装配已实现并自测（702 passed），最新交接为 `docs/TASKS.md` 第 22 节与 `docs/validation/T08_live.md`。**新增 live 路径已于 2026-10-16 通过独立交叉复核**（第 25 节，`docs/validation/T08_live_review.md`：五条要求未能证伪；标定源摘要 `31c5e598…` 的可重算规则 = 把 `T08_calibration.md` 以 UTF-8 读入后取起始到 `## 6.` 标题之前的 13208 字节切片作 SHA-256），但真实 20 次任务尚未执行，不能移用旧离线复核或宣称已达实机成功率。
- T08 新入口 `python -m vrc_agent navigate-live --task G1-1` 默认只读计划；真实执行须显式 `--mode live`、标定/付费/图像授权参数和本机 F9。新循环只通过独立 `ControllerProcess`，不得删除旧 Runtime 的 `ReplayController` 类型检查、自动 ARM 或换 transport 绕过。配置中的到达数值为工程初值，先现场核对再做完整批次；详见 live 验证记录。
- T08 方向 A 修订交接为 `docs/TASKS.md` 第 35 节、[方向 A 验证记录](docs/validation/T08_direction_A.md)，**已于 2026-10-16 通过独立交叉复核**（第 37 节，[方向 A 独立复核记录](docs/validation/T08_direction_A_review.md)：自建探针 61 项 0 失败，R1–R6 主体未能证伪；5 项非阻断问题——F1 策略 `max_actions`/`freshness_ms` 只声明不强制、F2 `stable_runtime.py:160` 乱码注释、F3 方向 A 新码未登记进 `KNOWN_CODES`（已由第 44 节登记修正）、F4 候选遮罩只覆盖左下 86×144、F5 黑帧阈值未写明）。候选稳定性策略 REQUIRES_FIELD_CALIBRATION，生产入口及运行时拒绝其接入 live；先按 F1/F2 修正、再现场标定并由用户选择新冻结版本，不能直接改候选状态或放宽租约。实际 live 配置和冻结世界清单未改，真实 20 次任务仍未执行，本轮复核不构成实机验收。
- **T10（日志、费用与预算）仍是 IN_PROGRESS；2026-10-16 已连续三轮独立复核退回，最新一轮的 R4c/R1c 已修正、待另派执行者复核**（`docs/TASKS.md` 第 26/27/28/29/30/31 节；`docs/validation/T10_review.md`、`T10_revision_review.md`、`T10_round3_review.md`，以及 `docs/validation/T10_telemetry.md` 第 7–9 节）。三轮反例（净化绕过与原因码回显、重试预算/费用漏记、预算包装层未接线、未知台账折成完整零值、费率分子分母不同源、损坏报告/时长覆盖、缺金额字段仍判完整、非标量原因拼接任意类名）已逐项修正并补回归（`tests/unit/test_t10_review_regressions.py`，51 项；四项门禁 843 passed），但**修正件必须再由另一执行者独立复核**：不得自己宣称通过，也不得以门禁全绿或重跑复核者探针替代复核结论。入口为 `report`、`logs`、`navigation-report`；原交付见第 23 节。真实实机 usage/hourly 证据仍缺失，不能将离线数据称为实机折算已验收。
- 本机解释器：`.venv/Scripts/python.exe`（Python 3.14.6）；依赖锁：`requirements.lock`。
- T08 稳定性最新候选为 v2（TASKS 第40节、`docs/validation/T08_stability_v2.md`）：依据第39节现场舍入抖动证据新增容差/全局/局部/导航区域保护与预热，v1保留，v2仍硬拒绝live。实现方自测893 passed（现场冒烟见第41节）。**2026-10-16 已通过 ADR-009 独立交叉复核**（第42节，[v2 独立复核记录](docs/validation/T08_stability_v2_review.md)：自建探针 71 项 0 失败，第40节可验证声明主体未能证伪，复核者复跑四项门禁 893 passed in 136.58s），3 项非阻断问题——N1 六个 v2/方向 A 结局码（`INFERENCE_TIMEOUT`/`STABILITY_TIMEOUT`/`IMAGE_ENCODING_TIMEOUT`/`SCENE_UNSTABLE`/`WARMUP_UNSTABLE`/`MASKED_SCENE_EVIDENCE`）未登记进 `KNOWN_CODES`、写事件时 `reason_code` 被整字段拒绝（**已由第 44 节登记修正**）；N2 `.runtime/verify/` 遗留 3 份旧格式 v1 工件不再通过现行复验（**已归档，第 43 节**）；N3 策略 `max_actions`/`freshness_ms` 仍只声明不强制（第38节既定方案，实测复现，未改）。实际五组静止能否通过仍未验证，不能沿用v1复核或声称16/20，也不构成实机验收；N1/N2 已处置完毕；**2026-10-16 现场复验未通过**（TASKS 第 46 节：408 对中仅 2 对 stable 且不连续、global_fail 304、tile_fail 102；定位补拍见第 47 节）→ **v3 候选见下条（第 48 节）**。
- T08 方向 A 非阻断项修正见 TASKS 第 38 节：F1 采用文档说明方案，max_actions/freshness_ms 对运行时是声明项，实际强制点见 `T08_direction_A.md` 的 R2–R4 节（报告校验仍读取 freshness_ms）；F2 中文注释恢复、F5 黑帧阈值补明。本次不改变运行行为或冻结摘要，F3 已由第 44 节处置（见下条）、F4 现场标定仍待后续，不能宣称本次为新的独立复核或实机验收。
- **T08 码表缺口（v2 复核 N1＋方向 A 复核 F3）已于 2026-10-16 处置完毕（TASKS 第 44 节，实现者 DSH / Flash，非复核）**：新增 `live_navigation/errors.py`，由该层自己声明结局码闭集——15 个中止码（`TASK_TIMEOUT`/`ARM_TIMEOUT`/`CAPTURE_TIMEOUT`/`CONTROL_TIMEOUT`/`CALIBRATION_MISMATCH`/`PATH_UNSAFE`/`RECOVERY_UNSAFE`/`CONTROL_UNAVAILABLE`/`SOURCE_CLOSE_FAILED`/`STABILITY_TIMEOUT`/`IMAGE_ENCODING_TIMEOUT`/`INFERENCE_TIMEOUT`/`SCENE_UNSTABLE`/`WARMUP_UNSTABLE`/`MASKED_SCENE_EVIDENCE`）、3 个批次汇总码（`NOT_RUN`/`DUPLICATE_ATTEMPTS`/`INVALID_REPORT`）与由 `PROVIDER_FAILURES` 派生的 `MODEL_*`；`telemetry/codes.py` 新增 `live_navigation` 家族（登记表 72→98 码、家族 9→10，`MODEL_TIMEOUT` 仍归 `navigation`）。回归 `tests/unit/test_telemetry_live_codes.py`（5 项：码字面量必须显式分类、闭集与源码双向一致、`MODEL_*` 跟随供应商闭集、`EventLog` 实测接受、`tally` 归入家族且真未知码仍单列）；`tests/unit/test_telemetry_codes.py` 层闭集清单同步扩充。改动只涉及码表、新文件 `live_navigation/errors.py` 与测试，未改稳定性策略/摘要/live 配置/运行行为；本轮不是实机验收，N3、F4 与现场复验仍待后续。
- **T08 稳定性 v3 候选已发布（2026-10-16，TASKS 第 48 节，实现者 DSH / Flash，非复核）**：v2 现场复验未通过（第 46 节：仅 2/408 对 stable）后，按第 47 节定位结果修订判据——新增 `live_navigation/stability_v3.json`（`.sha256`，版本 `local-rgb-stability-v3-candidate`，SHA256 `333739e324ccc30f63e0c1ec533290dbd572090c03ef0d2be85711239d4adb5a`，schema=4，指标 `rgb_tolerance_area_guard_v3`），全局面积上限 1%→**5%**（保留像素 2 848 256，142 412 以内通过、142 413 起拒绝）、排除区扩到 **`[1536,0,2048,512)`**、**删除逐块判据**（现场 408 对仅有 2 对 stable 且不连续，无法形成连续预热通过）、保留黑帧与导航证据保护区零容差；接线只加 `stable-inference-v3`（默认仍 legacy），`StableNavigationV3` 复用 v2 预热/检查点语义。新增 25 项测试（`tests/unit/test_stability_v3.py` 17＋`tests/integration/test_stability_v3_runtime.py` 8），四项门禁全绿。**这是实现方离线候选：v3 仍为 `REQUIRES_FIELD_CALIBRATION` 并拒绝 live，v1/v2/世界摘要未改，五组静止与 16/20 仍未验证，必须另派执行者按 ADR-009 复核后再做现场复验（含确认新遮罩不遮挡导航目标）与用户选版。**
- **T08 稳定性 v4 候选＝v3 复核修正件已发布（2026-10-16，TASKS 第 51 节，实现者 DSH / Flash，非复核）**：按 [v3 独立复核记录](docs/validation/T08_stability_v3_review.md) 第 7 节处置 F1/F2/N1/N2。新增 `live_navigation/stability_v4.json`（`.sha256`，版本 `local-rgb-stability-v4-candidate`，SHA256 `e4dcb100c6da68e360199593e90408806aa7753558650e4403e9497b2aefefea`，schema=5，装配键 `stable-inference-v4`），显式声明 `protected_mask_policy: "reject_on_overlap"`——外扩 32px 保护矩形与遮罩相交时，比较器（`protected region masked`）、运行时（任何动作前 `MASKED_SCENE_EVIDENCE`）、报告复验三层一致拒绝，不再出现「遮罩清零→判 stable」；**v4 与 v3 的键差异只有 `version` 与该保护语义**，判据阈值与 v1–v3 文件/摘要一字未改。F2：`StableNavigation` 基类只接受 v1（版本不符在任何采集/模型/动作前 `ValueError`），新增 `STABLE_NAVIGATION_CLASSES`／`stable_navigation_class()` 统一工厂，`session.py` 经工厂选类。N1 文档数值/措辞与 N2 字段严格性（`retained_pixels` 必须 `int`、块字段按键存在性判非法）一并处置。新增 14 项测试（`tests/unit/test_stability_v3_review_regressions.py` 5＋`tests/integration/test_stability_v4_runtime.py` 9 个用例），四项门禁 937 passed、ruff 与 pip check 通过（`.runtime/verify/t08v4_gates.log`）。**这是实现方修正件，不是复核结论也不是实机验收**：v4 仍 `REQUIRES_FIELD_CALIBRATION` 并拒绝 live，新遮罩 `[1536,0,2048,512)` 是否遮挡导航目标仍待现场确认；下一步是另派执行者 ADR-009 复核 → 现场复验 → 用户选版 → 延迟复测与固定 20 次任务。
- 验证：`python -m pytest -q`、`python -m ruff check src tests`、`python -m ruff format --check src tests`，使用项目虚拟环境。
- 离线示例：`python -m vrc_agent run --mode dry-run`。不要把 fake provider 的固定输入演示当作 T06 的视觉决策闭环。
- 核心控制测试必须继续覆盖主代理进程终止、独立急停、旧 epoch/lease、时限归零和真实 OSC 类型。
- live ARM/RESET 只通过本机热键，管道中的对应命令被拒绝；不得为了自动化测试改成模型可启用。
