# T06 规划与离线闭环验证

日期：2026-10-13；实现与本轮自测：Codex / root；版本：0.1.0（无 Git 提交）。

**实现与离线自测完成；按 ADR-009，待另一执行者独立交叉复核，TASKS 的 T06 保持 IN_PROGRESS。** 本文证据均为本执行者实际运行，不冒充独立评审或实机自主游玩验收。

## 交付与权限边界

| 文件 | 职责 |
| --- | --- |
| `src/vrc_agent/planner/__init__.py` | Planner 异步协议、可信 NavigationGoal、LandmarkPlanner、ResultVerifier。只依赖契约，不导入 control/transport。 |
| `src/vrc_agent/runtime.py` | 观测→感知→规划→Proposal 复验→bind_request→执行→新观测→验证；deadline、取消、session/epoch、证据及时效守卫。 |
| `src/vrc_agent/control/replay.py` | 仅离线的 T03 Controller 适配；记录型输入、虚拟时钟分步 tick、定时急停。 |
| `src/vrc_agent/simulation.py` | 通过 T04 ReplaySource 生成原始观测/内存帧，合成模型从绿色像素提取目标，交给 T05 Perception，再装配 Runtime。 |
| `src/vrc_agent/planner/fixtures/*.json` | navigation/no_progress/model_late/estop 四份合成图像与故障时序素材。随 wheel 打包。 |
| `tests/unit/test_planner.py` | 规划安全、验证反例、planner 无执行依赖。 |
| `tests/integration/test_runtime.py` | 四条端到端回放、归零、权限注入、迟到、异常、取消、确定性与无网络。 |

修改现有接口仅为 `bind_request(..., *, clock=None)` 增加可信时钟注入，默认仍使用 SystemClock。模型不提供 action_id/session/lease/epoch/时间；运行时拒绝带权限字段的载荷，并从控制器快照和绑定观测补齐 ActionRequest。动作执行仍经过 T03 内核的授权、时效及参数校验。

CLI 增加 `simulate --scenario ...`，原 `run --mode dry-run` 固定脉冲示例保留。simulate 没有 live/付费开关，只装配 RecordingTransport；没有自动启用真实客户端的路径。执行子集为 motion/wait/stop；真实控制进程、热键、OSC 白名单与急停实现未放宽。

## 四条验收回放

用项目虚拟环境运行，退出码均为 0。CLI 的 0 表示回放正常结束，不表示目标一定成功；结果看 outcome 和 verification。

```powershell
.\.venv\Scripts\python.exe -m vrc_agent simulate --scenario navigation
.\.venv\Scripts\python.exe -m vrc_agent simulate --scenario no_progress
.\.venv\Scripts\python.exe -m vrc_agent simulate --scenario model_late
.\.venv\Scripts\python.exe -m vrc_agent simulate --scenario estop
```

| 场景 | 实际结果 | 关键验收证据 |
| --- | --- | --- |
| navigation | SUCCESS；2 个 motion | 第一次 COMPLETED 的 verification 仍 UNKNOWN；第二次动作结束后的 obs-000003 中目标面积达到 0.35 才 SUCCESS；关联 obs-000002/obs-000003。 |
| no_progress | NO_PROGRESS；2 个 motion | 两次后观测均有目标，但面积无增长，verification 均 FAILURE；连续 2 次后停止。 |
| model_late | MODEL_LATE；0 个动作、0 条记录型输入 | 合成模型在虚拟 800ms 才返回，超过运行时 500ms deadline；故意忽略供应商 deadline 的结果仍被运行时拒绝。 |
| estop | MANUAL_STOP；1 个 motion 为 CANCELLED/UNKNOWN；最终 ESTOP | 200ms 动作在 t=100ms 时急停；该时刻三个运动轴均发 0，后续无恢复动作。 |

四场景均将活动输入归零。每条已开始运动的 finished_at_ns 都有三轴 0 值记录；测试逐项断言，不能只靠退出时最终归零掩盖中间动作的残留。没有目标像素时只执行等待、不发送运动；缺少后观测或素材耗尽时不能验证成功。

完整 JSON 报告保存于 `.runtime/t06/{navigation,no_progress,model_late,estop}.json`（本轮由验证命令显式保存，产品默认只向 stdout 输出，不自动写媒体或报告）。确定性摘要：

