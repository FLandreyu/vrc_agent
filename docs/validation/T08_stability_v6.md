# T08 v6：动作预算与可选前进包络候选

2026-10-17，Codex / root，实现方交付，依据TASKS第71/72节。v6是待独立复核、待用户重新冻结的候选，不代表现场可用或20次任务验收。本轮无live、无ARM/续租、无付费模型或真实输入。

## 1. 选择的参数及依据

| 项目 | 旧值 | v6选择 |
| --- | --- | --- |
| 动作/观测准入年龄 | 1500ms | 策略上限4000ms；控制配置同为4000ms，较小配置优先 |
| 单任务超时 | 30s | 180s上限 |
| cautious前进档 | forward0.2 / 200ms | 保留 |
| field前进档 | 无 | forward0.4 / 500ms |
| 转向 | yaw±1 / 200ms | 两档均不变 |
| 恢复侧移 | strafe±0.2 / 200ms | 两档均不变 |
| max_actions | 40 | 不增加，v6运行时明确强制40 |
| 控制lease | 30000ms | 不变，不续租/自动ARM |
| heartbeat间隔/失联上限 | 100/500ms | 不变 |

4000ms位于用户授权的3000–5000ms范围，给第71节实测1.53s观测到提交留余量；未来帧仍拒绝，年龄恰好4000ms即失效，不伪造时间。180s按每步约4.5–5s、重观测与恢复余量选择，作为任务硬上限。

按第71节旧档0.06m/步线性缩放，新field档约0.3m/步、40步约12m；这是工程估算，VRChat模拟输入不保证线性，尚无新档实机距离/转向/制动测量。180s最多约36–40个完整循环，故暂不提高max_actions；不能把估算当成第一世界可达证明。

**180s任务预算不等于180s控制权。现有30s lease仍是更早的硬门槛，到期即停，新参数不会使其自动续签。** 租约内可能仍不足以完成远距离任务；本次按用户要求不延长租约、不忽略lease_id变化、不自动重新ARM，也不宣称已经消除20次任务的现场阻断。后续现场方案必须服从这条边界。

## 2. 版本、冻结与不变项

新增`live_navigation/stability_v6.json`及sidecar，版本`local-rgb-stability-v6-candidate`、status=`REQUIRES_FIELD_CALIBRATION`，装配键`stable-inference-v6`，报告schema7。

策略字节SHA256：`63cdd1d179f9e03bd74df16fe1d37752606555336c639f22cd653fd6dd17e11c`。

相对冻结v5，字段差异恰为version/status/freshness_ms/task_timeout_s/motion_profiles，max_actions仍40。metric仍`rgb_tolerance_visible_guard_v5`，遮罩、5%全局面积、Δ2像素容差、32px保护区、黑帧、exclude_masked和预热全部原样复用；v1–v5 JSON与sidecar不变。v6加载器对照v5和闭集档位验证完整声明，拒绝夹带像素判据变更；JSON严格比较防止bool冒充数值。

新`.gitattributes`只对v6 JSON/sidecar设`-text`，保证其新字节哈希跨Git行尾转换可重算，不修改旧工件CRLF口径。旧v5仍`2e21c734…`，世界v2仍`b65b3dab…`；世界v1/v2、提示（包括runtime-clearance-v1）、clearance/到达/恢复判据与数量上限不改。

v6在session入口和直接运行时构造两处仍拒绝live；基类/v5不能通过选择v6绕过预热。schema7与schema6隔离，v6报告必须附准确run_parameters（档位及完整包络、实际freshness、task_timeout、max_actions），复验按实际较小freshness核对proof及动作时间。候选报告若声称live或sends_live_input=true直接拒绝。不将旧v5冻结授权移用于v6。

## 3. 新鲜度接线与控制边界

`ControlConfig.observation_max_age_ms`可配置范围100–5000ms，通用默认仍1500ms；AppConfig只有v6能选扩展年龄或field档，旧入口和旧策略保持1500ms上限。实际v6年龄为控制配置、策略4000ms与全局5000ms上限的最小值；例如控制配置3000→实际3000，5000→实际4000。

`parameters.action_freshness_ms`统一用于runtime采集/后续年龄验证、session感知装配、stable_runtime构造与before_submit，消除第71节列出的三个1500ms动作年龄截断。控制core继续按相同配置做准入与执行期间年龄检查。3999ms观测可以准入，但运动中年龄到4000ms仍立即停止归零；没有为了完成500ms单步而豁免中途失效。

第70节等待“动作后真正新帧”的采集竞态预算独立保留≤1500ms/最多50次/30ms，共用截止期；这不是动作准入年龄上限。单次采集、图像编码、稳定比较仍1000ms，云端推理仍8000ms。去重、严格递增、after_ns严格下界和未来帧拒绝语义不变。

## 4. 档位与配置

通用默认`navigation_motion_profile="cautious"`。现场配置显式选择：

```toml
mode = "dry-run"
navigation_design = "stable-inference-v6"
navigation_motion_profile = "field"

[control]
observation_max_age_ms = 4000
lease_ms = 30000
```

`parameters.py`定义不可变闭集包络。CalibratedSkill在正向Proposal已有当帧clearance时转换为所选前进档；Runtime独立保存并复查包络，不能通过替换skill包络或模型提案选择其他速度/时长。仅允许一个轴，不后退、不混轴；field只扩大前进，转向/侧移保持原档。控制max_motion_ms和MotionArgs契约仍500ms；配置的控制上限小于所选档位会在加载时报错。

