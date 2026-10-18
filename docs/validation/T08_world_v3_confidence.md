# T08 world v3：按地标显式目标识别门槛候选

日期：2026-10-18；实现：Codex；交接：TASKS 第110节。

**这是未冻结候选，不是实机验收。** 现场仍使用稳定性v9＋世界v2＋dry-run；本轮未启动live、ARM、付费模型、真实输入，也未读取密钥。世界v3在采集、供应商、控制器资源创建前拒绝live，等待独立复核与用户冻结。

## 1. 更正依据与候选范围

第107/109节现场数据说明目标置信度随视面积及位姿变化，模型输出集中在0.55/0.72/0.82/0.85/0.9粗桶。“必须先接近才可信、但不可信就不能接近”形成起步阻断。

任务原文和历史第105–109节把目标识别门槛写成0.9；经代码核对与用户末次更正，实际是`CalibratedSkill`中的**0.85**。0.9属于独立的clearance证据门槛，不能混用。本候选遵循更正：旧世界v1/v2全部显式映射0.85，不收紧旧行为；未测量地标也维持0.85。

| 地标 | 标签 | v1/v2实际门槛 | v3必填门槛 | 依据 |
| --- | --- | --- | --- | --- |
| G1 | home_blue_ad | 0.85 | 0.85 | 未测量，保持现状 |
| G2 | home_raccoon | 0.85 | 0.7 | 左斜位姿粗桶0.72以上，拒绝0.55 |
| G3 | home_mirror_frame | 0.85 | 0.85 | 未测量，保持现状 |
| G4 | home_vase | 0.85 | 0.7 | 标定远距粗桶0.72以上，拒绝更远的0.55 |
| G5 | home_floor_diamond | 0.85 | 0.85 | 未测量，保持现状 |

不是按面积动态分档，不改变模型或提示。阈值不是经过概率校准的正确率承诺；0.7仅位于已观察到的0.55与0.72桶之间。

## 2. 42条观测分布与内存复现

以下是**14张固定帧×3轮＝42条模型观测，不是42张独立图像**。本轮只读取既有本地JSON，未重新调用模型。最小测试夹具`tests/fixtures/world_v3_confidence.json`保存标签、数值、框、来源摘要，不保存图像或私人聊天。

| 位姿 | 0.55 | 0.72 | 0.82 | 0.85 | 0.9 | 合计 | 均值 | 旧0.85通过 | 新0.7通过 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| G2-2左斜 | 0 | 14 | 2 | 0 | 5 | 21 | 0.77238 | 5/21 | 21/21 |
| G4-1标定远距 | 0 | 20 | 1 | 0 | 0 | 21 | 0.72476 | 0/21 | 21/21 |
| 合计 | 0 | 34 | 3 | 0 | 5 | 42 | — | 5/42 | 42/42 |

这批没有0.85或0.55观测，不能虚称已经重放此类现场帧。0.85由合成边界回归覆盖；0.55另取第107节更远现场G4的三条推理证据（目标框面积约0.0021–0.00245，依原始框坐标计算），内存重建后仍全部被`entity_confident`拒绝。与本批面积约0.0041–0.0053的G4不同，三条不并入42条统计。0.70通过、0.699拒绝另有精确边界用例。

42条都有hazards和uncertainties；G2只有8/21有几何clearance，G4为0/21。**目标识别通过不等于允许前进**：无clearance仍只能wait；有信号仍不能自动SUCCESS。内存复现只验证目标识别与既有门禁组合，不是物理导航成功率。

来源与原始文件SHA256：

| 本地文件 | SHA256 |
| --- | --- |
| `.runtime/verify/t08_confidence_ab_20261018_220348.json` | `f8150637a6bf283834de774aafee7af601c206317a49a9a27c67595f4b26edb2` |
| `.runtime/verify/t08_confidence_ab_20261018_220439.json` | `68e8c3008935fce405bfc5dc5359f303f732c2031ef5e0e5264def9db42f7d48` |
| `.runtime/live/G4-1-v9pilot1/navigation-live/G4-1-ead0439a09874fa7ba49e82af47e55ce.json` | `057f97c45d960b910be45a9cf3b7b881dc8e34279a8715205380832aeddfbe12` |

## 3. 世界契约、摘要与装配

新增`vrchat_home_live_v3.json/.sha256`：`schema_version=3`、`version=vrchat-home-live-v3`、`status=REQUIRES_FIELD_CALIBRATION`，在世界v2全部内容上增加逐地标必填`min_confidence`、候选状态和显式v2来源摘要；版本/schema随新世界递增。原`source_world_digest`仍指向v1，新增`source_world_v2_digest`指向v2，不改写原来源链。

