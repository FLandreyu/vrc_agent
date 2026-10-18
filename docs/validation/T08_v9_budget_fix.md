# T08 v9 冻结阻断 B1：控制进程预算装配修正

日期：2026-10-18；负责人：Codex。依据 TASKS 第101节及`T08_stability_v9_review.md`的B1；交接见TASKS第102节。

本记录为实现方离线修正交付，**不是独立复核、不是冻结授权、不是实机验收**。v9仍为`REQUIRES_FIELD_CALIBRATION`，现场仍v8/dry-run。

## 1. 原因与最小修复

控制进程`_worker`先将`task_timeout_s`设为`30.0`，只有设计枚举匹配时才从`load_policy()`读取策略预算。原枚举遗漏v9，导致其控制预算为30s，而策略/报告声明180s；30s基础租约已经用尽全部预算，首次续租额度为0，报告又被`validate_leases`拒绝。

`src/vrc_agent/control/service.py`只增加一行：

```diff
             "stable-inference-v6",
             "stable-inference-v7",
             "stable-inference-v8",
+            "stable-inference-v9",
```

沿用同一`load_policy(design_suffix)[0]["task_timeout_s"]`取值，不重构、不增加前缀匹配、不修改默认30s分支。v6/v7/v8仍读取原策略的180s；legacy/v5仍回落30s；未知设计仍被配置闭集拒绝。

**180s是累计控制额度上限，不是基础租约或自动续租。** v9仍只有30s基础租约，扩展只能由现有本地入口申请；到期不可复活。动作TTL、急停、epoch和到期归零没有改动。

## 2. 回归与证据

新测试：`tests/integration/test_v9_control_budget.py`，共11项。

- 真实`ControllerProcess`使用`mode="dry-run"`、真实spawn/IPC及RecordingTransport；启动保持DISARMED，无录制输入。v9初始/状态预算为`task_timeout_ms=180000`、`base_ms=30000`，`extension_ms=30000`、`extension_max=5`与配置一致；另测17000×3自定义配置未被覆盖。真实IPC依然拒绝extend/renew。
- v6/v7/v8真实进程逐一断言180000；legacy/v5断言30000，未知v10配置仍拒绝。这些是预算接线回归，不是候选live启动测试。
- 假时钟控制器使用真实worker返回的预算，避免测试直接传默认180s掩盖装配漏项。仅内存本地夹具授权/续租，不注册真实热键、不创建live transport。分别验证未续租30s、首次续租获批30000ms后60s到期；执行中的动作TTL/截止时间及epoch/lease_id不变，最终运动被取消、各轴归零、续租及直接重新ARM均被拒绝。
- schema12内存闭环也使用真实worker预算与配置重建控制core。本地测试事件先续租一次、假时钟推进31s后才开始导航；报告携带180s任务预算/30s基础/一次30s扩展，总授予60s且elapsed>30s，并通过`validate_leases`、`validate_stability_report`与JSON往返复验。将同一报告任务预算改回30000，两个校验器仍拒绝，不放松任何校验来掩盖B1。
- 内存供应商、合成帧及RecordingTransport；未调用真实模型、采集或输入。报告工件：`.runtime/verify/t08_v9_b1_report.json`。

定向命令与原始输出（日志`.runtime/verify/t08_v9_b1_targeted.log`）：

```text
.venv/Scripts/python.exe -m pytest -q tests/integration/test_v9_control_budget.py
11 passed in 20.02s
```

四项全量门禁均exit 0，原始输出：

```text
.venv/Scripts/python.exe -m pytest -q
1405 passed in 642.36s (0:10:42)
.venv/Scripts/python.exe -m ruff check src tests
All checks passed!
.venv/Scripts/python.exe -m ruff format --check src tests
161 files already formatted
.venv/Scripts/python.exe -m pip check
No broken requirements found.
```

原始日志：`.runtime/verify/t08_v9_b1_{pytest,ruff_check,ruff_format,pip_check}.log`。

默认只读计划命令`.venv/Scripts/python.exe -m vrc_agent navigate-live --task G2-1 --config configs/navigation-live.toml --world-version v2`，exit 0；输出摘录：

```text
  "mode": "plan-only",
  "sends_live_input": false,
  "suite_digest": "b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0",
```

完整输出`.runtime/verify/t08_v9_b1_dry_run.log`；只读计划不证明v9可live。

## 3. 不变项自查

- 修改前记录114个受保护的src/configs文件字节摘要；唯一不同的生产文件为`control/service.py`。该文件与修改前原始字节的差异**恰好是插入v9枚举行**，原有CRLF也保留；`control/`其它文件不变。
- v1–v9策略JSON/sidecar、世界、提示、configs不变。v9摘要仍`990a1d7de902318f4ed04715adaa7bfd71c9c806c30fb90e82ad37a142ceef0e`，状态仍为候选；现场仍`stable-inference-v8`、dry-run、基础租约30000ms。
- clearance几何、目标七谓词、到达语义、4000ms/180s/40步、3次attempt、预热2对、运动包络、急停/epoch/TTL/到期归零均未修改。`leases.py`与schema12校验器没有变化。
- 基线：`.runtime/verify/t08_v9_b1_baseline.json`；修改前service字节：`t08_v9_b1_service_before.py`；自查工件：`t08_v9_b1_invariants.json`，确认114份基线中113份一致，唯一生产差异为service的v9枚举行。

## 4. N2现场复验清单（本轮不修判据）

第101节N2为预检重绑未显式比较epoch的报告审计风险。本轮不改变`verify_prechecks`、现行schema12或旧报告的接受范围，也不把B1回归全绿当作N2已解决。

现场复验时单列以下项目，须在用户另行授权的现场会话中执行：

1. 保存每次预检与绑定来源的`session_id`、`environment_epoch`、reference/current observation ID、attempt/attempt_cycle及时间顺序；逐项人工核对预检→来源的epoch是否一致。
2. 在人工ARM/RESET或环境epoch改变的边界核对旧预检是否被复用；已有运行时/控制器应停止旧epoch任务，不能据几何相同继续执行。记录实际停止原因和新旧观测，不自动ARM、不自动RESET。
3. 单独标注同reference ID但epoch不一致的审计见证，供离线独立复核；不要把当前校验器接受当作epoch绑定已被证明。
4. 若后续另案补强epoch校验，需先说明是否改变旧schema12报告接受范围以及历史报告隔离/兼容口径；本轮未作该项实现。

## 5. 后续

B1修正件须由另一执行者定向复核，至少核对枚举唯一差异、真实dry-run的180s预算、首次本地续租及schema12租约复验。之后仍需用户决定是否冻结与安排现场，不能自动切换配置或引用本轮门禁作为20次任务验收。
