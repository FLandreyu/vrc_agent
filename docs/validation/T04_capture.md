# T04 采集与回放：验证记录（2026-10-13）

本记录覆盖 T04「窗口采集、Observation 与回放」的实现证据。任务定义与完成条件见 [TASKS.md 第 3 节](../TASKS.md)（T04 一节），接口语义见 [ARCHITECTURE.md 第 4 节](../ARCHITECTURE.md)。

结论摘要：三条离线验收条件（失败不复用旧帧、回放可稳定复现、默认不落盘）均已满足并有可复现证据；采集在本机真实窗口上跑通（含被遮挡与未被遮挡两种情形）。仍**未**验证：VRChat 窗口未被遮挡时的实拍帧——需要人工把 VRChat 切到前台再跑一次，因此 T04 保持 IN_PROGRESS。

## 1. 环境

- Windows 10 桌面会话，交互式窗口站 `WinSta0` / 桌面 `Default`。
- 项目 `.venv`，Python 3.14.6（64 位）；采集实现只用标准库 `ctypes`，**未新增任何第三方依赖**（依赖仍是 pydantic + python-osc）。
- 显示：物理分辨率 2160×1440，系统缩放 150%（窗口 DPI 144）。非 DPI 感知进程看到的是虚拟化的 1440×960。
- 目标客户端：VRChat 正在运行（窗口类 `UnityWndClass`，标题 `VRChat`，全屏大小 2160×1440）。实测时它被编辑器窗口覆盖，因此遮挡路径可以在真机上被触发。

## 2. 只读探针与实测结论

探针脚本位于 `.runtime/`（开发用，不入库、不改产品代码、不发送任何输入、只在显式请求时把单帧写到临时目录供人眼核对）。四条结论决定了实现方式：

| # | 探针 | 观察 | 结论 |
| - | ---- | ---- | ---- |
| 1 | `.runtime/t04_capture_probe.py` | 窗口站/桌面为真实交互桌面；`EnumWindows` 可见窗口 17 个（含 VRChat）；桌面 DC 抓 1440×960 全非黑；`PrintWindow(PW_RENDERFULLCONTENT)` 对 VRChat 返回 `err=5 ERROR_ACCESS_DENIED` | 可以用 GDI 抓屏；窗口不被遮挡是前提；不能靠 `PrintWindow` 抓被覆盖的窗口 |
| 2 | `.runtime/t04_capture_probe2.py` | 从 VRChat 窗口 DC 连抓 3 帧，sha256 完全相同，均非黑、均值 RGB≈(40,45,52)；把该帧写成 PNG 后**人眼核对发现画面是遮挡它的编辑器窗口，不是 VRChat** | `BitBlt` 读的是**屏幕像素**，被遮挡时会把遮挡窗口的内容当成目标窗口 ⇒ 遮挡检测是必需项 |
| 3 | `.runtime/t04_dpi_probe.py` | DPI 不感知时：屏幕 1440×960、VRChat 矩形 (0,0,1440,960)、监视器 DPI (96,96)；调用 `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` 成功后：屏幕 2160×1440、VRChat 矩形 (0,0,2160,1440)、DPI (144,144) | 采集进程必须 per-monitor-v2 DPI 感知，否则拿到的是被系统缩放的错误分辨率画面 |
| 4 | `.runtime/t04_grab_probe.py` | 同一时刻对比两条抓取路径。未被遮挡的编辑器窗口（2160×1368）：窗口 DC 路径 `BitBlt`/`GetDIBits` 全部「成功」但缓冲全零（`max_rgb=(0,0,0)`，非零采样 0/11856）；屏幕 DC 路径正常（`max_rgb=(255,255,255)`，非零采样 11856/11856）。VRChat：两条路径结果相同（都是遮挡窗口的像素），其遮挡采样 0/25 命中、非前台 | 对 DWM/DirectComposition 窗口，窗口 DC 会静默返回全零画面；实现统一改从屏幕 DC 抓取，并把「已确认未遮挡」作为硬前提 |

另有两个实现期缺陷由真机运行暴露、已修复并复测：

