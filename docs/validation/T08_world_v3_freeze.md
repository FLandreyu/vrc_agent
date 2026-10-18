# world v3 机械冻结（2026-10-18）

依据 TASKS 第 111 节 ADR-009 定向复核（`aa25324`，**声明主体未能证伪、无阻断项**）及用户明确授权（回答“是否冻结 world v3”为“冻结”），本轮只做**状态位移**：不改任何门槛取值、几何、任务、提示、世界血统或策略。

## 1. 冻结内容

| 项 | 冻结前 | 冻结后 |
| --- | --- | --- |
| `vrchat_home_live_v3.json` `status` | `REQUIRES_FIELD_CALIBRATION` | `FROZEN` |
| `version` | `vrchat-home-live-v3` | 不变 |
| 语义摘要（规范化模型摘要） | `3e7b4566cfabcb6d2527c5d221b4e9d0427b542adb8d6183bc13cf90d3d9885f` | `1da677140aad5a76920a7ad5cd88a5ffc1899300062cb852392b9483067f19f7` |
| 文件字节 SHA256 | `1ab60148…bb0b33` | `48fc107954c7e76a71e7f40f947ed21989330239c332dfbb3eaef937a041171b` |

- sidecar `vrchat_home_live_v3.sha256` 已按**规范化模型摘要**重算（`json.dumps(model_dump, sort_keys, separators)` 的 SHA-256 + CRLF），与世界 loader 的校验口径一致；**不是**原始文件字节哈希。第 110/111 节已记录二者不可混称。
- 门槛取值不变：G1/G3/G5 = 0.85，G2/G4 = **0.7**；clearance 实体的 0.9 与几何不变。
- v1 `dda1d3b1…`、v2 `b65b3dab…` 世界与 sidecar 未动；稳定性策略 v1–v9 未动（v9 仍 `c9fb45ee…`、`FROZEN`）。
- 复核曾提示“改 status 会改变语义摘要，冻结时必须同步重生成 sidecar”，本轮已按此执行。

## 2. 候选门禁随状态变化

`LiveWorldV3.require_live()` 依据 `status != "FROZEN"` 拒绝；冻结后：

- 会话装配与 `SupervisedNavigation` 构造不再以 “candidate world …” 拒绝 v3；
- 其余门禁不变：live 仍需显式 `--mode live`、标定/付费/图像三项同意、真实 `openai_compat` 供应商与正预算、精确且不被遮挡的窗口采集、本机 F9 ARM；IPC 仍拒绝 ARM/续租。

## 3. 测试更新（2 个文件）

- `tests/unit/test_world_v3_confidence.py`：`WORLD_DIGEST` 改为冻结后语义摘要；v3 期望状态改 `FROZEN`。
- `tests/integration/test_world_v3_runtime.py`：
  - `test_candidate_world_refuses_live_before_any_resource` → `test_frozen_world_v3_live_needs_consents_and_unfrozen_still_refuses`：**未冻结世界**仍在任何配置/资源前拒绝 live（`monkeypatch` 一个候选副本）；冻结后仍须三项同意（缺一在资源前拒绝）；冻结世界可通过世界门禁（哨兵证明）；运行时层对候选世界仍拒绝；只读计划显示 `FROZEN`。
  - 报告篡改用例移除 `live`/`send`（冻结后声明 live 本身合法），保留 `missing`/`0.85`/`0.9`/`True`/`"0.7"`/`digest`；新增 `test_unfrozen_world_report_cannot_claim_live`，证明未冻结世界的报告仍不得声明 live。

没有删除或放宽任何门禁；判据语义零变化。

## 4. 验证

```text
$ .venv/Scripts/python.exe -m pytest -q
1458 passed in 290.12s (0:04:50)

$ .venv/Scripts/python.exe -m ruff check src tests
All checks passed!

$ .venv/Scripts/python.exe -m ruff format --check src tests
163 files already formatted

$ .venv/Scripts/python.exe -m pip check
No broken requirements found.

$ .venv/Scripts/python.exe -m vrc_agent navigate-live --task G2-1 \
    --config configs/navigation-live.toml --world-version v3
exit 0（只读计划，sends_live_input=false）
```

日志：`.runtime/tmp/w3_freeze_pytest.log`、`.runtime/tmp/w3_freeze_plan.log`。
定向回归（冻结后）：`tests/unit/test_world_v3_confidence.py` + `tests/integration/test_world_v3_runtime.py` = `53 passed in 33.02s`。

## 5. 边界与下一步

- 这是**离线机械冻结**，不是实机验收：未启动 live、未 ARM、未调用付费模型、未发送真实输入、未读取密钥。
- `configs/navigation-live.toml` 未改（仍 v9 稳定性 + dry-run）；世界版本由命令行 `--world-version v3` 选择。
- 现场预期（第 111 节 N5）：G2-2 应能通过识别并拿到 motion（42 条复现中 clearance 8/21）；**G4-1 即使识别通过，clearance 为 0/21，仍可能只能等待**——那是 clearance 输出问题，不是本冻结的缺陷。
- 20 次任务与人工逐条确认未开始；T08 仍 `IN_PROGRESS`。
