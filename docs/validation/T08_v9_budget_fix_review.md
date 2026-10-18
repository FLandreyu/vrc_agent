# T08 v9 冻结阻断 B1 修正件 ADR-009 定向复核

日期：2026-10-18；复核者：独立复核者（DSH / Flash，只审不改）；依据：TASKS 第101节 B1、第102节修正交付与用户定向复核任务书；被复核提交：`96917f4`（父 `77d1a5c`）。

本记录**不是冻结授权、不是实机验收、不是 20 次任务验收**。v9 仍为 `REQUIRES_FIELD_CALIBRATION`、仍拒绝 live；现场配置仍 `stable-inference-v8`、dry-run。

## 1. 结论

- **B1 已关闭**；本修正件的声明主体**未能证伪**，**无阻断项**。
- 自建探针 `.runtime/verify/t08_v9_b1_review_probe.py`（SHA256 `288f8c537b6bac35e32387dfa2399f8d2880e2bcc2485b60876fe54b9399219c`）**141 项断言 0 失败**；结果 JSON `.runtime/verify/t08_v9_b1_review_probe.json`，日志 `.runtime/verify/t08_v9_b1_review_probe.log`。
- 四项门禁由复核者在本提交、干净工作树复跑，均 exit 0（原始输出）：

```text
1405 passed in 537.68s (0:08:57)
All checks passed!
161 files already formatted
No broken requirements found.
```

- 默认只读计划 `.venv/Scripts/python.exe -m vrc_agent navigate-live --task G2-1 --config configs/navigation-live.toml --world-version v2` exit 0：`"mode": "plan-only"`、`"sends_live_input": false`、世界 v2 摘要 `b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0`。只读计划不等于可 live。
- 边界：B1 关闭只回答“控制进程预算装配是否已修好”。20 次现场任务、人工逐条确认、G1/G3/G5 遮罩交互复验、N2 预检 epoch 风险均未执行或未判定。

## 2. 为什么新建本文件而不是追加到第101节记录

`docs/validation/T08_stability_v9_review.md` 的复核对象是 `ab37394` 的 v9 判据/报告/装配交付；本次对象是其后 `96917f4` 的控制进程预算修正。两者是不同的提交与不同的问题域（判据 vs 控制装配）。若把结论追加到原文件，同一文档会混杂两个提交的门禁数字与结论边界，读者无法判断哪一段对应哪一份字节。因此新建本文件，并在 TASKS 第103节与原记录之间建立交叉引用；原记录保持与被复核字节一一对应。

## 3. 变更面与不变项

### 3.1 变更面

- `git show --stat 96917f4`：6 文件 `+304/−2`——`AGENTS.md +2`、`docs/TASKS.md +20`、`docs/validation/T08_stability_v9.md 4（+2/−2）`、新增 `docs/validation/T08_v9_budget_fix.md 86`、`src/vrc_agent/control/service.py +1/−0`、新增 `tests/integration/test_v9_control_budget.py 193`。`.gitattributes`、README 及世界/提示/configs 均未出现在该提交。
- `git diff --numstat`：生产代码唯一差异为 `src/vrc_agent/control/service.py`，`+1/−0`。该行是预算设计集合中的 `"stable-inference-v9",`（位于 `src/vrc_agent/control/service.py:171`，集合原为 v6/v7/v8）。
- 独立重建：取父提交该文件字节，在 v8 行后插入该行（LF），结果与当前字节**逐字节相等**（25081 字节）。
- 文档改动：`T08_stability_v9.md` 仅把第6节两行“当前 control 枚举仍只有 v6/v7/v8…未来冻结前必须核对”改为“第101节确认为 B1；第102节仅补入 v9 枚举行…待独立复核”，属事实更新，不改判据、不改摘要、不新增通过声明。
- **N1**：该文件实测为纯 LF（566 行、0 个 CRLF），而 `T08_v9_budget_fix.md` 第3节与 TASKS 第102节写“保留原CRLF”，与事实不符。差异实质（仅插入一行）成立，属文档措辞问题，非阻断。

### 3.2 不变项（逐字节）

