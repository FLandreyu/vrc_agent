# T08 v6 预算与运动包络（独立复核）

复核者：DSH / Flash。实现者：Codex / root。复核对象：[TASKS 第 72 节](../TASKS.md) 与 [v6 验证记录](T08_stability_v6.md)（提交 `47534ba`「v6 预算与运动包络（第72节）」，基线 HEAD `81199ff`，工作区干净；v6 策略字节 `63cdd1d1…`）。本轮按用户指令只做**定向复核**，覆盖五条：①预算三层贯通（策略 4000／控制上限 5000 取小，runtime/session/before_submit/报告同源）；②运动闭集包络（单步有界、到期归零、急停优先、`max_motion_ms=500`）；③权限/租约纪律不变；④像素判据/遮罩/预热/世界与提示一字未动；⑤schema6/7 隔离与候选拒绝。

不启动 live、不 ARM、不调用付费模型、不发送真实输入、不读密钥、不改产品文件/策略/世界/配置。

## 0. 结论

- 第 72 节与 v6 验证记录的五组声明**主体未能证伪，未发现阻断项**：预算在策略/控制配置/全局三处取小并贯通 runtime 采集、session 感知装配、`before_submit` 与报告 `run_parameters`；运动包络是五条单轴候选的闭集（模型或替换 skill 不能扩档、混轴、加时长），500 ms 到期归零、急停/心跳/租约/epoch 优先；30 s 租约与动作 TTL 1000 ms 未因 180 s 任务放宽；v1–v5 策略/摘要、世界 v1/v2、10 条提示与像素判据/遮罩/预热参数逐字节未改；schema7 与 6 隔离，v6 候选在 session 入口、运行时构造、报告复验三处拒绝 live。
- 复核者自建探针 **61 项断言 0 失败**（S1 策略字节 6、S2 加载器篡改 13、S3 预算三层 11、S4 到期/归零/急停 9、S5 包络 6、S6 报告隔离 6、S7 不变项 7、S8 权限租约 3）；独立复跑四项门禁 **1124 passed in 311.20s**，ruff check / ruff format --check（140 files）/ pip check 均通过。
- 五项**非阻断观察**：N1 v6 `status` 由「文件字节＋sidecar」决定（loader 已接受 `FROZEN`，作为将来冻结留口），当前提交是候选，冻结仍须用户授权并重新提交字节；N2 基类 `task_timeout_s` 构造上限从 60 放宽到 180 是对**所有**运行时生效，session 非稳定路径仍传默认 30.0，行为未变但未来调用方可在 legacy 路径传 180；N3 报告可下报 `freshness_ms`（≥100、≤4000），复验随之收紧，不存在放大路径，但报告值本身不能证明运行时实际取值；N4 180 s 与 40 步是两个独立上限，现场每步 4.5–5 s 时先撞 180 s，field 档实际位移/制动仍未测；N5 实现方交付审计列出的三份合成报告摘要中有两份（`4613be22…`/`2ac83d67…`）在 `.runtime/verify/` 有对应文件，`464c97ec…` 仅见于审计 JSON。五条都不影响预算/包络/纪律/不变项结论。
- 边界：这是**离线定向复核，不是实机验收**。v6 仍 `REQUIRES_FIELD_CALIBRATION` 且拒绝 live，尚未经用户重新冻结；未跨 `ControllerProcess`、未真实采集/推理/ARM、未发送输入；固定 20 次任务（≥16/20）未执行。T08 仍 IN_PROGRESS，T10 实机折算仍缺证据。

## 1. 逐项对照

### 1.1 预算三层贯通（S1/S2/S3/S6）

