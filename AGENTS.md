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

- T10 末次状态以 TASKS 第 32 节、`docs/validation/T10_round4_review.md` 为准：第四轮离线独立复核通过（38 项未能证伪，843 项产品测试通过），R4c/R1c 修正已复核；真实实机 usage/hourly 证据仍缺，因此保持 IN_PROGRESS，不得声称实机折算已验收。以下历史轮次状态以本条为最新。

- T00 已通过末次复核（`docs/TASKS.md` 第 13 节）；T01–T06 已交付。先读 `docs/validation/STAGES_1_2.md` 和 `docs/TASKS.md` 最新交接；T00 的 DONE 不代表自主游玩或音频能力已验收。
- T05 已交付；T06 已通过 ADR-009 独立交叉复核（`docs/validation/T06_review.md`），任务 DONE。先读 `docs/validation/T06_runtime.md`；闭环入口为 `python -m vrc_agent simulate --scenario navigation`，仅离线，不能接入 live。旧 `run --mode dry-run` 仍是固定输入演示。
- T08 仍为 IN_PROGRESS；离线部分已通过 ADR-009 独立交叉复核（第 20 节，45 项断言未能证伪），`navigate-suite` 的 16/20 仅为合成结果。现场标定见第 21 节；live 配置冻结与受监管装配已实现并自测（702 passed），最新交接为 `docs/TASKS.md` 第 22 节与 `docs/validation/T08_live.md`。**新增 live 路径已于 2026-10-16 通过独立交叉复核**（第 25 节，`docs/validation/T08_live_review.md`：五条要求未能证伪；标定源摘要 `31c5e598…` 的可重算规则 = 把 `T08_calibration.md` 以 UTF-8 读入后取起始到 `## 6.` 标题之前的 13208 字节切片作 SHA-256），但真实 20 次任务尚未执行，不能移用旧离线复核或宣称已达实机成功率。
- T08 新入口 `python -m vrc_agent navigate-live --task G1-1` 默认只读计划；真实执行须显式 `--mode live`、标定/付费/图像授权参数和本机 F9。新循环只通过独立 `ControllerProcess`，不得删除旧 Runtime 的 `ReplayController` 类型检查、自动 ARM 或换 transport 绕过。配置中的到达数值为工程初值，先现场核对再做完整批次；详见 live 验证记录。
- T08 方向 A 修订交接为 `docs/TASKS.md` 第 35 节、[方向 A 验证记录](docs/validation/T08_direction_A.md)，**已于 2026-10-16 通过独立交叉复核**（第 37 节，[方向 A 独立复核记录](docs/validation/T08_direction_A_review.md)：自建探针 61 项 0 失败，R1–R6 主体未能证伪；5 项非阻断问题——F1 策略 `max_actions`/`freshness_ms` 只声明不强制、F2 `stable_runtime.py:160` 乱码注释、F3 方向 A 新码未登记进 `KNOWN_CODES`、F4 候选遮罩只覆盖左下 86×144、F5 黑帧阈值未写明）。候选稳定性策略 REQUIRES_FIELD_CALIBRATION，生产入口及运行时拒绝其接入 live；先按 F1/F2 修正、再现场标定并由用户选择新冻结版本，不能直接改候选状态或放宽租约。实际 live 配置和冻结世界清单未改，真实 20 次任务仍未执行，本轮复核不构成实机验收。
- **T10（日志、费用与预算）仍是 IN_PROGRESS；2026-10-16 已连续三轮独立复核退回，最新一轮的 R4c/R1c 已修正、待另派执行者复核**（`docs/TASKS.md` 第 26/27/28/29/30/31 节；`docs/validation/T10_review.md`、`T10_revision_review.md`、`T10_round3_review.md`，以及 `docs/validation/T10_telemetry.md` 第 7–9 节）。三轮反例（净化绕过与原因码回显、重试预算/费用漏记、预算包装层未接线、未知台账折成完整零值、费率分子分母不同源、损坏报告/时长覆盖、缺金额字段仍判完整、非标量原因拼接任意类名）已逐项修正并补回归（`tests/unit/test_t10_review_regressions.py`，51 项；四项门禁 843 passed），但**修正件必须再由另一执行者独立复核**：不得自己宣称通过，也不得以门禁全绿或重跑复核者探针替代复核结论。入口为 `report`、`logs`、`navigation-report`；原交付见第 23 节。真实实机 usage/hourly 证据仍缺失，不能将离线数据称为实机折算已验收。
- 本机解释器：`.venv/Scripts/python.exe`（Python 3.14.6）；依赖锁：`requirements.lock`。
- T08 稳定性最新候选为 v2（TASKS 第40节、`docs/validation/T08_stability_v2.md`）：依据第39节现场舍入抖动证据新增容差/全局/局部/导航区域保护与预热，v1保留，v2仍硬拒绝live。实现方自测893 passed，用户要求另派独立复核；复核结果以第41节为准。实际五组静止能否通过仍未验证，不能沿用v1复核或声称16/20。
- T08 方向 A 非阻断项修正见 TASKS 第 38 节：F1 采用文档说明方案，max_actions/freshness_ms 对运行时是声明项，实际强制点见 `T08_direction_A.md` 的 R2–R4 节（报告校验仍读取 freshness_ms）；F2 中文注释恢复、F5 黑帧阈值补明。本次不改变运行行为或冻结摘要，F3 码表缺口和 F4 现场标定仍待后续，不能宣称本次为新的独立复核或实机验收。
- 验证：`python -m pytest -q`、`python -m ruff check src tests`、`python -m ruff format --check src tests`，使用项目虚拟环境。
- 离线示例：`python -m vrc_agent run --mode dry-run`。不要把 fake provider 的固定输入演示当作 T06 的视觉决策闭环。
- 核心控制测试必须继续覆盖主代理进程终止、独立急停、旧 epoch/lease、时限归零和真实 OSC 类型。
- live ARM/RESET 只通过本机热键，管道中的对应命令被拒绝；不得为了自动化测试改成模型可启用。