1. **DPI 感知报告恒为 `unknown`**：模块级缓存变量与访问器函数同名（`def _user32()` 覆盖了 `_user32 = None` 哨兵），访问器返回的是函数对象本身。修复后实测 `awareness: unaware → ensure: per_monitor_v2 → awareness: per_monitor_v2`，`GetSystemMetrics(0) = 2160`。
2. **未被遮挡的窗口被判为全黑帧**：根因即上表第 4 条。改为屏幕 DC 抓取后同一窗口正常出帧。

## 3. 实现与接口

- 新增 `src/vrc_agent/capture/`：`backend.py`（采集后端协议、`WindowInfo`、`OcclusionReport`）、`win32.py`（Win32/GDI 后端、DPI、窗口枚举、遮挡采样）、`source.py`（live/replay 共用的状态机）、`frames.py`（内存帧表、黑帧判定）、`synthetic.py`（确定性合成帧与内置素材）、`replay.py`（素材解析与回放源）、`live.py`（工厂）、`errors.py`（失败枚举与映射）。
- 失败映射（失败步 `frame_ref`/`frame_size` 恒为 null）：`WINDOW_NOT_FOUND`/`WINDOW_HIDDEN` → `lost`；`WINDOW_MINIMIZED` → `minimized`；`WINDOW_OCCLUDED`/`WINDOW_EMPTY_SIZE`/`WINDOW_TOO_LARGE`/`FRAME_BLACK`/`CAPTURE_FAILED`/`DPI_UNAWARE` → `unknown`。除 `visible` 外都会让已启用的控制器暂停。
- `captured_at_ns` 在抓取**之前**读取单调时钟：宁可让观测显得更旧（更保守），也不用抓取耗时把旧证据算成新的。
- `frame_ref` 受 `contracts.Identifier` 正则（`^[A-Za-z0-9_.:-]+$`）约束，不含路径分隔符，结构上不可能是文件路径；live 形如 `frame-<32 位十六进制>`，回放形如 `replay.<素材名>.<序号>`。
- `[capture]` 配置段与 `CaptureConfig`；`window_title` 省略时继承 `control.window_title`。
- 边界行为：`CaptureError` 是后端约定的唯一失败通道；后端抛出其他异常时状态机不吞——异常原样传播，不产生观测、不计入失败、帧表不增长（有回归测试固定该语义）。理由与后续归属见 ARCHITECTURE 第 4 节「采集」。
- CLI：`vrc-agent capture`（只读冒烟，打印帧元数据、窗口枚举与统计，`sends_input: false`、`writes_media: false`）、`vrc-agent replay`（内置或 `--fixture` 素材）、`doctor` 增加 DPI 感知等只读字段。

## 4. 验证命令与结果

以下命令均在仓库根目录、用项目 `.venv` 运行。

```text
python -m pytest -p no:cacheprovider --no-header --tb=no -q
  → 207 passed, 10 errors（退出码 1）

python -m pytest -p no:cacheprovider --no-header --tb=line -q --deselect tests/integration/test_process.py
  → 207 passed, 7 deselected, 3 errors

python -m ruff check src tests          → All checks passed!
python -m ruff format --check src tests → 39 files already formatted
```

- 10 个 error **全部**是沙箱限制：pytest 无法在 `C:\Windows\Temp\dsh-4hgbnb\pytest-of-sunyongjie` 下创建临时目录（`PermissionError: [WinError 5]`）。涉及 7 个 `tests/integration/test_process.py`、1 个 `tests/unit/test_contracts.py::test_config_defaults_and_secrets`、2 个 `tests/unit/test_telemetry.py`，都不触碰 `capture/`；在可写临时目录的环境里应当通过。本记录如实保留该差异，未把 error 当成通过。
- T04 之前基线是 51 passed + 同一批 10 errors；本任务净增 156 个通过的测试：`test_capture_frames.py` 24、`test_capture_synthetic.py` 26、`test_capture_source.py` 46、`test_capture_contract.py` 13、`test_replay_source.py` 40、`test_capture_privacy.py` 7。

## 5. 三条验收条件的证据

**① 采集失败不会复用旧截图冒充新帧。**
- 单元层面：`tests/unit/test_capture_source.py` 用假后端逐个触发八种失败，断言失败步 `window_state != "visible"`、`frame_ref is None`、`frame_size is None`，且成功帧的引用两两不同；`tests/contract/test_capture_contract.py` 用独立写死的期望序列（13 步）再核对一遍，并验证失败步 `frame_for()` 为 None。
- 真机层面（VRChat 当时被遮挡，默认配置）：