| 声明 | 独立证据 | 结论 |
| --- | --- | --- |
| v6 字节 `63cdd1d1…`，工作树、git blob、sidecar 三者同源 | S1.1–S1.3：工作树读字节复算 `63cdd1d179f9e03bd74df16fe1d37752606555336c639f22cd653fd6dd17e11c`（1020 字节），`git cat-file blob 47534ba:…` 同摘要（`.gitattributes` 只对 v6 的 JSON/sidecar 标 `-text`，未重写旧工件行尾），sidecar 同串；S1.6 `load_policy("v6")` 返回的 digest 与 sidecar 一致。对照：v5 工作树仍 `2e21c734…`，其 git blob 为 `d45d54f9…`——与第 68 节 N1 的「v5 摘要按工作树 CRLF 口径」完全一致，不是新问题 | 未能证伪 |
| v6 相对 v5 的字段差恰为 version/status/freshness_ms/task_timeout_s/motion_profiles | S1.5：两版 JSON 键集合相同，取值差异恰这五项；`metric`/`excluded_rectangles`/`max_changed_ppm`/`pixel_tolerance`/`protected_margin`/`black_threshold`/`warmup_*`/`inference_ms`/`max_actions`/`checkpoints`/`max_inference_attempts` 全同 | 未能证伪 |
| 加载器锁死「只许改预算/档位」，判据改动即拒绝 | S2.1–S2.12：逐项篡改 `pixel_tolerance=3`、`max_changed_ppm`、`excluded_rectangles`、`warmup_pairs=1`、`freshness_ms=4500`、`task_timeout_s=300.0`、field `forward=0.5`、夹带未知键、`freshness_ms=4000.0`（float）、`status=DRAFT`、`status=True`、sidecar 与字节不符，全部抛 `ValueError`（判据类文案 `invalid v6 budget policy or changed stability criteria`，摘要类 `stability policy digest mismatch`）；S2.13 未篡改版本作为对照组正常通过 | 未能证伪 |
| 策略 4000／控制上限 5000／全局 5000 取小 | S3.1：`action_freshness_ms` 实测 `(配置 4000, v6)→4000`、`(5000, v6)→4000`、`(3000, v6)→3000`、`(1500, v6)→1500`、`(配置 1500, v5)→1500`、`(1500, 无策略)→1500`；S3.3 配置矩阵拒绝 5001、非 v6 且年龄 4000、非 v6 且 field 档、v5 且 1600 | 未能证伪 |
| 现场配置显式 v6/field/4000，仍 dry-run，租约 30000 | S3.2：`load_config("configs/navigation-live.toml")` → `mode=dry-run`、`navigation_design=stable-inference-v6`、`navigation_motion_profile=field`、`observation_max_age_ms=4000`、`lease_ms=30000`、provider `openai_compat`、捕获精确/DPI/未遮挡、窗口标题 `VRChat`；S3.9 runtime 属性、控制配置、策略三处同为 4000 | 未能证伪 |
| 控制层准入按新年龄而非旧 1500 | S3.4：独立驱动真实 `Controller.submit`——观测年龄 1530/3000/3999 ms 均 `RUNNING`，4000/5000 ms 均 `REJECTED` 且 `STALE_OBSERVATION`、传输层无消息 | 未能证伪 |
| 执行期也不再被 1500 ms 截断，4000 ms 立即停并归零 | S3.5：年龄 3501 ms 提交、推进 400 ms（年龄 3901）仍在动、无 `STALE_OBSERVATION`；S3.6：年龄 3999 ms 提交、推进 1 ms（恰 4000）→ `STALE_OBSERVATION`，`active` 清空、各轴归零 | 未能证伪 |
| runtime 采集上限 4000 ms | S3.7：`capture()` 在 3900 ms 接受并返回 `max_age=4_000_000_000`，4001 ms 抛 `STALE_OBSERVATION`（帧 ref/尺寸/时间与观测严格对应，否则 `MISSING_FRAME`） | 未能证伪 |
| `before_submit` 用同一 4000 ms | S3.8：3999 ms 的 `before_submit` 直接通过与新鲜证明；4000 ms 触发一次重采（`observe` 调用计数 1）后仍失败即中止 | 未能证伪 |
| session 感知装配同源 | S3.10：哨兵实测 `navigation_entry` 传给 `Perception` 的 `max_observation_age_ms=4000`（在 `ControllerProcess` 之前拦截） | 未能证伪 |
| 报告 `run_parameters` 与运行时同源且可复验 | S3.11：合成 v6 field 闭环报告 `run_parameters.freshness_ms=4000` 等于 `runtime.action_freshness_ms`，`task_timeout_s=180.0`、`max_actions=40`，`validate_stability_report` 通过 | 未能证伪 |
| 旧入口/旧策略仍 1500 | S3.1/S3.3：无策略或 v5 时上限 1500，非 v6 配置不得 >1500 或选 field 档 | 未能证伪 |

