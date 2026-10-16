# T05 感知与模型 provider — 验证记录

- 任务：T05（见 `docs/TASKS.md` 第 3 节完成定义与第 14 节交接）
- 负责人 / 会话：DSH / Flash（本会话）
- 日期：2026-10-13
- 版本：0.1.0，**无 Git 提交**（`D:\vrc_agent` 不是 git 仓库，没有提交哈希可以锚定被测版本）
- 结论：**DONE**。三条完成条件都有证据；独立交叉验证判定**未能证伪**。真实供应商的实机冒烟**未执行**（本机没有密钥），单独记录在第 6 节。

## 1. 环境

- Windows，单显示器 2160×1440 @150% 缩放；`.venv\Scripts\python.exe`，Python 3.14.6；依赖只有 pydantic 与 python-osc（本任务**未新增任何依赖**）。
- 全部验证离线：回放素材 + 假供应商 + 注入的 stub 传输层。**没有打开 socket、没有调用任何模型、没有发送 OSC、没有落盘任何画面。**
- 本会话文件沙箱不允许 pytest 创建 `tmp_path`：全量套件固定有 10 个 `ERROR (setup)` 来自既有测试（7 个 `tests/integration/test_process.py`、1 个 `tests/unit/test_contracts.py`、2 个 `tests/unit/test_telemetry.py`），全部是 `PermissionError: [WinError 5]`，与 `perception/`/`providers/` 无关。

## 2. 实现与接口

| 文件                          | 职责                                                                         |
| ----------------------------- | ---------------------------------------------------------------------------- |
| `providers/base.py`           | `Deadline`、`CancelToken`、`ModelInput`、`ModelResponse`、`Usage`、`UsageLedger`、`run_attempts` |
| `providers/protocol.py`       | `ModelProvider`、`ProviderSink` 协议                                          |
| `providers/errors.py`         | `ProviderFailure`（九种）、`RETRYABLE_FAILURES`、`ProviderError`               |
| `providers/fake.py`           | `FakeSceneProvider`（确定性合成载荷，可模拟六种行为）+ 原 `FakeProvider`（T01 固定动作示例） |
| `providers/openai_compat.py`  | 真实供应商适配器（OpenAI 兼容 HTTP），**唯一的出网出口**                       |
| `providers/factory.py`        | `build_provider`/`build_ledger`/`describe_provider`；密钥只报布尔值            |
| `perception/interpreter.py`   | `Perception.interpret(observation, frame=…) -> SceneState`：守卫、乱序/误路由防护、统计 |
| `perception/scene.py`         | 载荷解析与约束：清洗、丢弃并留痕、越权字段识别、场景汇总                        |
| `perception/errors.py`        | `PerceptionFailure`（`OUT_OF_ORDER`/`MISROUTED_RESPONSE`）、`PerceptionError`  |

设计要点（完整说明见 `docs/ARCHITECTURE.md` 第 3/4/7/8 节）：

- `SceneState` 新增 `confidence`：场景整体可信度取实体置信度的**最小值**，模型自报值不被采信；`confidence=None` 表示看不清，此时契约强制 `entities` 为空，并且 `uncertainties` 至少有一条原因（模型没给就补默认原因）。
- **供应商失败不降级成 unknown 场景**：`ProviderError` 原样上抛、不产出场景。unknown 场景的含义是「问过了但看不清」，供应商失败是「根本没问到」；混在一起会让基础设施故障伪装成感知不确定性。「没有场景就没有动作」由控制层保证（提交动作必须绑定新鲜且 `visible` 的观测）。
- 守卫（`window_state != visible`、无帧字节、观测过旧）**不调用供应商**，只计入 `skipped_*`；`stats()` 的 `scenes`/`unknown_scenes` 只统计走过模型通道的场景（账目 `calls + skipped_*` = 调用次数）。
- 越权字段（`kind`/`action`/`lease_id`/`expires_at_ns`/`tool`/`osc` 等 24 个）一律不进场景，只留一条「已忽略」的说明；`bbox` 必须归一化四元组、`confidence ∈ [0,1]`、实体必须引用**本次**观测作为证据，否则丢弃并留痕。
- 费用：`UsageLedger` 区分已知与未知费用，`unknown_cost_calls` 非零时 `cost_complete=False`，**未知绝不记成 0**；缺价格表时成本是未知；付费供应商每次调用前过 `ensure_affordable`。
- 隐私：密钥只留在内存、只以布尔值出现在 `describe_provider`/`doctor`，`repr(provider)` 脱敏；`send_images` 默认 false，即使传了帧假模型也只留在内存（`image_count` 恒为 0）；`perception/` 不导入 `transport`/`control`/`osc`（有静态测试钉住），`urlopen` 只出现在 `providers/openai_compat.py`（有静态测试钉住）。

