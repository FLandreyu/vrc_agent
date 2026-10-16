# T06 独立复核记录（ADR-009 交叉验证）

- 复核者：DSH / Flash（本会话，与实现者 Codex / root 不同的执行者）
- 日期：2026-10-13
- 被复核对象：T06 离线规划与端到端闭环，即 [T06 实现与离线自测交接](../TASKS.md)（`docs/TASKS.md` 第 15 节）与 [T06_runtime.md](T06_runtime.md) 所声明的能力与数字
- 复核请求（用户下达）：四条场景完整回放、旧权限隔离、迟到丢弃、归零时序、控制路径回归（`bind_request` 的 Clock 注入）、四项检查全跑；**未能证伪则 T06 → DONE**
- 结论：**六项检查均未能证伪**；T06 转 DONE。残留缺口与独立性折扣见文末。

## 一、复核方法

复核不使用实现者编写的测试与内部判定表，另写独立探针 `.runtime/verify/v_t06_probe.py`（625 行，仅读，不写产品文件）：

| 分组 | 内容 |
| --- | --- |
| C1 | 四场景各跑两次（摘要一致）、与实现者保存的 `.runtime/t06/<name>.json` **逐字段 `dict` 相等**、与硬编码期望值对照、`check_zeroing()` 归零/残留检查 |
| C2 | 逐字段伪造 7 种权限字段（`lease_id`/`session_id`/`environment_epoch`/`action_id`/`issued_at_ns`/`expires_at_ns`/`command`）+ 越界 `yaw=5.0` + `chatbox` + 无目标 `motion` + 模型自称 `SUCCESS` |
| C2b | 伪造观测守卫：旧 epoch、别的会话、旧时间戳、不可见窗口、无帧引用 |
| C3 | 迟到：响应在取消后仍返回；虚拟时钟越过 deadline |
| C5 | `bind_request` 语义回归（位置参数、默认 TTL、注入时钟、伪造权限被内核拒绝） |
| C6 | 离线哨兵：打桩 `socket.getaddrinfo` / `socket.create_connection` / `urllib.request.urlopen` 后重跑场景 |

## 二、六项检查逐条结论

### 1. 四条场景完整回放 —— 未能证伪

四条场景在受限沙箱中直读 CLI 输出，全部 `exit=0`，报告摘要与文档逐字相符：

| 场景 | outcome | 动作 | 关键事实 | `sequence_digest` |
| --- | --- | --- | --- | --- |
| `navigation` | `SUCCESS` | 2 | 第一条 `COMPLETED` 但 `verification=UNKNOWN`，证据对 `(obs-000001,obs-000002)/(obs-000002,obs-000003)`；面积 0.04 → 0.16 → 0.49，阈值 0.35；第三个观测在第二条动作结束后才出现 | `9c40eb37…b6618b` |
| `no_progress` | `NO_PROGRESS` | 2 | 两条 result 都是 `COMPLETED` + `reason_code=NO_PROGRESS` + `verification=FAILURE`；`inputs_released count=3` → `stopped PAUSED/NO_PROGRESS` → 关闭时再一次 `inputs_released count=3` → `stopped PAUSED/MANUAL_STOP`；`final_state=PAUSED` | `08479730…a330be` |
| `model_late` | `MODEL_LATE` | 0 | `actions/results/messages` 全空；events 只有 observation@1.0s 与 `runtime_finished@1.8s`；control_events 只有 `armed` + `inputs_released count=0` + `stopped PAUSED/MANUAL_STOP` | `86f6edea…697ce73f` |
| `estop` | `MANUAL_STOP` | 1 | `action-0001` 200ms 于 1.0s 发出、`finished_at_ns=1100000000`（+100ms 取消），result `CANCELLED/MANUAL_STOP` + `verification=UNKNOWN`；`final_state=ESTOP` | `ed2cdeb2…e7dd92` |

说明一处与用户描述的措辞差异（不影响结论）：`navigation` 素材的绿色目标在**第一帧就已可见**（面积 0.04，小于到达阈值），所以第一条动作是「有目标但没走到」，第二条才 `SUCCESS`；「第三帧出现」指的是**满足面积条件的观测**出现在第二条动作结束之后。复核以代码与素材实际数据为准，未按「第一帧没有目标」重写期望。

