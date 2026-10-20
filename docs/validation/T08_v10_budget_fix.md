# T08 v10 冻结前预算装配修正（control/ 另案授权）

## 1. 背景与授权边界

第115节交付的 `stable-inference-v10` 候选在行为上完备，但存在与 v9 的 B1 同类问题：`src/vrc_agent/control/service.py` 的 180 秒预算枚举只列 `stable-inference-v6..v9`，因此即使策略声明 `task_timeout_s=180.0`，真实 dry-run 控制子进程仍回落 30 秒。

后果不是"预算小一点"，而是**冻结后报告必然被拒**：`live_navigation/leases.py` 的 `validate_leases()` 要求

```python
task_ms == policy["task_timeout_s"] * 1000
```

v10 的 schema13 报告带 `task_timeout_ms=30000`，与策略 180000 不符，校验直接抛 `lease configuration exceeds task timeout`。同时 30 秒任务预算下 `F9` 续租额度为 0，`control_budget` 到期即归零，现场无法完成任何需要换视角的任务。

本轮按用户"方案 1"授权，只做**预算枚举接线**这一件事，不触碰判据、包络、租约数值、`path_scan` 语义或任何候选/冻结状态。

## 2. 改动

`src/vrc_agent/control/service.py`（1 行，纯枚举追加）：

```python
        if config.navigation_design in {
            "stable-inference-v6",
            "stable-inference-v7",
            "stable-inference-v8",
            "stable-inference-v9",
            "stable-inference-v10",
        }:
```

v10 因此与 v6–v9 走同一分支：`load_policy("v10")["task_timeout_s"]`（= 180.0）。原有 legacy/v5 的 30 秒兜底不变，枚举外的未知设计仍由 `config.py` 在构造阶段拒绝。

## 3. 测试同步

`tests/integration/test_path_scan_v10_runtime.py` 中原先**把阻断固化为断言**的用例已改写：

|                   | 旧                                                              | 新                                                                         |
| ----------------- | --------------------------------------------------------------- | -------------------------------------------------------------------------- |
| 用例名            | `test_candidate_budget_mapping_remains_explicit_freeze_blocker` | `test_candidate_budget_mapping_is_wired_to_180s_and_worker_stays_disarmed` |
| `task_timeout_ms` | 断言 `30000`（阻断证据）                                        | 断言 `180000`（修复后）                                                    |
| 其它断言          | `state=DISARMED`、`base_ms=30000`、无发送                       | 同左，并追加管道 `extend`/`renew` 仍被拒绝                                 |

`tests/integration/test_v9_control_budget.py` 的参数化真实进程预算用例补入 `v9`/`v10`，现在覆盖 `v6/v7/v8/v9/v10 = 180000`、`v5/legacy = 30000`。

## 4. 验证

定向回归：

```text
.venv/Scripts/python.exe -m pytest -q tests/integration/test_path_scan_v10_runtime.py tests/integration/test_v9_control_budget.py
38 passed in 75.28s (0:01:15)
```

四项门禁（项目虚拟环境，均 exit 0，日志 `.runtime/tmp/v10b1_pytest.log`）：

```text
.venv/Scripts/python.exe -m pytest -q
1503 passed in 566.82s (0:09:26)

.venv/Scripts/python.exe -m ruff check src tests
All checks passed!

.venv/Scripts/python.exe -m ruff format --check src tests
166 files already formatted

.venv/Scripts/python.exe -m pip check
No broken requirements found.
```

1503 = 第 115.3 节基线 1501 + 新增 2 个参数化进程预算用例（v9、v10）。

## 5. 边界

- 本次修复**不构成**独立复核，也不构成实机验收；v10 仍为 `REQUIRES_FIELD_CALIBRATION` 候选，入口与运行时仍在资源/采集/推理/动作前拒绝 live，现场配置仍是 `stable-inference-v9` + world v3 + dry-run。
- 修复只影响控制进程的预算装配，不增加任何管道能力：`extend`/`renew` 仍被拒绝，ARM/RESET 仍只能本机热键。
- 与 v9 的 B1 处置一致：先修、再由另一执行者按 ADR-009 复核该增量，然后才谈冻结与现场切换。
