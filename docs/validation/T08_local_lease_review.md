# T08 本机热键续租 + 运行时成本 ADR-009 定向安全复核

- 复核对象：`b2dbf32`（A 本机 Ctrl+F9 续租）与 `d1aa93b`（B 运行时成本预检/阶段耗时），基线 `9ad071c`。
- 复核范围：ADR-009 定向安全复核六项必查（第 80 节）。只做离线审读 + 自建探针 + 门禁复跑。
- **边界（重要）**：本轮**不是实机验收**。全程未启动 live、未触发真实 ARM、未调用付费模型、未读取任何密钥、未产生真实输入；未在操作系统层面实际按下 Ctrl+F9（热键只做注册元组审读 + 代码路径分析）。真实到达、稳定性通过率、20 次固定任务仍未验证。
- 结论：六项必查的声明主体**均未能证伪，无阻断项**；6 项非阻断观察见第 8 节。T08 仍 IN_PROGRESS。

## 1. 方法与证据

| 手段 | 说明 | 结果 |
| --- | --- | --- |
| 静态审读 | `control/{core,service,platform,events}.py`、`config.py`、`live_navigation/{leases,stability,stability_report,stable_runtime,timings,parameters,runtime}.py`、`telemetry/{__init__,codes}.py`、`configs/navigation-live.toml`、两份实现文档 | 逐行核对 |
| 自建探针 | `.runtime/verify/t08_lease_probe.py` + `probe_extra.py`（未纳管 git），A/B/C/D/E/F/G 七组 | **132 项，0 失败**（`.runtime/verify/t08_lease_probe.log`） |
| 门禁复跑 | `.runtime/verify/t08_lease_review_gates.log` | `1170 passed in 229.27s`、ruff check 通过、format `146 files already formatted`、pip check 通过 |
| 差分取证 | `git diff --name-only 9ad071c..HEAD` | 两提交合计 27 个文件，**不含** world/prompt/mask/calibration/`stability_v*.json` |

探针具备失败检测力：首轮曾报 7 项失败，逐项定位后**全部是探针自身的期望错误**（B3 误把 `duration 500 < TTL 1000` 当 EXPIRED_ACTION；B4/B6 只前进 30 s 未到续租后的 60 s 到期点；B19 未 `observe` 就 submit 故被 STALE_OBSERVATION 拒；D3/D4 取错报告字段；D6 用 `source.index` 造成的帧位错位；E4/E7 误判 v5 的 `status` 与 `motion_profiles` 形状）。修正后 0 失败，修正过程与失败原文保留在日志中。探针使用 fake provider + 内存控制核心；唯一的"真实进程"用例是 `ControllerProcess` 的 dry-run IPC（不建 live 通道、不 ARM）。

复核者复跑门禁得到 1170，与第 78 节自报的 1170 一致（自报耗时 275.60 s，本轮 229.27 s，属机器负载差异）；第 77 节 B 单独交付时的 1129 是 A 合入前数字，不是矛盾。

## 2. 第 1 项：续租只能由本机热键触发（未证伪）

