# T08 v9 机械冻结（2026-10-18）

依据 TASKS 第 101 节 ADR-009 定向复核、第 103 节 B1 修正件复核（**B1 已关闭，无阻断项**）及用户明确授权（回答“要我现在执行第 1 步（冻结 v9）吗？”为“好的”），本轮只做**状态位移**：不改任何像素判据、容差公式、阈值、几何、预算、世界或提示。

## 1. 冻结内容

| 项 | 冻结前 | 冻结后 |
| --- | --- | --- |
| `stability_v9.json` `status` | `REQUIRES_FIELD_CALIBRATION` | `FROZEN` |
| `version` | `local-rgb-stability-v9-candidate` | 不变 |
| 策略摘要（字节 SHA-256） | `990a1d7de902318f4ed04715adaa7bfd71c9c806c30fb90e82ad37a142ceef0e`（第100/101节） | `c9fb45ee3295766e62f3ceda2c4a744aab9dd45e580d36385168a59f97e700a2` |

- `stability_v9.sha256` sidecar 已按冻结后字节重算（64 hex + CRLF，`-text`）。
- 版本名保持不变（沿用 v5/v6/v8 的机械冻结做法）：改的是状态与摘要，不是判据。
- v8 摘要仍 `11b98adff4003ee62d40f3a6c96960f50d0d5c2d743a264f2f9dd14af2680cae`，未受影响。

## 2. 加载器无需改动

与 v8 冻结不同，v9 的加载分支（`stability.py`）自候选交付起就把 `status` 限定在 `{REQUIRES_FIELD_CALIBRATION, FROZEN}` 闭集（按 v6/v8 做法，为未来冻结预留入口），并要求其余声明逐键严格相等。因此本轮冻结：

- 只改 `stability_v9.json` 的 `status` 一行与 sidecar；
- 未修改 `stability.py`、`stability_v9.py`、`stability_report.py`、`leases.py`、`control/`；
- 判据（逐框可见面积、100 ppm、`ceil`、最少 1 px、最多 128 px、`changed > allowed` 拒绝）、`exclude_masked` 语义、全局 5%/Δ2、黑帧/尺寸/绑定检查全部不变。

## 3. 测试更新（2 个文件）

- `tests/unit/test_stability_v9.py`：`DIGEST` 常量改为冻结后摘要；v9 期望状态改为 `FROZEN`；状态闭集循环扩到 v1–v9，`FROZEN` 集合为 `{v5, v6, v8, v9}`。
- `tests/integration/test_stability_v9_runtime.py`：
  - `test_v9_candidate_refuses_before_all_live_resources` → `test_frozen_v9_live_needs_consents_and_unfrozen_still_refuses`：冻结后 live 仍须标定/付费/图像三项同意；**未冻结策略**仍在资源前拒绝 live；冻结策略可通过策略门禁（哨兵）；“v9 装配必须用 v9 运行时”保留；非冻结策略在运行时层仍拒绝 live。
  - 报告篡改用例 `live`/`sends` 在冻结后不再必然被拒（真实 live 报告本就会这样声明），替换为 `run_parameters`（`max_actions` 篡改）；新增 `test_unfrozen_policy_still_cannot_claim_live`，证明“未冻结策略的报告不得声明 live”仍然有效。

冻结不改变任何门禁语义：同意门禁、`ControllerProcess` 类型要求、v9 运行时强制、报告重算与伪造拒绝、30 s 基础租约/续租/急停/epoch/TTL/到期归零全部原样。

## 4. 验证

```text
$ .venv/Scripts/python.exe -m pytest -q
1405 passed in 620.80s (0:10:20)

$ .venv/Scripts/python.exe -m ruff check src tests
All checks passed!

$ .venv/Scripts/python.exe -m ruff format --check src tests
161 files already formatted

$ .venv/Scripts/python.exe -m pip check
No broken requirements found.

$ .venv/Scripts/python.exe -m vrc_agent navigate-live --task G2-1 \
    --config configs/navigation-live.toml --world-version v2
exit 0（默认只读计划，sends_live_input=false）
```

日志：`.runtime/tmp/v9_freeze_pytest.log`、`.runtime/tmp/v9_freeze_dry.log`。

定向回归（冻结后）：`tests/unit/test_stability_v9.py` + `tests/integration/test_stability_v9_runtime.py` + `tests/integration/test_v9_control_budget.py` = `96 passed in 61.91s`。

## 5. 边界与下一步

- 这是**离线机械冻结**，不是实机验收：未启动 live、未 ARM、未调用付费模型、未发送真实输入、未读取密钥。
- `configs/navigation-live.toml` 仍为 `navigation_design="stable-inference-v8"`、`mode="dry-run"`；**切换到 v9 是独立的现场配置决定**，不在本轮改动。
- 20 次任务、人工逐条到达确认、G1/G3/G5 遮罩交互复验、第 101 节 N2（预检 epoch 绑定）均未执行，T08 仍 `IN_PROGRESS`。
- 下一步：用户决定是否把现场配置切到 `stable-inference-v9` → 人在场试点（预期保护区 ppm 噪声不再否决，重点观察是否出现真实 motion、目标置信度 0.9 是否仍卡住、“有信号仍运动”动作的现场判读）。
