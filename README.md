# VRChat AI Agent

面向 VRChat 玩家的本机 AI 代玩项目。当前已实现**阶段 1＋2 的工程与控制基础**：配置、数据契约、离线假模型、OSC 传输、独立控制进程、动作到期归零、心跳 watchdog、人工急停和接管入口；**T04 的窗口采集与回放**（已完成）：指定窗口的内存帧采集、窗口状态显式上报、确定性回放素材，并已在本机真实 VRChat 窗口上实测通过（2160×1440、逐帧不同、人工目视核对过画面内容）；**T05 的感知与模型供应商适配**（已完成）：把观测变成受约束的场景（实体、置信度、证据、不确定性）、可配置的真实供应商适配器、用量与预算台账；以及 **T07 的交流与音频层**（已完成，并经 ADR-009 独立交叉复核）：限长/节流/过期即弃的 chatbox 队列、半双工门控、可被本机停止在一个音频块内取消的语音播放、自声回流诊断。

**T06 的离线规划与端到端闭环已完成（含 ADR-009 独立交叉复核，六项检查均未能证伪）**：感知→Proposal→权限绑定→控制执行→新观测→结果验证。实机自主导航和自动游玩仍未实现；真实大模型冒烟未执行，**真实语音识别/合成引擎也未接入**（T07 只交付链路与协议）。T00 基础实机能力已验收（TASKS 第 13 节），不代表自主游玩能力。默认离线运行不创建网络输出、不打开音频设备。

## 快速运行

在项目目录用 PowerShell 执行。当前目录已配置 `.venv`，可以直接运行：

```powershell
.\.venv\Scripts\python.exe -m vrc_agent doctor --config configs/default.toml
.\.venv\Scripts\python.exe -m vrc_agent run --mode dry-run
.\.venv\Scripts\python.exe -m vrc_agent replay
.\.venv\Scripts\python.exe -m vrc_agent capture --frames 3
.\.venv\Scripts\python.exe -m vrc_agent perceive --max-steps 5
.\.venv\Scripts\python.exe -m vrc_agent simulate --scenario navigation
.\.venv\Scripts\python.exe -m vrc_agent navigate-suite
.\.venv\Scripts\python.exe -m vrc_agent dialogue --turns 20
```

离线演示用假模型提出一个 200 ms 的移动输入，独立进程只记录输入，再自动归零并退出。输出中的 `COMPLETED` 表示控制序列完成，`verification: UNVERIFIED` 表示没有验证游戏世界中的效果。

`replay`、`capture`、`perceive` 与 `dialogue` 都不会发送 OSC 输入：`replay` 跑内置合成素材，`capture` 只读取真实窗口并打印帧元数据，`perceive` 用假视觉模型把回放的观测变成场景，`dialogue` 默认全程离线（不打开音频设备、不出声）。

## T06 离线闭环回放

```powershell
.\.venv\Scripts\python.exe -m vrc_agent simulate --scenario navigation
.\.venv\Scripts\python.exe -m vrc_agent simulate --scenario no_progress
.\.venv\Scripts\python.exe -m vrc_agent simulate --scenario model_late
.\.venv\Scripts\python.exe -m vrc_agent simulate --scenario estop
```

四条素材分别得到 `SUCCESS`、`NO_PROGRESS`、`MODEL_LATE`、`MANUAL_STOP`；JSON 中有动作、验证、新观测关联、归零时刻和可重复的 `sequence_digest`。命令成功回放时退出码为 0，导航是否成功看 `outcome` 和 `results[].verification`。退出时始终停止，不自动恢复。

这是合成帧序列上的导航模拟：模型从绿色矩形像素识别目标，规划器依据新场景选择短动作，目标图像面积达到配置阈值才验证成功。素材不是动作驱动的物理世界；也不是 VRChat 实机导航。整个入口只使用记录型控制内核，不开 OSC socket、不计模型费用、不保存媒体、没有 live 参数。详细边界和验证见 [T06 记录](docs/validation/T06_runtime.md)。

## T08 离线导航与恢复

`python -m vrc_agent navigate-suite` 运行固定第一世界替身的 20 次任务，输出完整 JSON 报告。动作驱动合成画面，再经 T05 感知与 T06 控制闭环；导航按新观测转向、接近和确认到达，连续 3 次运动无进展后最多两轮短恢复，仍失败即暂停。每次运动结束归零，接管计失败。