### 2. 旧权限隔离 —— 未能证伪

- 逐字段伪造 7 种权限字段、越界 `yaw=5.0`、`chatbox` 动作、无目标 `motion`、模型自称 `SUCCESS`：**全部 0 动作 / 0 输入**；伪造权限字段触发 `ActionProposal` 严格校验而落入 `outcome=RUNTIME_ERROR`，`chatbox` → `RuntimeHalt("UNSUPPORTED_CAPABILITY")`，无目标 `motion` → `RuntimeHalt("UNSAFE_SCENE")`，模型自称 `SUCCESS` 不产生任何 `SUCCESS`。
- 伪造观测守卫（旧证据不得进入决策）：旧 epoch(99) → `EPOCH_MISMATCH`、别的会话 → `SESSION_MISMATCH`、旧时间戳（1.0s 捕获、时钟 3.0s）→ `STALE_OBSERVATION`、`window_state="lost"` → `FOCUS_LOST`、`frame_ref=None` → `FOCUS_LOST`；五种情形均 **0 动作 / 0 输入**。

### 3. 迟到丢弃 —— 未能证伪

两种构造（取消后响应才返回、虚拟时钟越过 deadline）都是 `MODEL_LATE`，且 0 动作、0 输入、0 消息；`model_late` 场景的 CLI 报告印证同一行为。

### 4. 归零时序 —— 未能证伪

- 每个已执行动作的 `finished_at_ns` 都有三轴（`/input/Vertical`、`/input/Horizontal`、`/input/LookHorizontal`）归零记录。
- 急停时序逐条核对：`armed` → `action_result RUNNING` → `inputs_released count=3` → `action_result CANCELLED/MANUAL_STOP` → `stopped ESTOP/MANUAL_STOP` → `inputs_released count=3` → `stopped ESTOP/MANUAL_STOP`；1.0s 发出三轴（Vertical 0.2、Horizontal 0、LookHorizontal 0），1.1s 起四次三轴全 0。
- 无进展停止同样先归零再停止。

### 5. 控制路径回归（`bind_request` 新增 Clock 注入）—— 未能证伪

`control/service.py` 的签名 `bind_request(proposal, observation, snapshot, ttl_ms=1000, *, clock: Clock | None = None)`：Clock 为仅关键字且默认 `None`（默认走 `SystemClock`），前四个位置参数顺序未变，旧调用方式兼容。实测：默认 TTL = 1e9 ns；`ttl_ms=250` 生效；注入 `FakeClock(5_000_000_000)` 后 `issued_at_ns/expires_at_ns` 确实按注入时钟计算；伪造 epoch/lease 的绑定仍被控制内核 `REJECTED`；未伪造的绑定可正常提交。T00 验收过的控制路径用例在本次全量套件中全部通过。

### 6. 四项检查 —— 见第三节（受限沙箱内 1 failed/10 errors 全部为环境，完全权限下 504 passed）

## 三、门禁原始结果

### 受限沙箱内（默认工作方式）

| 命令 | 结果 | 退出码 |
| --- | --- | --- |
| `python -m pytest -q` | `1 failed, 493 passed, 10 errors in 4.15s`（收集 504） | 1 |
| `python -m ruff check src tests` | `All checks passed!` | 0 |
| `python -m ruff format --check src tests` | `64 files already formatted` | 0 |
| `python -m pip check` | `No broken requirements found.` | 0 |

11 项非通过全部可归因到沙箱，而非产品：

- `tests/integration/test_runtime.py::test_replay_cli_across_processes`：`subprocess.run(..., capture_output=True)` 在 `subprocess.py:1390` 的 `_winapi.CreatePipe` 抛 `PermissionError: [WinError 5]`——沙箱禁止管道捕获子进程输出。
- 10 个 error：7×`tests/integration/test_process.py`、1×`tests/unit/test_contracts.py::test_config_defaults_and_secrets`、2×`tests/unit/test_telemetry.py`（`test_logs_do_not_persist_text_or_secrets`、`test_logs_keep_parameter_value_field`）；根因都是 `tmp_path` fixture 在 `_pytest/pathlib.py:175` 的 `os.scandir('C:\Windows\Temp\dsh-4hgbnb\pytest-of-sunyongjie')` 抛 `PermissionError: [WinError 5]`。

