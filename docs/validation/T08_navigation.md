# T08 导航与有限恢复：离线交付

日期：2026-10-14；实现与自测：Codex / root；版本 0.1.0。T08 仍为 **IN_PROGRESS**：本轮只交付用户指定的 T06 runtime 离线部分，未做 ADR-009 独立交叉复核或实机导航验收。

后续状态：本文保留离线交付时的记录；离线独立复核现已完成，见 [T08 复核记录](T08_review.md)。现场标定及新增受监管装配见 [标定记录](T08_calibration.md)、[live 验证记录](T08_live.md) 与 TASKS 第 22 节。新增路径独立复核、真实 20 次验收仍待完成。

## 入口与交付

```powershell
.\.venv\Scripts\python.exe -m vrc_agent navigate-suite
```

输出完整 JSON；退出码 0 表示试验正常结束，导航结果看 `successes/success_rate/failure_counts`。没有 live 参数，不打开 OSC、音频或付费模型。报告默认仅 stdout；本轮显式保存的证据为 `.runtime/t08/first_world_20.json`（UTF-8，755705 bytes），不包含截图、语音或真人聊天。

| 文件 | 职责 |
| --- | --- |
| `skills/world.py` | 严格、冻结的专用世界契约，五地标、每组四次任务，拒绝额外地图字段/live 模式/重复 ID/越界预算 |
| `skills/worlds/first_world.json` | 第一世界替身 `synthetic-first-world-v1`，五种颜色地标、初始视图和固定 20 项环境条件；真实 world_id 为 null |
| `skills/navigation.py` | NavigationSkill：Planner、到达/进展验证、有限恢复状态；只返回 Proposal，不导入 control/transport |
| `navigation_sim.py` | 图像空间环境积分实际记录型输入，再渲染像素、调用感知；装配 Runtime、统计固定任务集和失败分类 |
| `runtime.py` | 可选可信 `navigation` 策略；仍负责所有 Proposal 复验、权限绑定、执行、观测校验、取消和收尾归零 |
| `simulation.py` | 合成视觉增加可选目标颜色/标签，默认值保持 T06 行为 |
| `cli.py` / `pyproject.toml` | 新子命令及世界 JSON 随 wheel 打包，无新增依赖 |
| `tests/unit/test_navigation.py` | 46 项，证据反例、方向进展、配置边界、无执行依赖 |
| `tests/integration/test_navigation_runtime.py` | 28 项，20 次逐动作归零、恢复、接管、无网络、确定性、CLI 与环境因果验证 |

路径未加前缀的产品文件均位于 `src/vrc_agent/`。`requirements.lock`、live 热键/管道、安全阈值、OSC 白名单没有修改。T06 四条原回放摘要与第 15 节完全一致。

## 判据与状态机

- 目标由可信本地世界配置选定，不使用模型 `active_goal` 或 `expected_outcome` 改目标/判成功。感知只从指定颜色的合成像素找唯一目标，不读取环境 fault、模拟大小或结果字段；规划器只拿到 SceneState。
- 转向、前进、恢复侧移均为强度 0.2、200ms；等待/到达确认 100ms。每步都走 bind_request 和 T03 Controller，动作到期三轴归零，再获取新观测，没有运动队列或持续按键。
- 到达：**连续两个动作结束后的新观测**，同 session/epoch/track，唯一目标，场景和目标置信度 ≥0.8，无 hazard/uncertainty；面积达到地标阈值（0.35 或 0.36），水平偏心 ≤0.10。初始大目标不能直接成功，需两个短等待后的证据。重复/旧观测、缺帧、错误关联、目标消失、track/上下文变化、跳帧均不能拼成连续成功证据。
- 前进进展：同目标面积增长 >0.01；转向进展：偏心减少 >0.02。转头时不要求面积增大，前进时仅居中不算接近。目标身份无法连续比较时不宣称进展，仍受总动作上限约束。
- 连续 3 次有据的导航运动无进展：动作已归零后进入一次恢复，**等待新观测 → 200ms 侧移 → 再等待新观测**。第一轮向右，第二轮向左，整次任务最多两轮，预算不会因偶尔有进展而重置。恢复侧移不算导航进展；每一步都要求当前目标可信，未知时只能等待。
- 恢复之后再连续 3 次无进展且预算耗尽：`RECOVERY_EXHAUSTED`，控制器 PAUSED。连续 3 次无法观察目标：`TARGET_UNOBSERVED` 并暂停。每任务最多 40 个动作；急停优先，失败后不 ARM、不 RESET、不续租。
- 报告每步含动作/观测 ID、时间、verification、三轴记录，以及 `navigation_feedback.evidence` 的面积/偏心/track。COMPLETED 只表示输入序列结束；SUCCESS 必须带连续观测证据。