## 3. 验证命令与结果

```text
.venv\Scripts\python.exe -m pytest -p no:cacheprovider --no-header --tb=no -q
  → 437 passed, 10 errors in 3.43s（退出码 1；10 个 error 全部是上面的沙箱 tmp_path 问题）

.venv\Scripts\python.exe -m ruff check src tests
  → All checks passed!

.venv\Scripts\python.exe -m ruff format --check src tests
  → 58 files already formatted

.venv\Scripts\python.exe -m pip check
  → No broken requirements found.
```

T05 相关测试（9 个文件，合计 **219 passed**）：

| 文件                                          | 用例数 | 覆盖                                                     |
| --------------------------------------------- | ------ | -------------------------------------------------------- |
| `tests/unit/test_perception_scene.py`         | 37     | 载荷解析、越权字段、丢弃留痕、上限、汇总                  |
| `tests/unit/test_perception_interpreter.py`   | 21     | 守卫、乱序、误路由、aged-out、统计                        |
| `tests/unit/test_providers_base.py`           | 20     | `Deadline`/`CancelToken`/`Usage`/`UsageLedger`/`run_attempts` |
| `tests/unit/test_providers_fake.py`           | 20     | 假供应商的确定性、六种行为、零费用语义、账目              |
| `tests/unit/test_providers_openai_compat.py`  | 52     | stub 传输：超时/取消/重试/HTTP 码/畸形/拒绝/发图开关/脱敏/预算 |
| `tests/unit/test_providers_factory.py`        | 17     | 工厂、密钥存在性、`describe_provider`、预算门禁           |
| `tests/unit/test_t05_privacy.py`              | 8      | 不落盘/不出网哨兵、事件字段白名单、静态边界（AST）         |
| `tests/integration/test_perception_cli.py`    | 10     | `perceive`/`doctor` 退出码、JSON 形状、付费门禁、边界值    |
| `tests/contract/test_perception_contract.py`  | 34     | **独立复核者编写**：契约再校验、控制层交互、场景→动作不可行 |

## 4. 三条完成条件的证据

**① 超时/错误/乱序响应不会绕过控制层**

- 三条路径都不产出场景：`OUT_OF_ORDER`（入口乱序或 `await` 期间观测被更新）、`MISROUTED_RESPONSE`（响应里的 `request_id`/`observation_id` 与请求不符）、`ProviderError`（超时/取消/传输错误/被拒）。
- 场景与动作之间没有通路：`SceneState` 字段与 24 个动作/权限字段零交集（`extra="forbid"`），用 `SceneState` 当 `ActionRequest` 会被校验拒绝；`perception/` 不导入 `transport`/`control`/`osc`（AST 静态测试）。
- 控制层侧：模型伪造租约 → `REJECTED/EXPIRED_LEASE`；绑定没见过的观测 → `REJECTED/STALE_OBSERVATION`；非 `visible` 观测 → `PAUSED/FOCUS_LOST` 且动作 `NOT_ARMED`；独立复核运行中 transport 的动作计数为 0（正向对照：合法请求 `RUNNING` + motion 1 次）。
- 独立复核用 12 个越权字段的恶意载荷（含 `action`/`lease_id`/`expires_at_ns`/`tool`/`osc`/`forward`）验证：全部被忽略并留痕，场景 JSON 递归扫键无泄漏。

**② 看不清返回 unknown**

