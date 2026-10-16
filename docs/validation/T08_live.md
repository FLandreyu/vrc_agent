# T08 live 配置冻结与受监管装配

实现/自测：Codex / root；标定来源：2026-10-14 用户现场记录。**本轮实现与离线回归已交付，未执行真实导航或付费模型调用。T08 保留 IN_PROGRESS，新增 live 路径待独立复核和现场 20 次验收。** 第 20 节的独立复核只覆盖旧离线部分，不能移用到本轮代码。

## 冻结的第一世界

文件：`src/vrc_agent/skills/worlds/vrchat_home_live_v1.json` 及同名 `.sha256`。版本 `vrchat-home-live-v1`；世界 VRChat Home、Avatar「小太阳小狗」、客户端 2026.3.3p2、2160×1440、私密实例，具体 ID 在配置中。没有坐标查询或通用地图接口。

```text
suite_digest = dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335
```

摘要算法：对严格验证后的完整配置 `model_dump(mode="json")`，用 `json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))` 编成 UTF-8，再 SHA-256。加载时核对随包的摘要，拒绝漂移。配置含标定源第 1–5 节（即第 6 节之前全文）的 UTF-8 摘要、每个起点/到达参考 JPEG 的文件 SHA-256。本轮核对了全部引用，25 项引用对应 23 张不同图片；运行不读取或上传标定 JPEG，只读取当前窗口内存帧。

| 组  | 冻结任务 ID / 条件             | 第四次处理                                                               | 到达目标                             |
| --- | ------------------------------ | ------------------------------------------------------------------------ | ------------------------------------ |
| G1  | G1-1…G1-4，正面/左/右/部分遮挡 | 保留已拍石结构缝隙机位，不要求完全不可见                                 | 广告牌牌面及木框                     |
| G2  | G2-1…G2-4，正面/左/右/正面重复 | G2-4 重复 G2-1 的已标定位置/朝向/截图；明示 `front_repeat`，不是遮挡任务 | 实体浣熊，排除头像面板上的图片       |
| G3  | G3-1…G3-4，正面/左/右/正面重复 | G3-4 重复 G3-1；同样不伪造被挡截图                                       | 物理木质面板框，排除反射内容与便携镜 |
| G4  | G4-1…G4-4，正面/左/右/部分遮挡 | 保留矮墙机位                                                             | 金色花瓶及花枝，不用整个桌面代替     |
| G5  | G5-1…G5-4，正面/左/右/部分遮挡 | 保留图案只见一部分的机位                                                 | 下半画面内的青蓝/白线菱形图案        |

每项的完整出发位置、朝向、参考图与恢复限制直接写入配置；冻结的替代规则来自标定记录允许的“改用其他出发条件”，没有声称 G2/G3 补拍了遮挡机位。

尺寸均为全帧归一化比例，连续两次动作后的有效新观测都满足才到达，水平偏心均 ≤0.12，场景与目标置信度 ≥0.85：

| 组  | 最小宽度 | 最小高度 | 额外判据                                     |
| --- | -------- | -------- | -------------------------------------------- |
| G1  | 0.80     | 0.45     | 牌面/木框为主体                              |
| G2  | 0.22     | 0.23     | 浣熊实体大小，不认同屏头像图片               |
| G3  | 0.70     | 0.70     | 木框大小与居中，不依赖反射人物               |
| G4  | 0.045    | 0.15     | 花瓶及花枝                                   |
| G5  | 0.60     | 0.25     | bbox 与画面下半部的交集面积 / 半帧面积 ≥0.30 |

这些数值是实现者查阅五张到达图后定义的**工程初值**，字段明确为 `ENGINEERING_INITIAL_FIELD_CHECK_REQUIRED`，不是用户已测量/认可的数值门槛。G5 到达原图为俯视，程序不增加俯仰接口；同一水平视角下能否触发门槛仍需现场核对。阈值若需修改，应发布新配置版本/摘要并重新冻结任务，不能在一个 20 次批次中静默修改。

## 实现与安全边界