本轮合成回归 **16/20（80%）**，包含四个恢复成功和四个预设故障失败。配置与任务集在 `src/vrc_agent/skills/worlds/first_world.json`；不是通用地图，也不是实机成功率。此离线入口无 live 开关、无 OSC 输出、无付费模型。离线独立复核已完成，见 [T08 导航记录](docs/validation/T08_navigation.md) 与 [复核记录](docs/validation/T08_review.md)。

现场标定后的五组 × 四次 live 配置已冻结，新增受监管装配已实现并通过离线自测；T08 仍 `IN_PROGRESS`，新增路径独立复核与真实 20 次验收待完成。先查看计划（不采集、不出网、不创建控制器）：

```powershell
.\.venv\Scripts\python.exe -m vrc_agent navigate-live --task G1-1
```

真实运行使用单独的 `navigate-live` 入口和独立控制进程，必须显式 live、确认标定/付费/图像发送并本机按 F9；不自动 ARM。配置示例为 `configs/navigation-live.example.toml`。完整操作、冻结摘要、失败计数规则与待现场核对的到达门槛见 [T08 live 验证记录](docs/validation/T08_live.md)。本轮未执行真实导航或付费模型调用。

2026-10-17：用户已授权冻结v5，`configs/navigation-live.toml` 显式选择 `stable-inference-v5`，默认仍dry-run；世界v2须显式 `--world-version v2`，不传仍v1。该现场配置的 `[perception]` 使用 `image_format = "jpeg"` / `jpeg_quality = 85`；通用默认及旧配置保持PNG，可显式 `image_format = "png"`。安装 `requirements.lock` 包含锁定Pillow依赖。编码仅影响模型上传，稳定性仍比较原始BGRA，预算/判据不变。见[冻结验证](docs/validation/T08_v5_freeze.md)与[JPEG验证](docs/validation/T08_jpeg_encoding.md)；真实模型延迟和20次任务尚待现场复测，T08仍IN_PROGRESS。

## 新环境安装