```text
python -m vrc_agent capture --frames 2 --interval-ms 200
  → 2/2 window_state="unknown"，frame_size=null，frame_ref=null
    stats.failures = {"WINDOW_OCCLUDED": 2}，遮挡采样 0/25 命中
    events: {"event":"capture","status":"failed","reason_code":"WINDOW_OCCLUDED","count":1,2}
    frames_held = 0
```

**② 回放可以稳定复现事件顺序。**
- `tests/replay/test_replay_source.py`：同一素材两次运行逐字段一致，时间戳差值等于素材步进，回放不等待真实时间（13 步在 1 秒内跑完），素材耗尽后抛 `ReplayExhausted` 且不循环填充。
- 跨进程一致：两次独立运行 `python -m vrc_agent replay` 得到同一可复现摘要

```text
sequence_digest = fadc5ee10ccf227abea0ff6cf8374073919e94aed94878c2a38f08de7786e22a
```

  从磁盘素材 `tests/replay/t04_smoke.json` 跑出来的结果与内置素材完全相同（13 条观测、5 帧、attempts=13、ok=5、八种失败各 1 次、分辨率变化 2 次）。该摘要值本身不是目标，它变化时必须有人说明原因。
- 真实窗口的采集也会给出**不同**的帧摘要（见下节两帧 sha256 前缀不同），说明采集不是返回缓存画面。
- 独立探针（脚本由另一位执行者编写，见第 8 节）用自写的假后端把「成功 2 帧 → 消失 → 最小化 → 隐藏 → 空尺寸 → 超限 → 遮挡 → GDI 失败 → 黑屏 → 短缓冲 → 两次不同尺寸成功 → 后端抛非 `CaptureError`」共 14 步走完，逐条断言：失败步 `frame_ref`/`frame_size` 均为 null、`frame_for(失败观测) is None`、`frames_held` 不因失败增长、成功帧内容与引用两两不同。结果 `violations: []`（无违例）。同一脚本还确认：DPI 不感知时不调用 `grab_bgra` 且不产出 `visible` 观测；`retain_frames=false` 时成功观测仍如实报告尺寸但没有帧引用（`frames_held=0`）；旧观测仍能按其 `frame_ref` 取回旧帧（引用是唯一凭据）。

**③ 默认不将原始画面落盘。**
- `tests/unit/test_capture_privacy.py` 静态扫描 `capture/` 源码里的模块导入、文件写入调用与函数名，并拦截 `open`/`Path.open`/`Path.write_*`/`os.open` 后跑完整回放；帧只进内存帧表，`frame_ref` 不能当文件打开；遥测只在失败时记录事件、状态与原因码，不带帧内容与长度。
- 真机运行 `capture` 后仓库与 `.runtime/` 下没有新增任何帧文件；`frame_ref` 形如 `frame-c661e8828b2a401e8d8e2ec17d841153`，不含路径分隔符。

## 6. 本机真实窗口采集证据

```text
# 未被遮挡的窗口（配置指向当时唯一未被覆盖的窗口，采集 2 帧）
python -m vrc_agent capture --config .runtime/verify_visible.toml --frames 2 --interval-ms 250
  → dpi_awareness: "per_monitor_v2"
    2/2 window_state="visible"，frame_size=[2160,1368]，遮挡采样 625/625 命中
    frame_ref: frame-c661e8828b2a401e8d8e2ec17d841153
               frame-c1efd367f56a40b3a0b664d2569217f6
    sha256[:16]: 00ecb3c989df3db8 / 65e2608c2820c2d3   ← 两帧不同，确实在实时采集
    frames_held=2，events=[]（成功不写日志）

# 默认配置打 VRChat（VS Code 覆盖在上面）：失败路径如实报告，不冒充成功
python -m vrc_agent capture --frames 1
  → 1/1 window_state="unknown"、WINDOW_OCCLUDED、遮挡采样 0/25、frames_held=0

# 人工把 VRChat 切到最前后，默认配置（control.window_title="VRChat"）：
python -m vrc_agent capture --frames 3 --interval-ms 250
  → 3/3 window_state="visible"，frame_size=[2160,1440]（真实物理分辨率，不是 DPI 虚拟化的 1440×960）
    遮挡采样 25/25 ratio=1.0，dpi_awareness="per_monitor_v2"，events=[]，frames_held=3
    frame_ref / sha256[:16]：
      frame-7d3e3f36fe984b51a56aea5d8bcf9c45 → 36852730c36738c9
      frame-c20f7c3b772f4f589b4bdb2fe6ca909c → 977215d85d1189c8
      frame-fbd7f65aa3984be7b6ea6f25f37a8432 → 2e9b86d1103c0f48   ← 三帧互不相同
    captured_at_ns: 43783242026100 / 43783722696100 / 43784195477900（实际采集时刻）
```