### 1.2 运动闭集包络（S5）

| 声明 | 独立证据 | 结论 |
| --- | --- | --- |
| 两档闭集：cautious 0.2/200、field 0.4/500，yaw ±1/200、strafe ±0.2/200 | S5.1：16 例矩阵——两档各自接受 forward 单轴（0.2/200、0.4/500）、±strafe、±yaw，拒绝 0.3、0.5、混轴、duration 300/1000、后退（负 forward 落到 ±strafe 候选也不匹配）；S5.2 `MotionArgs.duration_ms` 仍 `ge=1, le=500` | 未能证伪 |
| runtime 独立复查，模型/替换 skill 不能越档 | S5.3：把 `skill.decide` 换成返回越档提案（0.4/200、0.2/500、混轴、0.5/500）四例，均在 `submit` 前抛 `INVALID_ARGS`，控制层未收到动作；S5.4 skill 只按配置档位产生提案（cautious→0.2/200，field→0.4/500，yaw/strafe 不变） | 未能证伪 |
| 控制上限仍 500 ms，配置小于档位即拒绝加载 | S5.2/S5.5：`AppConfig(v6, field, max_motion_ms=400)` 加载即 `ValueError`；控制层 `max_motion_ms=400` 时 500 ms 动作仍 `INVALID_ARGS`（S4.8） | 未能证伪 |
| `max_actions=40` 明确强制 | S5.6：v6 runtime 的 `max_actions` 取自策略 40（不再走 skill 默认），报告写入 40 | 未能证伪 |
| 单步有界、到期归零 | S4.1：duration 500 ms 的动作推进 499 ms 仍 `RUNNING` 且 `active` 非空，推进到 500 ms 变 `COMPLETED`、`active` 清空、各轴归零（`zeroed` 断言 active 为空、每条传输消息为 `(0.0,)`、轴在 `AXES` 内） | 未能证伪 |

### 1.3 权限/租约/归零/急停纪律不变（S4/S8）

| 声明 | 独立证据 | 结论 |
| --- | --- | --- |
| 动作 TTL 仍 1000 ms | S4.2：绑定后推进 1001 ms 再提交 → `EXPIRED_ACTION` | 未能证伪 |
| 急停优先且可重复归零 | S4.3：动作执行中 `stop(emergency=True)` → `MANUAL_STOP`/`ESTOP`，active 清空、各轴归零 | 未能证伪 |
| 30 s 租约到期优先停，180 s 任务不延长租约 | S4.4：动作执行中推进 30000 ms → `EXPIRED_LEASE` 并归零；S4.9：180 s 任务的 runtime 仍使用 30 s 租约，只等待 F9 ARM、不续租 | 未能证伪 |
| 心跳失联优先停 | S4.5：推进 500 ms 后 `tick()` → `HEARTBEAT_LOST` 并归零（心跳检查先于动作到期） | 未能证伪 |
| epoch 变化拒绝旧请求 | S4.6：提交前 `environment_epoch += 1` → `EPOCH_MISMATCH` | 未能证伪 |
| 未来帧仍拒绝 | S4.7：`captured_at` 超前 1 s 的观测在 `Controller.observe` 阶段即被拒，传输层无消息 | 未能证伪 |
| 控制/传输/provider/采集/感知未被改 | S8.1：`git diff 81199ff..47534ba -- src/vrc_agent/{control,transport,providers,capture,perception}` 为空；S8.2 runtime 源码无 `rpc("arm")`/`request("arm")`/`.arm(` 调用，只经 `wait_for_arm` 等本机热键；S8.3 任务结束停控后不自动 ARM | 未能证伪 |