恢复是在已有租约内、已归零的动作间隙执行少量短动作；不是先把控制器 PAUSED 再偷偷 ARM。PAUSED/ESTOP/租约失效都是任务终止。侧移的安全区域只在这个合成空地成立，**真实世界未标定前不能复用**。

## 固定第一世界离线 20 次任务

这是实际第一测试世界尚未选定时的**合成替身**，不是 VRChat 世界或地图。模型可见的是五种地标颜色；配置中的初始偏心/大小仅供环境初始化，不传给规划器。环境按实际输入时长积分：转向改变偏心、前进增大标志；暂时阻挡在足够侧移后解除，永久阻挡不解除。等待不会自动让目标接近。

任务集在第一次运行前固定；四项强制故障用于回归安全行为，因此这个混合任务集的 80% **不是现实场景的统计估计**，也不替代 T08 的实机 ≥80% 门槛。任何任务失败或接管都计入固定分母 20，不剔除、不重试替换。

| ID | 地标 / 初始视图或故障 | 实际结果 | 恢复次数 | 动作数 |
| --- | --- | --- | --- | --- |
| W1-01 | 绿色门牌 / 正面远景 | SUCCESS | 0 | 6 |
| W1-02 | 绿色门牌 / 偏左 | SUCCESS | 0 | 8 |
| W1-03 | 绿色门牌 / 偏右 | SUCCESS | 0 | 8 |
| W1-04 | 绿色门牌 / 暂时阻挡 | SUCCESS | 1 | 11 |
| W1-05 | 蓝色告示板 / 正面远景 | SUCCESS | 0 | 6 |
| W1-06 | 蓝色告示板 / 偏左 | SUCCESS | 0 | 7 |
| W1-07 | 蓝色告示板 / 偏右 | SUCCESS | 0 | 7 |
| W1-08 | 蓝色告示板 / 暂时阻挡 | SUCCESS | 1 | 12 |
| W1-09 | 红色标记 / 正面远景 | SUCCESS | 0 | 5 |
| W1-10 | 红色标记 / 偏左 | SUCCESS | 0 | 9 |
| W1-11 | 红色标记 / 偏右 | SUCCESS | 0 | 9 |
| W1-12 | 红色标记 / 暂时阻挡 | SUCCESS | 1 | 11 |
| W1-13 | 黄色路牌 / 正面远景 | SUCCESS | 0 | 5 |
| W1-14 | 黄色路牌 / 偏左 | SUCCESS | 0 | 9 |
| W1-15 | 黄色路牌 / 偏右 | SUCCESS | 0 | 9 |
| W1-16 | 黄色路牌 / 暂时阻挡 | SUCCESS | 1 | 11 |
| W1-17 | 青色面板 / 动作中接管 | TAKEOVER（失败） | 0 | 1 |
| W1-18 | 青色面板 / 永久阻挡 | STUCK（失败） | 2 | 15 |
| W1-19 | 青色面板 / 目标不可见 | TARGET_UNOBSERVED（失败） | 0 | 3 |
| W1-20 | 青色面板 / 感知延迟 800ms | MODEL_TIMEOUT（失败） | 0 | 0 |

合计 **16/20 = 80%**。W1-18 为 9 个导航运动 + 两轮各 3 步恢复，最后 PAUSED；W1-17 为 CANCELLED/UNKNOWN、最终 ESTOP，停止后无非零输入。四项暂时阻挡均真实执行一轮恢复再看到目标接近。无输入仅等待的反例最终 ACTION_LIMIT，目标图像大小不变。

失败分类（报告保留原始 outcome，不丢失细分原因）：

