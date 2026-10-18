# T08 v8 机械冻结（2026-10-18）

依据 TASKS 第 95 节 ADR-009 定向复核（`c1af37e`，摘要 `98bf0aa7…82e2ef`，声明主体未能证伪、无阻断项）及用户明确授权（“按你推荐的想法继续”，即在复核通过后冻结新摘要），本轮只做**状态位移**，不改任何像素判据、阈值、几何、预算或世界。

## 1. 冻结内容

| 项                                           | 冻结前                                                                                           | 冻结后                                                             |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------ |
| `stability_v8.json` `status`                 | `REQUIRES_FIELD_CALIBRATION`                                                                     | `FROZEN`                                                           |
| `version`                                    | `local-rgb-stability-v8-candidate`                                                               | 不变                                                               |
| 策略摘要（`stability_v8.json` 字节 SHA-256） | `98bf0aa7986f8349c678f6196e307239ad02df5945004c23266e9b740082e2ef`（第 94/95 节）                | `11b98adff4003ee62d40f3a6c96960f50d0d5c2d743a264f2f9dd14af2680cae` |
| 历史初版摘要                                 | `908af1df0eb214af1e26312d1eb308a1094feb96f37a478136894364e7bfc0bc`（第 90 节，父提交 `508d518`） | 历史保留，不可加载                                                 |

- `stability_v8.sha256` sidecar 已按冻结后字节重算（64 hex + CRLF，`-text`）。
- 版本名保持不变（沿用 v5/v6 的机械冻结做法）：改的是状态与摘要，不是判据；不升 v9。

## 2. 必需的加载器改动

冻结前 `stability.py` 的 v8 分支把 `status` 固定写死为 `REQUIRES_FIELD_CALIBRATION`，因此仅改 JSON 状态会让 `load_policy("v8")` 抛 `ValueError`。本轮按 v6 既有做法改为：

```python
expected_v8 = {
    **load_policy("v6")[0],
    "version": "local-rgb-stability-v8-candidate",
    "status": policy.get("status"),
    **DECLARATION,
}
if (
    type(policy.get("status")) is not str
    or policy["status"] not in {"REQUIRES_FIELD_CALIBRATION", "FROZEN"}
    or json.dumps(policy, sort_keys=True, allow_nan=False) != json.dumps(expected_v8, sort_keys=True)
):
    raise ValueError("invalid v8 acceptance candidate declaration")
```

其余 18 个像素/时限/包络键仍与 v6 严格相等比较，`DECLARATION`（七谓词识别、路径安全、作用域声明、到达语义）仍逐键相等——冻结只放开 `status` 取值闭集，其他任何改动仍被拒绝。

## 3. 测试更新（2 个文件）

- `tests/unit/test_acceptance_v8.py`：v8 期望状态改为 `FROZEN`；状态闭集循环扩到 v1–v8，`FROZEN` 集合为 `{v5, v6, v8}`。
- `tests/integration/test_acceptance_v8_runtime.py`：
  - `test_candidate_refuses_live_before_resources_and_base_bypass` → `test_frozen_v8_live_needs_consents_and_unfrozen_still_refuses`：冻结后 live 仍须标定/付费/图像三项同意（缺一在资源前拒绝）；**未冻结策略**仍在资源前拒绝 live（monkeypatch `status=REQUIRES_FIELD_CALIBRATION`）；冻结策略可通过策略门禁（哨兵证明）；“v8 装配必须用 v8 运行时”检查保留。
  - 报告篡改用例 `live`（把 dry-run 报告改标 live）在冻结后不再必然被拒，替换为 `max_actions` 篡改；新增 `test_unfrozen_policy_still_cannot_claim_live`，证明“未冻结策略的报告不得声明 live”这条门禁仍有效。

冻结不改变任何门禁的**语义**：同意门禁、`ControllerProcess` 类型要求、v8 运行时强制、报告重算与伪造拒绝、`control/` 内 30s 租约/急停/epoch/TTL/到期归零全部原样。

## 4. 验证

```text
$ .venv/Scripts/python.exe -m pytest -q
1309 passed in 402.21s (0:06:42)

$ .venv/Scripts/python.exe -m ruff check src tests
All checks passed!

$ .venv/Scripts/python.exe -m ruff format --check src tests
157 files already formatted

$ .venv/Scripts/python.exe -m pip check
No broken requirements found.

$ .venv/Scripts/python.exe -m vrc_agent navigate-live --task G2-1 \
    --config configs/navigation-live.toml --world-version v2
exit 0（默认只读计划，sends_live_input=false）
```

日志：`.runtime/tmp/freeze_v8_pytest2.log`、`.runtime/tmp/freeze_v8_dry.log`。

定向回归（冻结后）：`tests/unit/test_acceptance_v8.py` + `tests/integration/test_acceptance_v8_runtime.py` = `60 passed`。

### 4.1 过程中发现并修正的问题

首次全量门禁出现 `34 failed`，全部集中在 `tests/unit/test_acceptance_v8.py`，报 `ValueError: stability policy digest mismatch`。原因是编辑工具写回 `stability_v8.json` 时触发了 JSON 自动格式化（数组被折叠成单行），文件字节变化但 sidecar 仍是旧值。处置：用 Python 从 `git show c1af37e:src/vrc_agent/live_navigation/stability_v8.json` 取**原始字节**，仅做 `REQUIRES_FIELD_CALIBRATION → FROZEN` 单行替换后写回（CRLF 保持 127 处），再重算 sidecar；`git diff --stat` 恢复为 1 行差异，随后定向 60 项与全量 1309 项通过。该问题只影响字节完整性，未改任何判据。

## 5. 边界

- 这是**离线机械冻结**，不是实机验收：未启动 live、未 ARM、未调用付费模型、未发送真实输入、未读取密钥。
- `configs/navigation-live.toml` 仍为 `navigation_design="stable-inference-v6"`、`mode="dry-run"`；选用 v8 是独立的现场配置决定，不在本轮改动。
- 第 95 节 P1-3 记录的“`status=FROZEN` 被拒”是 `c1af37e` 当时的行为，已被本节加载器改动有意放开；其余篡改反例（旧声明、旧摘要、改 requires、删作用域字段）仍然被拒。
- 第 95 节非阻断观察 N4（模型误报 clearance 时整场危险信号不再拦运动）不因冻结而消失；20 次任务仍按第 93 节 `arrival_geometry_met` ＋人工逐条确认验收，不要求自动 SUCCESS。