### 1.4 像素判据/遮罩/预热/世界/提示一字未动（S1/S7）

| 声明 | 独立证据 | 结论 |
| --- | --- | --- |
| v1–v5 策略文件与 sidecar 逐字节不变 | S1.4：`git show 81199ff:…` 与当前工作树逐件比对 v1–v5 的 `stability_v*.json`/`.sha256`（及 v2/v3/v5 的 `.py`），全部相同；v5 JSON 仍 `2e21c734…` | 未能证伪 |
| 像素判据同源 | S7.6：同一实体、同一小帧输入（含变化像素），v5 与 v6 的 `compare` 结果 4 例完全一致；S7.5 `protected_rectangles` 对同一实体两版相同；S7.7 `masked_rgb` 输出字节相同 | 未能证伪 |
| 遮罩/面积/像素/保护区/黑帧/预热参数未改 | S1.5（字段差仅五项）＋ S2.3/S2.4（改遮罩或预热配对即拒）；v6 JSON 的 `excluded_rectangles=[[1536,0,2048,512]]`、`max_changed_ppm=50000`、`pixel_tolerance=2`、`protected_margin=32`、`black_threshold=8`、`warmup_pairs=2`/`8` 帧/`100` ms 与 v5 相同 | 未能证伪 |
| 世界 v1/v2 语义摘要未改 | S7.3：`load_live_world` 摘要 v1 `dda1d3b1…`、v2 `b65b3dab…`，与第 70/72 节记录一致；世界四份文件与基线 blob 相同 | 未能证伪 |
| 10 条有效提示逐字节未改 | S7.1：两版世界 × G1–G5 共 10 条 `calibrated_prompt` UTF-8 摘要与第 70 节审计的 10 条逐条相同（含 v1/v2 共用的 G1/G2/G4）；S7.2 `CLEARANCE_GUIDANCE_VERSION=runtime-clearance-v1`、共享指导 UTF-8 摘要仍 `fceef139…` | 未能证伪 |

### 1.5 schema6/7 隔离与候选拒绝（S6/S2）

| 声明 | 独立证据 | 结论 |
| --- | --- | --- |
| v6 报告 schema7 可复验，v5 schema6 不受影响 | S6.1：完整 v6 field 闭环报告 `schema_version=7`、`navigation_design=stable-inference-v6`，`validate_stability_report` 通过；S6.2：同一夹具的 v5 报告 `schema_version=6` 仍通过 | 未能证伪 |
| 报告参数完整且不可篡改 | S6.3：16 例篡改全部拒绝——schema 6/7.0/`True`、design 回退 v5、缺 `run_parameters`、缺 `motion_envelope`、夹带未知键、`mode=live`、`sends_live_input=true`、档位与包络不一致、包络 forward 篡改、`freshness_ms=4001`、`freshness_ms=4000.0`、`task_timeout_s=30.0`、`max_actions=41`、伪造 `status=FROZEN`、伪造 digest | 未能证伪 |
| 报告年龄可下报但不可超策略值 | S6.4：`run_parameters.freshness_ms=3000` 的报告通过复验（更严格），99/0/−1 被拒；上限由策略 4000 与 `stability_policy` 摘要共同锁定 | 未能证伪 |
| 候选在 session 入口与运行时构造均拒绝 live | S6.5：`navigation_entry` 在 `build_provider`/`ControllerProcess` 之前抛 `ValueError`（"field calibration"/"cannot enable live navigation"）；`StableNavigationV6` 在 `mode=live` + 候选状态时构造即抛 "candidate stability policy cannot enable live navigation" | 未能证伪 |
| 旧版不能通过基类/工厂选 v6 漏预热 | S6.6：`StableNavigation` 基类对 v1–v6 逐版本抛 "must match the navigation class"；`STABLE_NAVIGATION_CLASSES` 显式登记 v6→`StableNavigationV6`；未知版本仍 "unknown stability policy version" | 未能证伪 |
| 报告复验拒候选声称 live | S6.3 的 `mode=live`/`sends_live_input=true` 两例（v6 候选）被 `candidate v6 cannot claim live execution` 拒绝 | 未能证伪 |