- 新世界sidecar摘要：`3e7b4566cfabcb6d2527c5d221b4e9d0427b542adb8d6183bc13cf90d3d9885f`。
- JSON原始字节SHA256：`1ab6014836c030a8f9e0fc29d06ab54c2df9bee2f6a96d0f6fa338e245bb0b33`。
- 世界sidecar沿用旧世界的**规范化模型内容摘要**（排序JSON、UTF-8、紧凑分隔符），不是原始JSON文件字节哈希；两者不能混称。新文件用`.gitattributes -text`保留现有CRLF字节口径。
- v1语义摘要不变：`dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335`。
- v2语义摘要不变：`b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0`。

v3 loader要求每个`min_confidence`为有限数值且在[0,1]内，拒绝缺失、bool、字符串、NaN/Inf与越界；还严格核对本候选只有授权门槛及版本声明变化，拒绝私改到达/提示/任务/来源。skill读取对应世界类型显式提供的门槛：v1/v2固定映射0.85，v3读取必填字段，没有“缺字段就用0.85”的回落。

CLI增加显式`--world-version v3`（默认仍v1）；dry-run只读计划可查看候选。live入口先检查世界冻结状态，再读配置与建资源；运行时构造也检查候选，避免绕过入口。没有切换现场配置，没有冻结v3。

## 4. 报告与兼容边界

稳定运行时v6–v9的新报告在`run_parameters.target_min_confidence`记录实际`skill.goal.min_confidence`；v3报告必填并与世界版本、`suite_digest`、task地标绑定重算，拒绝缺字段、bool/字符串、错误门槛或错误世界摘要。采信audit及动作后verification仍由既有校验器重建skill和观测重算，不能只伪造该字段。

稳定性schema12未改变含义，世界schema3与版本/摘要区分候选门槛；没有把v3门槛回填给旧世界。历史v1/v2报告可缺少新增字段（保持旧格式可读），其识别仍按显式0.85契约重算；带字段时必须精确匹配0.85。v3不得借此历史兼容分支省略字段，也不得报告为真实live输入。

## 5. 风险与仍需复验

放低G2/G4门槛会提高错误目标被采信的风险；唯一标签不保证模型认对对象，0.72也不是72%的已校准概率。其余识别谓词、帧/观测/session/epoch绑定与保护区可见性保持原实现。forward/recovery clearance仍独立，前进证据几何x1≤0.35、x2≥0.65、y1≤0.7、y2≥0.9且conf≥0.9一个数字未改。模型若同时误认目标和误报clearance，依然可能许可错误短动作；有信号仍运动的既有风险没有消除。

到达几何、连续两个新观测和人工确认口径不变；hazards/uncertainties仍记录，有信号不自动SUCCESS。真实G4样本无clearance，降低目标门槛未必解除其起步阻断。推理8s超时仍直接停止，不改为重试；稳定性重试仍最多3次。

独立复核后须由用户决定是否冻结新世界摘要，随后人在场复验G2-2、标定距离G4及更远0.55拒绝、误认/遮挡与clearance缺失、G1/G3/G5未测量位姿。固定20任务仍需人工逐条确认；本候选不证明≥16/20，也不构成T08完成。

## 6. 验证与不变项

回归覆盖边界0.7/0.699/0.55、未测量地标与旧世界0.85、字段缺失/伪造、42条数值内存重放、其余识别谓词拒绝、clearance0.9与几何、有信号运动但不假成功、无clearance只能wait、候选资源前拒绝、报告字段重算和历史格式兼容。

完整门禁、默认只读计划、变更清单及逐字节自查结果见TASKS第110节。v1–v9稳定性策略及sidecar、v1/v2世界及sidecar、control/、configs/与实际生成的旧世界提示均须与开工基线一致；4000ms/180s/40步/3attempt、预热2对、包络、30s基础租约、急停/epoch/TTL/到期归零不改。

本轮实测门禁（四项均exit 0，日志`.runtime/verify/t08_world_v3_{pytest,ruff_check,ruff_format,pip_check}.log`）：

```text
1459 passed in 295.33s (0:04:55)
All checks passed!
163 files already formatted
No broken requirements found.
```

定向113项通过；四项门禁之外，`navigate-live --task G2-1 --config configs/navigation-live.toml --world-version v2`返回`mode=plan-only`、`sends_live_input=false`、原世界v2摘要；显式v3同样只读并显示候选新摘要。两条命令均exit 0，完整输出为同前缀`plan_v2.log`、`plan_v3.log`。

开工114个既有生产/config文件自查通过：仅9个授权生产源码变化；18个稳定性文件、4个旧世界文件、control/、configs/及`acceptance_v8.py`未变。旧提示10份摘要一致，新世界5份提示等于v2。字节自查见`.runtime/verify/t08_world_v3_invariants.json`；修改文件摘要清单见同前缀`manifest.json`。GraphFlow工具当前不可调用，未伪造context/plan/index。这些均为实现方离线结果，不是独立复核或现场验收。