项目声明支持 Python 3.12–3.14，目前实际验证的是 Windows / Python 3.14.6。规划中的 3.12 在当前机器未安装。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
.\.venv\Scripts\python.exe -m vrc_agent --help
```

`requirements.lock` 固定运行、测试及构建依赖版本，不包含哈希。首次安装需要网络，运行离线演示不需要网络或模型密钥。本机默认镜像无法解析依赖、官方下载站超时；本次成功使用了以下安装命令，可在相同网络问题下采用：

```powershell
.\.venv\Scripts\python.exe -m pip install --index-url https://repo.huaweicloud.com/repository/pypi/simple -r requirements.lock
```

该参数只影响本条命令，不修改用户全局 pip 配置。

## 离线交互与停止

```powershell
.\.venv\Scripts\python.exe -m vrc_agent run --mode dry-run --interactive
```

控制台命令：`arm` 启用、`pulse` 记录一次短移动、`status` 查看状态、`stop` 急停、`reset` 回到未启用状态、`quit` 退出。急停后需要 `reset` 再 `arm`；不会自动恢复。

另一个 PowerShell 可独立发出急停，即使主代理卡住：

```powershell
.\.venv\Scripts\python.exe -m vrc_agent stop
```

使用自定义 `--state-dir` 时，运行和停止必须指定同一个目录。`stop` 回执只说明本机停止信号已发出，不能证明 VRChat 中的角色已停止。

## Live 控制入口（待阶段 0 实机验证）

```powershell
.\.venv\Scripts\python.exe -m vrc_agent run --mode live --config configs/default.toml
```

- 仅支持 Windows，启动后保持 `DISARMED`，不会自动移动或播放语音。
- 将 VRChat 切到前台；窗口标题必须与配置 `control.window_title` 完全匹配。按 **F9** 人工启用，默认租约 30 秒。
- **F8** 急停，**F10** 重置回 `DISARMED`。租约到期、失去焦点或异常后需要重新人工启用；按键占用会导致启动失败。
- 人工探针默认禁用（`configs/default.toml` 中 `enable_motion_probe = false`）；实机监督测试使用 **`configs/probe.toml`**（现场显式配置）。已启用状态下可用探针键：**F7** 前进 / **Ctrl+F11** 后退、**F5** 左移、**F4** 右移（各 200 ms、强度 0.2 的短动作）、**F6** 右转 / **Ctrl+F12** 左转（各 500 ms 满强度，便于观察）、**F3** 交互（UseRight 脉冲 300 ms）、**F1** 抓取（GrabRight 脉冲 300 ms）、**F11** 丢下（DropRight 脉冲 300 ms）、**F2** 聊天框（固定文本，2 秒节流）。按钮发出前经控制层授权检查（状态/租约/待归零），未释放前连按被拒。这是人工测试探针，不是自主决策。
- Ctrl+C 或关闭主程序会触发停止；独立控制进程还检查心跳。自动识别普通键鼠输入接管尚未实现，必须使用 F8、停止命令或切出目标窗口。
- 收到 Avatar 切换消息后暂停，并要求检查参数配置、重启控制器。尚不能自动识别同窗口中的所有世界加载或场景变化。
- 不存在保证角色停止的网络 ACK；系统崩溃、控制进程自身被终止和 UDP 丢包仍可能造成释放失败。

当前只开放本机 loopback OSC。默认发送到 9000、接收 9001；端口冲突或同一状态目录的控制器重复启动会明确失败。live 同时还有目标端口单实例保护。

## 窗口采集与回放（T04）

采集只读取目标窗口，**不发送任何输入、不把画面写入磁盘**：帧只放在内存的有界帧表里，观测里给出的 `frame_ref` 是一个内存引用（受标识符正则约束的字符串，不是文件路径）。

```powershell
.\.venv\Scripts\python.exe -m vrc_agent capture --frames 3 --interval-ms 200
.\.venv\Scripts\python.exe -m vrc_agent replay
.\.venv\Scripts\python.exe -m vrc_agent replay --fixture tests/replay/t04_smoke.json
```

- `capture` 是只读冒烟：按 `[capture]` 配置找窗口，抓 N 帧，打印每帧的 `observation_id`、`window_state`、`captured_at_ns`、`frame_size`、`frame_ref` 和摘要前缀，并附窗口枚举与统计。**采集失败时不会拿上一帧冒充新帧**，而是如实报失败原因。
- `window_state` 的取值：`visible` 表示拿到了新帧；`lost`（窗口消失或不可见）、`minimized`、`unknown`（被遮挡、黑屏、尺寸异常、抓取失败、DPI 不达标）都表示这一帧没有拿到。除 `visible` 之外的状态会让已启用的控制器暂停——看不清就不动。
- 失败原因码：`WINDOW_NOT_FOUND`、`WINDOW_HIDDEN`、`WINDOW_MINIMIZED`、`WINDOW_OCCLUDED`、`WINDOW_EMPTY_SIZE`、`WINDOW_TOO_LARGE`、`FRAME_BLACK`、`CAPTURE_FAILED`、`DPI_UNAWARE`。
- 本机实测过、写进架构的四条限制：GDI `BitBlt` 读的是屏幕，被遮挡时会把遮挡窗口的画面当成目标窗口，所以**必须**先把 VRChat 切到前台、别被别的窗口盖住；窗口 DC 对 Electron/Chromium 一类 DWM 窗口会返回全零画面；`PrintWindow` 对本机 VRChat 窗口返回拒绝访问；采集进程必须是 per-monitor DPI 感知（本机 150% 缩放下否则会拿到被系统缩放的 1440×960 而不是 2160×1440）。
- 本机实测结果（2026-10-13，单显示器 2160×1440 @150%）：VRChat 在最前时 `capture --frames 3` 拿到 3/3 `visible`、`frame_size=[2160,1440]`、遮挡采样 25/25、三帧摘要互不相同，并另存一帧做过人工目视核对（画面确实是 VRChat 本体）；VRChat 被其他窗口覆盖时如实报 `WINDOW_OCCLUDED` 且不产出帧。证据见 [T04 采集验证记录](docs/validation/T04_capture.md)。
- 回放用于离线回归：素材是人工合成的小图与脚本化窗口状态（含八种失败与一次分辨率变化），`replay` 用虚拟时钟重放，事件顺序和摘要可跨进程复现，素材跑完即报 `ReplayExhausted`，不会循环填充。
- 默认配置在 `[capture]` 段：窗口标题默认继承 `control.window_title`；可关掉未遮挡要求、改遮挡采样密度、调整黑帧阈值与尺寸上限、设置内存帧数。

## 感知与模型供应商（T05）

`perceive` 把「观测」变成「受约束的场景」，默认全程离线：

```powershell
.\.venv\Scripts\python.exe -m vrc_agent perceive --max-steps 5
.\.venv\Scripts\python.exe -m vrc_agent perceive --max-steps 5 --fixture tests/replay/t04_smoke.json
.\.venv\Scripts\python.exe -m vrc_agent doctor   # 打印感知供应商块（只说密钥在不在，不打印密钥）
```

- 输出里有 `scenes`（每条含 `confidence`、`entities`（`track_id`/`label`/`bbox`/`evidence_observation_id`）、`hazards`、`uncertainties`）、`usage`、`perception` 与 `capture` 统计，以及 `produces_actions: false`、`sends_input: false`、`writes_media: false`。**这条路径不可能产生动作、租约或 OSC 消息**：场景字段与动作/权限字段没有交集，也没有任何代码把它接到控制层。
- 场景置信度是**实体置信度的最小值**；看不清（窗口不可见、没有帧、观测过旧、模型回答不可用）时是 `confidence: null` + 空实体 + 一条说明原因的文字，**不会编造实体**。这些「没看清」的观测不会去调用模型。
- 假模型（默认 `provider = "fake"`）是确定性的合成载荷，标签带 `synthetic` 前缀，uncertainties 里写明「合成载荷，不是真实感知」。它免费、不出网，token 与费用是**确实的 0**。
- 切到真实供应商（`openai_compat`，OpenAI 兼容 HTTP）必须同时满足三件事：`[perception].budget_usd > 0`、环境变量里存在密钥（`api_key_env`，默认 `OPENAI_API_KEY`，只保存变量名）、命令行显式加 `--allow-paid`。缺一样就拒绝，不会发请求。**画面默认不发送**，`--send-images` 才会把内存里的帧交给适配器。
- 费用语义：有准确 usage 用供应商数据，缺价格表时成本显示为未知（`cost_complete: false`、`unknown_cost_calls`），**绝不把未知记成 0**；零预算或超出预算直接 `BUDGET_EXCEEDED`。超时、取消、传输错误、被拒都会如实报原因码并且**不产出场景**（「没问到」不等于「看不清」）。
- **真实供应商的实机冒烟尚未执行**（本机没有密钥，`doctor` 报 `api_key_present: false`）：适配器只用注入的 stub 传输层做过离线测试（超时、重试、HTTP 错误码、畸形响应、密钥不泄漏、预算门禁）。这一步按 T05 定义单独记录，不是离线测试的前置条件；要跑真机冒烟请先自己配好密钥与预算。证据见 [T05 感知验证记录](docs/validation/T05_perception.md)。

## 交流与音频（T07）

```powershell
.\.venv\Scripts\python.exe -m vrc_agent dialogue --turns 20          # 20 轮计时（离线，不开设备）
.\.venv\Scripts\python.exe -m vrc_agent dialogue --scenario cancel   # 100ms 处本机停止 → 立即取消播放
.\.venv\Scripts\python.exe -m vrc_agent dialogue --scenario pause    # 暂停清空待发，恢复后不补播
.\.venv\Scripts\python.exe -m vrc_agent dialogue --scenario echo     # 自声回流诊断（离线为模拟回流）
.\.venv\Scripts\python.exe -m vrc_agent dialogue --devices           # 只读枚举音频设备（不打开流）
.\.venv\Scripts\python.exe -m vrc_agent doctor                       # 含 dialogue 只读块
```

- **默认什么都不打开**：`[dialogue].enabled = false`，`dialogue` 默认用离线假后端与虚拟时钟，不打开设备、不出声。`--live-audio` 是显式开关，会真的打开设备并在报告里标注。
- **队列**：有界（满了淘汰最旧）、两次 chatbox 发言之间有最小间隔（控制层还会再拦一次 `RATE_LIMITED`）、每条发言有生存期，到点即弃、**不在恢复后补播**。轮到发送但还没到窗口时会等，而不是丢。语音通道不受 chatbox 节流限制。
- **半双工**：首版不做回声消除，AI 播放期间一律门控麦克风（不录音、不转写）；暂停与停止同样不监听；急停后需要人工 `reset()`。
- **播放可被本机停止立即取消**：播放按 20 ms 的音频块进行，取消在一块之内生效；`--live-audio` 下取消回调接到与本机 `stop` 命令同一个停止事件。真机实测：请求 2685 ms 的语音在 100 ms 处被停止，实际只播了 60 ms。
- **默认不保存真人语音**：音频只在内存里，转写后立刻清空缓冲；报告与日志只有长度、时长、能量和原因码，**没有文本、没有音频**；本任务没有实现任何「调试媒体落盘」开关。
- **真实设备与路由**：设备用名字子串选择（如 `output_device = "Voicemeeter Input"`、`input_device = "Voicemeeter Out B1"`）。把 Voicemeeter 的 `Input → B1` 回流打开、并在 VRChat 里选择对应虚拟麦克风，属于**用户可执行步骤**，代码不自动安装驱动、不改路由。真机回流诊断在未配置路由时如实报 `no_signal`。
- **真实语音引擎未接入**：目前只有协议与确定性假实现（假合成 = 定频短音，假识别 = 脚本或能量门）。要接 Piper/SAPI/Whisper 等请按验证记录里的说明自行接入。证据见 [T07 交流验证记录](docs/validation/T07_dialogue.md)。

## 日志、费用与预算（T10）

```powershell
.\.venv\Scripts\python.exe -m vrc_agent report --scenario estop --turns 2 --max-steps 2  # 一份离线报告贯穿采集→感知→规划/执行→交流
.\.venv\Scripts\python.exe -m vrc_agent logs --keep-days 14                              # 只列出过期日志候选，不删任何东西
.\.venv\Scripts\python.exe -m vrc_agent logs --keep-days 14 --apply                      # 显式删除过期日志
.\.venv\Scripts\python.exe -m vrc_agent navigation-report --report-dir <报告目录>        # 实机报告汇总（含 usage 与每小时折算）
```

- **报告是结构化 JSON**：`elapsed_ms`、五阶段计时（每阶段 count/min/mean/p50/p95/max/sum）、按家族分组的错误码、日志回读、计数、`usage`、`hourly` 与 `warnings`，并带 `offline`／`sends_live_input`／`writes_media` 三个自述标志。
- **未知不以 0 替代**：没有价格表是 `PRICE_UNKNOWN`、token 缺失是 `TOKENS_UNKNOWN`、时长为 0 是 `NO_DATA`、有未知计费调用时每小时费用是 `None`（`COST_INCOMPLETE`）。只有确实已知的 0（假模型 0 token）才写 `0.0`。
- **价格表必须带生效日期与币种**：`price_as_of` 缺失或不是真实日期时，加载配置与构造供应商都会直接失败——没有日期的价格比「不知道花了多少」更危险。
- **预算在每次付费调用前检查**：耗尽即 `BUDGET_EXCEEDED`，运行时停止控制器并进入 PAUSED，而不是算作运行时错误；有未知费用调用时 `remaining_usd` 只作为上界（`remaining_is_upper_bound`）。
- **日志净化**：字段白名单九项，值必须是标量且字符串需标识符形状；命中 `sk-`、`Bearer`、`Authorization`、`api_key`、`data:image/` 等标记写 `[redacted]`，不透明长串与超长文本直接丢弃并计数。密钥、授权头、原始画面、转写文本都进不了日志。
- **保留策略默认不动手**：`logs` 不删任何东西，只有 `--apply` 才删除超过保留期的 `.jsonl`（默认 14 天），不跟随符号链接、不进子目录。
- 证据与限制见 [T10 日志与费用验证记录](docs/validation/T10_telemetry.md)：三个条件有离线证据，**「实机报告能折算每小时估计用量」尚没有真实实机报告核对过**，T10 因此保留 IN_PROGRESS。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m ruff format --check src tests
.\.venv\Scripts\python.exe -m pip check
```