- 7 条路径全部得到 `confidence=None` + 空 `entities` + 至少一条原因文本：非对象载荷；`entities: []` 且模型没给解释；实体 bbox 越界/证据不匹配（全部被丢弃）；`window_state=lost`；没有帧字节；观测过旧（5000 ms > `max_observation_age_ms`）。
- 契约层强制：`SceneState` 校验拒绝「`confidence=None` 却有实体」。
- 守卫挡下的观测不会去调用供应商（省时间、省钱、不出网）。

**③ 合成数据测试、默认不计费**

- 默认 `provider=fake`（免费、无网络、确定性合成载荷，uncertainties 写明「不是真实感知」）；`send_images=False`；`budget_usd=0.0`。
- 运行期哨兵（`urlopen`/`getaddrinfo`/`create_connection` 全部换成会抛异常的桩）下跑默认 `perceive` 与无参 CLI：**拦截 0 次**；ledger `calls=5`、`spent_usd_known=0.0`、`unknown_cost_calls=0`、`cost_complete=True`、`image_count=0`。
- 付费供应商需要三件事齐备（配置 `budget_usd>0` + 环境变量里存在密钥 + 命令行 `--allow-paid`）；缺密钥 → `NOT_CONFIGURED`；零预算 → `BUDGET_EXCEEDED`。
- 费用为未知时（没有价格表）`unknown_cost_calls` 递增且 `cost_complete=False`，**不记成 0**。

## 5. 冒烟输出（离线）

```text
.venv\Scripts\python.exe -m vrc_agent perceive --max-steps 3
```

- `mode="perceive-offline"`、`produces_actions=false`、`sends_input=false`、`writes_media=false`、`environment_epoch=1`、`stopped_reason="max_steps"`。
- 3 个场景：`confidence=0.6`、实体 `track_id="synthetic-obs-00000N"`、`label="synthetic_landmark"`、`bbox=[0.1,0.1,0.3,0.3]`、`evidence_observation_id` == 该场景的 `observation_id`、uncertainties 为合成载荷说明；`generated_at_ns` 依次 1000000000 / 1150000000 / 1300000000（与回放共用同一个虚拟时钟）。
- `usage.calls=3`、`spent_usd_known=0.0`、`unknown_cost_calls=0`、`cost_complete=true`；`capture.ok=3`、`last_size=[96,72]`。
- `.venv\Scripts\python.exe -m vrc_agent doctor`：感知块为 `provider="fake"`、`paid=false`、`offline=true`、`api_key_env="OPENAI_API_KEY"`、`api_key_present=false`、`send_images=false`、`budget_usd=0.0`、`price_configured=false`、`fake_behaviour="ok"`；输出里没有任何密钥值（测试用哨兵字符串验证过）。
- `doctor` 的 `dpi_awareness: "unaware"` 是 **doctor 进程自身**的状态（它不做采集、也不设感知级别），不是采集路径的问题：`live_capture()` 会先确保 per-monitor-v2，T04 已实测。

## 6. 真实供应商：未执行（如实记录）

- 本机没有可用的模型密钥（`doctor` 报 `api_key_present: false`），因此**没有对任何真实端点发出请求**，也就没有真实的 usage/价格数据。
- 适配器的行为只用**注入的 stub 传输层**覆盖：HTTP 200 正常、400/401/403/404/429/500/503、超时、连接中断、非 JSON 响应体、缺 `choices`、空内容、`finish_reason="length"`、显式拒绝、`response_format` 与图片附带的开关、`Authorization` 头存在而密钥不进 repr/响应/台账/遥测、价格表缺失时成本未知、预算门禁。
- 影响与边界：**「能连上真实供应商」这件事没有被验证过**，`openai_compat` 的端到端可用性仍是未知；换用真实供应商时至少要先人工确认 base_url、模型名、价格表与预算，再单独记一次冒烟（按 T05 定义，这一步是单独记录项，不是离线测试的前置条件）。

## 7. 未验证项、已知限制与待改小项

