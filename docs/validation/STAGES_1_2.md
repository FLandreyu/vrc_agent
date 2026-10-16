# 阶段 1＋2 验证记录

日期：2026-10-12（Asia/Shanghai） · 项目版本：0.1.0 · 执行者：Codex / root

## 交付范围

- T01：Python 工程、严格配置与数据契约、命令行、fake clock、fake provider、基础脱敏事件日志及锁定依赖。
- T02：OSC 地址/类型映射、dry-run transport、loopback UDP、白名单参数接收、端口冲突处理。
- T03：独立 spawn 控制进程、租约、心跳 watchdog、动作期限、去重、取消/抢占、显式人工停止、单实例保护。
- Windows live 入口包含 F8/F9/F10、焦点检查和默认禁用的 F7 人工探针；**本次没有在真实 VRChat 中启用或验证这些操作**。

没有实现 T04–T09 的画面/语音/导航/真实模型能力。T10 仅实现日志基础，费用、预算和日志自动保留尚未完成。

## 环境与安装

- Windows，实际解释器 `Python 3.14.6`；原规划的 3.12 本机未安装。3.12/3.13 兼容性尚未实测。
- 主环境 `.venv`，干净安装验证环境 `.runtime/install-check`，均位于项目内。
- 依赖由 `requirements.lock` 固定版本，含构建后端 setuptools；不包含制品哈希。
- 默认清华镜像未能提供构建依赖；官方 PyPI 元数据可访问但包下载站超时。最终采用华为云镜像安装，未更改全局 pip 配置。
- 未创建 Git 仓库或提交，代码版本用项目包版本 0.1.0 标识。

干净安装步骤实际执行成功（退出码 0）：

```powershell
.\.venv\Scripts\python.exe -m venv .runtime/install-check
.\.runtime\install-check\Scripts\python.exe -m pip install --index-url https://repo.huaweicloud.com/repository/pypi/simple --timeout 10 --retries 1 -r requirements.lock
.\.runtime\install-check\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
```

## 测试与结果

| 验证 | 实际结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q` | **55 passed in 5.78s**，退出码 0 |
| Ruff lint / format | All checks passed；22 files already formatted；退出码 0 |
| `.venv/Scripts/python.exe -m pip check` | No broken requirements found，退出码 0 |
| `vrc-agent.exe --help` | 正常显示 doctor/run/stop，退出码 0 |
| 主环境与干净环境的 doctor | 配置有效；明确报告客户端能力未验证，退出码 0 |
| 主环境与干净环境的 dry-run | 记录 3 个 float 轴输入＋3 个归零；COMPLETED / UNVERIFIED，退出码 0 |
| 干净环境 `pip check` | No broken requirements found，退出码 0 |
| 六份项目/交接文档 | UTF-8 编码检查通过，本地链接缺失数为 0 |

固定可重复检查命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m ruff format --check src tests
.\.venv\Scripts\python.exe -m pip check
```

覆盖的关键行为：

1. **契约**：额外字段拒绝、非法动作、伪造租约字段、字符串/布尔轴值、NaN/Infinity、越界时长、聊天 UTF-16 限制、Avatar 精确类型和 OSC 表示范围。
2. **控制**：短动作到期归零、完成不等于世界成功、重复请求不续时、动作不排队、旧 session/epoch/lease、过期观测和动作期限、无心跳、接管、失去窗口、部分发送失败时仍尝试释放全部轴、会话容量上限。
3. **OSC**：真实本机随机 UDP 测试端口，float/int/string/bool 类型标签，归零与按钮释放，受控参数白名单、类型不匹配拒绝、畸形包丢弃和端口独占。
4. **独立进程**：从另一调用路径发出命名 Event 急停；从独立 CLI 发出 stop；管道保持打开但无心跳时暂停；单实例冲突与退出后重启。
5. **主代理终止**：测试专用父进程被 kill，独立控制进程记录 `HEARTBEAT_LOST`、输入释放、动作取消与正常退出。测试断言父进程终止到停止事件小于 500 ms，本次通过；这是 dry-run 控制路径结果，不是 VRChat 的实际停下延迟。
6. **隐私**：日志字段白名单不写入测试密钥、转写或聊天正文；日志 I/O 位于后台线程。

本轮修复了两个测试/复核发现的问题：畸形 OSC 包需要捕获 python-osc 专用 ParseError；带 frame_size 的 Observation 经 JSON IPC 传输后需用 JSON 验证入口还原 tuple。对应回归均已通过。

## 未验证项与限制

- 未启动、登录或操控用户的 VRChat 客户端；所有 UDP 测试均使用本机随机测试端口。
- 实际游戏输入、停止效果、Avatar 参数以及桌面交互支持情况仍由 T00 验证。
- F7/F8/F9/F10 与前台窗口匹配的真实操作尚待实机验证；Windows 独立停止 Event 和跨进程 stop CLI 已在测试中验证。
- 无普通键鼠输入自动接管检测；无同窗口世界加载检测；Avatar 切换需重新验证配置并重启。
- 500 ms 心跳阈值不是硬实时保证；还需考虑循环与系统调度延迟。控制进程/系统故障及 UDP 丢包可能导致释放失败。
- `doctor` 不探测实际端口、热键或客户端，仅验证配置与程序环境。
- 工程默认不调用任何真实模型、不计费，也未实现完整视觉感知决策闭环。
- GraphFlow 未暴露于本会话可调用工具列表，未运行 context、plan 或 index，不能宣称图谱已刷新。

## 后续入口

先完成 T00 实机能力验证，或开发 T04 采集/回放。继续开发时保持控制边界：规划器只提出 ActionProposal，可信运行时封装 Request，唯一控制进程负责输出；不能为了演示让模型自行 ARM。