| 路径 | 证据 | 结论 |
| --- | --- | --- |
| IPC 命令闭集 | `service.py` `Command.operation` 为 `Literal[...]` 11 值闭集，**无** extend/renew（探针 C1 打印全表） | 无法表达续租 |
| 大小写/别名/批量 | 探针 C2 用 `extend`/`renew`/`EXTEND`/`Renew`/`Extend`/`lease_extend`/`lease-extend`/`extend_lease`/`LEASE_EXTENDED`/`extend `（尾空格）/` extend`（前空格）/`Extend_local` 共 12 种构造 `Command`，全部 `ValidationError` | 全拒 |
| 模型提案 | `ActionProposal.kind` 闭集 7 值，`extend`/`renew`/`EXTEND` 全拒（C3/C4） | 无能力 |
| 线路层 | 向真实 `ControllerProcess` 的控制通道写非法命令 → `Command.model_validate_json` 抛错 → `disconnected` → 下一轮 `core.stop("HEARTBEAT_LOST", emergency=True)`（C8/C9 复现断链判定，不静默忽略） | 失败即停控 |
| 真实 dry-run IPC | `request("extend"/"renew"/"EXTEND")` 均 `ValueError`；之后 `state` 仍 `DISARMED`、`lease_extensions==[]`、`lease_expires_ns` 未变（C10/C11/C12） | 无副作用 |
| 唯一调用点 | `extend_local` 全仓**仅 1 处调用**（C5）；`extend_from_hotkey` 1 定义 + 1 调用（C6），位于 `service.py:212-219` 的 `event == "extend"` 分支，前置 `pending_button_releases` 为空与 `controls.focus_ok()` | 单点可达 |
| 热键注册 | `platform.py` 仅新增 `keys[14]="extend"` 与 `(14, 0x78, 0x4002)`＝VK_F9 + `MOD_CONTROL|MOD_NOREPEAT`；F8/F9/F10 与探针键未动（C7/单元测试 `test_hotkey_registration_preserves_bindings`） | 仅本机热键 |
| 状态回填 | `StableNavigationV6.rpc("status")` 仅在 `environment_epoch` 与 `armed_at_ns` 都与已授权快照一致时把状态存为 `control_snapshot`，并把 `lease_extensions` 镜像成报告事件（`stable_runtime.py:400-415`）；内核租约不受影响（C13：回填后 `lease_expires_ns`/`lease_id`/`epoch` 均不变） | 只写报告 |
| 事件回流 | `lease_extended` 的读取方只有 `live_navigation/leases.py:37-41`（报告重算）、`stability_report.py:139`（schema 隔离）与 `service.py:522`（控制台打印）；无任何代码把事件回灌内核 | 不可反向延长 |

限定说明：本项证明的是**代码路径单点性**，不是物理按键签名。若攻击者能重写全套一致证据（见 N3），本设计不声称可识别。

## 3. 第 2 项：续租不削弱到期语义（未证伪）

`Controller.extend_local()`（`core.py:79-112`）的拒绝顺序：`pending_releases` 非空（**在 `tick()` 之前**，防止靠本次 tick 消化待归零后再续）→ `tick()`（到期/急停/心跳优先）→ 复合条件（state ∈ {ARMED_IDLE, EXECUTING}、`lease_id` 存在、`now < lease_expires_ns`、`len(lease_extensions) < lease_extension_max`、`lease_started_ns` 存在）→ 剩余预算 `granted = min(lease_extension_ms, started+task_ms-expires)`，`granted<=0` 拒绝。

| 场景 | 探针 | 结果 |
| --- | --- | --- |
| ESTOP 后 | B12 | 拒绝续租 |
| 失焦 `FOCUS_LOST` 后 | B12b/B12c | 立即 PAUSED，拒绝续租 |
| `pending_releases` | B13/B14/B15 | 拒绝，`pending_releases` 仍为 2，租约未变 |
| 心跳失效 | B16/B17 | 拒绝，且 `reason=="HEARTBEAT_LOST"` |
| 租约到期后 | B6/B8/B9 | `EXPIRED_LEASE`/PAUSED/`lease_id is None`；再续租、再 ARM 均拒 |
| 到期动作归零 | B7/B11/B21 | transport 所有 AXES 全 `(0.0,)`，且归零消息可重发 |
| 动作 TTL | B2/B5 | 续租不改 `expires_at_ns`/`active_until_ns`；短 TTL 动作到点仍 `EXPIRED_ACTION` |
| 复活路径 | B19 | 只能 `reset()`→`arm()`：新 epoch、`lease_extensions==[]`、`expires-started==30 s` |

同 tick 优先级由代码顺序保证：worker 先 `estop`（并清空本 tick 其余事件）→ 再焦点检查 → 最后才处理 `extend`；A 的 `test_worker_local_extension_and_same_tick_estop_priority` 用同一 tick 的 `["estop","extend"]` 脚本验证只产生 `MANUAL_STOP` 且无 `lease_extended`。到期路径仍是 `stop()`→`lease_ended_ns=min(now, expires)`→`pending_releases=2`→`neutralize()`，续租不参与该路径。

