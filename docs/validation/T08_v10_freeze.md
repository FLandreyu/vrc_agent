# T08 v10 机械冻结

## 1. 授权与依据

- 依据：第 117 节（v10 主体与预算修正，结论“未能证伪、无阻断项”）与第 119 节（冻结前置状态闭集放开，结论同）。两者都是 ADR-009 定向复核记录。
- 授权：用户于 2026-10-20 明确指示“冻结”。
- 边界：机械冻结 = 只改策略 `status` 与 sidecar，不改任何判据、阈值、包络、时限、租约、`path_scan` 语义。**冻结不是实机验收**，固定 20 次任务与人工逐条到达确认仍未开始，T08 仍 IN_PROGRESS。

## 2. 摘要变化

|             | 候选期                                                             | 冻结后                                                                 |
| ----------- | ------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| 文件 SHA256 | `437849cfee57cc157cc01cb76a0bc56eb8ab5eade7510a31bff47d4a5c14bff3` | **`a1348cc3f8b222777b8b214b56ab7bf58d4ea4f837a7dead8f0361832881cbcb`** |
| `status`    | `REQUIRES_FIELD_CALIBRATION`                                       | `FROZEN`                                                               |
| 版本名      | `local-rgb-stability-v10-candidate`                                | 同名（与 v5/v6/v8/v9 做法一致，不以改名改变判据）                      |
| 字节长度    | 4056                                                               | 4031（少 25 字节 = 状态字符串差）                                      |

## 3. 改动方式（字节级，防摘要漂移）

`stability_v10.json` 是 `.gitattributes` 锁定的 `-text` 文件、CRLF 换行、149 个 CRLF。冻结脚本 `.runtime/tmp/freeze_v10.py`（不入 git）只做一次单行字节替换：

```python
OLD = b'"status": "REQUIRES_FIELD_CALIBRATION"'
NEW = b'"status": "FROZEN"'
assert raw.count(OLD) == 1
frozen = raw.replace(OLD, NEW)
```

并用两条断言兜住范围：原文该字节串只能出现 **1** 次；替换前后长度差必须恰为 25。随后 sidecar 写入 `sha256(frozen)` + `\r\n`（66 字节）。

`git diff` 结果：`stability_v10.json` 与 `stability_v10.sha256` 各 **1 行增 1 行删**，其它 148 行零改动——即未重排数组、未改键顺序、未动换行风格。

## 4. 同批改动

| 文件                                              | 改动                                                                                                                                                                                                                                             |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `stability_v10.json`                              | `status` → `FROZEN`（单行）                                                                                                                                                                                                                      |
| `stability_v10.sha256`                            | 重算为 `a1348cc3…cbcb`                                                                                                                                                                                                                           |
| `configs/navigation-live.toml`                    | `navigation_design` → `stable-inference-v10`（`mode` 仍 `dry-run`）                                                                                                                                                                              |
| `tests/unit/test_path_scan_v10.py`                | 新增 `DIGEST` 常量并断言摘要/ sidecar / CRLF 单行；主用例改断言 `status="FROZEN"`；闭集回归改为双向（两态都可加载，闭集外仍拒）                                                                                                                  |
| `tests/integration/test_path_scan_v10_runtime.py` | 候选拒绝用例改为 `test_frozen_v10_live_needs_consents_and_unfrozen_still_refuses`（照 v9 冻结做法：冻结不替代三项同意、候选仍拒 live、运行时自带候选拒绝仍在）；`live` 报告伪造项改为独立用例 `test_unfrozen_v10_policy_still_cannot_claim_live` |
| `tests/unit/test_t08_v5_proposal_config.py`       | 现场配置断言 `stable-inference-v9` → `stable-inference-v10`                                                                                                                                                                                      |

### 4.1 为何移除 `[live]` 报告伪造项

冻结前 `stability_report.py:271` 会在策略 `status != "FROZEN"` 时拒绝 `mode=live` 的报告。冻结后 v10 的 live 报告本身是合法状态，同一条变异不再构成造假——所以不能保留"断言必拒"的旧用例。改为独立用例：**冻结策略下 live 声明通过；把策略换成候选（monkeypatch `load_policy`）后同一份报告仍被 `cannot claim live` 拒绝**，即冻结没有放宽"候选不得冒充 live"。

## 5. 验证

定向回归：

```text
.venv/Scripts/python.exe -m pytest -q tests/unit/test_path_scan_v10.py tests/integration/test_path_scan_v10_runtime.py
44 passed in 54.20s
```

过程中两次失败均为我方断言写错（闭集双向用例误设"两态差异集合相同"，实测 v9 本身已 FROZEN、仅候选态的差异集合含 `status`），按实测更正，未放宽产品断言。

四项门禁（项目虚拟环境，日志 `.runtime/tmp/v10frozen_pytest2.log`）：

```text
.venv/Scripts/python.exe -m pytest -q
1504 passed in 373.21s (0:06:13)

.venv/Scripts/python.exe -m ruff check src tests
All checks passed!

.venv/Scripts/python.exe -m ruff format --check src tests
166 files already formatted

.venv/Scripts/python.exe -m pip check
No broken requirements found.
```

首轮门禁为 `2 failed, 1502 passed in 404.11s`（`.runtime/tmp/v10frozen_pytest.log`），两项失败都是现场配置断言仍写死 v9，同步后重跑得上述结果；失败证据保留，未覆盖。

## 6. 冻结后的现场边界

- 策略 `status=FROZEN` 使 `stable_runtime.py:38` 与 `session.py:83` 的 live 门槛**条件上**可通过，但现场仍有三重前置：`mode = "live"` 显式指定、`--confirm-calibration --allow-paid --send-images` 三项同意、以及本机 F9 人工 ARM。缺任一项仍在资源创建前拒绝。
- `configs/navigation-live.toml` 的 `mode` 仍是 `dry-run`，默认运行不发送任何真实输入。
- 现场复验清单承接第 117 节 N3：3–4 s 推理下无 clearance 场景约 15 决策 / 45–60 s，会先撞 30 s 基础租约，只能靠本机 Ctrl+F9 人工续租（最多 5 次、总 ≤180 s）；不自动续租。
- 20 次任务按 `arrival_geometry_met` + 人工逐条确认，不得用模型或自动 SUCCESS 替代。
