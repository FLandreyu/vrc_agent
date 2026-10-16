# T07 交流：chatbox 队列、半双工与虚拟麦克风路由 — 验证记录

- 任务：T07（定义见 `docs/TASKS.md` 第 3 节，交接见第 17 节）
- 负责人 / 会话：DSH / Flash（本会话）
- 日期：2026-10-13
- 版本：0.1.0，**无 Git 提交**（`D:\vrc_agent` 不是 git 仓库，没有提交哈希可锚定被测版本）
- 结论：实现、离线与真机自测完成；**已由另一执行者独立交叉复核（第一轮六项未能证伪；F1/F2 按复核意见修复后又做了一轮复检确认）**，详见第 9 节。复核者明确列出 8 项不在其证据覆盖内的残留缺口（真实 ASR/TTS 引擎、Voicemeeter 实际路由、`--live-audio` 全路径、与 live 控制进程联动、真实语音打断、并发与长时间稳定性等）。

## 1. 环境与前置

- Windows、Python 3.14.6（`.venv\Scripts\python.exe`）、单显示器 2160×1440 @150%。
- 新增依赖（已写入 `requirements.lock`）：`sounddevice==0.5.6`、`cffi==2.1.1`、`pycparser==3.11`，PortAudio V19.7.0-devel。只使用原始 float32 缓冲（`RawStream`），**不依赖 numpy**。
- 本机音频设备（`.runtime/verify/audio_devices_probe.py` 只读枚举，88 台）：默认输入 index 1「本机麦克风（英特尔® 智音技术）」、默认输出 index 11「本机扬声器 (Audio Device)」；VB-Audio Voicemeeter 已装且有输出型 `Voicemeeter Input`（WASAPI 42 / MME 14）、`Voicemeeter AUX Input`（43/15）、`Voicemeeter In 1–5`、`VAIO3 Input`，输入型 `Voicemeeter Out A1–A5/B1–B3`（WASAPI 49–57、MME 2–9）。
- 设备用**名字子串**选择（索引会随驱动插拔变化）；后端按宿主 API 偏好排序并保持该顺序（WASAPI 优先于低索引 MME）。
- 本会话沙箱策略为 `danger-full-access`：`pip install` 与 `>` 重定向可用（受限沙箱下 `>` 会得到 0 字节文件、`pip download` 因 mkdtemp 目录写不进而失败）。PowerShell 5.1 的 `>` 输出是 UTF-16LE，读取脚本需按 BOM 识别编码（`.runtime/verify/t07_summary.py` 已做）。

## 2. 交付物

| 文件 | 职责 |
| --- | --- |
| `src/vrc_agent/dialogue/queue.py` | `ChatQueue`：有界（满员淘汰最旧）、节流、过期即弃、暂停清空 |
| `src/vrc_agent/dialogue/halfduplex.py` | `HalfDuplex`：IDLE/LISTENING/SPEAKING/PAUSED/STOPPED 迁移表与监听门控 |
| `src/vrc_agent/dialogue/dsp.py` | 只用 `array("f")`+`math`：正弦、RMS、峰值、Goertzel 单频占比、采样摘要 |
| `src/vrc_agent/dialogue/devices.py` | `AudioDevice`、按名字的选择与过滤、`virtual_mic_hint()` |
| `src/vrc_agent/dialogue/backend.py` | `AudioBackend` 协议、离线 `FakeBackend`、真实 `SoundDeviceBackend` |
| `src/vrc_agent/dialogue/tts.py` | `Tts` 协议 + 确定性 `FakeTts`（真实引擎属用户步骤） |
| `src/vrc_agent/dialogue/asr.py` | `Asr` 协议、`EnergyVad`/`QuietAsr`、脚本化 `FakeAsr` |
| `src/vrc_agent/dialogue/echo.py` | `run_echo_check()` 自声回流诊断（播放探针音同时录音） |
| `src/vrc_agent/dialogue/session.py` | `DialogueSession`：队列→合成→播放→监听 + `TurnRecord`/`DialogueReport` |
| `src/vrc_agent/dialogue/chatbox.py` | `ControllerChatSender`（经真实控制内核）、`SteppedControllerClock` |
| `src/vrc_agent/config.py` | 新增 `DialogueConfig` 与 `AppConfig.dialogue` |
| `src/vrc_agent/cli.py` | `dialogue` 子命令、`dialogue_run()`、`describe_dialogue()`（doctor） |
| `configs/default.toml` | `[dialogue]` 段（默认 `enabled=false`）与真机命令示例 |
| 测试 | `tests/unit/test_dialogue_{queue,halfduplex,devices_dsp,backend_echo,session}.py`、`tests/unit/test_t07_privacy.py`、`tests/integration/test_dialogue_cli.py` |

