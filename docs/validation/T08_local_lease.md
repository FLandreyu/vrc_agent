# T08 本机热键续租 A（2026-10-17，离线实施）

依据 TASKS 第76节及用户明确授权；负责人 Codex/root。B 提交 `d1aa93b` 已独立交付；本项另行提交。T08 仍 IN_PROGRESS，不自动恢复现场20次任务；本记录不是 ADR-009 独立复核或实机验收，交付后仍须另派复核。

## 1. 权限与安全边界

Ctrl+F9 注册为 EXTEND（MOD_CONTROL | MOD_NOREPEAT，标识14）。F8/F9/F10 及探针映射不变。只有独立控制进程的本机热键分支调用 `extend_local`；模型 Proposal 和 Command 均无 extend/renew 操作，JSON/IPC 在 dry-run/live 均拒绝。命名停止信号仍只能停。运行时只读取账本，不发续租命令；没有自动 ARM/RESET/续租。

控制内核先拒绝 pending_releases，再 tick 检查到期/心跳，仅 ARMED_IDLE 或 EXECUTING、有效 lease、未到期、无待释放时授予；worker 还拒绝焦点不符或未释放探针按钮。ESTOP 同 tick 优先并丢弃其余热键，过期不能被续租复活，仍须 stop→完成释放→reset→重新 ARM。只增加 `lease_expires_ns`，不改 epoch、lease_id、pending_releases，不修改 active_until_ns 或动作 expires_at_ns。TTL 仍由 bind_request 默认1000ms提供，动作时限/归零/watchdog 不变。

每次默认加30000ms，最多5次；从 ARM 时刻计算总控制预算，不从按键时刻重开窗口。超过任务剩余预算则截断授予，没有剩余即拒绝。服务端总预算从冻结 v6 的180s读取。AppConfig 校验 `lease_ms + lease_extension_ms * lease_extension_max <= task_timeout_s*1000`，且仅显式 v6 可启用；默认 max=0，现场配置 max=5，基础 lease=30000，mode仍 dry-run。次数最大5、单次最大30000ms为配置契约硬上限；合法现场配置通常无须截断，但内核仍独立防御截断。

成功消息经非阻塞 outbox 送给主代理打印，控制循环不等待控制台 I/O；例如 `Lease extended: +60s (2/5), expires in 60s. F8=ESTOP`（剩余秒数按当时实际截止时间计算）。队列满沿用断连安全停止，通知不携带任何控制指令。

## 2. 遥测与报告

控制家族新增登记码 `LEASE_EXTENDED`，事件 `lease_extended`。EventLog 仅增加整数白名单 `at_ns/granted_ms/expires_ns/sequence`，保留原净化和未知字段丢弃规则。

v6 新报告 schema9：

- `lease_extensions`：每次 `at_ns/granted_ms/expires_ns/sequence`，来自独立控制进程快照。
- `control_budget`：`base_ms/extensions/total_ms/extension_ms/extension_max/task_timeout_ms/armed_at_ns/control_until_ns/elapsed_ms`。
- `events` 镜像每次 `lease_extended` + `reason_code=LEASE_EXTENDED`；周期状态检查及结束状态读取补齐记录，去重但不自动续租。

校验器重算序号、每次增量、累计截止时间/总预算、次数上限、事件对应、动作发出时的有效租约与≤1000ms TTL；超时长却无续租、缺事件、回填到过期后、grant/expiry/count/total造假均拒绝。schema7/8历史格式保留，新租约字段或事件夹带到旧 schema 拒绝。计时字段继续验证 nearest-rank 分位数和类型。

`elapsed_ms` 明确表示 ARM 到授权时间窗结束，不是包含等候 ARM、到期后的失败处理和停机清理的墙钟总耗时；`control_until_ns` 最迟为有效租约截止，提前 stop 则固定在 stop 时刻，重复 stop 不后移。因模型等待等原因报告结束晚于 lease 到期，可以如实报告 EXPIRED_LEASE；这不表示具有额外控制权。校验器仍拒绝预算中 elapsed 超 base 且无续租记录，且拒绝任何未被当时授权覆盖的动作。该账本是结构一致性证据，不是物理热键的密码学签名；不声称能阻止整份文件与全部证据一致重写。

## 3. 验证与未改清单