复核者从两个提交的 git blob 直接比对，以下文件逐字节未变：`live_navigation/{stability_v1..v9}.json/.sha256`（16 份全部）、`leases.py`、`stability_report.py`、`stability_v9.py`、`stability.py`、`acceptance_report_v8.py`、`stable_runtime.py`、`runtime.py`、`session.py`、`config.py`，以及世界/提示/`configs/`。`src/` 与 `configs/` 全树比对只有 `control/service.py` 一个文件不同。

- v9 摘要：JSON 自述、`.sha256` sidecar、`load_policy("v9")` 三者一致 `990a1d7de902318f4ed04715adaa7bfd71c9c806c30fb90e82ad37a142ceef0e`，状态仍 `REQUIRES_FIELD_CALIBRATION`。
- v8 摘要仍 `11b98adff4003ee62d40f3a6c96960f50d0d5c2d743a264f2f9dd14af2680cae`。
- 现场 `configs/navigation-live.toml` 仍 `stable-inference-v8`、`mode="dry-run"`、基础租约 30000ms。
- clearance 几何、目标七谓词、到达语义、4000ms 新鲜度、180s 任务上限、40 步、3 次 attempt、预热 2 对、运动包络、急停/epoch/TTL/到期归零均无字节变化（对应文件逐字节一致）。

## 4. 枚举语义

- 预算集合 token 恰为 `stable-inference-v6/v7/v8/v9` 四项；无 `startswith`/`fnmatch`/正则前缀匹配，无未知设计直通。
- `task_timeout_s = 30.0` 兜底在分支之前，只有精确命中集合才用 `load_policy(config.navigation_design.rsplit("-", 1)[-1])[0]["task_timeout_s"]` 覆盖。
- `AppConfig(navigation_design=...)` 闭集仍拒绝 `stable-inference-v10`、`stable-inference-v09`、大写、尾空格；v9 被接受且模式仍 dry-run。
- v9 租约 30000 + 30000×5 = 180000 通过；若基础租约抬到 60000 则 `lease base plus extensions exceeds task_timeout_s`；v5/legacy 带扩展参数仍被拒（“require v6”）。

## 5. 真实进程装配（dry-run）

用真实 `ControllerProcess`（spawn + IPC，`mode="dry-run"`、RecordingTransport、无录制输入）逐一取 `initial_status["control_budget"]`：

| 设计 | task_timeout_ms | base_ms | extension_ms | extension_max | extensions | total_ms |
| --- | --- | --- | --- | --- | --- | --- |
| v9 | 180000 | 30000 | 30000 | 5 | 0 | 30000 |
| v6 | 180000 | 30000 | 30000 | 5 | 0 | 30000 |
| v7 | 180000 | 30000 | 30000 | 5 | 0 | 30000 |
| v8 | 180000 | 30000 | 30000 | 5 | 0 | 30000 |
| v5 / legacy | 30000 | 未断言 | 未断言 | 未断言 | — | — |

- 末行只断言“任务预算回落 30000”（本项要求即此）；v5/legacy 的其它预算字段不在本项结论范围内。

- v9 自定义 `lease_extension_ms=17000, lease_extension_max=3` 未被覆盖：`task=180000/base=30000/ext=17000/max=3`。
- v9 进程启动 `DISARMED`；`status` 的预算与 initial 完全一致；`recorded_messages=[]`、`sent_count=0`；IPC `extend`/`renew` 均被拒绝（“lease extension requires local hotkeys”）。无自动 ARM。
- 说明：这是真实子进程与真实 IPC 的 dry-run 装配，仍不含 live 采集、模型调用或真实输入，不构成实机验收。

## 6. 续租、到期归零与急停

假时钟 + RecordingTransport，`Controller` 用真实 worker 返回的 180000ms 预算重建（不是直接传默认 180s）：