## 3. 四条完成条件与证据

### ① 20 轮一对一交流有时间记录

- 离线：`.venv\Scripts\python.exe -m vrc_agent dialogue --turns 20` → `turn_count=20`、**40 条记录**（每轮一次 `listen` + 一次 `playback`）、全部 `COMPLETED/OK`、`total_ms=15240` 且等于各记录 `duration_ms` 之和；监听 300 ms/轮、合成语音 435–480 ms/轮（虚拟时钟，工件 `.runtime/t07_turns.json`）。
- 真机：`--live-audio --scenario turns --turns 2` → 4 条记录全 `OK`，listen 343/333 ms、playback 422/427 ms（墙钟），`audio.played_ms=870`、`audio.recorded_ms=600`（工件 `.runtime/t07_live_turns.json`）。
- 每条记录含 `started_at_ns`/`ended_at_ns`/`duration_ms`/`action`/`status`/`reason_code`/`text_chars`/`played_ms`/`requested_ms`，**不含文本**。

### ② 播放能被本机停止立即取消

- 口径：播放逐块进行（默认 20 ms），取消回调在**一块之内**生效；真实后端取消时 `abort()` 放弃缓冲，不等待排空。`--live-audio` 下取消回调接到与本机 `stop` 命令同一个 `StopSignal`。
- 离线：`dialogue --scenario cancel` → 请求 2685 ms 的语音在 100 ms 处被取消，记录 `played_ms=100`、`requested_ms>1000`、`status=CANCELLED`（集成测试同断言）。
- **真机**（工件 `.runtime/t07_live_cancel.json`）：输出 `Voicemeeter Input` index 42（WASAPI）、输入 `Voicemeeter Out B1` index 52；请求 2685 ms，实际只播 **60 ms（3 个 20 ms 块）**，记录时长 129 ms，`CANCELLED/MANUAL_STOP`，半双工转入 `STOPPED`。

### ③ 暂停期间不积压旧发言

- 离线：`dialogue --scenario pause` → `pending_before_pause=5`、`dropped_on_pause=5`、`pending_after_pause=0`，恢复后 `chat.sent=1`（只发出恢复之后那一条）；另外让时间越过生存期后 `expired_dropped=1`，证明过期内容到点即弃、不补播。
- 单元：暂停中 `enqueue` 抛 `PAUSED` 并计 `rejected_paused`；恢复后旧内容一条都没有重放（`playback_log=[]`）。

### ④ 默认不保存真人语音

- 静态：`tests/unit/test_t07_privacy.py` 扫 `dialogue/` 全包——没有 `open()`、没有写盘属性、禁 `wave`/`soundfile`/`pydub` 等会写音频的库、没有 `print()`；除 `chatbox.py`（职责就是把文本交给控制层）外不得导入 `control`/`transport`；`import sounddevice` 只出现在 `backend.py`。
- 运行期：把 `builtins.open`/`Path.open`/`read_text`/`write_text`/`write_bytes`/`os.open`/`socket.getaddrinfo`/`create_connection`/`urllib.request.urlopen` 全部换成会抛异常的哨兵，完整 20 轮对话照常跑完（40 条记录）。
- 数据面：采集缓冲在转写后立刻 `discard()`（有断言）；报告与遥测只有字符数、时长、能量、状态与原因码，**没有文本、没有音频**；遥测字段 ⊆ `ALLOWED_FIELDS`。
- **没有实现任何调试媒体落盘开关**：本任务里不存在「打开就把语音写文件」的路径。