测试包含 fake clock、真实本机随机 UDP 测试端口及专用测试子进程。不会向默认 VRChat 端口输出，也不调用收费服务（默认 provider 是免费的假模型；没有任何测试打真实模型端点）。默认不打开音频设备、不保存任何音频。实际验收范围见 [阶段 1＋2 验证记录](docs/validation/STAGES_1_2.md)，T04 采集的证据见 [T04 采集验证记录](docs/validation/T04_capture.md)，T05 感知的证据见 [T05 感知验证记录](docs/validation/T05_perception.md)，T06 闭环的独立复核见 [T06 复核记录](docs/validation/T06_review.md)，T07 交流的证据见 [T07 交流验证记录](docs/validation/T07_dialogue.md)，T08 离线导航的独立复核见 [T08 复核记录](docs/validation/T08_review.md)，T10 日志与费用的证据见 [T10 验证记录](docs/validation/T10_telemetry.md)。

当前完整套件为 789 passed（T10 新增 86 项：错误码 8、延迟统计 8、费用 13、预算 9、报告 17、日志保留 7、隐私 16、CLI 集成 7、实机报告口径 1）；在受限沙箱里跑会出现 10 个 error 与若干 failed，全部来自环境限制（沙箱不允许 pytest 创建可枚举的 `tmp_path` 临时目录、也不允许捕获子进程输出），与产品代码无关，一次性完全权限重跑即为全绿（机制证据见 [T06 复核记录](docs/validation/T06_review.md) 第三节）。