## 2. 观察与最小反例

**N1（非阻断，冻结机制）**：`load_policy("v6")` 的期望值把 `status` 取自文件本身，只约束取值闭集 `{REQUIRES_FIELD_CALIBRATION, FROZEN}`。这为将来冻结留了「只改文件字节＋sidecar、不改代码」的口，但也意味着 live 放行完全由提交的文件状态决定：若某次提交把 v6 改成 `FROZEN` 并更新 sidecar，session/运行时/报告三处都会放行。本机提交是 `REQUIRES_FIELD_CALIBRATION`，因此候选拒绝成立；冻结仍须用户授权（与 v1–v5 同一机制，非本轮引入）。

```text
S2.10 status=DRAFT          → ValueError invalid v6 budget policy or changed stability criteria
S2.11 status=True           → ValueError invalid v6 budget policy or changed stability criteria
# 当前提交：status=REQUIRES_FIELD_CALIBRATION；session 状态门槛 → candidate cannot enable live navigation
```

**N2（非阻断，守卫放宽范围）**：`SupervisedNavigation.__init__` 的任务上限从 `<= 60` 放宽到 `<= 180`，对**所有**运行时生效，不只 v6。session 的非稳定路径仍按默认 `task_timeout_s=30.0` 装配，稳定路径随后由策略覆盖为 180，因此当前行为未变；但未来调用方可在 legacy 路径直接传 180 而不选 v6。建议后续把上限与设计绑定（`task_timeout_s <= 180 if v6 else <= 60`），非阻断。

**N3（非阻断，报告自证范围）**：`validate_run_parameters` 接受 `100 <= freshness_ms <= 4000`，复验按报告值收紧证明与动作年龄；报告不含控制配置，因此复验无法证明「报告写的年龄＝配置实际值」。方向安全（下报更严格、上超被策略锁定、`stability_policy` 整字典比对），但若要完全同源，可把控制的 `observation_max_age_ms` 一并写入 `run_parameters` 并纳入比对。

**N4（非阻断，预算与步数不同时可达）**：180 s 与 40 步是两个独立上限。第 71 节现场每步约 4.5–5 s，field 档单步 500 ms×0.4，则 180 s 先于 40 步用尽；本轮只证明两个上限都被正确强制，未证明 40 步能跑满。实际位移/制动、遮挡下的重观测率仍待现场测量。

**N5（非阻断，交付审计可复现性）**：第 72 节列出的三份合成报告摘要中，`4613be22…` 与 `2ac83d67…` 能在 `.runtime/verify/` 找到同摘要文件，`464c97ec…` 只见于 `t08_v6_delivery.json` 本身（可能是临时 state 目录产物）。不影响本轮任何结论，仅提示交付审计的文件清单与现存工件未完全对齐。

## 3. 已声明限界

1. 未启动 live：不跨 `ControllerProcess`、不真实采集、不推理、不 ARM、不发送输入。候选拒绝只验证到「在 provider/控制器创建前抛错」。
2. 探针复用 `tests/integration` 的 `MemoryClient`/`assemble` 夹具与合成帧（`\x14\x20\x30\xff` 全屏），不是独立重写的采集与模型栈；预算与包络结论依赖这些夹具的真实 `Controller`/`bind_request` 路径。
3. 「像素判据/遮罩/预热/世界/提示未改」用字节比对、AST/调用等价与合成输入等价证明，未用现场 G3/G5 帧重放，也未重做 v5 复核中的现场语义复验。
4. 报告复验只用离线合成报告，未验证真实运行落盘内容、usage/费用入账与 `hourly` 折算（T10 线）。
5. Windows 捕获/DPI/焦点/窗口标题/F8/F9 与授权流程沿用既有门禁，本轮未重跑；非 autocrlf 平台上 v5 摘要口径（第 68 节 N1）仍未统一。
6. 未测 field 档实际位移/制动距离、遮挡/低照度下的重观测率与供应商 p95，也未执行固定 20 次任务。

## 4. 探针与门禁