## 4. 测试与门禁

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q` | **579 passed in 10.96s**（T07 新增 75 项：单元 59 + 隐私 6 + CLI 集成 10） |
| `python -m ruff check src tests` | All checks passed! |
| `python -m ruff format --check src tests` | 83 files already formatted |
| `python -m pip check` | No broken requirements found. |

三个数是累加的：T07 交付时 72 项（576 passed），按独立复核的 F1/F2 意见修完后再加 3 项（`test_audio_totals_are_summed_from_the_records`、`test_items_expose_no_clock_dependent_fields`、`test_a_long_chatbox_replay_eventually_crosses_the_lease_and_is_refused`）→ 75 项 / 579 passed。

## 5. 真机证据（`--live-audio`，会真的打开设备）

| 场景 | 命令要点 | 结果 |
| --- | --- | --- |
| 取消 | `dialogue --live-audio --scenario cancel --output-device "Voicemeeter Input" --input-device "Voicemeeter Out B1"` | 2685 ms 请求 → 实播 60 ms；`CANCELLED`；半双工 `STOPPED` |
| 回流诊断 | 同上 `--scenario echo` | 播放 300 ms、录音 340 ms、`rms=0.0`、`tone_ratio=0.0` → **`no_signal`** |
| 双轮对话 | 同上 `--scenario turns --turns 2` | 4 条记录全 `OK`；listen 343/333 ms、playback 422/427 ms |

回流诊断为 `no_signal` 是**如实结果**：Voicemeeter 当前没有把 `Input` 回流到 `Out B1`，所以录音里确实没有那段探针音。诊断不会在无信号时编造「检测到回流」。

## 6. 用户可执行步骤（代码不代做）

1. **Voicemeeter 路由**：把 `Voicemeeter Input`（我们的播放目标）→ `B1`（监听总线）打开；在 VRChat 里把麦克风选为 `Voicemeeter Output`（或对应虚拟输出）。完成后重跑 `dialogue --live-audio --scenario echo` 应得到 `loopback_detected`。
2. **真实语音引擎**（可选）：安装 Piper / Windows SAPI / Whisper 等，并按 `[dialogue]` 里的 `tts`/`asr` 适配器接入；当前版本只提供协议与确定性假实现，不自动下载模型。
3. **默认设备确认**：`dialogue --devices` 只读列出设备；`[dialogue].output_device`/`input_device` 用名字子串指定，建议 `"Voicemeeter Input"` 与 `"Voicemeeter Out B1"`。

## 7. 未验证项与已知限制

- 未接入真实 ASR/TTS 引擎：现有实现只验证「队列 → 合成 → 播放 → 取消 → 监听 → 转写」这条**链路**，不代表有语音识别或自然语音合成能力。
- 未验证**用户语音打断**：首版半双工在 AI 播放期间门控麦克风，只能靠本机急停/停止打断。
- 未与 live 控制进程一起跑：`dialogue` 的 chatbox 通路只在离线用真实控制内核 + 记录型传输验证（`RATE_LIMITED`、`NOT_ARMED` 都可见），未在真实 OSC 端口上发过 chatbox。
- 回流诊断依赖路由配置；当前实测只能给出 `no_signal`。
- 音频只按「一块 20 ms」计时，不承诺采样级精确；`played_ms` 统计的是**完整写入的块**。
- `dialogue` 不实现 VRAI 世界内的语音距离/音量建模，也不做回声消除。

## 8. 复现命令

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m vrc_agent dialogue --turns 20                 # 20 轮计时（离线）
.venv\Scripts\python.exe -m vrc_agent dialogue --scenario cancel          # 100ms 取消
.venv\Scripts\python.exe -m vrc_agent dialogue --scenario pause           # 暂停不积压
.venv\Scripts\python.exe -m vrc_agent dialogue --scenario echo            # 模拟回流诊断
.venv\Scripts\python.exe -m vrc_agent dialogue --turns 2 --with-chatbox  # 经真实控制内核发 chatbox
.venv\Scripts\python.exe -m vrc_agent dialogue --devices                  # 只读设备枚举
.venv\Scripts\python.exe -m vrc_agent doctor                             # dialogue 只读块
# 真机（会打开设备并出声；建议先确认路由）
.venv\Scripts\python.exe -m vrc_agent dialogue --live-audio --scenario echo --output-device "Voicemeeter Input" --input-device "Voicemeeter Out B1"
```

## 9. 独立交叉复核（ADR-009）

实现与离线/真机自测均由本会话完成；按项目约定，另由子代理 `t07-verifier` 只读复核，报告 `.runtime/verify/v_t07_report.md`。

