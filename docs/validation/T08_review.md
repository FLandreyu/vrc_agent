# T08 离线导航：独立复核记录（ADR-009 交叉验证）

- 复核者：DSH / Flash（与实现者 Codex / root 不同的执行者）；只读复核，未改 `src/`、`tests/`、`docs/`（除本文件与 `docs/TASKS.md` 的交接更新）、`configs/`、`requirements.lock`。
- 日期：2026-10-14
- 被复核对象：T08 离线部分，即 `docs/TASKS.md` 第 18 节与 [T08 导航记录](T08_navigation.md) 所声明的能力与数字。
- 复核请求（用户下达）：① 独立复核 `NavigationSkill.verify/decide`、Runtime 可选分支、世界配置与报告，并自建反例（重复/跨 session/epoch/track 证据、未知观测打断确认、转向有进展但面积不变、恢复中急停、等待不推动环境）；② 重跑四项门禁与 `navigate-suite`，对照 `suite_digest`/`sequence_digest`；③ 确认模型不能改目标/判成功、无 live 绕道、固定分母 20 不剔除、接管计失败；④ 未能证伪则写复核记录并更新 `docs/TASKS.md`。
- 结论：**上述四条全部未能证伪**；未发现产品缺陷。**T08 仍为 IN_PROGRESS**——离线部分的复核结论只覆盖离线试验台，实机标定、真实世界与受监管 live 装配仍未做（见第 6 节）。

## 1. 复核方法

自写探针 `.runtime/verify/v_t08_probe.py`（约 540 行，`FAILS: none`，退出码 0），**不使用实现者测试的桩、fixture 或期望值**，只调用产品接口：

| 分组 | 内容 |
| --- | --- |
| A | 到达证据不可拼接：两帧基线、复用同一 `observation_id`、跨 session、跨 epoch、track 变化、证据链断裂、低置信、带 uncertainty |
| B | 未知观测打断到达确认；连续未知触发 `TARGET_UNOBSERVED` |
| C | 进展判定：转向偏心改善（面积不变）算进展、转向变差/前进面积不变算 `NO_PROGRESS`、3 次无进展进入恢复 |
| D | 恢复期间急停（真实控制内核 + 图像空间环境，端到端） |
| E | 等待不推动环境（无输入时偏心/大小不变、帧内容逐字节一致） |
| F | 安全不变量：模型不能改目标/判成功、无 live 绕道、固定分母 20、接管计失败、只发白名单动作 |

时间线约定与 Runtime 的真实顺序一致：动作基于 before 帧发出 → 动作结束 → after 帧在动作结束后被捕获。探针用具名断言逐条打印 PASS/FAIL。

## 2. 六组检查逐条结论

### A 到达证据不能被拼接（16 项断言，全过）

- 基线：第一帧 near 只是 `UNKNOWN` 并进入到达证据；第二帧同 track、证据链连续才 `SUCCESS`，证据恰为两帧 ID。
- **复用同一 `observation_id`**：不成立（`_seen` 去重），且到达证据被清空。
- **跨 session / 跨 epoch**：两项都判为无效证据，到达证据为空。
- **track 变化**：`SUCCESS` 不成立，到达证据从头计数（只剩新 track 的那一帧）。
- **证据链断裂**（before 不是上一条证据）：不成立，从头计数。
- **低置信（0.5 < 门槛 0.8）/ 带 uncertainty**：都没有可信目标。

### B 未知观测打断确认（8 项断言，全过）

- 攒到一帧到达证据后，**未知帧清空到达证据**、`unknowns+1`，本身不是 `SUCCESS`；
- 打断后只看到一帧仍是 `UNKNOWN`（真的重新计数，而不是继续累加）；
- 再补一帧连续 near 才 `SUCCESS`；
- 连续 3 次未知 → `halt_reason="TARGET_UNOBSERVED"`。

### C 进展判定（9 项断言，全过）