| 文件                                                      | 实际修改                                                                                                       |
| --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `live_navigation/world.py`                                | 固定 20 项、五组、版本/世界/Avatar/分辨率、替代条件、参考证据与摘要验证                                        |
| `live_navigation/skill.py`                                | 从现场配置生成可信感知提示；G3/G5 专用判据、按尺寸比例判断前进进展、正面通行与侧移区域证据守卫                 |
| `live_navigation/runtime.py`                              | 真实时钟、等人工 ARM、IPC、感知/规划截止期、session/epoch/lease 复核、新帧验证、逐动作执行及退出停止           |
| `live_navigation/session.py`                              | 显式 live 门槛、真实 provider/采集/控制器装配、逐次报告与固定分母汇总                                          |
| `skills/navigation.py`                                    | 抽出尺度/到达/恢复允许区域三个可覆盖方法，离线默认行为不变                                                     |
| `providers/images.py`                                     | 无新依赖的 BGRA→PNG 编码；CRC/尺寸/扫描行/颜色回归                                                             |
| `perception/interpreter.py`、`providers/openai_compat.py` | 原始 BGRA 使用正确媒体类型，HTTP 侧编码 PNG；提示中提供真实 observation ID，使模型能正确引用证据               |
| `control/service.py`                                      | 启动错误包含锁/端口/热键阶段与已知原因，不回显任意异常正文                                                     |
| `cli.py`                                                  | 默认只读计划、单次 live 命令、报告汇总；GBK/ASCII 输出预检，必要时用 JSON Unicode 转义；编码错误不冒充配置错误 |
| `configs/navigation-live.example.toml`                    | 默认 dry-run 的配置示例；真实模型名、接口和预算需操作者填写                                                    |

以上产品路径相对于 `src/vrc_agent/`（configs 除外）。无新增依赖，`requirements.lock` 不变。

`Runtime` 的 ReplayController 类型检查原样保留。新循环必须拿到真实 `ControllerProcess` 对象；生产入口只创建配置为 live 的独立控制进程，不创建 RecordingTransport/OSC 替身。测试使用相同控制进程的 dry-run 模式，并把报告标为 `supervised-dry-run`，不能混入实机统计。

生产模块没有 ARM/RESET/自动续租调用。F9 由子进程本机热键接收，管道 live ARM/RESET 拒绝规则不变；F8、焦点丢失、watchdog、旧 epoch/lease、到期归零仍在独立进程执行。新循环只允许单轴 200ms 短动作：forward=0.2、strafe=±0.2、yaw=±1.0；wait=100ms；拒绝反向前进、混合轴、自带权限、长等待和额外能力。yaw 强度依据 T00 对低强度转头不明显的记录，效果仍待本任务现场测量。

控制与感知限制：

- 每任务人工确认私密世界、Avatar、版本、分辨率、初始机位/朝向与恢复区域；程序只自动核对窗口/帧尺寸，**不能通过 OSC 自动证明当前 world ID、Avatar ID 或机位**。
- 等待 F9 最长 60 秒；人工启用后任务最多 30 秒/40 动作，仍服从原有 30 秒租约。单次感知最多 1000ms（配置更短则从短），不重试。观测年龄仍 ≤原控制配置且最大 1500ms，不延长安全阈值迁就慢模型。
- 每帧检查 session/epoch、时间、分辨率、帧引用、可见状态、模型证据 ID 和实体采样时间；推理返回、权限绑定、动作完成和成功收尾时再查控制权。迟到或控制权变化即停止，旧结果不绑定新租约。
- 前进必须有当帧 `forward_clearance` 正面可通行地面的高置信证据；恢复侧移必须有 `recovery_clearance` 开阔木地板及双侧空间证据。二者均需置信度 ≥0.9 并覆盖规定的下方行走区域，不能只因 hazards 为空就视为安全。
- 标定中贴墙、茶几/沙发之间、圆凳、池边、石块、石柱、台阶、Mirror Settings 小桩与灯笼附近都不允许侧移。看不清只等待；连续无通行证据或恢复区域不安全则暂停。没有通用避障/路径绕行能力。
- 原连续新观测确认、最多两轮恢复规则仍适用。输入序列完成不等于到达，模型解释不能改任务或安全门槛。暂停、急停、租约失效后本任务终止，下一次必须人工重新就位与启用。

## 现场启动与 20 次记录

先查看一个任务（此命令不创建控制器、不采集、不出网）：

```powershell
.\.venv\Scripts\python.exe -m vrc_agent navigate-live --task G1-1
```

现场前复制 `configs/navigation-live.example.toml` 为自己的配置，填写已选的视觉模型/接口、环境变量名、预算及供应商价格；占位模型名被拒绝。密钥只通过本机环境变量提供，不写配置或报告。保留禁用人工探针、窗口完全匹配、DPI 感知与无遮挡采集。付费账目仍有 T05 的限制：没有价格表/超时调用时费用可能 unknown，不能把 `budget_usd` 宣称为供应商绝对扣费硬上限。