**第一轮（复核者自写探针 `.runtime/verify/probe_t07.py`，35 项 CHECK、`FAILS: none`）**：六项检查（20 轮计时、立即取消、暂停不积压、不保存真人语音、队列语义、半双工门控）逐条判定**未能证伪**；门禁数字当时为 `576 passed in 10.75s`、ruff 两项与 `pip check` 均退出码 0；`dialogue --devices` 只读枚举 88 台设备、`opens_stream=false`。它同时提出四项瑕疵，处置如下：

| 编号 | 复核者发现 | 处置 |
| --- | --- | --- |
| F1 | 离线报告 `audio.played_ms`/`recorded_ms` 恒为 0（假后端没有计数器），而逐条合计是 9600 ms —— 同一份报告自相矛盾 | **已修**：`DialogueSession.report()` 改为按记录汇总（`played_ms`/`requested_ms`/`listen_ms` + 记录条数），不再读后端计数器；新增 `test_audio_totals_are_summed_from_the_records` 钉住 |
| F2 | `ChatItem.age_ms` 恒返回 0，是死属性 | **已修**：删除该属性；新增 `test_items_expose_no_clock_dependent_fields` 钉住「队列项不带依赖当前时间的字段」 |
| F3 | `--with-chatbox` 第 20 条 chatbox 被拒 `REJECTED:EXPIRED_LEASE`（约 38.8 s 虚拟时间后），来源未定位 | **有意保留并记录**：离线 20 轮每轮约 2.3 s 虚拟时间，会越过 `ControlConfig.lease_ms`（默认 30000 ms）；**租约到期后每一条都会被拒**（第二轮实测 20 条里 15 条 `OK`、5 条 `REJECTED:EXPIRED_LEASE`，不是只拒最后一条）。控制层到期即拒是设计行为（真实会话到期需要人工重新 ARM/F9），队列统计 `dropped_expired=0`/`dropped_full=0` 证明没有静默丢。新增 CLI note 说明租约长度，并加测试 `test_a_long_chatbox_replay_eventually_crosses_the_lease_and_is_refused` 固定该行为 |
| F4 | 实时探针里定时器先于 TTS 合成启动，导致 `played_ms` 只有 40 ms | **非缺陷**（探针自身的计时顺序）；块内取消的量化结论由假时钟探针给出（`played_ms=100`，`block_ms=1` 对照同样 100） |

**第二轮（修复后复检，`t07-verifier` 在 F1/F2 修完、F3 加注并固定后的当前工作树上复检）**：

- **F1 确认修复**：20 轮探针得 `audio = {"played_ms": 9195, "requested_ms": 9195, "listen_ms": 6000, "playback_records": 20, "listen_records": 20}`，与逐条之和逐项相等且 > 0；`recorded_ms` 已不存在，后端计数器不再被读；交付 CLI 的 `--with-chatbox` 输出同为 9195。
- **F2 确认修复**：`ChatItem.model_fields = ['item_id','text','source','created_at_ns','expires_at_ns']`，全文无 `age_ms`。
- **F3 确认为设计行为，并更正第一轮表述**：新测试确实断言「越界 + 无静默丢弃」（`any(...REJECTED:EXPIRED_LEASE)`、`dropped_expired == 0`、`dropped_full == 0`，`1 passed`）；CLI 复跑为 `chatbox records: 20 | reasons: {"OK": 15, "REJECTED:EXPIRED_LEASE": 5}`，`sent=20 / pending=0`。第一轮说「第 20 条被拒」是低估，实际是租约到期后每一条都被拒——这更支持「到期即拒」的结论。
- **门禁（复检逐字）**：`579 passed in 10.72s`（exit 0）／`All checks passed!`（exit 0）／`83 files already formatted`（exit 0）／`No broken requirements found.`（exit 0）；`dialogue --devices` exit 0、`device_count=88`、`opens_stream=false`、`playback_target_found=true`。复检未运行 `--live-audio`（按约定禁止）。
- **残留缺口不变**（真实 ASR/TTS 引擎、Voicemeeter 实际路由、`--live-audio` 全路径、与 live 控制进程联动、真实语音打断与噪声门控、`--scenario echo` 由复核者跑、`_records` 无锁并发压测、长时间/内存稳定性）。**未能证伪 ≠ 证明正确**：上述缺口不在本轮证据覆盖内。
