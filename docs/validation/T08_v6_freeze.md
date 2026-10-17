# T08 v6 机械冻结记录（2026-10-17）

依据 TASKS 第73节独立定向复核通过结论及用户本轮明确授权，由 Codex/root 实施机械冻结。本记录不是新的独立复核，也不是实机验收。T08 仍 IN_PROGRESS，固定20次任务不自动恢复。

## 1. 冻结内容与摘要

`stability_v6.json` 仅替换 `status`：`REQUIRES_FIELD_CALIBRATION` → `FROZEN`。版本名保持 `local-rgb-stability-v6-candidate`，原缩进、换行及其他字节保持不变。

- 原 SHA256：`63cdd1d179f9e03bd74df16fe1d37752606555336c639f22cd653fd6dd17e11c`。
- 新 SHA256：`e46e0c2816c6f512fbd4fd5e5c60ff1ef3fc4d0560e0e8b29d3b6a6315caa513`。
- sidecar 为小写64位十六进制加 CRLF，共66字节。

附加检查：`git diff --check` 将 sidecar 的 CRLF 报为 trailing whitespace，因为该文件标记为 `-text`。按用户明确要求保留66字节，不改为65字节 LF，也不修改 Git 属性；这不影响四项门禁或加载器摘要校验。

## 2. 未改清单与审计方法

以修改前工作树字节快照 `.runtime/verify/t08_v6_freeze_before.json` 和 v6 原字节 `.runtime/verify/t08_v6_freeze_before.bin` 为基线；冻结后逐文件比较，并断言新 v6 字节恰等于原字节的一次状态字符串替换。

- v1–v5 的 JSON/sidecar 字节不动；v1–v4 仍为候选，v5 仍 FROZEN，v5 摘要仍 `2e21c734ec902c44b3111de8f6f62f06626fbbe9410ff9a0b959469248eb4faa`。
- 世界 v1/v2 文件、参考图、提示不动；语义摘要仍分别为 `dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335` / `b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0`。
- 判据、遮罩、容差、预热、阈值、4000ms 新鲜度、180s 任务预算、40次动作上限、motion_profiles 全部不动。
- 配置选择及整个 `configs/` 不动；生产 Python 代码不动，30s 租约、急停、epoch、动作到期归零、安全门槛不动。
- 未启动 live、未 ARM、未调用付费模型、未发送真实输入、未读密钥。

## 3. 回归范围

仅同步两份指定测试：摘要/状态和 v5/v6 差异集；显式断言旧版状态；session 参数化 FROZEN 与候选，前者只到控制器创建前哨兵且确认感知4000ms接线，后者在资源创建前拒绝；运行时用内存控制器确认真实 FROZEN 构造无采集/无消息，再回退候选确认采集前拒绝。报告候选拒绝用例同步构造候选策略元数据，并匹配 candidate 错误，避免冻结后仍把真实 FROZEN 当候选。v1–v4 拒绝用例保留。

## 4. 门禁与默认计划

完整门禁原始输出（四项退出码均0）：

```text
1125 passed in 305.03s (0:05:05)
All checks passed!
140 files already formatted
No broken requirements found.
```

命令分别为项目解释器的 `-m pytest -q`、`-m ruff check src tests`、`-m ruff format --check src tests`、`-m pip check`。pytest 原始日志 `.runtime/verify/t08_v6_freeze_pytest.log`；不变项审计 `.runtime/verify/t08_v6_freeze_audit.json` 为 PASS。默认计划使用用户指定命令，无 `--mode live`：

```powershell
.venv/Scripts/python.exe -m vrc_agent navigate-live --task G2-1 --config configs/navigation-live.toml --world-version v2
```

原始完整输出保存为 `.runtime/verify/t08_v6_freeze_plan.log`；关键字段为 `mode="plan-only"`、`sends_live_input=false`、`task.task_id="G2-1"`、`world.version="vrchat-home-live-v2"`，suite_digest 为上列 v2 摘要，退出码0，无 field calibration 拒绝。终端默认编码下中文显示失真，未改编码实现；保存日志采用 UTF-8 子进程输出以保留中文。

注意：默认只读计划在配置/策略 live 闸门前返回；这次命令成功只能证明计划路径只读，不证明真实 live 装配或现场导航通过。冻结放行与候选拒绝由上述无真实资源的哨兵/内存测试单独验证。后续仍需显式 live、本机人工 ARM 和现场验证。

GraphFlow 工具本轮未暴露，无法调用 context/plan/index；按项目约定如实记录，未伪造结果。