控制进程实现、bind_request、OSC/热键、急停可重复归零、session/epoch/lease校验、heartbeat/watchdog、动作TTL及到期归零均未改。场景风险、clearance缺失与有限恢复仍可提前暂停。生产装配仍独立ControllerProcess，显式live和本机F9双门槛不变，候选在其前额外拒绝。

## 5. 离线回归

新增48项v6用例，连同实际配置/JPEG相关旧回归定向**77 passed in20.81s**：

- 策略仅改预算/档位字段、两版相同输入得到完全相同像素比较结果（静止、可见保护像素变化、遮罩变化），新摘要自洽；严格配置、默认兼容、较小年龄优先。
- 两档真实内存HTTP→感知→v6预热/证明→规划→真实控制core→结果验证闭环均合成SUCCESS、结束归零，报告完整复验；schema、参数、候选伪造live均拒绝。
- 1530/3000/3999ms观测控制准入通过，4000/5000ms拒绝；capture与before_submit实际使用4000ms，session装配哨兵实测感知接收到4000ms。
- 500ms单步499ms仍执行、500ms归零；中途新鲜度/急停/心跳/lease失效均归零。epoch变化打断；180s任务上限不延长30s lease；候选两档直接live与session均在采集/模型调用前拒绝。
- 模型提议其他档、其他持续时间、后退、混轴、提高侧移或非法转向被拒；即使测试故意替换skill包络为“总接受”，Runtime仍拒绝，未提交任何动作。

## 6. 完整门禁、交付审计

初次完整运行1121 passed/3 failed（两项旧版本闭集及大写字面量分类需同步v6），原日志保留`t08_v6_initial_pytest.log`。已扩充schema/预热/工厂闭集至v6，未知版本哨兵改v7，新增POLICY_VERSION属性名和REQUIRES_FIELD_CALIBRATION状态的明确非错误码分类说明；未改实际遥测结局码表。相关旧闭集回归27 passed in9.29s。最终四项门禁均退出0，使用项目虚拟环境、从项目根执行：

| 命令 | 结果 | 日志（`.runtime/verify/`） |
| --- | --- | --- |
| `python -m pytest -q` | **1124 passed in281.64s** | `t08_v6_pytest.log` |
| `python -m ruff check src tests` | All checks passed | `t08_v6_ruff.log` |
| `python -m ruff format --check src tests` | 140 files already formatted | `t08_v6_format.log` |
| `python -m pip check` | No broken requirements found | `t08_v6_pip.log` |

日志`.runtime/verify/t08_v6_{targeted,pytest,ruff,format,pip}.log`；审计探针`t08_v6_audit.py`与结果`t08_v6_delivery.json`位于同目录。源码哈希采用本机工作树字节，新v6 JSON采用固定原始字节，提示采用UTF-8文本；保留历史验证工件。另以世界v2/G1-1和field档做完整内存HTTP合成装配，报告`t08_v6_world_v2-44b246755f3948169f295a02bebf2edf.json` SHA256 `464c97ec795ea18117e6a97304c8af885d52e48963750e2ca359adc71b006872`，合成SUCCESS、PAUSED/归零、sends_live_input=false，完整报告复验通过；不代表真实LLM推理或现场到达。

交付审计JSON SHA256 **`d29dde51b62459d9470af0f5531f7287e10457b22f689600b061bbd2335edb60`**。含全部修改源码/测试/配置哈希、旧工件摘要、基线提交、比较/clearance/提示AST不变项、日志摘要及三份合成报告。关键字节SHA256：

| 文件 | SHA256 |
| --- | --- |
| `config.py` | `a5f9a0d6f44dcd930650302f13934ab199361a2e08fc859afd3afa22eea0b0ab` |
| `live_navigation/parameters.py` | `1520a66ad501e0c7427020e5da2b17d78d55d365b548281c37eaa66fe9f117ea` |
| `live_navigation/runtime.py` | `10a3946a2e58f54a1f8df1bf964371e3280f1e6f1cc5bf4c8a173cad9eca37bf` |
| `live_navigation/session.py` | `2f10c70f17a001208f3dcadb334e81bb967b3cad31208dab0536904217d946e8` |
| `live_navigation/skill.py` | `0e2b91cbcf59a1b056767273edb9876087c052b34722e4f11201f4a1335fa962` |
| `live_navigation/stable_runtime.py` | `4d27d3cf64d930fc7d8ff4189ad937eec281b0fc4f55dff8de4efad5e20ff7cd` |
| `live_navigation/stability.py` | `31a634f553e8f283ea94e685f227f212c0ad61c7a49fbac6d1130201e7e3d4cb` |
| `live_navigation/stability_report.py` | `b1acc5d0662be8e52039d3822e35db4832ea07b380695f1b9d23b94da18d7cad` |
| `configs/navigation-live.toml` | `3b1f0a65f3196fa0d1729735b3007cd331e4224838a4676ee9f35b0f8eaefa2c` |

前8行以`src/vrc_agent/`为根。最终完整门禁生成的cautious合成报告`t08_v6-cautious-c2f22a726399411aa4a46e21f4831082.json` SHA256 `4613be2284309083438d70241e843972c4627046a1ba17c7f18482f3a5e2b0c7`；field报告`t08_v6-field-c604a61aed1b4e3fb52a5fd65bec2e43.json` SHA256 `2ac83d6705d96605d56a81f5c605a60f774d587204ece897e837d02a356c5b9a`。均为离线/dry-run证据，不纳入20次现场批次。

GraphFlow工具不可用，未伪造context/plan/index。下一步：另一执行者按ADR-009复核预算贯通、闭集包络、到期/急停/权限优先与v5判据同源→用户重新冻结v6→按租约纪律现场确认运动、时延和任务可行性；不自动恢复20次批次。T08仍IN_PROGRESS。
