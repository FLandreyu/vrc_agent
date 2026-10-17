# T08 试点 F-B / F-A 集成修复

2026-10-17，Codex / root，实现方交付；对应TASKS第69/70节。仅离线合成验证，未继续试点、未ARM、未付费调用或发送真实输入。

## 1. 问题与修复范围

第69节G2-1试点在wait后因首次帧不严格晚于动作完成而立即STALE；模型只报告目标、未报告forward_clearance，导航只能wait。本轮修改`live_navigation/runtime.py`采集等待和`skill.py`共享运行时提示，保留原clearance类全部方法、原始BGRA稳定性链路、v1–v5策略/摘要、世界v1/v2及各自addendum、live配置和所有预算/控制门槛。

共享提示发生明确版本化变化（runtime-clearance-v1），不将世界摘要未变误称为有效模型提示未变。依据当前用户指令实施；本记录不能代替ADR-009独立复核或重新进行现场语义/时延验证。

## 2. F-B：有上界地等待新帧

`capture(after_ns=...)`在首次进入时计算单个截止期：当前时刻加`min(1500, control.observation_max_age_ms)`毫秒；最多50次采集、两次之间等待30ms，不能按尝试刷新截止期。`after_ns`是动作完成的严格时序下界，不作为本次等待的起始时间；稳定性预热可以多次调用capture，整项任务仍受任务截止和lease约束。

仅对年龄合格但ID重复、时间不严格大于last_capture或不严格大于after_ns的候选返回“继续等待”。不修改/补写ID和采集时间，不登记到seen/last_capture，不读取其已淘汰的frame缓存，不提交observe、更不交给模型。真正接受的帧仍须同时满足唯一ID、严格递增时间、严格晚于after_ns、`0 <= now-captured_at_ns < max_age`、窗口/DPI尺寸/帧引用关联与控制层observe校验。只有校验过的真实候选才记录到本地seen/last_capture。

| 情况 | 行为 |
| --- | --- |
| 有after_ns、帧年龄合格但尚未严格更新 | 在同一窗口内等待/重试；超时或次数耗尽STALE_OBSERVATION |
| 无after_ns、重复/倒退/相等 | 保留首次立即STALE_OBSERVATION |
| 超龄或未来帧 | 立即STALE_OBSERVATION，不靠等待洗成新帧 |
| 正好截止才返回有效帧 | 拒绝，未发送observe/模型请求 |
| session/epoch、失焦、尺寸、帧引用异常 | 立即拒绝；不因缓存时间旧而忽略安全异常 |
| 等待中急停/接管、lease或epoch变化 | 既有bounded与check_authority继续生效；退出，不提交动作 |

单次采集仍有原1000ms上限，整个after_ns操作另受共同新鲜度截止期约束，二者谁先阻断就退出。30ms是既有异步检查的调度间隔，不是操作系统实时保证；没有增加输入权限、租约或任务超时，也没有伪造动作后观测。

## 3. F-A：明确观察要求，不放宽门槛

新增常量`CLEARANCE_GUIDANCE_VERSION="runtime-clearance-v1"`与共享提示，附加到原世界特有addendum之后，所有五组及两世界的真实装配调用同一`calibrated_prompt()`。

- 明确全画面归一化坐标、左上原点，地板须覆盖前方下部走廊，原几何为`x1<=0.35, x2>=0.65, y1<=0.7, y2>=0.9`；置信度须>=0.9，证据ID必须是当前真实帧。
- 条件性正例为`[0.1,0.55,0.9,0.98]`；窄框`[0.4,0.55,0.6,0.98]`、高框`[0.1,0.2,0.9,0.6]`、未覆盖上边界的底部条带`[0.1,0.75,0.9,0.98]`是反例。
- 模型必须观察，但不必须输出实体。禁止抄示例、扩框、跨障碍合并、外推边界或编造置信度。全屏框不证明地板；有水、落差、台阶、人物/家具遮挡或深度不确定就省略clearance并保留风险/不确定性，不能凭“没报hazard”推出可走。
- recovery仍需同样几何/置信度和可见木地板、双侧短步空间的额外正面证据；forward实体不能替代recovery实体。`CalibratedSkill`的AST与基线完全一致，没有修改几何、置信度、wait计数或恢复门槛。

缺失clearance时仍在第3次缺证据决策置PATH_UNSAFE；运行时在提交前检查halt_reason，因此实际仅前2次wait被提交，不会提交第3次或新增motion。模型被明确提示不等于模型已经正确遵从；真实G2/G3/G5等画面的结果需另做只读语义与生产时延复测。新提示增加输入文本，原8000ms推理与1024输出额度均未放宽。

## 4. 回归与离线闭环

新增55项回归，定向运行：`python -m pytest -q tests/integration/test_pilot_capture.py tests/unit/test_clearance_prompt.py`，**55 passed in12.94s**。

