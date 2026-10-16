# T08 live 路径独立交叉复核（ADR-009）

复核者：DSH / Flash（与本轮 live 装配的实现者 Codex / root、以及第 24 节现场冒烟会话均不同执行者）。
日期：2026-10-16。范围：`docs/TASKS.md` 第 22 节与 [T08 live 验证记录](T08_live.md) 声明的**新增 live 路径**（`live_navigation/` 四个模块、`navigate-live` / `navigation-report` 入口、冻结配置与摘要），以及用户下达的五条复核要求。
独立性：只读复核。**未修改任何产品代码、测试、配置与锁文件**；只新增本记录与三个自写探针（`.runtime/verify/v_t08_live_probe_{a,b,c}.py`、`.runtime/verify/v_t08_live_freeze.py`），并在 `docs/TASKS.md` 第 22 节登记结论。全部探针由复核者自写断言与自建素材，**不引用实现者的测试夹具**（`.runtime/verify/t08live/` 下的目录与 `replay_supervised.py` 均未使用）。

## 1. 被复核的声明（逐条对号）

| # | 声明（第 22 节 / 验证记录） | 本次复核结论 |
| --- | --- | --- |
| 1 | live 安全性：旧 `Runtime` 的 `ReplayController` 检查保留；新循环严格要求 `ControllerProcess`；显式 live + 本机 F9 ARM；无自动续租 / 换任务 / 备用 transport | 未能证伪（§2.1） |
| 2 | 老保护不因新路径回退：权限/观测校验、动作到期归零、急停、接管优先、预算闸门 | 未能证伪（§2.2） |
| 3 | 标定/配置/付费与图像授权门槛，命令面（默认只读计划） | 未能证伪（§2.3）；另解开一处摘要可重算性缺口（§3 F1） |
| 4 | dry-run / 计划安全：无 live transport、无 OSC 端口、不自动 ARM、无设备访问、无不受控工作线程 | 未能证伪（§2.4） |
| 5 | 验收记录可复查：冻结摘要、引用图、标定源摘要、批次汇总口径 | 未能证伪；标定源摘要**已确认可独立重算**（§2.5 / §3 F1） |

## 2. 逐条证据

### 2.1 live 安全性（探针 B，20 项断言 0 失败）

| 场景 | 构造 | 观察到的结局 |
| --- | --- | --- |
| 未 ARM | 只跑循环、不发 ARM | `ARM_TIMEOUT`；**从未采集**（信号源未被创建）、0 动作、0 输入 |
| 旧帧 | 捕获时间戳回拨 2 秒 | `STALE_OBSERVATION`，0 动作 0 输入 |
| 复用同一 `observation_id` | 每步返回同一个 ID | 第一步 `wait` 提交后即 `STALE_OBSERVATION`，只提交 1 个动作 |
| 伪造证据 | 实体 `evidence_observation_id` 指向别的观测 | `INVALID_SCENE_EVIDENCE`，0 动作 0 输入 |
| 越界参数 | `motion(forward=0.5)`（技能层放行） | `INVALID_ARGS`，未提交、0 输入 |
| 无侧移证据 | 缺 `recovery_clearance` 时请求侧移 | `RECOVERY_UNSAFE` |
| 无前进通行证据 | 缺 `forward_clearance` 时请求前进 | `PATH_UNSAFE` |
| 运动中急停 | 监视到 `EXECUTING` 即发 `estop` | `MANUAL_STOP` / 最终 `ESTOP`；三轴末值全 0 |
| 租约到期 | `ControlConfig(lease_ms=500)` | `EXPIRED_LEASE`；**无自动续租**，结束时保险归零 |
| 类型门禁 | 传入普通对象而非 `ControllerProcess` | `ValueError: independent ControllerProcess required` |

静态佐证：`src/vrc_agent/runtime.py:42-43` 的 `if not isinstance(controller, ReplayController): raise ValueError("T06 Runtime only supports the offline replay controller")` **原样保留**；`live_navigation/`（`runtime.py` 349 + `session.py` 228 + `skill.py` 84 + `world.py` 108 = 770 行）**不含任何** `socket` / `udp_client` / `Thread` / `subprocess` / `Popen` 引用——传输只在被注入的独立控制进程里。

### 2.2 旧保护未回退

- 旧控制面的回归测试仍在同一批次里跑（§4 门禁 789 passed），未跳过、未删改。
- 新循环每一步重新校验：观测新鲜度与会话/纪元、场景证据链（`INVALID_SCENE_EVIDENCE`）、动作参数限值（`INVALID_ARGS`）、前进/侧移的独立可通行证据、最多两轮恢复后有界停止（探针 C 中同类策略跑到 15 个动作即以 `RECOVERY_EXHAUSTED` 停下，**没有无限循环**）。
- 权限与到期语义仍由独立控制进程裁决：探针 B 的 `STALE_OBSERVATION` / `EXPIRED_LEASE` / `MANUAL_STOP` 均来自控制层拒绝或授权撤销，不是运行时自己的判断。
- 预算闸门（T10 第 23 节）在模型调用前生效；本次未调用任何付费供应商，也未出网（§2.4）。