**画面内容的人眼核对。** 冒烟只能说明「拿到非黑、逐帧变化的像素」，不能说明这些像素就是 VRChat；第 2 节的探针恰恰是靠人眼核对才发现「窗口 DC 读到的是遮挡窗口的画面」这个静默错误，所以 live 验收同样补了一张图：

```text
.venv\Scripts\python.exe .runtime\t04_visual_check.py        # 开发探针，非产品路径
  → window_state: visible、frame_size: (2160, 1440)、occlusion: OcclusionReport(samples=25, matching=25)
    frame_ref: frame-7ad5f5458a1b4aa3b25702a7829169a9、sha256[:16]: 710c1c0ec7b488bf
    写出 .runtime/verify/t04_vrchat_visual.png（只写 .runtime/，由人工显式运行）
```

目视确认：画面就是 VRChat 本体——头像选择菜单（「1/60」缩略图页）、VRChat 世界与其中的角色、VRChat 自己的 UI（Esc / R / Tab / Y、Mirror Settings HI/LOW）。⇒ 采集读到的确实是 VRChat 的内容，DPI 感知下 2160×1440 全分辨率，未被遮挡。

`doctor` 会额外打印本进程的 DPI 感知级别、采集目标窗口标题与是否要求未遮挡，均为只读字段。

## 7. 未验证项与已知限制

- **VRChat 未被遮挡时的实拍帧**：已在第 6 节补齐（3 帧 2160×1440 全分辨率 + 一张人工目视核对的 PNG）。该 PNG 由开发探针写出，产品路径仍然不落盘。
- 被遮挡时按设计直接判失败，因此采集要求目标窗口可见且没有被其他窗口覆盖；这不是可以自动绕过的限制（`PrintWindow` 在本机 VRChat 窗口不可用）。VRChat 全屏运行时若被其他窗口覆盖，采集会失败而不是给出被覆盖的画面——这是刻意的取舍。
- 小问题（不影响正确性，尚未处理）：`replay --fixture` 指向不存在的文件时，CLI 走 `OSError` 分支打印 `FileNotFoundError: [Errno 2] ...` 并以退出码 1 结束。输入错误按现有约定应归为退出码 2（`ValidationError`/`ValueError` 分支），措辞也可以更直白；这条由独立复核的 Q2 探针发现，记在这里备改。
- 遮挡判定是在客户区上做 `samples × samples` 网格采样（默认 25 点），不是逐像素证明；极小面积的遮挡可能漏检。采样密度可配置。
- 本机结论只对这一套桌面环境（单显示器、150% 缩放、桌面模式）成立；换机器需重跑第 2 节探针。
- 音频采集属 T07；世界加载/场景切换识别未实现（T06/T08），不能从截图推断世界 ID。
- 采集能力尚未写入 T00 的能力矩阵（该矩阵由 T00 会话维护），因此“窗口采集”在 `CAPABILITIES.md` 里仍是 UNTESTED；本记录只对该矩阵有补充作用，不替代它。
- 显示层面的小问题（不影响正确性）：`capture` 会打印窗口枚举结果，其中非 ASCII 的窗口标题在 936 代码页的终端里可能显示为乱码，字符串本身是正确的（匹配、比较都在 Python 内部完成）。若后续需要给人看，可改为对非 ASCII 转义输出。

## 8. 独立交叉验证（ADR-009）

实现与独立复核由不同执行者完成。复核者的结论、命令与仍未验证项记录如下（未完成的复核不得写成已通过）。