## 4. 第 3 项：预算上限（未证伪）

- 配置层（`config.py`）：`lease_ms∈[500,60000]`（默认 30000）、`lease_extension_ms∈[1,30000]`、`lease_extension_max∈[0,5]`（默认 **0**）；非 v6 设计开启续租直接 `raise`；`lease_ms + lease_extension_ms*max <= task_timeout_s*1000` 否则 `raise`（A13/A14/A15/A16）。
- 内核对账：`granted = min(step, task_ms - total)`，过量截断（A8 截到 15 s、A10 截到 10 s），无剩余拒绝（A9/A11）；等值 180000 放行（A12）而配置 180001 拒绝（A13）。
- 基础 30 s 不变：现场配置 30000/30000/5（A15）、默认 max=0（A16）、ARM 单时刻基准恰 30 s（A17）、续租只 +30 s 且不改基准（A18）。
- 不被续租改动的量：`environment_epoch`/`lease_id`（A4）、动作 `expires_at_ns`/`active_until_ns`（B2）、动作 TTL 上限（`bind_request` 默认 1000 ms，校验器强制 `issued+1 ≤ expires_at_ns ≤ issued+1_000_000_000`，F3「动作 TTL>1s」被拒）。
- `total_ms == base_ms + Σgranted_ms` 由快照每次重算（A5/F1），不由调用方传入。

## 5. 第 4 项：报告不可伪造（相对事件流，未证伪）

`live_navigation/leases.py`（84 行）的重算规则：预算键集全等 → `base/step/limit/task_ms` 类型与范围 → `task_ms == policy.task_timeout_s*1000` 且 `base+step*limit ≤ task_ms` → 续租事件与记录**逐条全等** → `extensions==len(records)` → 记录时刻单调且 `at_ns < 当时 expires`、`sequence` 连续 → `granted==min(step, remaining)` → `expires` 递推 → `total` 递推 → `until ≤ expires` 且 `elapsed==(until-started)/1e6`（必须 float）→ 每条动作 `issued_at_ns ∈ [started, until]` 且 `issued < 动作签发时已生效的 expires`（**续租前签发的动作不能用续租后的窗口**）→ 动作 TTL ≤ 1 s。

探针 F3 的 24 种伪造**全部被拒**：次数+1、总时长+1、删续租事件、`elapsed>base` 却无记录、`sequence` 跳号、`expires-1ns`、`granted=30001`、续租早于 ARM、续租在旧到期之后、动作早于 ARM、动作晚于窗口、动作 TTL>1s、`extension_ms=0`、`extension_max=6`、`base_ms=499`、`task_timeout_ms` 改值、`control_until` 超出、`elapsed` 改整数、`elapsed` 差 1.0、记录/预算夹带额外字段、schema8 夹带租约字段、schema9 缺 `control_budget`/缺 `lease_extensions`。其中 `sequence`/`expires_ns`/`granted_ms`/`at_ns` 四类会**同步改写镜像事件**仍被拒，说明拦截不是「报告 vs 事件」的简单一致性比对。

**限定（N3）**：`control_budget` 是运行时自报量，校验只把它与自身事件流和策略 `task_timeout_s` 交叉，未与配置交叉。一个内部自洽的改写（如 `base_ms=60000` + 4 次扩展，合计仍 180000）可通过校验（探针 N3 用例确认）。因此本项的准确定论是：**不能在不自洽的前提下伪造，也不能把 30 s 控制权洗成更长的实际控制权**，但**不能识别整套证据一致重写**——与第 78 节自述一致。

## 6. 第 5 项：B 的预检不替代检查点 + `max_inference_attempts` 判定

结构证据（`stable_runtime.py`）：