- 采集22项：重复ID但更新时间、倒退、等于after_ns后等到真正新帧；300/1500ms共同截止期；冻结测试时钟下50次有限上界；有效帧恰好迟到仍不交控制层；无after_ns立即拒绝；超龄/未来、epoch/焦点/尺寸/缺帧不重试；等待时急停/接管/lease/epoch中断。
- 提示33项：两世界×五组全部附带指导且保留addendum；正例/每个几何边界及反例在forward/recovery门槛下实测一致；低置信度、缺失、旧证据、hazard/uncertainty不可因提示更新变为motion。
- 采集用例中含2条完整合成闭环：world v2 + v5 + JPEG85 + G2-1；在已完成动作后注入等于finished_at_ns的候选，再提供真正更新帧。有合法clearance时合成SUCCESS（2个动作/2个结果）并动作后归零；不提供clearance时第3次缺证据决策置PATH_UNSAFE（实际提交2次wait）、无motion。两条报告均通过完整稳定性报告复验，最终PAUSED，sends_live_input=false。不是LLM推理/现场到达或20次成功率证据。

## 5. 门禁与哈希

使用项目虚拟环境，从项目根执行四项门禁，均退出0：

| 命令 | 结果 | 日志（`.runtime/verify/`） |
| --- | --- | --- |
| `python -m pytest -q` | **1076 passed in282.17s** | `t08_pilot_pytest.log` |
| `python -m ruff check src tests` | All checks passed | `t08_pilot_ruff.log` |
| `python -m ruff format --check src tests` | 137 files already formatted | `t08_pilot_format.log` |
| `python -m pip check` | No broken requirements found | `t08_pilot_pip.log` |

定向日志`t08_pilot_targeted.log`；交付审计探针`t08_pilot_audit.py`及结果`t08_pilot_delivery.json`位于同目录。审计重新读取合成报告并完整复验，同时核对冻结文件、CalibratedSkill AST、门禁与10份有效提示摘要。源码摘要按本机工作树字节，提示按UTF-8文本，避免混淆Git blob LF与本机CRLF口径。

| 对象 | SHA256 |
| --- | --- |
| `src/vrc_agent/live_navigation/runtime.py` | `26ca82972e507676c143a7561d12fa9dbc12b7b28012754015cce86bed9fdaea` |
| `src/vrc_agent/live_navigation/skill.py` | `8f7aa315fe85335da524f65db25cf8d85a241d9a45f66e41bcc515feff876b8b` |
| `tests/integration/test_pilot_capture.py` | `f2e384c381512a6e5b2d74610b4dc09c02ca028c3d55b522fc61a3097901e470` |
| `tests/unit/test_clearance_prompt.py` | `f2045e76ba0da39befe605ec69ab7b044a0435f6443c435504247963a3e9f2d9` |
| `CLEARANCE_GUIDANCE` UTF-8 | `fceef13937071dc9e9bbe9913a419f190fccb4beb73998dbc5c15595e08db821` |
| world v2 / G2 完整有效提示 UTF-8 | `01077007d7a98a17bb2cf26905693dfd6a13ee9ad7efba6be20f84c1003c58d8` |
| `t08_pilot_delivery.json` 字节 | `5a3d1186aabcbc4ee0cbb3b8b62fa8badff1c3e338a0de5ef9a4bd7bf99590af` |

两条本轮完整门禁生成的合成报告：

- SUCCESS：`t08_pilot-SUCCESS-0243d4912036424db050a5f91a765486.json`，SHA256 `3adaeb6618873f1c5f1502a5c23d08f234c400a330b41af357c4ff30363da2c7`。
- PATH_UNSAFE：`t08_pilot-PATH_UNSAFE-5f39cb1975d9456789ceab4d682250b1.json`，SHA256 `b3e0bad3c8774a4597d791ce85e60f2873a3f3b7fe5973b964801f2a50e02166`。

v5冻结策略摘要仍`2e21c734ec902c44b3111de8f6f62f06626fbbe9410ff9a0b959469248eb4faa`，世界v2语义摘要仍`b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0`。v1–v5全部策略/sidecar与比较/报告代码、世界v1/v2与sidecar、live配置、依赖锁及控制/传输/provider均通过Git差异不变项核对；完整文件字节摘要、基线提交、10个有效提示和五份日志摘要在交付JSON内，不覆盖历史工件。

GraphFlow工具本会话不可用，未伪造context/plan/index。沙箱启动器Windows740，实际命令经自动审批在可用执行器执行；未修改用户配置或全局环境。下一步须另一执行者按ADR-009复核采集有限性/新鲜度、提示/门槛一致性与不变项，再现场验证真实模型遵从和时延；20次任务暂停，T08仍IN_PROGRESS。