下面是**约好现场后**的真实启动命令，本轮没有执行。新批次用独立 `state-dir`，避免与冒烟/重试记录混在一起；已有 live 控制器须先由人工退出。

```powershell
.\.venv\Scripts\python.exe -m vrc_agent navigate-live --task G1-1 --mode live --config configs/navigation-live.toml --state-dir .runtime/t08-field-01 --confirm-calibration --allow-paid --send-images
```

`--confirm-calibration` 表示操作者已核对冻结环境、机位与安全区域；`--allow-paid --send-images` 明确允许此任务调用配置的视觉服务并发送当前窗口帧。控制器启动为 DISARMED；切回 VRChat 后按 F9 开始，F8 急停，Ctrl+C 退出。一次命令只执行一个任务；不能自动连续执行 20 次或自动复位到起点。

按 G1-1 到 G5-4 顺序逐项人工就位，执行一次，记录现场目视结果/接管原因。元数据自动写入 `<state-dir>/navigation-live/<task_id>-<session_id>.json`，控制进程日志为 `<state-dir>/<session_id>.jsonl`。报告含配置摘要、UTC 开始/结束、控制进程 PID、动作/结果/观测关联、图像摘要、目标和通行区域 bbox；不保存截图、音频、API 密钥、原始模型解释或未筛选的场景文本。

```powershell
.\.venv\Scripts\python.exe -m vrc_agent navigation-report --report-dir .runtime/t08-field-01/navigation-live
```

汇总固定分母 20。缺失为 NOT_RUN；同一任务重复记录为 DUPLICATE_ATTEMPTS，不挑最好一次；不同摘要、离线记录或损坏文件使批次不达标。接管、超时、卡住、缺目标、无安全区域与权限失效都是失败。成功项还复验最终 COMPLETED/OK、连续观测 ID、同一 track、当前数值满足配置判据；正常清理必须 PAUSED，ESTOP 优先计接管。20 项都有唯一记录且 ≥16 成功才令 `threshold_met=true`，但 `field_acceptance` 仍要求人工复核；它不是自动将 T08 标 DONE 的命令。

如中途要调整模型、判据或起点，不覆盖失败记录或把重试当原试次；重新冻结版本/另建完整批次。当前配置无需代理按键鼠交互、俯仰或自动切世界。

## 本轮实际验证

Windows / Python 3.14.6 / 项目 `.venv`，最终四项检查均退出 0：

| 命令                                      | 结果                                         |
| ----------------------------------------- | -------------------------------------------- |
| `python -m pytest -q`                     | **702 passed in 37.49s**（原 653 + 新增 49） |
| `python -m ruff check src tests`          | All checks passed!                           |
| `python -m ruff format --check src tests` | 97 files already formatted                   |
| `python -m pip check`                     | No broken requirements found.                |

新增 27 项单元 + 22 项集成，文件为 `tests/unit/test_live_navigation.py`、`tests/integration/test_supervised_navigation.py`。测试覆盖：冻结清单/替代与中文文本完整性、G3 木框/G5 下半部判据、无恢复区域证据不侧移、无前进证据有界等待、PNG CRC/像素颜色、实际 HTTP 请求图像与 observation ID、GBK/ASCII 输出、明确启动原因且不泄露任意异常、未 ARM 不动作、独立 PID 与实际 IPC、错误/旧帧拒绝、模型迟到、推理/运动中急停、租约到期、任务时限、两轮恢复后暂停、自带权限/非技能动作拒绝、模型私密文本不入日志、汇总证据复验。

真实供应商适配器贯穿感知与独立 IPC 的测试使用**内存 HTTP stub**，没有实际调用供应商。未修改任何 live 热键以自动 ARM；IPC 用例中 `client.request("arm")` 只用于明确的 dry-run 控制进程。

归档工件：`.runtime/t08_live/replay_supervised.py`（使用测试素材，不是独立复核）、同目录 `{normal,stuck,late,estop}.json` 和 `control-*/*.jsonl`。每份报告均为 `supervised-dry-run` / `sends_live_input=false`：

| 工件   | 实际 outcome       | 动作数 | 边界                       |
| ------ | ------------------ | ------ | -------------------------- |
| normal | SUCCESS            | 3      | 连续两个新证据、结束归零   |
| stuck  | RECOVERY_EXHAUSTED | 15     | 最多两轮恢复、最终 PAUSED  |
| late   | MODEL_LATE         | 0      | 无非零输入                 |
| estop  | MANUAL_STOP        | 1      | 动作 CANCELLED、最终 ESTOP |