**Q1「采集失败不会复用旧帧」——独立脚本，违例为零，但运行者不是独立执行者。**
- `.runtime/verify/v_q1_stale_frame.py`（253 行）由本次会话的独立复核子代理编写：它自写假后端（不引用 `tests/` 下任何桩代码），把 14 步序列走完，并按自己的判据逐条断言（失败步不得带 `frame_ref`/`frame_size`、`frame_for(失败观测)` 必须为 None、失败步不得让帧表增长、成功帧的引用与内容不得重复），另加 DPI 不感知、`retain_frames=false`、旧观测取帧三项附加检查。
- 该子代理在产出结论前异常结束（无收尾消息），其运行输出为空（同目录遗留的 `q1_out.json` 是 0 字节文件，不作为证据）；因此脚本由实现者在本机重跑，命令与结果如下：

```text
.venv\Scripts\python.exe .runtime\verify\v_q1_stale_frame.py
  → "violations": []，14 步全部符合上述判据
  → dpi_grabs: grab_calls=0（DPI 不感知时不抓帧，也不产出 visible）
  → retain_off: window_state="visible"、frame_ref=null、frames_held=0
  → old_obs_still_resolves: 旧观测按其 frame_ref 仍取回同一帧
  → exception_step: 后端抛 OSError 时异常逃逸、attempts+1、帧表与失败计数不变
```
- **独立性折扣**：脚本与判据来自另一位执行者，但运行与判读由实现者完成，所以它只能算「独立编写、非独立运行」的复核。最后一条（`OSError` 逃逸）已由实现者定性为刻意的边界行为，并补了回归测试与文档（第 3 节、ARCHITECTURE 第 4 节）。

**Q2「回放可稳定复现」——独立编写、独立运行，但复核者的结论文件缺失（判读由实现者完成）。**
- 探针与运行都出自第二位独立复核子代理（脚本与原始输出全部留在 `.runtime/verify/`，前缀 `v_q2_`）。它在产出结论之前异常结束（无收尾消息，也没有 `v_q2_report.md`），因此判读由实现者按其**已有的原始输出**完成；下面每个数字都取自它的产物，没有为了「凑结论」重新跑一遍，也没有改写它的脚本。
- 跨进程一致性：10 份完整 CLI 输出**逐字节相同**——`v_q2_out_smoke.json` 与 `v_q2_out_replay_{a,b,cwd,hash0,hash999,temp,fixture,fixture_abs,fixture_hash}.json`，均 4025 字节、rc=0、stderr 空、SHA256 同为 `2c0ed538d85a7536`。覆盖的扰动：两次独立进程、不同工作目录、`PYTHONHASHSEED=0/999`、不同 `TEMP`、`--fixture` 的相对路径 / 绝对路径 / 另一份同内容文件。
- 与存档素材、与独立模型一致：`built-in == tests/replay/t04_smoke.json`（Python 对象与规范化 JSON 均相等）；13 步逐条与复核者自建的独立期望模型比对 `all steps match independent model: True`；渲染规格里 `.5` 这类舍入敏感分量为 0；序列 digest `fadc5ee10ccf227abea0ff6cf8374073919e94aed94878c2a38f08de7786e22a` 与实现者的黄金值一致；`stats: attempts 13, ok 5, frames_held 5, evicted 0, resolution_changes 2, last_occlusion {samples 25, matching 25, ratio 1.0}, last_captured_at_ns 2800000000`，八种失败各 1 次，`observation_ids in order: True`；13 步的 `at_ms` 序列 0/150/…/1800 与 `window_state`（三 visible → unknown → minimized → lost → visible → unknown → unknown → lost → unknown → unknown → visible）逐步吻合。
- 破坏性变异（进程内，素材由复核者改写）：耗尽后再取三次都是 `ReplayExhausted(fixture 't04_smoke' is exhausted after 13 steps)`、`remaining=0 extra_collect=0`（**不循环、不填充**）；`dup-step` 14 步、digest `8f793bf924ecd075`、确实出现重复时间戳；`key-reordered`（同一素材改键序）digest 与 baseline 相同；`all-gone` 4 步全 lost、digest `19df9ec8cab05e24`、`frames_held 0`；`20-successes` `frames_held 8`、`evicted 12`（帧表 LRU 淘汰生效）；`dup` / `gone` / `many` 三个变异素材两次运行的输出文件哈希各自相同（`6E1934EE374B4782` / `A566EBCDB7236194` / `5A81705C459FC8F4`）。
- 被拒绝的素材（两次运行都失败、输出 0 字节、stderr 相同）：0 步的 `zero` 与 `at_ms` 倒退的 `backwards`，都是 `Invalid configuration or command. Check types, bounds and controller state.`（退出码 2 路径）。
- **独立性折扣**：脚本编写与运行都在独立执行者手里，比 Q1 更硬；但它的结论文件没产出，判读由实现者完成，所以只能算「独立编写 + 独立运行 + 实现者判读」。
- 复核顺带发现的问题：`--fixture` 指向不存在的文件时退出码是 1（`OSError` 分支）而不是输入错误应有的 2，措辞也偏原始；已记入第 7 节待改项。

