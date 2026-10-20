# T08 v10 冻结前置：状态闭集放开

## 1. 背景

第 117 节定向复核确认 v10 主体无阻断项，同时指出冻结前置：`src/vrc_agent/live_navigation/stability.py` 的 v10 分支把 `status` **钉死**为 `REQUIRES_FIELD_CALIBRATION`，因此只改策略 JSON 的 `status` 并重生成 sidecar 无法启用 v10——loader 会以 `invalid v10 path scan candidate declaration` 拒绝。

本轮按用户"方案 2"授权，只做**状态闭集对齐**（与 v6/v8/v9 既有做法一致），不改任何判据；第 117 节的 N1/N4/N5 非阻断观察本轮不处理。

## 2. 改动

`src/vrc_agent/live_navigation/stability.py` 的 v10 分支：

```python
        expected_v10 = {
            **load_policy("v9")[0],
            "version": "local-rgb-stability-v10-candidate",
            "status": policy.get("status"),
            "path_scan": PATH_SCAN,
        }
        if (
            type(policy.get("status")) is not str
            or policy["status"] not in {"REQUIRES_FIELD_CALIBRATION", "FROZEN"}
            or json.dumps(policy, sort_keys=True, allow_nan=False)
            != json.dumps(expected_v10, sort_keys=True)
        ):
            raise ValueError("invalid v10 path scan candidate declaration")
```

等价于 v6/v8/v9 的三段式：**闭集**（只允许 `REQUIRES_FIELD_CALIBRATION` 与 `FROZEN`）+ **类型**（必须是 `str`）+ **其余键严格相等**（仍与 v9 主体逐键比对，`path_scan` 仍与模块常量 `PATH_SCAN` 相等）。

**放宽的只有 status 这一个字段的取值集合**；`max_changed_ppm`、`pixel_tolerance`、`max_scans_per_task`、`yaw_step`、`task_timeout_s` 等任何其它键被改动仍然拒绝。

本轮**没有**修改 `stability_v10.json`/`.sha256`：v10 仍是 `REQUIRES_FIELD_CALIBRATION`、摘要仍 `437849cfee57cc157cc01cb76a0bc56eb8ab5eade7510a31bff47d4a5c14bff3`，live 仍被 `stable_runtime.py:38` 与 `session.py:83` 的 FROZEN 门槛拒绝。

## 3. 测试同步

`tests/unit/test_path_scan_v10.py`：

| 用例                                                                    | 变化                                                                                                         |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `test_candidate_loader_rejects_mutated_declarations[status]`            | 故障值从 `"FROZEN"` 改为 `"CANDIDATE"`（旧的 FROZEN 故障假设已不成立，闭集外的取值仍必须拒绝）               |
| `test_frozen_v10_declaration_loads_without_relaxing_other_keys`（新增） | 冻结后的 v10 可加载；与 v9 的差异仍只有 `{version, path_scan}`；`status` 闭集外的 `"FROZEN_APPROVED"` 仍被拒 |

新增用例先写错过一次断言（误以为冻结后与 v9 的差异含 `status`），实测 v9 本身已是 `FROZEN`、差异只有 `{version, path_scan}`，已按实测更正——不是放宽断言，而是更正事实假设。

## 4. 验证

定向回归：

```text
.venv/Scripts/python.exe -m pytest -q tests/unit/test_path_scan_v10.py tests/unit/test_stability_v9.py tests/unit/test_stability_v3_review_regressions.py tests/integration/test_path_scan_v10_runtime.py
80 passed, 1 failed  →  更正断言后 tests/unit/test_path_scan_v10.py 19 passed in 1.78s
```

四项门禁（项目虚拟环境，均 exit 0，日志 `.runtime/tmp/v10freeze_pytest.log`）：

```text
.venv/Scripts/python.exe -m pytest -q
1504 passed in 572.35s (0:09:32)

.venv/Scripts/python.exe -m ruff check src tests
All checks passed!

.venv/Scripts/python.exe -m ruff format --check src tests
166 files already formatted

.venv/Scripts/python.exe -m pip check
No broken requirements found.
```

1504 = 第117节基线 1503 + 1 个新增冻结声明用例。

## 5. 边界

- 本轮**不是冻结**：策略文件未动，v10 仍候选、仍拒绝 live；冻结仍需用户明确授权 → 改 `status` → 重生成 sidecar → 新摘要记录 → 本次增量经 DSH 复核 → 切 `navigation_design` → 人在场现场复验。
- 未改判据、阈值、包络、租约、`path_scan` 语义、世界、提示、`configs/`、`control/`；未启动 live、未 ARM、未调用付费模型。
