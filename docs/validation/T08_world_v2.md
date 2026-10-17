# T08 世界 v2 定稿实现与验证（2026-10-17）

负责人 Codex / root，实现与自测。按 TASKS 第59节、[v5 现场复验](T08_stability_v5_field.md)及用户指定定稿：G3 box 0.50/0.52＋新激活前参考；G5仅替换到达参考而保留判据；G3/G5提示按原提案落地。**本轮没有启动 live、调用付费模型、操作本机ARM或发送真实输入；实现仍待另一执行者独立复核与后续选版/冻结。T08 保持 IN_PROGRESS。**

## 1. 版本、摘要与来源

| 项 | 值 |
| --- | --- |
| 世界版本 | `vrchat-home-live-v2`，世界 schema=2（不是稳定性 proof schema=6） |
| 文件 | `src/vrc_agent/skills/worlds/vrchat_home_live_v2.json` + `.sha256` |
| 世界语义摘要（suite_digest / sidecar） | **`b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0`** |
| JSON 文件字节 SHA256 | `98798198c6dffce329444a7be4c808e8e0046d9ca7b4270ee4897675db565276` |
| 前代世界语义摘要 | `dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335` |
| 标定来源 | `docs/validation/T08_stability_v5_field.md#sections-2-3` |
| 来源切片 SHA256 | `91b09394db1f28ce549db5dd3b7121e3b7d174e01124f61070b35ba19c68d62e` |

世界摘要沿用 v1 的语义口径：Pydantic 完整 JSON 数据，键排序、ensure_ascii=False、分隔符 `(',', ':')`，编码UTF-8后SHA256；与文件字节摘要区分。来源切片直接读取现场文档**原始字节**，从 ASCII 标题 `## 2.` 的起始位置到 `## 4.` 标题之前，共1438字节，不转换CRLF。新提示字段也参与世界摘要，不能改提示而沿用原摘要。

v1 JSON/sidecar逐字节不改，v1五组提示的UTF-8摘要也与实施前一致；v1–v5稳定性策略文件及sidecar不改。基线检查 `.runtime/verify/t08_world_v2_before.json` 与 `t08_world_v2_v1_prompts_before.json`。本机Git基线为 `b6d9484`；工作树内已有第59节现场证据修改，本轮保留其内容，未将现场文档归一化或纠正历史计算文本。

## 2. 实际定稿差异

| 地标 | 到达规则 | 参考变更 | 提示 |
| --- | --- | --- | --- |
| G1/G2/G4 | 与v1逐字段相同 | 不变 | 字节相同 |
| G3 | box；width **0.50**、height **0.52**；centre_tolerance 0.12及其余字段不变 | 新镜面未激活参考，见第3节 | 仅框物理木框/可见木缘；激活后仍找可见边缘，不可见就省略目标并说明，不推断遮挡边界 |
| G5 | **与v1逐字段相同**：lower_half，0.6/0.25/0.3，centre_tolerance 0.12 | 新站立取景参考，见第3节 | 仅白描边嵌套菱形本体，排除池面/水面/周边，不能用全图或池面冒充目标；不可分辨则缺目标/保留不确定性 |

20项任务的task_id、顺序、出发、朝向、起点参考、遮挡替代及恢复区域均与v1相同。地标标签、描述与恢复区也不改；v2只通过新 `prompt_addendum` 字段追加G3/G5观察约束，其他三组为空。两段英文完整提示取自原提案，已落入JSON并由 `calibrated_prompt` 组合；并非只把文档状态改成“已实现”。

G3采用用户本次确认的0.50/0.52，取代历史提案0.62/0.65；0.54×0.57的参考框留出0.04/0.05的尺寸余量，且居中偏移0.015满足0.12。它仍只是图像尺寸/居中谓词，不是镜面激活状态检测器，也不保证所有轨迹都在激活前停下。未实现未经验证的激活态分类器。

G5框 `[0.09,0.42,0.93,0.89]` 的宽高0.84×0.47满足原阈值。**现场文档0.328是相对于全画面的未归一化交集面积（精确0.3276）；产品既有lower_half公式除以0.5，得0.6552**，与阈值0.3比较同样满足。本轮未改公式或阈值，也未修改现场历史记录来掩盖口径差异。

现场 G3 遮挡/镜面设置桩等 hazard、G5“疑似水面” uncertainty 继续保留为安全约束：提示不得把不确定性抹掉，尺寸满足不能覆盖这些标记。原场景置信度、两份新观测结果验证及急停/接管优先级不变。

## 3. 新参考图与本机核验

| 地标 | 到达参考文件名 | PNG SHA256 |
| --- | --- | --- |
| G3 | `clean_g3_preactivation2_20261017_115715.png` | **`41b2d90236fa8fb8b79ba2cf699275539626e6ecf5bfb5b4296ce7cba0c09a6b`** |
| G5 | `clean_g5_pattern_20261017_115903.png` | **`51a7858833c66280a2fae457f4af4220ef5246c2ffbbcf0d17efafd36d27145c`** |

从本机 `.runtime/tmp/probe_frames/` 直接重算PNG字节摘要，核对签名及IHDR尺寸2160×1440；使用现场实际文件名，没有复制、压缩、改名或修改图片。原图继续只在本地，不入库；仓库只含文件名和摘要。没有借用弃用样本 `clean_g3_preactivation_20261017_115557.png` 或旧激活态JPG。