运行日志存放在 `.runtime/<session_id>.jsonl`，不保存密钥、聊天正文、截图或录音；默认不自动删除日志，可在控制器退出后人工清理。状态查询只返回最近 32 条 dry-run 输入，不能替代完整录像或世界结果验证。

## 给后续 agent

1. 阅读 [AGENTS.md](AGENTS.md)。
2. 阅读 [开发规划](docs/DEVELOPMENT_PLAN.md) 和 [架构契约](docs/ARCHITECTURE.md)（第 4 节有 T04 采集、T05 感知、T06 离线闭环、T07 交流与音频四段实现说明）。
3. 阅读 [任务状态与交接](docs/TASKS.md)，不要重做已完成的 T01–T07：**T04 采集与回放、T05 感知与模型供应商、T06 规划器与离线闭环、T07 交流与音频都已 DONE**，验收条件与证据在各自的交接节（第 9、14、15、17 节）与验证记录里，T06 与 T07 都经过另一执行者的独立交叉复核。T04 已在本机真实 VRChat 窗口上跑通未被遮挡的实拍帧（2160×1440、逐帧不同、人工目视核对过画面内容）。**T10（日志、费用与预算）代码已交付、789 项测试全绿，但仍是 IN_PROGRESS**：待 ADR-009 独立复核与一份真实实机报告的折算核对（TASKS 第 23 节）。
4. 当前推进 **T08（地标导航、结果验证与恢复）**：受监管 live 装配已实现，下一步是新增路径独立复核及现场 20 次验收（TASKS 第 22 节）；现场 20 次任务一旦产生报告，用 `navigation-report --report-dir <目录>` 就能看到 `usage` 与每小时折算（这正是 T10 第四条完成条件所需的真机证据）。`perceive` 本身只提供感知，不代表已实现真人身份、精确位置或世界语义；T07 的假合成/假识别也不代表真实语音引擎。T04/T05 遗留的小项：`replay`/`perceive --fixture` 指向不存在的文件时退出码是 1 而不是输入错误的 2。控制进程启动错误已增加阶段与已知原因；CLI 的 GBK/ASCII JSON 输出回退已修复。

GraphFlow MCP（`graphflow_context` / `graphflow_plan` / `graphflow_index`）在 T04/T05/T06 这几次会话中是**可用**的（此前 README 记录的“本会话没有可调用的 GraphFlow”指阶段 1＋2 与 T00 的会话）；这几次都没有用它生成计划（T05 的 `graphflow_context` 只返回低相关度的泛化锚点，T06 复核以源码、fixtures 和独立探针为准），图谱索引状态以图谱侧为准。T04 与 T05 收尾各执行过一次增量索引；T06 复核收尾也执行了一次增量索引（`indexedFiles=9`、`indexedSymbols=54`、`indexedReferences=0`，向量回填受 2s 预算限制刷新 256 个、`missing=19286`）——只宣称这次增量已执行，不宣称图谱已完全同步。
