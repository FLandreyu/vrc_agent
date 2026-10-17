# T08 v6 有界重试修复（甲，2026-10-18）

依据 TASKS 第80/81节及用户授权，负责人 Codex/root。第77节“拒绝即暂停”的行为由本修复取代，v6策略文件/摘要不变，运行时不改判据，可供用户后续现场选择；本轮不启动live、不ARM、不调付费模型、不发真实输入、不读密钥，不表示实机验收。

每个观测/动作决策周期最多3次 attempt，observe 内 after_inference 重试与 before_submit 回调共享计数，不能嵌套成3×3。每个 attempt 重新取得连续2对预热参考，再进行一次预检，通过才编码、推理、after_inference、before_submit。失败短路：预检拒绝不花推理，模型后拒绝不会假造成功场景供提交前检查；只有各关都通过的同一 attempt 才可发动作。首次失败可在第2次恢复，3次都失败为SCENE_UNSTABLE，不会启动第4次。

每份新帧仍检查4000ms新鲜度，不刷新旧ID/采集时间；旧attempt证据不能绑定新attempt动作。180s任务、40步、租约/epoch/TTL/急停和归零仍优先；WARMUP_UNSTABLE、超龄或权限失效等异常不被当作可无限重试的稳定性拒绝。

schema9保留，新增 run_parameters.max_inference_attempts=3、stability_attempt事件与预检/执行proof/源事件的attempt、attempt_cycle。校验器核对共享预算、连续序号、预检与源/动作同attempt；历史schema9无attempt元数据仍可读，新格式不能删预算冒充历史。预检不能携带validates_observation_id，两个原执行证据检查点仍强制。

ARM拒绝仍使用原来的preflight/焦点/状态判定，仅通过非阻塞outbox向主代理发arm_refused提示；打印在主代理，不堵塞独立控制进程，不新增IPC授权能力。

测试覆盖三关首次拒绝→第2次恢复、三次都拒绝→暂停且不超过3次、提交前重试也重新做预检/模型后检查、报告预算与预检绑定伪造拒绝；同步第77节test_navigation_cost为持久变化成为新合法参考后可恢复。门禁原始输出交付时补充。

甲门禁原始输出（四项均退出0）：

```text
1177 passed in 235.15s (0:03:55)
All checks passed!
148 files already formatted
No broken requirements found.
```

依次为项目解释器的pytest -q、ruff check src tests、ruff format --check src tests、pip check；完整日志`.runtime/verify/t08_retry_pytest.log`。本提交仅纳入AGENTS、本记录、control/service.py、live_navigation的attempts/parameters/stability_report/stable_runtime，以及test_navigation_retries/test_navigation_cost/test_local_lease_integration三份测试；不纳入既有文档改动与乙候选文档。

工作树开始时已有ARCHITECTURE/TASKS/T13提案的未提交改动。未改ARCHITECTURE及T13提案；TASKS仅原字节末尾追加第83节，保留第82节，不将既有文档改动混入提交。

GraphFlow工具未暴露，无法调用context/plan/index，未伪造图谱结果。