- 未验证：真实 HTTP 端点；`deadline`/`cancel` 的**真实到期/取消传播**（只验证了「已过期的 deadline 直接超时」与「已取消的 token 直接取消」两种静态情形）；`send_images=true` 的实际请求体（只在 stub 层断言过）；契约测试里 34 条断言的内容未被第三方逐条审计（只被独立执行者运行过）。
- 哨兵缺口：运行期哨兵拦的是 `urlopen`/`getaddrinfo`/`create_connection`，没有拦裸 `socket.connect`（静态检查显示 `perception/`/`providers/` 都不导入 `socket`）。
- 已知限制（设计如此）：感知只描述看得见的东西——没有真人身份、没有精确位置、没有世界语义；`track_id` 是本次跟踪标识，不可跨会话绑定真人；规划器、结果验证、语音仍未实现（T06/T07）。
- 待改小项（由独立复核与实现者共同发现，不影响正确性，未在本轮改动以免让已验证的产物失效）：
  1. CLI 参数错误只给通用提示（`Invalid configuration or command. Check types, bounds and controller state.`），付费门禁与 `--max-steps` 越界的**具体原因**只由库层异常给出；
  2. `replay`/`perceive --fixture` 指向不存在的文件时走 `OSError` 分支、退出码 1，输入错误按约定应为 2（T04 期间记录的同一问题）。

## 8. 独立交叉验证（ADR-009）

实现（DSH / Flash）与独立复核（两个子代理）由不同执行者完成，全部产物在 `.runtime/verify/` 下。**独立性折扣如实记录**：脚本与判读都由复核者完成，但运行在同一台机器、同一个工作树上；没有 git 提交可以锚定版本。

**第一轮（子代理 `769e7f02`，交付 `tests/contract/test_perception_contract.py`，34 个用例）**：期望值独立写死（13 步窗口状态序列、5 次调用 / 8 次拦截、18 条畸形实体、字段集、协议面），不复用实现内部的判定表；由复核者**自行运行**得 `34 passed`（首轮 5 处失败全是复核者自己的期望写错，已在其报告中披露）。它提出四条不一致/存疑点：

1. 供应商失败不降级成 unknown 场景 → **按现状保留**，并在架构与本文档写明理由（「没问到」不等于「看不清」）；其判断「该改的是任务书措辞而不是实现」被采纳为文档说明。
2. `stats()` 语义无文档 → **已改**：`Perception.stats()` 补了语义 docstring，架构第 4 节同步。
3. 空实体列表的 unknown 场景没有原因文本 → **已改**：`build_scene` 在没有可用实体且模型没给解释时补一句默认原因；复核者那一条期望值随之从 `False` 改为 `True`（改动点与理由已记录在此，未隐藏）。
4. `Observation.frame_ref` 与 `capture.frames.Frame.ref` 命名并存 → **按文档说明处理**：架构第 4 节写明两者是同一个值在两个地方的两种叫法。

**第二轮（子代理 `t05-verifier`，只读证伪，报告 `.runtime/verify/v_t05_final_report.md`，攻击脚本 `v_t05_attack2.py` 全部 CHECK PASS）**：验证对象是上述两处改动**之后**的工作树。三条完成条件逐条判定 **未能证伪**（判定口径：未能证伪 ≠ 证明正确）；门禁数字与本文档一致（ruff 全绿、契约测试 `34 passed in 0.57s`、全量 `437 passed, 10 errors in 3.21s`）；两份改动与架构措辞一致（`calls=5 + skipped_not_visible=8 = 13` 步吻合）。它如实记录了残留缺口（见第 7 节）并指出 `stale_after_call` 计数器在文档里没写——该计数器已在架构第 4 节补上说明。

## 9. 复现命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests/contract/test_perception_contract.py -q
.\.venv\Scripts\python.exe -m vrc_agent perceive --max-steps 3
.\.venv\Scripts\python.exe -m vrc_agent perceive --max-steps 100        # 跑到素材耗尽：stopped_reason=replay_exhausted
.\.venv\Scripts\python.exe -m vrc_agent doctor
.\.venv\Scripts\python.exe -m vrc_agent perceive --provider openai_compat              # 预期退出码 2（缺 --allow-paid）
.\.venv\Scripts\python.exe -m vrc_agent perceive --provider openai_compat --allow-paid  # 预期退出码 2（没有正预算）
```