- 探针：`.runtime/verify/t08_v6_review_probe.py`，SHA256 `43784f27dc2b331b4e434e408fcb8f40d7f100499ad0a614bb43295dba533bb1`；61 项断言（S1 6 / S2 13 / S3 11 / S4 9 / S5 6 / S6 6 / S7 7 / S8 3），**0 失败**；结果 JSON `.runtime/verify/t08_v6_review_probe.json`，SHA256 `8b3db8fadd599a05953f1a74c73cd14b8ccb759b7d8349386e649b78bef6d0ac`（UTF-8）。
- 复核者独立复跑（不引用实现方日志）：`pytest -q` **1124 passed in 311.20s**；`ruff check src tests` All checks passed!；`ruff format --check src tests` 140 files already formatted；`pip check` No broken requirements found.。日志 `.runtime/verify/t08_v6_review_gates.log`，SHA256 `9a7699cdf1fe2f77f7428b497af405976e0899bfbe93ffa9333fd8da9fdf86ae`；完整 pytest 输出 `.runtime/verify/t08_v6_review_pytest.log`，SHA256 `2655c33762bc3857f2b51e360e3dc96e4e6ac546ea8193aecb77f12785e4ba97`。
- 与实现方数字同量级（第 72 节 1124 passed in 281.64s；ruff/format 140/pip 同结论），测试数相同、耗时因机器负载不同；无新增失败。
- 探针只读产品代码并写 `.runtime/verify/`，不触碰 `src/`、`configs/`、世界/策略文件；两次运行中修正的 5 处均为探针自身写法（provider 断言、执行期年龄窗口、帧 ref/时间对应、duration 参数、未来帧在 `observe` 阶段被拒），不是产品缺陷，见探针脚本内注释。

## 5. 未覆盖

- 真实供应商请求：4000 ms 预算下云端延迟分布、`STALE_OBSERVATION` 实际消失率、tokens/费用入账。
- 现场 G3/G5 帧在新预算与 field 档下的语义、遮挡交叠与到达判据；遮罩 `[1536,0,2048,512)` 是否遮挡导航目标。
- field 档单步 0.4×500 ms 的实际位移、制动/滑行距离与碰撞风险；180 s/40 步的真实步数。
- v6 冻结后的 live 全链路：F9/F8、急停、租约/epoch 在真实控制器下的时序。
- 固定 20 次任务与 ≥16/20；T10 实机 usage/hourly 折算。
- 非 autocrlf 平台的摘要复现；`464c97ec…` 报告工件的来源（N5）。

## 6. 复现

```bash
cd /d/vrc_agent
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe .runtime/verify/t08_v6_review_probe.py
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
.venv/Scripts/python.exe -m pip check
# 策略字节三口径（v6 == sidecar；v5 工作树 ≠ git blob，属第68节 N1 既有口径）
sha256sum src/vrc_agent/live_navigation/stability_v6.json src/vrc_agent/live_navigation/stability_v6.sha256
git cat-file blob 47534ba:src/vrc_agent/live_navigation/stability_v6.json | sha256sum
```

## 7. 下一步

1. 由用户决定是否重新冻结 v6（新字节＋sidecar）；冻结前不得把 `status` 改成 `FROZEN` 或移用 v5 冻结授权，也不得在候选状态下开 live。
2. 冻结后按纪律做现场复验：4000 ms 预算下 `STALE_OBSERVATION` 消失率、field 档位移/制动、单步 500 ms 与 180 s/40 步可达性、供应商 p95；再判断 G1–G5 距离是否可达。
3. 现场复验通过后再恢复固定 20 次任务（≥16/20），不自动恢复批次，不宣称实机成功率。
4. 任何判据/遮罩/阈值/世界/提示改动都需新版本＋ADR-009 复核；急停、到期归零、租约、epoch、TTL 语义不得放宽以迁就演示（含 N2 的 legacy 180 s 缺口）。
5. 建议：把 N2 的上限与设计绑定、把 N3 的控制年龄写入 `run_parameters` 并纳入比对、核对 N5 的报告工件清单。