```text
navigation  9c40eb376479d39c8516e2130a29a3e2d450fa43d87219171145355ab2b6618b
no_progress 08479730b11df1d8fbaac2189d4086df310c97b86c9f4444eafb7c0c22a330be
model_late  86f6edea569a30eecebc6caa8d8daab992f1056c9f23726b4313c7fe697ce73f
estop       ed2cdeb2e4154c5fc4024d19caabed8d091ae6066c58bf08dde8b06d67e7dd92
```

报告关联 session/observation/action；回放动作 ID 和报告 event_id 由可信运行时确定性编号。随机 lease_id 不导出，不得把报告当作可重放权限凭据。虚拟 at_ns 与原始捕获时间保留，不能因推理迟到而刷新旧证据。

## 安全与故障自测

新增 **57 项**测试：24 项 planner/verifier 单元测试、33 项 runtime 集成测试。

- Proposal 注入 lease/session/epoch/action_id/时间/任意 command 均拒绝；越界参数及本循环不支持的 chatbox/interact 不进入传输。
- 非 visible、缺帧、旧时间、重复观测、错误 session/epoch、错误实体证据无法形成成功链路；验证器有 17 类反例，包括旧目标、低置信、不确定性、hazard、多目标歧义、错误动作和非 COMPLETED。
- 目标由 NavigationGoal 固定，忽略模型 active_goal 与 expected_outcome 的执行/成功指令；规划器不接触 transport 有 AST 依赖检查。
- 规划期间急停或重新启用改变 epoch/lease 后，旧规划不能绑定新租约；不会自动 ARM。
- 真实 asyncio 等待也有墙钟截止期。挂起规划器会超时；捕获取消后才返回 motion 的规划器，其迟到结果仍被丢弃。
- 执行中取消 Runtime 任务，先急停归零再传播 CancelledError；采集异常在归零后报告 RUNTIME_ERROR，不打印可能包含密钥的异常内容。
- 四条回放各运行两次，完整报告逐字段相同；CLI 在两个独立进程输出相同。离线测试用 socket 哨兵检查产品回放不创建网络连接。

## 本轮检查

本机 Windows、Python 3.14.6、项目 `.venv`；以下最终检查退出码均为 0：

| 命令 | 实际结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q` | **504 passed in 28.67s** |
| `.\.venv\Scripts\python.exe -m ruff check src tests` | All checks passed |
| `.\.venv\Scripts\python.exe -m ruff format --check src tests` | 64 files already formatted |
| `.\.venv\Scripts\python.exe -m pip check` | No broken requirements found |
| `.\.venv\Scripts\python.exe -m pip wheel --no-deps --no-build-isolation . --wheel-dir .runtime/t06-wheel` | wheel 构建成功，无新增依赖 |

另外把 wheel 路径放在 sys.path 最前，确认 simulation 模块实际从 `.whl` 导入，再执行全部四个场景，结果与源码运行一致；验证了打包的 JSON 素材可读取。wheel SHA-256：`cb04634f849f13dfd606624181a1249f9f692a23230f4a35b067ffb8a7f4ba03`。此检查不等于干净系统全依赖安装验收。

本轮没有复现 T05 会话的 tmp_path 沙箱错误；全量 504 项通过包含既有进程控制、OSC 契约及 T00 探针回归。没有新增第三方依赖，requirements.lock 未改。

## 限制与独立复核入口

- 这是合成帧序列回放，不是动作驱动的物理模拟，也不是实机导航。目标变大只是可信配置的合成成功判据；不能推断真实世界距离、物体状态或通用导航成功率。
- 首版 Planner 是确定性策略，不是已接入付费 LLM 的规划器。真实供应商调用、自动世界加载识别、T07 语音、T08 实机导航/恢复仍未实现。
- ReplayController 与 Runtime 在同一进程，仅用于离线。虚拟时间 tick 和异常处理不能替代 live 独立 watchdog；无视取消或阻塞事件循环的第三方 Python 代码无法被安全强杀。本轮不开放 live 闭环。
- 测试由实现者编写和运行，尚无 ADR-009 所需的另一执行者交叉验证。复核应独立构造旧证据/新租约、迟到返回、取消中断、目标未出现但模型声称成功等反例，并检查归零时间点及报告关联，而非只重跑演示。
- GraphFlow 本轮无可调用工具；context/plan/index 未执行，不宣称索引同步。后续工具可用时补建索引。

下一步：独立复核上述闭环与安全边界；无问题后将 TASKS 的 T06 转 DONE。若复核发现问题，保留具体输入、输出与动作/观测 ID 回退修正。