### 2.3 授权与命令面（探针 C 的 C2 + 冻结探针）

`live_navigation/session.py:27-60` 的全部危险能力门禁都在**创建控制器（第 72 行）与构造供应商（第 63 行）之前**：

| 命令 | 结果 |
| --- | --- |
| `navigate-live --task G1-1`（默认） | 退出码 0，`mode=plan-only`、`sends_live_input=false`，五组到达判据均带 `ENGINEERING_INITIAL_FIELD_CHECK_REQUIRED` |
| `--mode live`（缺授权） | 退出码 2：`live requires calibration confirmation, paid and image consent` |
| `--mode live --confirm-calibration --allow-paid --send-images`（默认配置仍是 fake provider） | 退出码 2：`live requires a real image provider and positive explicit budget` |
| `--task G9-9` | 退出码 2：`unknown calibrated task` |

代码另有三道在同类位置的门禁：模型名不得是占位符、`enable_motion_probe` 必须关闭、采集必须 exact + 未遮挡 + DPI 感知且窗口标题与受控窗口一致；进入 live 后强制 `send_images=True`、`max_retries=0`、`timeout_ms=min(1000, …)`，并打印 `DISARMED … F9=ARM, F8=ESTOP … One task only; no automatic re-arm`。

### 2.4 dry-run / 计划安全（探针 C，12 项断言 0 失败；C1 哨兵见下、C2 门禁见 §2.3）

在 dry-run 受监管循环上挂哨兵：`socket.socket.sendto`、`urllib.request.urlopen`、`python-osc SimpleUDPClient` 全部**零触发**；`socket.connect/connect_ex` 只出现 1 次**回环**连接（`127.0.0.1`，asyncio 事件循环自管道），无任何外部地址。线程命名白名单外零越界：仅 `asyncio_0/1`（`asyncio.to_thread` 执行器）、`_do_shutdown`、多进程管道读线程与心跳线程。

报告口径：dry-run 报告固定 `mode=supervised-dry-run`、`sends_live_input=false`；`.runtime/t08_live/` 里的既存工件同为此口径，`navigation-report --report-dir .runtime/t08_live` 把它们如实计为 `invalid_files`、`successes=0`、`hourly.reason=NO_DATA…`，**不用 0 冒充数据**。

### 2.5 验收记录可复查

- **冻结摘要四处一致**：`suite_digest=dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335` 出现在配置派生值、`vrchat_home_live_v1.sha256` sidecar、加载器自校验、`navigate-live` 计划输出；注意它是对**规范化 JSON**（`sort_keys=True, ensure_ascii=False, separators=(",",":")`，`live_navigation/world.py:93-97`）计算，与文件字节摘要 `acb0c90d…` 不同（探针显式核对了这一点）。
- **引用图逐字节核对**：25 项引用（5 张到达图 + 20 张起点图）对应 23 张不同 JPEG，全部存在且 SHA-256 与配置逐字节一致；标定目录共 28 个文件（含候选图）。
- **标定源摘要可重算**（解开第 24 节的待补项，见 §3 F1）：`31c5e598b75bd6929df786e87cd9812402fbb7340010b092c24195a9fc6aa28a`。
- **结构不变量**：5 组地标、每组 4 项任务共 20 项且 ID 唯一；`front_repeat` 只出现在 G2-4/G3-4 且有非空替代理由；部分遮挡任务为 G1-4/G4-4/G5-4（G2/G3 未伪造遮挡机位）；到达阈值与标定记录一致（G1 `box 0.8/0.45`、G2 `box 0.22/0.23`、G3 `box 0.7/0.7`、G4 `box 0.045/0.15`、G5 `lower_half 0.6/0.25/0.3`）；18 种唯一起点描述；中文描述未被破坏。
- **篡改可检出**：改动任一阈值后规范化摘要变化（探针 A）。
- **批次汇总口径**：`summarize_reports()` 先做来源校验（`suite_digest` 必须等于冻结摘要、`mode` 必须为 `live`、`sends_live_input` 必须为 `True`），不合法文件单列 `invalid_files`；`threshold_met` 要求 ≥16 且无非法文件且每任务恰好一次尝试；`field_acceptance=REQUIRES_HUMAN_REVIEW`；`verified_success()` 对 `SUCCESS` 再复验数值证据（两个不同证据观测、同一 track、时间戳晚于动作结束、置信度达标、无 hazard/uncertainty）——记录里如实声明这不是数字签名或人工现场确认。

## 3. 发现