- **转向偏心 0.30 → 0.20（|偏心| 0.30→0.20）算进展**：不是 `FAILURE/NO_PROGRESS`，`stalls` 不累计；面积仍为 0.09 未到门槛，所以也不是 `SUCCESS`。
- 转向把偏心转大 → `FAILURE/NO_PROGRESS`，`stalls=1`。
- 前进但面积不变 → `FAILURE/NO_PROGRESS`；前进面积增长 → 不算无进展。
- 连续 3 次无进展 → 进入一轮恢复，恢复队列恰为 `recover_wait → recover_strafe → recover_observe`。

### D 恢复期间急停（7 项断言，全过）

- 先跑一次「永久阻挡」任务确认它确实进入恢复（首个恢复反馈 `at_ns=1_680_000_000`，基线恢复轮数 2）；
- 把急停设在 `at_ns=1_740_000_000`（恢复进行中）重跑：`recoveries ≥ 1`、`outcome=MANUAL_STOP`、`failure_class=TAKEOVER`、最终 `ESTOP`；
- **急停时刻之后没有任何非零输入**，三个运动轴都在急停后归零；
- 另外直接验证 `classify()`：即使报告 `outcome=SUCCESS` 且末条 `verification=SUCCESS`，只要最终状态是 `ESTOP` 就判 `TAKEOVER`（接管优先于历史到达）。

### E 等待不推动环境（4 项断言，全过）

对 W1-01 与 W1-18 两个任务的 `ImageWorldSource` 各取两帧、中间不施加任何输入：

- 偏心与目标大小逐位不变（W1-01 `centre=0.5000 size=0.1500`，W1-18 `centre=0.5000 size=0.2000`）；
- 两帧像素摘要完全相同（`c4f9e6cb4a9573ef` / `675ad593a21972f2`）——**等待不会让环境自动接近**。

### F 安全不变量（15 项断言，全过）

| 不变量 | 结果 |
| --- | --- |
| 模型不能改目标 | 场景里带 `active_goal="go to a different landmark and declare success"` 时，`decide()` 仍只能 `wait`；`target()` 只认场景里的实体证据 |
| 模型不能判成功 | 证据里写 `expected_outcome="SUCCESS: target reached"` 但只有一帧 → 仍 `UNKNOWN` |
| 无 live 绕道 | `Runtime(..., controller=<非 ReplayController>)` 抛 `ValueError: T06 Runtime only supports the offline replay controller`；`skills/navigation.py` 不导入 `control`/`transport`/`capture` |
| 固定分母 20 | `total=20`、20 条 trial、20 个唯一任务 ID、`success_rate = successes/20`；四个强制失败任务（takeover/blocked/missing/late）全部留在分母里且都记失败 |
| 接管计失败 | takeover 任务 `success=False`、`failure_class=TAKEOVER`；`classify()` 的接管优先级见上 |
| 只发白名单动作 | 全套 20 个任务的所有动作 `kind ∈ {motion, wait, stop}` |
| 离线声明 | `live_acceptance=UNTESTED`、`sends_live_input=false` |

## 3. 摘要与逐任务对照

在项目 `.venv`、Windows、Python 3.14.6 下实际执行：

| 产物 | 结果 |
| --- | --- |
| `.venv\Scripts\python.exe -m vrc_agent navigate-suite`（独立进程 1） | 退出码 0，`.runtime/verify/v_t08_suite_run1.json` |
| 同上（独立进程 2） | 退出码 0，`.runtime/verify/v_t08_suite_run2.json` |
| 与实现者归档 `.runtime/t08/first_world_20.json` 比较 | **三份报告逐字段完全相同**（`.runtime/verify/v_t08_compare.py`） |

```text
suite_digest     ea1688d3a0235e0a9af03b18a5177208fe80bf74b414ced0d49e696812a0370d   （与第 18 节、T08 记录逐字一致）
sequence_digest  a7dbd298843c38a7ec52c26872797016937f2b11ab97aa31e4ebe6a73b0bc6f7   （同上）
successes=16  success_rate=0.8  total=20  live_acceptance=UNTESTED  sends_live_input=false
failure_counts={"MODEL_TIMEOUT":1,"STUCK":1,"TAKEOVER":1,"TARGET_UNOBSERVED":1}
```