- `reference_capture()` 在模型调用**之前**做预检，预检证明随后 `self.proofs.pop()` 并改记 `prechecks`（:441），**不进入** `stability_proofs`；`validates_observation_id` 只由 `before_submit()` 写入（:227）。因此预检在报告里既不是 after_inference 证据，也不是 before_submit 证据。
- 预检帧**不作为** roll 参考：`evidence` 取自原参考（:432-433）。
- 报告侧：`stability_report.py` 对 precheck 携带 `validates_observation_id` 直接 `raise ValueError("precheck cannot authorize actions")`；每个 `inference_source` 必须有「`reference_observation_id` 对齐 + `stable` + `checked_at_ns <= source.at_ns`」的预检，否则拒；每个动作必须有对应 `stability_proofs` 且 `checked_at_ns <= issued < captured_at_ns + freshness`。

探针实测：真实成功报告 after_inference 证明 3/3（每次推理一条）、before_submit 2/2（每个动作一条）（D4）；删除全部预检、删除 after_inference 证据、删除 before_submit 证据、预检夹带授权字段、预检全判不稳定、预检与推理源脱钩、删一条推理事件、分位数被改、分位数整数冒充——9 个反例全拒（D5）。单帧突变扫描（D6，8 个帧位）：**变化帧从未成为动作的 `based_on` 观测**（按帧摘要比对）；第 5/6 帧突变 → `SCENE_UNSTABLE`、1 次推理、0 动作；第 1–4 帧突变 → 预热重试后仍只有 2 对通过（D3），随后 3 次推理完成；预热重试**不产生额外模型调用**。

**`max_inference_attempts=3` 是否被削弱——判定：不构成削弱，但属语义收紧。**

- 上限仍是硬的：`observe()` 的 `for _ in range(self.policy["max_inference_attempts"])` 未改，`policy` 值仍为 3；探针 G3 实测单次运行 3 次推理（3 个动作 × 1），无路径超过 3。
- v6 只是更早拒绝：`retry_observe()` 直接 `raise RuntimeHalt("SCENE_UNSTABLE")`（:422-424），after_inference/before_submit 第一次拒绝即终止，因此**每次动作实际最多 1 次模型调用**（≤3）。这是收紧，不是放宽。
- 预检不能换推理次数：`reference_capture` 的 3 次循环只调 `fresh_proof`（比较），失败即 `SCENE_UNSTABLE`，不触发 `perception.infer`；预检失败也不消耗也不增加推理预算。
- 但与第 77 节声明一致，这确实改变了该字段的**实现语义**：从「最多 3 次推理尝试」变成「最多 1 次推理 + 最多 3 次预检比较」，而报告的 `run_parameters` 仍声明 3。记为非阻断观察 N2（文档/报告需注明"声明上限 vs 实际拒绝策略"），不构成对冻结值的安全削弱。

## 7. 第 6 项：不变项（未证伪）

| 不变项 | 证据 |
| --- | --- |
| `stability_v6.json` 字节 + sidecar | sha256 = `e46e0c2816c6f512fbd4fd5e5c60ff1ef3fc4d0560e0e8b29d3b6a6315caa513`，等于第 74 节冻结值；`load_policy("v6")` 通过，status `FROZEN`（E1/E2/E3） |
| 与 v5 的差异面 | 只差 `version`/`freshness_ms`/`task_timeout_s`/`motion_profiles` 四个键，`status` 同为 `FROZEN`（E4，探针按全 JSON 逐键比较） |
| 判据数值 | `pixel_tolerance=2`、`max_changed_ppm=50000`、`protected_margin=32`、`black_threshold=8`、`warmup_pairs=2`、`freshness_ms=4000`、`task_timeout_s=180.0`、`max_actions=40`、`max_inference_attempts=3`（E5） |
| 遮罩/保护区策略 | `excluded_rectangles=[[1536,0,2048,512]]`、`protected_mask_policy="exclude_masked"`（E6） |
| 保护区零容差 | 篡改 `protected_changed=1` → `invalid protected change count`（D9）；变更计数越界 142413 像素 → 拒（D10） |
| 2 对预热 | 首次推理前恰好 2 对通过（D3）；`validate_warmup` 强制 `indices` 长度=`warmup_pairs` 且链式承接 |
| 40 步 / 180 s | G1 报告 `run_parameters` 绑定 40；E5 `task_timeout_s=180.0` |
| 运动档 | `cautious` 0.2/200、`field` 0.4/500、侧移 0.2/200、转向 1.0/200（E7 全等比较） |
| 世界/提示/遮罩资产 | `git diff --name-only 9ad071c..HEAD` 的 27 个文件中无 `world.py`、无 prompt/mask/calibration 文件、无 `stability_v*.json`/`.sha256`；世界 v2 语义摘要 `b65b3dab…` 的资产未被本两提交改动 |

