# T08 运行时成本优化（B，离线实施）

依据 TASKS 第76节及用户指令；负责人 Codex/root。GraphFlow 工具本轮不可用，未伪造 context/plan/index。不是独立复核或实机验收。

冻结 v6 的 checkpoints 列表声明两个执行证据检查点；新增预检只作推理准入，不能派生观测或绑定动作，所以不改变这两个检查点的时机/校验语义，无需另发 v7。`stability_prechecks` 与 `stability_proofs` 分开，校验器不允许预检携带动作绑定，仍要求每个动作的 after_inference 派生及 before_submit 证据；删掉任一原检查点仍拒绝。预检在 BGRA 原帧比较，编码格式不参与稳定性判据。

每次 observe 先取得连续两对通过的原预热参考，然后抓新帧与其比较（有已接受场景时同时保护原场景证据框）。预检最多3次，仅通过才编码/推理。重试只复用这份参考且受4000ms预算约束，既不链式滚动参考，也不改时间戳；预检失败不调用模型、不重启预热。模型后复验失败即安全暂停 SCENE_UNSTABLE，不再次推理同一源或重复整轮预热。动作后仍重新预热：实际运动不能被误当同场景稳定参考复用。这是保守的停止策略，可能牺牲瞬态抖动后的自动恢复，须现场评估，不保证提高任务成功率。

v6 新报告 schema8，历史 schema7 可继续复验；旧 schema 不接受夹带新字段。新增 `stability_prechecks`、`stage_timings`，事件 `stage_timing` 的 stage/duration_ns/at_ns 支持 capture/encode/infer/stability/submit 的计数与 nearest-rank p50_ms/p95_ms 重算；无样本为 null。计时覆盖失败/取消；stability 包括预热等待及比较，不含 capture（各阶段不是可直接求和的互斥总时长，capture 包含自身授权等待）。不把摘要当原始像素签名。

验证：合成帧分别在预检、after_inference、before_submit 改变，三者均无动作；推理次数分别0/1/1且预热只2对。分位数、删预检、删动作检查点伪造报告均被拒。四项门禁与提交结果见 TASKS 后续交接节。

提交 B 门禁原始输出（均退出0）：`1129 passed in 370.63s (0:06:10)`、`All checks passed!`、`142 files already formatted`、`No broken requirements found.`。完整日志 `.runtime/verify/t08_cost_pytest.log`；前次失败为新增测试暴露提交前拒绝仍重启预热，已修正后全量重跑。

未改：v6 JSON/sidecar、v1–v5、判据/保护区零容差/5%/Δ2/遮罩、世界/提示、30s基础租约、急停/TTL/epoch/到期归零；未 live/ARM/付费调用/真实输入/密钥读取。配置保持 dry-run。固定20次任务不恢复。
