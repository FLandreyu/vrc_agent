# T08 方向 A 独立交叉复核（2026-10-16）

复核者 DSH / Flash，实现者 Codex / root（ADR-009：复核者未参与本修订的实现）。对照 `docs/TASKS.md` 第 34 节（R1–R6）、第 35 节实现交接与 [方向 A 验证记录](T08_direction_A.md)。

**结论：R1–R6 的主体未能证伪**（自建探针 61 项检查 0 失败；实现者四项门禁由复核者复跑重现），**但发现 5 项非阻断问题**：1 项「声明与强制点不一致」（F1）、1 项代码卫生（F2）、3 项观察/登记缺口（F3–F5）。T08 保持 IN_PROGRESS；现场标定与用户选版仍是进入 live 的前置。

## 1. 复核范围与独立性

- 不导入实现者测试、夹具或报告生成器；自建探针 `.runtime/verify/v_t08_dir_a_review.py`，结果 `.runtime/verify/v_t08_dir_a_review.json`：**61 项检查，0 项失败**。四段：
  - A 指标与策略边界（遮罩语义、黑帧、alpha、尺寸、相交判定、摘要重算）；
  - B 运行时与安全不变量：真实 `ControllerProcess` dry-run 子进程 + 自建信号源/内存供应商/脚本技能；
  - C 契约与交付物（配置隔离、CLI 拒绝、请求体、错误码、证据篡改矩阵）；
  - D 策略强制点与汇总接线。
- 本轮**未启动 live、未 ARM、未付费、未发送真实输入、未读密钥、未改产品源码/配置/锁文件**；只用合成像素、内存供应商与自定义技能。
- 与实现者不同人：复核者此前做过 T08 离线（第 20 节）与 live 路径（第 25 节）复核，本修订的实现者为 Codex / root。

## 2. 逐条判定

| 要求 | 判定 | 复核者独立证据 |
| --- | --- | --- |
| R1 慢推理承载 + 本机可验证帧稳定性证据 | 未能证伪 | 遮罩内（左下 86×144 = 全帧 0.398%）像素变化被忽略、遮罩外单像素变化即拒绝；全黑帧拒绝；仅 alpha 变化忽略（指标只看 RGB）；帧尺寸不符报 `stability frame mismatch`；遮罩相交判定在边界（左/下贴边为真、画面中部为假）正确；推理前后持续变化 → `SCENE_UNSTABLE` 且 0 动作 0 输入；稳定序列 → `ACTION_LIMIT`、`schema_version=2`、报告含策略与逐条 proof 且通过独立校验 |
| R2 安全不变量 | 未能证伪 | 推理在途急停：5s 内存供应商下 **0.29s** 退出为 `MANUAL_STOP`、零动作零输入（30ms 轮询取消在途）；租约 500ms 到期 → `EXPIRED_LEASE`，无自动续租；未 ARM → `ARM_TIMEOUT`；候选策略在 `StableNavigation` 构造期与 CLI 层双重拒绝；帧尺寸不符 → `CALIBRATION_MISMATCH`；实体框与遮罩重叠 → `MASKED_SCENE_EVIDENCE`；`configs/navigation-live.toml` 未启用新设计（无 `navigation_design`，仍 `mode="dry-run"`）；`AppConfig` 默认 `legacy`；旧 `Runtime` 的 `ReplayController` 类型检查（`src/vrc_agent/runtime.py:42`）保留 |
| R3 供应商请求形态 | 未能证伪 | 请求体字段 ⊆ `{model, messages, temperature, max_tokens, response_format, thinking}`（`response_format` 是既有类型化字段），无任意 `extra_body`；`disable_thinking=true` 时才有 `thinking:{"type":"disabled"}`，`False` 时字段不存在；非布尔值在配置层 `ValueError` |
| R4 参数与报告 | 未能证伪（附 F1） | 策略在报告中完整记录且被汇总校验逐项比对；`INFERENCE_TIMEOUT` 作为新结局独立于旧 `MODEL_LATE`；8000ms/30s/3 次尝试由策略强制；40 次仅为上限（报告不承诺用满） |
| R5 报告兼容 | 未能证伪 | 旧 `schema_version=1` 无证据字段仍通过；带证据却声明 schema 1 → 拒绝；9 类篡改（翻转 equal、改摘要、改复核时刻超时效、删 proof、动作绑定未知观测、签发时间超时效、改派生来源、改策略摘要）全部拒绝；交付件被独立校验接收；汇总把证据链坏的报告整份计入 `invalid_files` 并给出原因，好报告不被误伤；坏文件同时把 `usage.complete/cost_complete` 置否 |
| R6 复现 | 未能证伪 | 复核者复跑四项门禁：`pytest -q` → **865 passed in 51.31s**；`ruff check src tests` → All checks passed!；`ruff format --check src tests` → 116 files already formatted；`pip check` → No broken requirements found.。实现者测试确实使用可控时钟与内存 HTTP |