新增单元/集成回归覆盖：默认禁用、Ctrl+F9修饰符与探针并存、worker本机入口、急停同tick优先、pending/探针按住/失焦前置、过期/急停/DISARMED/PAUSED/心跳失败拒绝、次数与总预算/截断、活动动作TTL/时限不变、最终到期归零、重置再ARM清账、真实 dry-run IPC拒绝、整数遥测落盘、父进程控制台通知、超过30s且有续租的闭环报告、缺事件/超次数/超总时长/序号/截止/增量/迟到/无续租超时/TTL/旧schema伪造拒绝。具体门禁输出见 TASKS 第78节。

ARM起点与基础到期使用一次时钟读数，逐次递增时钟回归确认相差严格30000000000ns；修正前两次读取会产生纳秒差。新增续租定向37 passed in 8.50s。隐私/T10定向71 passed in 1.74s：白名单显式锁定新增四个整数，原R1b的44字符URL-safe base64反例参数化全部13个允许字段，新增字段同样无落点。首次全量唯一失败为旧白名单断言未同步（1 failed, 1165 passed），同步后重新跑完整门禁，不将这次失败记作通过。

默认只读计划退出0，关键输出 `mode="plan-only"`、`sends_live_input=false`、任务G2-1、世界v2摘要b65b3dab…，无gate拒绝。完整UTF-8输出 `.runtime/verify/t08_pilot_fix_plan.log`；此计划先于live资源门禁返回，不能证明live装配。字节/语义审计 `.runtime/verify/t08_pilot_fix_audit.json` 为PASS：14份冻结策略/世界代码快照一致，另四份世界JSON/sidecar与原冻结工作树快照逐字节一致，像素判据实现相对9ad071c无差异。

未改 v6 JSON/sidecar（`e46e0c2816c6f512fbd4fd5e5c60ff1ef3fc4d0560e0e8b29d3b6a6315caa513`）、v1–v5、世界/提示、Δ2/5%/保护区零容差/遮罩/预热2对、4000ms/180s/40步和运动档位；基础租约仍30s，急停/到期归零/epoch/TTL/pending释放未放宽。未启动live、未ARM、未调用付费模型、未发送真实输入、未读密钥。所有“live”测试都替换外部设施并使用内存传输。

GraphFlow 工具本轮未提供，context/plan/index不可调用，未伪造结果。

最终四项门禁（项目解释器，均退出0）：

```text
1170 passed in 275.60s (0:04:35)
All checks passed!
146 files already formatted
No broken requirements found.
```

分别为 `-m pytest -q`、`-m ruff check src tests`、`-m ruff format --check src tests`、`-m pip check`。全量原始日志 `.runtime/verify/t08_local_lease_pytest.log`；B独立门禁见第77节及成本文档。没有用定向测试取代全量回归。

## 4. 两次提交的文件清单

B：`d1aa93b`（9个文件）：

```text
docs/ARCHITECTURE.md
docs/TASKS.md
docs/validation/T08_runtime_cost.md
src/vrc_agent/live_navigation/stability_report.py
src/vrc_agent/live_navigation/stable_runtime.py
src/vrc_agent/live_navigation/timings.py
tests/integration/test_navigation_cost.py
tests/integration/test_navigation_v6.py
tests/unit/test_stability_v3.py
```

A（24个文件，提交号见本轮交付回报及 git log）：

```text
AGENTS.md
configs/navigation-live.toml
docs/ARCHITECTURE.md
docs/TASKS.md
docs/validation/T08_local_lease.md
src/vrc_agent/config.py
src/vrc_agent/control/core.py
src/vrc_agent/control/events.py
src/vrc_agent/control/platform.py
src/vrc_agent/control/service.py
src/vrc_agent/live_navigation/leases.py
src/vrc_agent/live_navigation/stability_report.py
src/vrc_agent/live_navigation/stable_runtime.py
src/vrc_agent/live_navigation/timings.py
src/vrc_agent/telemetry/__init__.py
src/vrc_agent/telemetry/codes.py
tests/integration/test_local_lease_integration.py
tests/integration/test_navigation_v6.py
tests/integration/test_probe_safety.py
tests/unit/test_lease_extensions.py
tests/unit/test_stability_v3.py
tests/unit/test_telemetry_codes.py
tests/unit/test_t10_privacy.py
tests/unit/test_t10_review_regressions.py
```