**Q3「默认不落盘」——第三位复核子代理完成，判定「未能证伪」。**
- 报告：`.runtime/verify/v_q3_report.md`；探针：`.runtime/verify/v_q3_probe.py`（写模式即 `AssertionError` 的哨兵 + 自写假后端，未导入 `tests/` 下任何桩）、`.runtime/verify/v_q3_cli_probe.py`、对照实验 `.runtime/verify/v_q3_control.py`；原始 stdout 存 `.runtime/verify/v_q3_cli_*_stdout.txt`。
- 静态：`capture/` 全包 grep `open(` / `write_text` / `write_bytes` / `os.open` / `os.mkdir` / `shutil` / `tempfile` / `subprocess` / `PIL` / `cv2` / `numpy` / `mss` / `imageio` → `No matches found`；包里唯一的 I/O 是 `load_fixture` 的只读 `Path.read_text`。
- 动态：把 `builtins.open` / `io.open` / `Path.open` / `Path.write_text` / `Path.write_bytes` / `os.open` / `tempfile.*` 换成写模式即抛错的哨兵后，跑采集状态机（8 步：2 次成功 + `HIDDEN`/`MINIMIZED`/`NOT_FOUND`/`EMPTY_SIZE`/`OCCLUDED`/`CAPTURE_FAILED` 各 1）与回放源（13 观测 / 5 帧 / 耗尽抛 `ReplayExhausted`）：`sentinel_violations: []`、`error: null`，内存帧 61440 B 且 `frame.data` 是 `bytes`，`sys.modules` 里没有图像库；所有 `frame_ref`（`q3.0001`、`replay.t04_smoke.0001`–`0005`）都不含 `/ \ :`，逐个 `open(ref, "rb")` 都得到 `FileNotFoundError`；跑前跑后的全目录快照 90 → 90，新增/丢失/修改为空集。
- 真实 CLI（无 patch）：`python -m vrc_agent replay` rc=0，前后目录 diff 全空；`python -m vrc_agent capture --frames 1` rc=0，如实返回失败态（`window_state="unknown"`、`frame_ref=null`、`WINDOW_OCCLUDED`、`writes_media=false`）。
- 反例排查：capture 后 diff 里有 4 个变动文件（`.graphflow-cache/index-state.json`、`.graphflow/observation-pressure.json`、`docs/validation/T04_capture.md`、`graphflow-out/*.delta.jsonl`）。它用对照实验（不跑任何 `vrc_agent` 命令、只等 25 秒）证明这 4 个文件同样在变，mtime 都在 22 秒内 ⇒ 后台噪声（GraphFlow 缓存与父会话并发写，其中验证记录文件正是实现者在同一时间编辑的），**不是采集路径所为**。这个排除法值得保留：在没有对照的情况下，这类噪声很容易被误判成写盘违规。
- **残留缺口（复核者自己标注）**：真实窗口「采集成功」分支这次没能在哨兵下跑（VRChat 当时被遮挡），所以该组合只有静态覆盖；另外哨兵只拦 Python 层的写 API，理论上 `ctypes` 可绕过（三路证据互证）。实现者补充：成功分支的写盘风险由本实现的隐私测试（AST 扫描 + 运行期哨兵，含成功路径）与 Q3 的假后端成功路径覆盖，`win32.grab_bgra` 只把 `ctypes` 缓冲区转成 `bytes`。
- **独立性**：脚本、运行、判读全部出自独立执行者，且留下了报告与反例清单——三条复核里独立性最完整的一条。