第 35 节其余声明的独立核对：交付报告 `.runtime/verify/t08_direction_a-a04973d85cc840278f5c390a078d84eb.json` 的 SHA256 = `3d9658923c10046b741c90b3acb2f19090681d60b2d212203aa79dc5857c759f`（与第 35/36 节一致），且能被独立校验接收；策略摘要 `e935046805…` 可由 `stability_v1.json` 字节独立重算并与同目录 `.sha256` 一致；世界摘要仍为 `dda1d3b1…`；第 36 节的候选 live 硬拒绝在复核者临时配置下原样重现（创建控制器前 exit 2，消息一致）。

## 3. 发现（均为非阻断）

- **F1 / P3，声明与强制点不一致**：策略文件记录 5 个参数，但只有 3 个由策略强制——`inference_ms`、`task_timeout_s`、`max_inference_attempts`。`max_actions` 与 `freshness_ms` 在 `src/vrc_agent/live_navigation/stable_runtime.py` 中**没有任何读取点**：动作上限来自技能 `NavigationLimits.max_actions`（`src/vrc_agent/skills/world.py:33`，默认 40），观测时效来自硬编码 `min(1500, observation_max_age_ms)`（`stable_runtime.py:158`）。复核者把策略 monkeypatch 成 `max_actions=1, freshness_ms=1` 后端到端实跑：仍执行 **3** 个动作，报告却如实声明了被改写的策略值。今天两处取值与策略一致（40 / 1500），**运行时行为没有缺口**；但将来发布新版本策略（新摘要）改动这两个字段时，只会改变报告声明与汇总校验的时效窗口，不会改变运行时的上限与时效。建议二选一：让运行时读策略（单一事实来源），或在验证记录中明确这两项是「声明项」，并点名各自的强制点。
- **F2 / P3，代码卫生**：`src/vrc_agent/live_navigation/stable_runtime.py:160` 留有编码损坏的乱码注释 `# ???????????????????????????????`（32 个问号），交付件的注释已被破坏。
- **F3 / P3，码表登记缺口（非本轮引入）**：方向 A 新增的 `INFERENCE_TIMEOUT`、`STABILITY_TIMEOUT`、`IMAGE_ENCODING_TIMEOUT`、`SCENE_UNSTABLE`、`MASKED_SCENE_EVIDENCE` 都不在 `src/vrc_agent/telemetry/codes.py` 的 `KNOWN_CODES` 里；既有的 live 中止码同样不在（`ARM_TIMEOUT`、`CALIBRATION_MISMATCH`、`CONTROL_TIMEOUT`、`PATH_UNSAFE`、`RECOVERY_UNSAFE`、`TASK_TIMEOUT`）。未知码被显式当作未知处理（`family_of` 返回 `None`、汇总单独计数），因此不破坏 T10 合约；但码表/文档对 live 结局的覆盖不完整，建议连同既有缺口一起补齐。
- **F4 / 观察（验证记录已声明）**：候选遮罩只排除画面左下 86×144（0.398%）。遮罩外任何变化都拒绝，包括画面另一角的 200×60 HUD 尺寸元素——这与 [验证记录](T08_direction_A.md) 第 22 行的声明一致（"区域之外的 HUD/时钟动画会保守拒绝；不会自动扩大遮罩"）。代价是：真实运行时只要遮罩外存在任何动画（时钟、麦克风图标、nameplate 等），稳定性检查会持续失败。这正是该候选保持 `REQUIRES_FIELD_CALIBRATION` 的理由，也是现场标定的核心待证项。
- **F5 / 观察**：实现里「黑」的判据是分块 `max(chunk) > 8`，即所有通道 ≤ 8 才算黑；均匀近黑帧（如 9,9,9）不算黑，两帧相同即判稳定。文档写的是"任一保留区域全黑拒绝"，未给出阈值。影响有限（全黑场景经 `build_scene` 得不到可用实体，不会产生动作），但建议在验证记录里写明该阈值。

## 4. 复核者自建反例（探针自身修正记录）

复核过程中修正了 6 处**探针自身**的错误，均非产品缺陷：

