# T08 world v3 定向复核（ADR-009）

- 复核对象：`src/vrc_agent/skills/worlds/vrchat_home_live_v3.{json,sha256}`、`src/vrc_agent/live_navigation/world.py`、`skill.py`、`session.py`、`runtime.py`、`parameters.py`、`stability_report.py`、`acceptance_report_v8.py`、`stable_runtime.py`、`src/vrc_agent/cli.py`、新增测试与 `docs/validation/T08_world_v3_confidence.md`、`docs/TASKS.md` 第110节。
- 提交：`aa25324`（世界v3识别门槛候选，父 `32fd234`），工作树干净。
- 性质：只读定向复核。未启动 live、未 ARM、未调用付费模型、未发送真实输入、未读取密钥；未修改产品代码/策略/阈值/世界/配置/提示。
- GraphFlow：本机 `graphflow_context` 只返回 symbol anchor（overlap≈11%），无可用摘要/规划/索引结果；本记录不引用它作为证据。
- 结论：**声明主体未能证伪、无阻断项**。以下 N1–N7 为非阻断观察。**不是冻结，也不是实机验收**。

## 1. 结论

1. 世界 v3 只把 G2/G4 的识别门槛降到 0.7，G1/G3/G5 保持 0.85；v1/v2 仍按 `(version, group)` 显式映射 0.85，没有「缺字段就用 0.85」的回落。
2. v3 是独立候选：语义摘要 `3e7b4566…9885f` 与 sidecar、loader 三方一致；原始文件字节 SHA256 `1ab60148…bb0b33` 只作文件指纹，二者未混称；v1/v2 世界与稳定性 v1–v9 文件在本提交中零改动。
3. `run_parameters.target_min_confidence` 按「世界版本 + 摘要 + 任务地标」重算并严格比对；伪造、缺失、跨世界摘要均被拒；旧 v1/v2 无字段报告仍按显式 0.85 通过，接受范围没有放宽。
4. live 在会话装配与 `SupervisedNavigation` 构造两处均在资源创建前拒绝候选世界；只读 plan 正常且不创建资源。
5. 其它识别谓词、到达几何、clearance 的 0.9＋几何、运动包络、4000ms/180s/40步/3attempt、预热2对、30s租约与急停归零均未改；`control/`、`configs/`、`acceptance_v8.py` 在本提交中零改动。
6. 0.7 会把 0.72–0.85 区间放入识别通过面，但 0.55 样本仍全部拒绝；其余谓词未放宽，且全部 42 条观测仍带 hazards/uncertainties，到达不会自动放行。

## 2. 方法

- 自建探针：`.runtime/verify/t08_world_v3_review_probe.py`（不 import 实现方测试与夹具代码，独立构造世界/实体/场景/报告与损坏样本）。
- 结果：**192 项检查，0 失败**，exit 0；工件 `.runtime/verify/t08_world_v3_review_probe.json`、日志 `…probe.log`。
- 探针分组：S0 基线/变更面；S1 摘要与文件纪律；S2 门槛语义与契约；S3 候选纪律与资源前拒绝；S4 报告重算与历史兼容；S5 边界、42条观测复现与独立性；S6 文档/任务声明核对。
- 四项门禁由复核者在本提交上复跑（见第4节），未沿用实现方日志。
- 只读入口复跑：默认 plan（世界 v1）与显式 `--world-version v3` 均 exit 0；v3 plan 未创建任何状态目录或控制器资源。

## 3. 逐项复核

### 3.1 摘要与版本纪律

| 检查 | 结果 |
| --- | --- |
| v3 语义摘要 = sidecar = loader | `3e7b4566cfabcb6d2527c5d221b4e9d0427b542adb8d6183bc13cf90d3d9885f` |
| v3 原始文件 SHA256 | `1ab6014836c030a8f9e0fc29d06ab54c2df9bee2f6a96d0f6fa338e245bb0b33`，与语义摘要不同且未被混用 |
| v3 结构 | schema 3、`vrchat-home-live-v3`、`REQUIRES_FIELD_CALIBRATION`、5 地标/20 任务 |
| v3 与 v2 的差量 | 仅 `version`/`schema_version`/`status`/`source_world_v2_digest` 与 5 个 `min_confidence`；其余逐字段相等 |
| 提示 | v3 五份提示逐一等于 v2（`calibrated_prompt` 相等） |
| 血统 | `source_world_digest` = v1 `dda1d3b1…`，`source_world_v2_digest` = v2 `b65b3dab…` |
| 旧世界 v1/v2 | json/sidecar 在本提交中零改动，语义摘要不变 |
| 稳定性 v1–v9 | 18 份 json/sidecar 在本提交中零改动；v9 仍 `c9fb45ee…` 且 `FROZEN` |
| 行尾 | v1/v2/v3 均 CRLF（v3 311 行），`.gitattributes` 为 v3 两文件加 `-text` |
| 现场配置 | `configs/navigation-live.toml` 字节未变：`stable-inference-v9`、`dry-run`、`lease_ms=30000` |