原离线 20 次报告仍逐字段一致：`sequence_digest=a7dbd298843c38a7ec52c26872797016937f2b11ab97aa31e4ebe6a73b0bc6f7`。wheel 构建成功，并确认从 wheel 导入可加载配置与 `.sha256`，中文描述保持完整；工件 `.runtime/t08-live-wheel/vrc_agent-0.1.0-py3-none-any.whl`，SHA-256 `8006c43d3c153fa3630646f855e2274d43ed1f5f5ec63ef2324b33a143898c89`。不是干净机器安装或实机验证。

过程中修正的真实问题：BGRA 被误标成 PNG；模型提示缺少可引用的 observation ID；IPC JSON 数组不能用 Python 严格 tuple 模式直接解析；PowerShell 标准输入导致生成配置的部分中文变成问号。最后一项已用 UTF-8 文件重新写入并重新冻结摘要，新增中文完整性断言；本文仅列修正后的最终摘要。未放宽控制保护来让测试通过。

## 下一步与剩余边界

- 另一执行者独立复核本轮 live 入口、IPC 监督、感知编码/证据与恢复限制；第 20 节旧复核不代替这一步。
- 约现场：先核验模型配置、实际图像识别速度，以及 G3 木框/G5 水平视角的数值判据；不通过则修订配置并重新冻结，不能放宽观测时效或盲动。
- 然后完整执行固定 20 次，目视核对、接管计失败，≥16/20 且其余门槛通过后才转 T08 DONE。真实 API 速度/费用、识别误差、实际碰撞/恢复安全和客户端无 ACK 的不确定性都尚未测量。
- GraphFlow 本轮无可调用工具；context/plan/index 未执行，未宣称图谱更新。

## 现场实测（2026-10-16，现场会话；未改产品代码）

用户选定云端 DeepSeek。实测（`deepseek-flash`，该服务唯一支持视觉的型号）单张 2160×1440：思考关闭 3.4–3.9s；最快形态 2.2s（思考关闭 + `detail:low` + 输出上限 200）；供应商默认（思考开）3.5–7.9s，且两轮出现 reasoning 吃满输出上限、content 为空。与本篇「单次感知最多 1000ms」及 ≤1500ms 观测年龄的冻结约束冲突：按现设计首个观测即中止，20 次批次不可完成，故未启动 live/ARM/输入。

完整记录见 `docs/TASKS.md` 第 33 节；探针与原始输出为 `.runtime/tmp/ds_latency_probe.py`、`.runtime/tmp/ds_latency_probe_20261016.txt`；现场配置 `configs/navigation-live.toml` 已备（修订前不可用于完成任务）。是否修订 live 设计属产品范围（ADR-009 流程），本现场未改任何阈值。

2026-10-16 更新：用户选定方向 A（修订 live 设计以承载秒级云端推理）；任务已起草为 `docs/TASKS.md` 第 34 节，待指派实现者；复核通过后由现场会话执行 20 次批次。

## 方向 A 本轮实现（2026-10-16，Codex / root）

第 34 节已领取并实现离线候选路径；详细设计、参数论证、证据格式、对抗测试及边界见 [T08_direction_A.md](T08_direction_A.md)。新增 StableNavigation、独立版本化 RGB 稳定性策略、disable_thinking 类型化请求字段、schema=2 报告和汇总校验。推理 8000ms，任务仍 30s、动作上限仍 40、绑定观测仍受 1500ms 及原租约保护；世界任务文件和实际 live 配置未修改。

候选遮罩只是工程初值，不是已标定 HUD。生产入口及运行时明确拒绝 REQUIRES_FIELD_CALIBRATION 策略，不能凭原有 confirm-calibration 参数启动候选 live。后续必须先独立复核，再现场确认遮罩并由用户选择新冻结版本；随后复测真实延迟和执行固定 20 次。租约容量与云端识别质量仍可能造成失败，不承诺 ≥16/20。

新增内存 HTTP/可控时钟测试与真实 spawn IPC dry-run 报告均不发送真实输入。未知费用保持 unknown；取消协程不能撤销远端请求或收费。四项门禁实跑结果和独占报告路径记录于 TASKS 第 35 节。T08、T10 现场验收仍未完成。

## 现场判据修订 v2（2026-10-16）

按 T08_stability_field.md 新增 v2 容差/面积/局部及导航区域保护与预热机制；[设计和剩余反例](T08_stability_v2.md)、交接见 TASKS 第40节。旧版和实际 live 配置不变，新候选仍拒绝 live，不能宣称全部五组现场静止可通过。独立复核后仍须现场复验及用户选择新冻结版本，再进入时延复测与20次任务。