- ARM 后基础租约为 30s。
- 首次本地续租：`granted_ms=30000`、`sequence=1`、`expires_ns = 初始到期 + 30s`，租约变 60s；执行中动作的 `expires_at_ns`、epoch、lease_id 不变。
- 在到期前 50ms 再发运动，到 60s：`reason=EXPIRED_LEASE`，该动作 `CANCELLED`、`active=None`、`lease_id=None`，记录 transport 上三个轴均被写 `(0.0,)`。
- 到期后续租被拒、直接重新 ARM 被拒、`lease_expires_ns` 不变（不可复活）。
- 未续租路径：30s 到期同样 `EXPIRED_LEASE`、动作取消、各轴归零。
- 急停路径（本次修复未触碰，仅回归确认）：执行中 `stop(emergency=True)` → `ESTOP`、动作 `CANCELLED`、`active=None`、各轴 `(0.0,)`、再 ARM 被拒。
- **N2**：到期后若归零的 pending release 尚未清空，第一次本地续租抛 `pending releases prevent lease extension`；清空后再试抛 `local lease extension unavailable`。两者都是拒绝、都不会延长租约；只是现场提示文案取决于时序。

## 7. schema12 报告复验

工件 `.runtime/verify/t08_v9_b1_report.json`（实现方内存闭环生成，复核者在独立进程重放其校验）：

- `schema_version=12`，设计 `local-rgb-stability-v9-candidate`，`outcome=SUCCESS`、`controller_pid=0`（实现方内存闭环，不是真实 worker 报告）；`control_budget.task_timeout_ms=180000`、`base_ms=30000`、`extensions=1`、`total_ms=60000`、`elapsed_ms=44017.0`；`lease_extensions` 记录 `{at_ns 10e9, granted_ms 30000, expires_ns 70e9, sequence 1}`。
- `validate_leases`、`validate_stability_report`、JSON 往返复验均通过；其预算字段与第5节真实 dry-run worker 的预算字段相等。
- 变异后被**两个校验器同时拒绝**：`task_timeout_ms=30000` → “lease configuration exceeds task timeout”；`task_timeout_ms=180001` → “invalid lease integer”；`base_ms=30001` → “lease configuration exceeds task timeout”；`total_ms=30000` → “elapsed control exceeds granted lease”；`extensions=0` → “lease extension count mismatch”。即 B1 不能被“改报告数字”或“放宽校验器”掩盖。
- 旧 v8 schema11 现场报告 `.runtime/live/G2-1-v8pilot1/navigation-live/G2-1-d5794dc1904d4c1b87cafb993a3763e2.json` 仍通过现行校验，且其 `task_timeout_ms` 仍为 180000；旧报告接受范围未被改变。

## 8. N2 边界

- `stability_v9.py` 逐字节未变；`verify_prechecks` 与预检 epoch 绑定未改；schema12 与旧 schema 隔离、旧报告接受范围均未变。
- 因此本轮**不判定第101节 N2**（预检重绑未显式比较 epoch 的报告审计风险），它仍留在现场复验清单：保存预检/来源的 session、epoch、观测 ID、attempt 与时间顺序；人工 ARM/RESET/epoch 变化边界核对有无旧预检复用；不一致见证单列给离线复核。

## 9. 非阻断观察

- **N1**：TASKS 第102节与 `T08_v9_budget_fix.md` 称“保留原CRLF”，实际该文件为纯 LF。实质差异（仅插入一行）经重建逐字节验证成立；建议后续文档改为“保留原行尾”。
- **N2**：到期后首次续租的拒绝文案可能是 `pending releases prevent lease extension`，清空后才是 `local lease extension unavailable`；语义相同（拒绝、不延长），仅现场提示时序差异。
- **N3**：B1 的根因是“配置闭集”和“控制进程预算枚举”两处独立维护同一设计集合。本次只补 v9 一项，未来新增版本仍可能重演同类漏项。建议后续（非本轮）改为无条件按 `load_policy` 读取并让未知/缺策略显式报错，或增加“配置闭集 ↔ 服务枚举”一致性回归；`config.py` 闭集与 `leases.py` 任务预算校验已能在离线报告层兜底，故不阻断本次。
- **N4**：本次探针与工件位于 `.runtime/verify/`，不属于产品交付物；结论只能通过本记录与 TASKS 交接。

## 10. 后续

B1 已关闭只代表“冻结前必修项已修好”。下一步仍须：用户决定是否冻结 v9 与切换现场配置（本轮未做、未授权）→ 人在场的现场复验（含 G1/G3/G5 遮罩交互、真实移动对照、N2 清单）→ 20 次任务与人工逐条确认。T08 保持 `IN_PROGRESS`。