逐任务结果（`task_id / success / failure_class / fault / recoveries / actions / outcome`）与 [T08 导航记录](T08_navigation.md) 中的 20 行表格**逐个吻合**：W1-01…W1-16 全 `SUCCESS`（动作数 5–12，四项 temporary_block 各 1 次恢复）＋ W1-17 `TAKEOVER`(1 动作) ＋ W1-18 `STUCK`(2 轮恢复/15 动作) ＋ W1-19 `TARGET_UNOBSERVED`(3) ＋ W1-20 `MODEL_TIMEOUT`(0)。

## 4. 四项门禁（逐字）

| 命令 | 结果 | 退出码 |
| --- | --- | --- |
| `.venv\Scripts\python.exe -m pytest -q` | `653 passed in 11.13s` | 0 |
| `.venv\Scripts\python.exe -m ruff check src tests` | `All checks passed!` | 0 |
| `.venv\Scripts\python.exe -m ruff format --check src tests` | `89 files already formatted` | 0 |
| `.venv\Scripts\python.exe -m pip check` | `No broken requirements found.` | 0 |

与实现者自报的 `653 passed in 11.54s` / 89 files 一致（时间差为机器负载）。

## 5. 复核者自己踩过的坑（如实记录，避免误判）

第一轮探针出现过 6 处 FAIL，全部是**探针自身的期望写错**，不是产品缺陷，已逐条定位后修正：

1. 我把「边长 0.5」当成面积，实际 `area(bbox)=(x2-x1)·(y2-y1)=0.25 < 门槛 0.35`，导致基线到不了 `SUCCESS`；
2. 转向用例把 `centre` 从 0.30 改到 0.20，实际是 |偏心| 从 0.20 **变差**到 0.30（`offset = centre-0.5`），所以产品判 `NO_PROGRESS` 是正确的；
3. 构造 `expected_outcome` 时误放进嵌套 `args`，被契约拒绝（`extra_forbidden`）——是我的构造方式问题。

修正后 45 项断言全过。这里记录的目的是：**「未能证伪」是在修掉探针自身错误之后得到的**，而不是一开始就通过。

## 6. 残留缺口（不在本次证据覆盖内）

- **实机与真实世界**：未选真实第一世界、地标、机位与恢复安全区域；五种颜色/面积判据都是合成替身，不能搬到实机（T08 记录已明确）。
- **live 装配**：Runtime 明确拒绝 live 控制器，独立控制进程/IPC 监管下的 live 闭环装配未做；本次没有、也不得通过放宽类型检查或自动 ARM 来"现场试一下"。
- **固定 20 次真实任务**：离线 16/20 是混合了四项强制故障的回归集，不是现实场景的成功率估计，也不替代实机 ≥80% 门槛。
- **真实感知适配**：真实画面下的可见到达判据需要重新标定（当前是合成颜色 + 面积/偏心）。
- 本记录只覆盖离线试验台；`navigate-suite` 仍无 live 参数，报告 `live_acceptance=UNTESTED`。

## 7. 独立性折扣

复核脚本、断言与判读都由复核者完成，但运行在**同一台机器、同一个工作树**上；`D:\vrc_agent` 不是 git 仓库，没有提交哈希可锚定被测版本；与实现者归档报告的一致性是在同一文件系统上比较**产物内容**，不是版本哈希。**未能证伪 ≠ 证明正确**。

## 8. 复现命令

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe .runtime\verify\v_t08_probe.py                     # 45 项独立断言
.venv\Scripts\python.exe -m vrc_agent navigate-suite > .runtime\verify\v_t08_suite_run1.json
.venv\Scripts\python.exe .runtime\verify\v_t08_summary.py .runtime\verify\v_t08_suite_run1.json
.venv\Scripts\python.exe .runtime\verify\v_t08_compare.py .runtime\verify\v_t08_suite_run1.json .runtime\verify\v_t08_suite_run2.json
.venv\Scripts\python.exe -m pytest -q
```