- **F1（可复查性缺口，已解开，非缺陷）**：第 24 节指出 `calibration_source_sha256=31c5e598…` 的生成/校验脚本未注明。复核确认它**可独立重算**，规则是：对 `docs/validation/T08_calibration.md` 以 UTF-8 文本读取后，取**文件起始到 `## 6.` 标题之前**的切片（13208 字节）作 SHA-256。等价地：`sha256(Path('docs/validation/T08_calibration.md').read_text(encoding='utf-8')[: text.index('## 6.')].encode('utf-8'))`。两个探针独立复算均命中（探针 A 的候选名 `prefix+LF`、探针 B 的 `1–5 节切片（规则）`）。建议把这条规则写进冻结流程（本次已写入第 22 节）。
- **F2（观察，非缺陷）**：dry-run 运行期父进程会出现 asyncio 执行器线程与多进程管道读线程（本次 4–12 个，随 IPC 调用数变化），均由授权组件创建，未见任何采集/音频/网络工作线程。实现者的"无不受控工作线程"表述成立，但"零线程"不成立——记录以免未来误读。
- **F3（口径提醒，非缺陷）**：报告文件用 `path.open("x")` 独占创建，路径为 `{task_id}-{session_id}.json`。同一会话重复执行同一任务会以 `FileExistsError` 失败而不是静默覆盖；不同会话产生不同文件名，因此"重复记录"只在跨会话时出现，并由 `summarize_reports()` 的 `DUPLICATE_ATTEMPTS` 捕获。这属于"不丢证据"的取舍，但现场执行者需要知道**同会话重跑要先清理旧报告**。
- **F4（残留缺口，属现场范围）**：真实视觉服务/模型/预算链路、真实推理时延、G3/G5 工程初值的现场核对，以及冻结顺序的 20 次真实任务**都未执行**；`live_acceptance` 仍为 `UNTESTED`，第 22 节归档的 dry-run 预览（`normal`/`stuck`/`late`/`estop`）不能当成功率。本次复核同样没有触发 live 传输、没有付费调用、没有发送输入。

## 4. 四项门禁（项目 `.venv`，实际执行、退出码均为 0）

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q` | **789 passed in 39.87s** |
| `python -m ruff check src tests` | `All checks passed!` |
| `python -m ruff format --check src tests` | `111 files already formatted` |
| `python -m pip check` | `No broken requirements found.` |

与第 23 节记录的 789 passed 数字一致（第 22 节的 702 passed 是 T10 之前的批次）。

## 5. 复核者自身错误（如实记录）

探针首轮共 7 处错误，全部属探针自身，修正后才得到上述结论：

1. 探针 B 未留 `__main__` 守卫 → Windows `spawn` 触发 `RuntimeError: An attempt has been made to start a new process before …`，控制器启动超时。
2. 探针 B 用**空字节**当 BGRA 帧 → `Frame.__post_init__` 校验 `width*height*4` 抛错，全部用例退化成 `RUNTIME_ERROR`（教训：`Frame` 会校验缓冲长度）。
3. 探针 B 访问 `LiveWorld.data`（该对象是 pydantic 契约，无此属性）→ `AttributeError`。
4. 探针 B 给子进程 `PATH=""` → CLI 子进程起不来，stdout 为空导致 JSON 解析失败。
5. 探针 C 把连接地址 `str()` 后做回环判定 → 把 `('127.0.0.1', 51749)` 误判成外部地址。
6. 探针 C 忘记给自建信号源赋 `self.authority` → `AttributeError` → 用例退化成 `RUNTIME_ERROR`（0 动作）。
7. 探针 C 首版断言"运行期零线程" → 与 asyncio/多进程的固有线程冲突，改为命名白名单。

## 6. 复现命令

```bat
set PYTHONUTF8=1
.venv\Scripts\python.exe .runtime\verify\v_t08_live_probe_a.py      :: 冻结配置与引用图（18 项断言）
.venv\Scripts\python.exe .runtime\verify\v_t08_live_probe_b.py      :: 受监管循环对抗用例（20 项，需 __main__ 守卫）
.venv\Scripts\python.exe .runtime\verify\v_t08_live_probe_c.py      :: dry-run 哨兵 + CLI 门禁（12 项）
.venv\Scripts\python.exe .runtime\verify\v_t08_live_freeze.py       :: 摘要/引用/结构/计划（25 项）
.venv\Scripts\python.exe -m vrc_agent navigate-live --task G1-1     :: 只读计划
```

## 7. 结论与折扣

- **五条要求全部未能证伪**：live 安全性、旧保护未回退、授权门槛、dry-run/计划安全、验收记录可复查均有可复现证据；未发现产品缺陷，唯一的可复查性缺口（标定源摘要规则）已解开并附在 §3 F1。
- **独立性折扣**：复核者与实现者不同执行者；探针自写、素材自建，但都在同一台机器、同一 Python 解释器与同一套依赖上运行；探针本身也曾出错（§5），判读由复核者完成。
- **状态**：`T08` **保留 IN_PROGRESS**。新增 live 路径已通过独立交叉复核，但真实 20 次现场任务未执行，`live_acceptance=UNTESTED`，不能把本次复核当作实机成功率。
- 本次复核未启动 live 控制、未发送真实输入、未出网、未调用付费供应商；除本记录与 `docs/TASKS.md` 第 22 节登记外未改动仓库。