实现方审计使用现场记录框和v5几何函数，只是几何复算，不重新调用模型或重放现场像素。G3原框/外扩均部分进入UI区，按v5声明接纳；向外取整后的遮罩交集面积与现场探针数值可能有像素取整差异，以审计工件明确口径，不能将交集称为“已验证稳定”。G5原框及外扩均无交叠。

## 4. 加载、提示与报告隔离

- 新 `LiveWorldV2` / `LiveLandmarkV2` 为明确版本契约；校验前代摘要并继承固定五组×四任务校验；`load_live_world("v2")` 读新文件并重算世界语义摘要。未知版本拒绝，不把任意输入拼成文件路径。
- 默认 `load_live_world()`、旧CLI和配置仍使用v1。显式 `--world-version v2` 同时用于计划、live装配及汇总。没有替用户选择或冻结稳定性版本，live配置也未改；v5仍为候选且拒绝live。
- v2的摘要包含G3/G5提示追加字段；runtime按所选世界的地标生成模型请求，报告记录该世界version/suite_digest，成功复验按同一世界实例执行。
- 汇总仅接受所选世界摘要；v2必须有正确world_version。显式世界不符即invalid，不把v1/v2尝试混成同一20项批次。v1兼容早期缺world_version字段的报告；字段存在时也须匹配。
- `verified_success` 入口也核对报告版本与摘要，避免直接调用时用v2较小G3尺寸认可v1报告。模型不可选择世界版本或修改摘要。

只读验证命令（均不会发送输入）：

```powershell
.venv/Scripts/python.exe -X utf8 -m vrc_agent navigate-live --task G3-1 --world-version v2
.venv/Scripts/python.exe -X utf8 -m vrc_agent navigation-report --report-dir .runtime/navigation-live --world-version v2
```

旧报告放在原目录仍按v1汇总；使用同一混合目录时，非选定版本会逐文件invalid。正式批次应使用独立目录；不能通过改报告标签或仅换版本名迁移旧证据。

## 5. 测试与工件

新增19项定向回归，首次定向运行19 passed in16.76s：

- 精确限定v1→v2字段差异、世界/文件/来源切片摘要、参考图哈希、前代/世界schema/20项任务与坏摘要校验；G5规则逐字段相等。
- G3现场框及尺寸边界/尺寸不足/偏心负例，v1仍不认可该小框；G5归一化下半部公式与uncertainty阻止动作。
- v2提示进入运行时模型输入，v1及其他三组提示不变；显式CLI计划/汇总、v5候选live拒绝。
- 合成v2 G3/G5内存闭环、报告版本/摘要与成功复验、混合批次/缺版本/错版本拒绝；G5不确定性不能被数值成功覆盖。
- 真实独立ControllerProcess **dry-run** 的v2 G3报告生成，全部为合成场景、没有现场运动。

新增测试初次ruff提示导入顺序，已修正；没有放宽测试期望。最终四项门禁（项目虚拟环境、实际运行）：

| 命令 | 结果 | 日志（`.runtime/verify/`） |
| --- | --- | --- |
| `python -m pytest -q` | **991 passed in217.22s (0:03:37)** | `t08_world_v2_final_pytest.log` |
| `python -m ruff check src tests` | All checks passed! | `t08_world_v2_ruff.log` |
| `python -m ruff format --check src tests` | 133 files already formatted | `t08_world_v2_format.log` |
| `python -m pip check` | No broken requirements found. | `t08_world_v2_pip.log` |

真实控制子进程的合成G3闭环报告 `.runtime/verify/t08_world_v2-b79f1b817a534e19b3786989d2a92552.json`，SHA256 **`0d6d5aec1e4bf8da3132ae1d56a0eaa1d46b242370f9521371eca124d62c533f`**；world_version=vrchat-home-live-v2、suite_digest与新世界一致、稳定性schema6、合成SUCCESS、mode=supervised-dry-run、sends_live_input=false，完整稳定性报告复验通过。这不是现场到达证据。

只读CLI实跑工件 `.runtime/verify/t08_world_v2_plan.json`：显式v2得到plan-only、正确世界摘要、sends_live_input=false。实施前基线、旧提示摘要、最终代码清单 `t08_world_v2_code_manifest.json` 与最终文件清单 `t08_world_v2_final_files.json`（同目录）留供复核；测试后重算旧文件及被测源码均一致。

实现方审计 `.runtime/verify/t08_world_v2_delivery.json`，SHA256 **`c5150f1fd3f8269dff6fe2f45c3f9ab66d3bf82d7aa2dfe18e4f31726364d328`**；脚本 `t08_world_v2_delivery.py`、日志 `t08_world_v2_delivery.log`（同目录）。记录本机图片字节核验、v1不变清单、旧提示摘要、v2世界/文件/来源摘要、G3/G5几何与提示摘要；这不是独立复核结论。

## 6. 后续门槛

本次用户已确认世界v2的具体规则和提示落地，本轮完成实现定稿；独立复核及运行选版/冻结仍按第59节顺序进行。另一执行者需独立核对实际参考摘要、G3/G5语义和提示、旧世界隔离、报告复验与安全门槛，再决定后续现场验证。

第59节配对复验标记 `protected_unexercised=true`，不能用其286/286替代模型实体保护区/端到端到达验证。真实新提示尚未向供应商调用；20次真实任务、接管/超时/卡住计数与T10实机usage/hourly折算仍缺。没有将几何满足、合成SUCCESS或参考更新写成实机验收，T08不转DONE。