1. 起初假设遮罩在左上角，A3/A4 断言方向写反；用逐帧摘要比对直接确认排除区是左下 `[0,1296,86,1440)`（x=10,y=1400 被忽略，x=10,y=100 被拒），断言随之改写。
2. B3 的急停观察线程原本等控制器状态变化，结果等到第一个动作执行后才触发（6.04s）；改为由供应商进入信号触发，急停落在推理在途（0.29s）。
3. C3 的「翻转 equal」变异原本写成 `equal=True`，而交付件该条 proof 本来就是 `True`（空变异）；改为取反。
4. C8 原本要求请求体键集合完全相等，忽略了既有的 `response_format`（json_mode）字段；改为子集断言。
5. C9 原本断言新码已登记；实测未登记（与既有 live 码同状况），改为记录现状的观察项，并另加「未知码被显式处理」的断言。
6. D4b 的篡改最初改 `current_digest`，命中的是派生的 `derived frame mismatch`（下游首个报错）；改为翻转 `equal`，原因串才指向 `contradictory stability verdict`。

## 5. 局限

- 摘要是**本地一致性检查，不是数字签名**；没有保存原始图片，复核无法独立重算像素。复核者按此口径复核：可证伪的是「报告内部自洽、时间与绑定关系成立」，不能证明帧内容真的来自当时的窗口。
- 检查点是离散的（`after_inference`、`before_submit`），不能证明两次采样之间或最后一次采样之后没有瞬间变化；这是第 34 节要求"如实限界"的部分，验证记录第 19 行已写明。
- 取消是协作语义：`urllib` 阻塞线程无法被 `asyncio` 强杀。复核者实测"已发送的在途调用被计一次未知费用"（`calls=1`、`unknown_cost_calls=1`、`cost_complete=false`），但**不能**据此推断远端已停止运算或停止计费。
- 本轮没有任何现场/实机证据：遮罩在真实 HUD 下是否长期无变化、秒级推理的真实延迟分布、以及 20 次任务的实机成功率，均未验证；`live_acceptance` 仍为 UNTESTED。

## 6. 门禁与复现

```text
python -m pytest -q                          → 865 passed in 51.31s
python -m ruff check src tests               → All checks passed!
python -m ruff format --check src tests      → 116 files already formatted
python -m pip check                          → No broken requirements found.
python .runtime/verify/v_t08_dir_a_review.py → 61 checks, 0 failed
```

复核者只运行上述命令；未修改产品源码、配置、锁文件与实现者测试。

## 7. 下一条动作

1. 实现方修 F2（乱码注释）并按 F1 的选择二选一（运行时读策略 或 文档点名声明项/强制点）；F3 建议随 T10 码表一起补齐。
2. 现场按候选遮罩采集静止序列，核对左下 86×144 是否长期只含不参与导航的界面；由用户选择新冻结版本（新 JSON + 新 `.sha256`），不得原位改候选摘要。
3. 新版本冻结后再做真实延迟复测与固定 20 次任务；此前不得把本候选接入 live，也不得移用离线证据宣称实机成功率。

GraphFlow：本轮未调用 `graphflow_context/plan/index`（复核以源码与自建探针为准），未宣称图谱更新。

## 8. 实现方修正回应（2026-10-16，Codex / root；不改变上述独立复核结论）

- F1 选择第 7 节第 1 条允许的**文档方案**：在 `T08_direction_A.md` 的 R2–R4 节区分三项运行时读取参数和两项声明，点名 `NavigationLimits.max_actions` / `SupervisedNavigation.run`、`SupervisedNavigation.capture` / `StableNavigation.before_submit` 的实际强制点，并明确报告校验器仍读取 freshness_ms。理由是保留已复核运行行为，不新增策略调参入口或原位改冻结摘要。只改两项 JSON 不改变运行时的事实仍保留；未来改版必须同步实际强制点和校验、回归、复核，不能只改声明。
- F2 已用 UTF-8 中文恢复注释，说明额外帧仅复验既有观测、不刷新采集时间；修正前后去位置属性的 Python AST 摘要相同，未改变可执行逻辑。
- F5 已补明「保留区域全部 RGB 通道 ≤8 才判黑」、(9,9,9) 近黑可通过稳定性比较，以及 `build_scene` 不检查像素、不能保证拒绝高置信幻觉的限制。这同时限定第 3 节“得不到可用实体”的解释：不能将其作为普遍安全保证。
- F3 未纳入本次修改，F4 仍待现场标定。修正与验证结果见 TASKS 第 38 节；候选 live 拒绝、T08 IN_PROGRESS 及实机 UNTESTED 均保留。