### 沙箱机制诊断（为什么这些失败不是项目缺陷）

自写 `.runtime/verify/scandir_probe.py`：`os.scandir` 对 `D:\vrc_agent`(19 项)、`.runtime`(33)、`.runtime\verify`(82)、`C:\WINDOWS\TEMP\dsh-4hgbnb`(2) 全部正常；对 `D:\vrc_agent\.runtime\pytest-tmp` 与 `C:\Windows\Temp\dsh-4hgbnb\pytest-of-sunyongjie` 都抛 `PermissionError [WinError 5]`；进程自己新建目录再 `iterdir()` 正常。

技能 `diagnose-windows-sandbox-acl` 的诊断脚本（一次性完全权限运行）判定 `VERDICT=NOT_THIS_CLASS`、`nextAction=stop`、`FIXED=0 GRANTED=0 REFUSED=0 RESTORED=0`，**未做任何权限更改**（报告：`D:\vrc_agent\.runtime\acl-repair\acl-report-6f84d4e8dd6247ba9c784c352f9fd968.jsonl`）。其机制证据：`pytest-tmp` 的 DACL 只有 3 条（`S-1-3-4` OWNER RIGHTS、`S-1-5-18` SYSTEM、`S-1-5-32-544` Administrators），**没有当前用户 SID `S-1-5-21-517890285-2284004226-3804744640-1001`**，`LOW_LABEL=True`、`MY_RIGHTS=[]`；而 `.runtime`、`D:\vrc_agent` 有 `S-1-5-11` Users 与沙箱 SID 的继承项。即：受限沙箱在创建临时目录时给出「受保护 + 低完整性标注 + 无用户/应用包 ACE」的权限集，随后受限进程自己无法枚举，也无法删除（`Remove-Item` 与 pytest 的 `rm_rf` 都失败）。这是沙箱 provisioning 边界，不是仓库内容的问题，按技能要求不手工编辑权限。

### 完全权限下（一次性授权重跑，用于确认上表 11 项）

`python -m pytest -q` → `504 passed in 11.51s`，退出码 0，收集 504 项——与实现者自报的 `504 passed` 一致（时间差异来自机器负载）。四项检查因此全部成立。

## 四、结论、残留缺口与独立性折扣

- **结论：六项检查均未能证伪，T06 → DONE。** 四条场景的报告摘要与实现者保存的 `.runtime/t06/*.json` 逐字段相同，且由不同进程重跑得到相同摘要。
- 残留缺口（不因本次复核消失）：合成帧不是物理仿真，面积判据不能直接用于真实导航；无实机输入、无付费模型、无自动世界加载识别；Runtime 只接离线控制适配，不能替代 live 独立 watchdog；CLI 跨进程一致性在受限沙箱内无法直接跑该项测试，本次改为「不同进程分别跑 CLI 并比对摘要」+ 完全权限下重跑全量套件来覆盖。
- 独立性折扣如实记录：探针与判读都由复核者完成，但运行在同一台机器、同一个仓库工作树上；`D:\vrc_agent` 不是 Git 仓库，没有提交哈希可锚定被测版本；实现者保存的 `.runtime/t06/*.json` 与本轮 CLI 输出比对的是**产物内容**，不是版本哈希。

## 五、复现命令

```powershell
# 六项检查的独立探针（只读，不写产品文件）
.venv\Scripts\python.exe .runtime\verify\v_t06_probe.py            # C1/C2/C3/C5
.venv\Scripts\python.exe .runtime\verify\v_t06_probe.py --guards  # C2b/C6

# 四条场景（直读终端输出；本沙箱禁止管道捕获与文件重定向）
.venv\Scripts\python.exe -m vrc_agent simulate --scenario navigation
.venv\Scripts\python.exe -m vrc_agent simulate --scenario no_progress
.venv\Scripts\python.exe -m vrc_agent simulate --scenario model_late
.venv\Scripts\python.exe -m vrc_agent simulate --scenario estop

# 四项检查（受限沙箱内 10 errors / 1 failed 均为环境，见第三节；完全权限下为 504 passed）
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check src tests
.venv\Scripts\python.exe -m ruff format --check src tests
.venv\Scripts\python.exe -m pip check
```