| failure_class | 依据 |
| --- | --- |
| NONE | outcome=SUCCESS 且最终 ActionResult.verification=SUCCESS |
| TAKEOVER | MANUAL_STOP、PREEMPTED 或最终 ESTOP；优先于曾经到达 |
| STUCK | RECOVERY_EXHAUSTED，恢复预算耗尽 |
| TARGET_UNOBSERVED | 连续未知目标超限 |
| ACTION_BUDGET | ACTION_LIMIT，动作预算耗尽 |
| MODEL_TIMEOUT | MODEL_LATE |
| OBSERVATION_INVALID | 焦点丢失、旧观测、缺帧或错误场景证据 |
| AUTHORITY_LOST | session/epoch 变化、租约过期或未启用 |
| CONTROL_FAILED | TRANSPORT_ERROR |
| UNVERIFIED | 报告声称 SUCCESS 但没有最终成功验证 |
| RUNTIME_FAILED | 其余异常，需根据 trace.outcome/runtime_error 追查 |

不可见场景可能走 OBSERVATION_INVALID（窗口/帧失败），有有效画面却看不到地标才是 TARGET_UNOBSERVED；不将两类合并成“卡住”。

## 可重复证据与检查

世界配置的规范化 JSON（含任务集）摘要由报告 `suite_digest` 固定，测试钉住这个版本。`sequence_digest` 对去掉该字段的完整报告计算；控制动作不导出随机 lease_id，不能把日志当权限凭据。

```text
suite_digest     ea1688d3a0235e0a9af03b18a5177208fe80bf74b414ced0d49e696812a0370d
sequence_digest  a7dbd298843c38a7ec52c26872797016937f2b11ab97aa31e4ebe6a73b0bc6f7
```

在 Windows / Python 3.14.6 / 项目 `.venv` 实际执行，最终退出码均为 0：

| 命令 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q` | **653 passed in 11.54s**（T07 基线 579 + T08 新增 74） |
| `.\.venv\Scripts\python.exe -m ruff check src tests` | All checks passed! |
| `.\.venv\Scripts\python.exe -m ruff format --check src tests` | 89 files already formatted |
| `.\.venv\Scripts\python.exe -m pip check` | No broken requirements found. |

额外验证：两个独立进程 CLI/API 完整报告一致；socket 哨兵下整套任务运行；T06 四个旧摘要一致；wheel 构建成功，确认模块从 `.whl` 导入后执行 20 次，完整报告与源码保存件逐字段相同。wheel：`.runtime/t08-wheel/vrc_agent-0.1.0-py3-none-any.whl`，SHA-256 `f0d3004a904db240f825d7c8387f1510b5a4aa7119294ddd2e8687e3bbd20982`。这不是干净机器依赖安装验收。

实现中首次试跑暴露 MotionArgs 被误作字典，已修复并重跑；首次新增测试命名与 unit 同名导致收集冲突，已重命名；Windows CLI 子进程编码测试改为 `-X utf8`，无产品接口放宽。上述中间失败不计为最终通过证据。所有最终结果由本执行者自测，未冒充交叉复核。

## 下一执行者与现场交接

1. 独立复核 `NavigationSkill.verify/decide`、Runtime 可选分支、世界配置和报告；重点自行构造重复/跨 session/epoch/track 证据、未知观测打断确认、转向有进展但面积不变、恢复时急停，以及等待不能驱动环境前进。运行四项门禁并记录 ADR-009 结论。
2. 现场人工选择真实第一世界与五组地标任务，记录 world ID/版本、Avatar、视角/分辨率、可见到达标志、每组四次初始机位/朝向及两轮恢复的允许区域。当前五种颜色仅是离线占位，不可冒称真实世界已配置；真实面积门槛必须重新标定，可能需要不同的可见判据。
3. 当前 Runtime 明确拒绝 live 控制器。实机之前还需完成独立控制进程/IPC 监管下的 live 装配、真实感知与世界配置适配；保持显式 live 与本机 ARM 双门槛，不能通过删除 ReplayController 类型检查、自动 ARM 或换传输来“现场试一下”。本轮没有宣称这部分已实现。
4. 现场任务清单在试跑前冻结为新的 live 配置版本及摘要，真实 20 次全部留记录（任务 ID、初始机位、开始/结束、session/epoch、动作/观测证据、人工确认、接管、恢复次数、失败分类）。接管/超时/卡住都计失败；≥16 次成功才满足导航成功率要求。故障回归另记，不能删掉失败任务或用离线结果填数。
5. 实机验收、独立复核均通过后才能转 T08 DONE。当前下一步是复核离线实现，并约现场标定；不自动登录、换世界、进入公开实例或发送社交消息。

GraphFlow 工具本轮不可用；context/plan/index 未执行，未宣称索引更新。配置不是通用地图；没有世界坐标读取、路径规划器、未知世界自主探索或真实避障能力。