### 3.2 门槛语义

- 旧版：`LEGACY_MIN_CONFIDENCE` 对 v1/v2 的五个组全部显式 0.85；v1/v2 地标没有 `min_confidence` 字段，读取路径按「契约版本 + 组」查表，不存在缺字段回落。
- v3：`min_confidence` 必填、`strict=True`、`0<=v<=1`。`True`/`False`/整数 `1`/`0`/字符串/1.5/−0.1/`None`/NaN/Inf/缺字段全部被拒；用 `model_construct` 绕过校验后调用读取函数会抛 `AttributeError`，不会静默取默认值。
- 越权修改：到达参数、描述、提示、标签、任务、v1/v2 血统、状态、schema、版本共 10 类改动均被 `confidence_only_revision` 拒绝（错误文本为 “world v3 may only declare landmark confidence thresholds” 或“predecessor mismatch”）。
- 冻结态：把 status 改为 `FROZEN` 后模型可校验、`require_live()` 放行，但语义摘要随之改变（`1da67714…`）——冻结时必须同步重生成 sidecar，属既有冻结纪律而非缺陷。
- clearance：`CalibratedSkill.clearance` 与 v9 生产用的 `AcceptanceSkillV8.clearance` 仍是独立 `confidence>=0.9`＋`x1<=0.35`/`x2>=0.65`/`y1<=0.7`/`y2>=0.9` 几何，本次未改。

### 3.3 候选纪律

- 会话装配：v3 `mode="dry-run"` 返回 `plan-only`/`sends_live_input=false`/摘要 `3e7b…`，且在任何资源创建前返回；把 `mode` 改为 `live` 后，在 `build_provider`/`ControllerProcess`/采集/推理任一构造前以 “candidate world requires field calibration and freezing before live” 拒绝。
- 运行时装配：`SupervisedNavigation` 对候选 v3 同样在构造期拒绝；`FROZEN` 版本可构造，且技能门槛取该地标值（G2=0.7）。
- 只读复核：全仓仅 `session.py`、`runtime.py` 各一处 `.require_live(` 调用；world 模块无环境变量旁路。
- 对照：同一装配流程下 v2（已冻结）可通过世界门禁并继续到 provider 构造；v3 在资源前即止。未发现“候选可 live”的旁路。

### 3.4 报告重算与历史兼容

| 用例 | 结果 |
| --- | --- |
| 真实 v9/v2 live 报告（无新字段） | 通过 |
| 同报告显式 `target_min_confidence=0.85` | 通过 |
| 改 0.7 / 0.85+1e-10 / `True` / `"0.85"` / `None` | 全部 “target confidence threshold mismatch” |
| 删除字段的旧 v2 报告 | 仍通过（历史隔离） |
| v3 dry-run 报告 target=0.7 | 通过 |
| v3 报告 target=0.85 | 拒绝（recomputed 0.7） |
| v3 报告缺字段 | “missing world v3 target confidence threshold” |
| v3 声明配 v2 摘要、v2 声明配 v3 摘要 | 均 “confidence world digest mismatch” |
| v3 报告声明 live | 候选世界拒绝 |
| 纯重算 | v3/G4→0.7，v2/G2→0.85，旧报告→`None` |
| `validate_run_parameters` | 字段存在时必须与重算值严格相等；不存在时不额外要求 |
| 接受回放 | 世界映射含 v3，`AcceptanceSkillV8` 从地标取值（G4=0.7），SEMANTICS 仍 `target-path-v8` |

`validate_report_confidence` 在稳定性报告校验入口最先执行；`validate_run_parameters` 对 v6–v9 的 `run_parameters` 再做严格比对。

### 3.5 边界与独立性

- 识别门槛：G2/G4 在 0.70 通过、0.699 拒绝、0.55 拒绝、0.72 通过、0.85 通过；G1/G3/G5 在 0.849 拒绝、0.85 通过、0.72 拒绝。
- 同一 0.72 置信度：G2（0.7）通过、G1（0.85）拒绝；v2 同条件拒绝。
- 观测复现：`tests/fixtures/world_v3_confidence.json` 的 3 个来源工件哈希逐一复核一致；42 条观测分布 `{0.72:34, 0.82:3, 0.90:5}`，全部六项识别谓词通过；旧 0.85 下通过数为 G2 5/21、G4 0/21；3 条更远 0.55 样本全部拒绝。
- 失败闭合：重复标签、非法 bbox、证据 id 不匹配、完全遮罩、隐藏窗口、帧引用/会话/纪元不符均拒绝。
- 到达/几何/包络：到达 `model_dump` 与 v2 逐字段相等；`target_size` 相等；cautious 0.2/200ms、field 0.4/500ms；v9 预算 4000ms/180s/40步/3attempt/2对预热。
- 信号语义：hazards/uncertainties 在场时识别可通过，但 `arrival_permitted` 为假；声明仍为 recognition/motion `record_only`、arrival `record_review_required`。
- 生产链路：v9 运行时在 `stable_runtime.py:545` 用 `self.world.landmark(self.task.group)` 构造 `AcceptanceSkillV8`，因此 v3 门槛会真实进入决策与接受回放，不是只改报告字段。

