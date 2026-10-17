# T08 v5 冻结实施与验证

2026-10-17，Codex / root，实现方交付；对应 TASKS 第63/65节，独立提交①。

## 1. 范围与摘要

依据用户冻结决定，仅将 `stability_v5.json` 的 `status` 从 `REQUIRES_FIELD_CALIBRATION` 改为 `FROZEN`。保留版本名 `local-rgb-stability-v5-candidate`、schema6、exclude_masked 和全部阈值、遮罩、判据。旧候选字节摘要 `1cc4287a4c944ae898012e016ee21b591041f1eb7000041844312c3ec02de21c`；冻结后字节 SHA256：

`2e21c734ec902c44b3111de8f6f62f06626fbbe9410ff9a0b959469248eb4faa`

sidecar 同步重算；旧摘要的候选工件保留为历史，不能冒充本次冻结策略的报告。`navigation-live.toml` 根配置增加 `navigation_design = "stable-inference-v5"`，仍为 `mode = "dry-run"`；本提交配置字节 SHA256 `f3e1a487f1cdc5d57bd4a2684a4941a5b5d26d7fda97d40abe74223815372079`。JPEG 改造留给独立提交②。

冻结前快照 `.runtime/verify/t08_freeze_before.json` 与审计 `t08_freeze_audit.json` 实际核对：v5 对象差异集合恰为 `{status}`；v1–v4 策略及 sidecar、世界 v1/v2 JSON 及 sidecar 的12个文件均字节不变。世界 v2 语义摘要仍 `b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0`；到达规则和提示不改。

## 2. 状态门槛回归

`test_stability_v5.py` 锁定新摘要和 FROZEN；与 v4 的差异清单增加 status。配置测试要求 dry-run 与显式 v5。集成测试以内存客户端及资源创建哨兵证明：候选状态在模型/控制器/采集之前拒绝；FROZEN 通过该状态门槛；基类/工厂版本限制仍生效。原候选参数集保留 v1–v4。世界 v2 装配测试覆盖候选/FROZEN 两种状态；运行时构造不产生采集、模型调用或发送。

FROZEN 只解除策略候选状态门槛，显式 live、标定/付费/图像授权及本机 F9、lease/epoch 等既有安全门槛继续有效。未删除 ReplayController 类型检查，未切换 transport 或自动 ARM。

## 3. 实跑门禁

解释器 `.venv/Scripts/python.exe`；命令从项目根执行：

| 命令（解释器后参数） | 结果 | 日志（`.runtime/verify/`） |
| --- | --- | --- |
| `-m pytest -q` | 991 passed in 126.55s | `t08_freeze_pytest.log` |
| `-m ruff check src tests` | All checks passed | `t08_freeze_ruff.log` |
| `-m ruff format --check src tests` | 133 files already formatted | `t08_freeze_format.log` |
| `-m pip check` | No broken requirements found | `t08_freeze_pip.log` |

GraphFlow 工具本会话不可用，未声称执行 context/plan/index。未启动真实 live、未 ARM、未付费调用、未发送真实输入。本记录为实现方验证，不能替代独立复核、生产编码延迟复测、固定20次真实任务或 T10 实机费用证据；T08 保持 IN_PROGRESS。