## 8. 非阻断观察

- **N1（预算注入点单薄）**：`Controller.__init__` 默认 `task_timeout_s=180.0`，只有 `service._worker` 按 `navigation_design == "stable-inference-v6"` 显式取值；`control/replay.py:25` 等调用点用默认 180。无续租时 `extension_max=0` 使截断分支不可达，故当前不改行为；新增设计键/新调用点时须同步（与第 79 节第 (2) 点同源）。
- **N2（声明上限 vs 实际策略）**：报告 `run_parameters` 仍声明 `max_inference_attempts=3`，v6 实际每次动作最多 1 次推理（第 6 节判定）。建议在报告或文档注明，避免读者误判重试预算。
- **N3（`control_budget` 自报）**：见第 5 节限定。校验能证明「与事件流自洽且 ≤ 任务预算」，不能证明「与进程内配置一致」。若要更强，可把 `lease_ms`/`lease_extension_ms`/`lease_extension_max` 纳入 `run_parameters` 并纳入重算。
- **N4（硬编码设计键）**：`service.py` 用 `config.navigation_design == "stable-inference-v6"` 单点判定任务预算，将来新增 v7 会静默退回 30 s/180 s 默认（第 79 节已提，本轮确认）。
- **N5（通知失败不可见）**：`extend_from_hotkey` 只吞 `OSError`，控制台不可用不影响已生效租约（设计如此），但主代理侧只表现为「无提示」，操作者可能误判热键未生效；建议后续把通知失败也记为事件。
- **N6（焦点检查位置）**：`focus_ok()` 只在 `service._worker` 的 extend 分支检查，`Controller.extend_local()` 自身不查焦点。当前调用点唯一（C5/C6），故不可绕过；若将来新增第二个调用点，会绕过焦点/按钮检查——属结构性风险提示。

## 9. 复跑命令

```powershell
.venv\Scripts\python.exe -m pytest -q                     # 1170 passed in 229.27s
.venv\Scripts\python.exe -m ruff check src tests           # All checks passed!
.venv\Scripts\python.exe -m ruff format --check src tests  # 146 files already formatted
.venv\Scripts\python.exe -m pip check                      # No broken requirements found.
.venv\Scripts\python.exe .runtime\verify\t08_lease_probe.py # 132 passed, 0 failed
```

日志：`.runtime/verify/t08_lease_review_gates.log`、`.runtime/verify/t08_lease_probe.log`；探针脚本 `t08_lease_probe.py`（主件）与 `probe_extra.py`（子件），均未纳管 git。

## 10. 未覆盖 / 遗留

1. **不是实机验收**：真实 VRChat 前台下 Ctrl+F9 的端到端效果、热键与被控窗口焦点竞态、现场 60 s 续租窗口下的到达能力，均未验证。
2. 20 次固定任务未恢复；续租对「每动作连续三关通过率」的影响未知（第 79 节关注点 1）。
3. 未见 N3 所述「整套证据一致重写」的防护手段；本设计不声称具备该能力。
4. 第 6 节判定依赖「`max_inference_attempts` 是上限语义」这一读法；若产品定义要求「每次动作必须尝试至 3 次」，则 v6 属有意变更，需按新版本流程明示，不得静默回退。