### 3.6 现场反事实与风险焦点

- 门槛降低的实际面：G2 从 5/21 变为 42/42、G4 从 0/21 变为 42/42（42 条为 14 固定帧×3 轮的内存复现，不是 42 张独立图像）。
- 0.72–0.85 区间被纳入识别通过面；0.55 仍拒绝，说明降低有边界而非“全部放行”。
- 未发现其它判据放宽：本提交只把技能构造里硬编码的 0.85 换成地标值，clearance 0.9＋几何、到达、运动包络、自动 SUCCESS/接受语义均未动。
- G4 的 21 条观测 clearance 为 0/21，因此 0.7 只解决“认识目标”，不保证能前进；G2 clearance 8/21。该差别应写入现场复验清单，而不是当作代码缺陷。
- 全部 42 条观测均带 hazards 与 uncertainties，故门槛降低不会制造自动到达或自动 SUCCESS。

## 4. 四项门禁（复核者复跑，`aa25324` 干净树）

```text
.venv/Scripts/python.exe -m pytest -q
1459 passed in 308.09s (0:05:08)          # exit 0；.runtime/verify/t08_world_v3_review_pytest.log

.venv/Scripts/python.exe -m ruff check src tests
All checks passed!                        # exit 0

.venv/Scripts/python.exe -m ruff format --check src tests
163 files already formatted               # exit 0

.venv/Scripts/python.exe -m pip check
No broken requirements found.             # exit 0
```

只读入口：

```text
navigate-live --task G2-1 --config configs/navigation-live.toml                 # exit 0，默认 plan-only
navigate-live --task G2-1 --config configs/navigation-live.toml --world-version v3
mode=plan-only, sends_live_input=false,
suite_digest=3e7b4566cfabcb6d2527c5d221b4e9d0427b542adb8d6183bc13cf90d3d9885f,
world=vrchat-home-live-v3 REQUIRES_FIELD_CALIBRATION；未新建成状态目录
```

## 5. 非阻断观察

- **N1 基类门禁 fail-open**：`LiveWorld.require_live()` 对 v1/v2 是 no-op（文档写明新候选必须显式覆盖）。当前 v3 已覆盖，但未来若新增世界类型忘记覆盖会静默放行 live。建议后续把基类改为「非 v1/v2 必须显式覆盖或抛错」。
- **N2 旧世界重贴标签仍可过**：把 v2 报告改成 v1 并换 v1 摘要仍能通过校验（v1/v2 结构相同，属既有局限）；v3 因候选门禁不能这样利用。本提交未扩大该面。
- **N3 错误顺序变化**：`validate_report_confidence` 在 schema/design 校验之前执行，畸形旧报告可能先报世界/摘要错误而非结构错误；仍为拒绝路径，仅影响排错可读性。
- **N4 证据工件在 `.runtime/`**：42 条观测与 3 个来源工件未入库，仅文档记录哈希；复算依赖本机 `.runtime/verify` 与 `.runtime/live` 原件。
- **N5 G4 的 0.7 不等于可前进**：G4 clearance 0/21，现场即使识别通过也可能只能等待；不要把本候选当作 G4 运动解锁。
- **N6 严格浮点**：整数 `1`/`0` 被拒（`strict=True` 的预期行为），未来脚本生成 v3 JSON 时必须写 `0.7`/`0.85` 而非整数。
- **N7 门禁耗时**：实现方记录 295.33s，复核者 308.09s，属机器差异；通过数一致（1459）。

## 6. 未覆盖

- 未做任何 live、ARM、真实模型调用或真实画面检查；42 条观测的“识别可通过”是数值/内存复现。
- 未重拍 G1/G3/G5，也未证明其 0.85 在真实画面上的分布；保持 0.85 是“不放松”，不是“已验证足够”。
- 未执行固定 20 次任务，不主张 ≥16/20；`arrival_geometry_met` 与人工逐条确认仍按第93节。
- 未验证现场标定/延迟/白天光照变化对 0.7 的实际误认率。

## 7. 边界

本记录只说明：在 `aa25324` 的离线证据与代码路径上，第110节及 v3 提案的声明主体未能证伪、无阻断项。它不构成对 v3 的冻结授权，也不构成实机验收；v3 仍是 `REQUIRES_FIELD_CALIBRATION` 候选并拒绝 live。下一步应由用户决定是否冻结（冻结需同步重生成 sidecar），再安排现场复验；现场仍保持 v9＋世界 v2＋dry-run。
