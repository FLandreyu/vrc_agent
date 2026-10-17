# T08 世界 v2 独立复核（ADR-009，2026-10-17）

复核者 DSH / Flash；实现者 Codex / root。对象为 TASKS 第 60/61 节与 [T08_world_v2.md](T08_world_v2.md)，新世界 `vrchat-home-live-v2`（世界 schema 2），语义摘要 `b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0`，JSON 字节 SHA256 `98798198c6dffce329444a7be4c808e8e0046d9ca7b4270ee4897675db565276`；前代 v1 摘要 `dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335`。

本次独立编写并实际运行探针 `.runtime/verify/v_t08_world_v2_review.py`（106 项条件检查），未导入实现者测试、夹具或旧复核探针；不改产品源码、世界 JSON/sidecar、稳定性策略、配置或实现者测试。探针只用内存合成场景与临时报告目录，通过真实 CLI 子进程与 `load_live_world`/`summarize_reports`/`build_scene`/`calibrated_prompt` 等产品入口做黑盒与半白盒对照。未启动 live、未操作本机热键/ARM、未付费、未读取密钥、未发送真实输入；只写 `.runtime/verify/`。GraphFlow 本会话仅能给出 symbol anchor，按项目约定不作证据，全部事实来自源码与文件字节核对。

## 0. 结论

**第 60/61 节与 `T08_world_v2.md` 的可验证声明主体未能证伪，本轮离线独立复核通过；发现三项非阻断观察 N1–N3。**

- 哈希链：v1/v2 世界摘要与 sidecar 一致；v2 的 `source_world_digest` 恰指 v1；两版参考 PNG 的 SHA256、尺寸 2160×1440 与本地文件逐一相符；PNG 未入库、世界目录 git 跟踪恰 5 个文件；v1 JSON/sidecar、v1 五组提示、稳定性 v1–v5、`configs/navigation-live.toml` 与 `control/` 在交付提交中零改动。
- 来源切片：`T08_stability_v5_field.md` 第 2–3 节原始字节切片 1438 字节，起止标题字节精确，SHA256 `91b09394db1f28ce549db5dd3b7121e3b7d174e01124f61070b35ba19c68d62e` 与世界 `calibration_source_sha256` 一致；切片内含 `41B2D902`/`51A78588`/`0.54`/`0.328`/`0.09`。v1 仍指向自己的标定源，两版不同源。
- G3 判据：v2 只把到达改为 box **0.50/0.52**，其余到达字段（centre 0.12 等）不变；现场框 0.54×0.57 在 v2 到达、在 v1 不到达；恰好 0.50×0.52 到达，0.499 宽或 0.519 高被拒；`arrived(entity)` 签名不含激活标志。
- G5 判据：lower_half 0.6/0.25/0.3 逐字段不变，50 组随机框在 v1/v2 下 `target_size`/`arrived` 完全相同；现场框 target 1.4、覆盖率 0.6552（文档 0.328 为未除以 0.5 的全画面面积）；hazard/uncertainty 仍阻断。
- `prompt_addendum` 注入面：v2 提示 = v1 提示字节 + 固定标记 + addendum，addendum 在安全句之后；只有 G3/G5 非空（395/335 字符，与提案引文逐字相同），其余为空串；动作/权限类字段被丢弃并记录，不能变成 motion、不能改写世界版本或到达结论；提示只是地标与冻结世界的纯函数。
- v1 隔离与默认值：默认世界仍 v1；v2 顶层只比 v1 多 `source_world_digest`；缺失/未知/19 任务/跨代互斥均拒绝；v1 提示未被 v2 改写。
- 报告复验：v2 报告在 v2 接受、在 v1 拒绝；v1 legacy 与 v1 标签报告在 v1 接受、在 v2 拒绝；错版本、缺 `world_version`、v1 摘要配 v2 标签、混合批次、重复尝试、dry-run 报告、`sends_live_input=false`、坏 telemetry 均被判 invalid；20 份全成功时离线 `threshold_met` 才为 True；CLI `navigation-report --report-dir ... --world-version v2` 走同一复验。
- 双世界候选拒绝与 ARM 门槛：v5 候选在两个世界下都在供应商/控制器创建前 exit 2（`cannot enable live navigation`），不创建 state 目录、无 DISARMED；F8/F9 本机热键注册与提示未变；live 配置默认仍 dry-run/legacy，候选状态未改。

**本结论不是实机验收或冻结许可。T08 保持 IN_PROGRESS，v5 与世界 v2 均未由用户选版/冻结；20 次真实任务、真实供应商对新 addendum 的复测、G3 激活前停止与 G5 水面排除仍未执行。**

## 1. 逐项对照与对抗结果

| 复核项 | 自建反例/证据 | 结果 |
| --- | --- | --- |
| 版本与摘要 | A01–A03、A16、B14、B15、B16、B18、B19：两版完整摘要与 sidecar 一致；独立 canonical-json 重算得 `b65b3dab…`，原始字节摘要不等于该语义摘要；`source_world_digest` 指向 v1；未知版本拒绝；默认加载 v1；v1/v2 模型互不接受对方 JSON | 符合声明；v2 与 v1 摘要隔离 |
| 参考图哈希链 | A10–A12、B03、B04、B22、B24：两 PNG 本地字节 SHA256 与世界字段一致、均 2160×1440；PNG 未入库；交付提交对字段文档的改动仅表格排版；两段 addendum 与提案引文逐字相同，G1/G2/G4 为空 | 符合声明；新增图只在本地，无法再证“同一物理画面”，只能证字节与声明一致 |
| 来源切片 | A06–A09、A13–A15：切片 1438 字节、起止标题字节精确、含七个现场数值；v2 `calibration_source` 指向字段文档第 2–3 节且 SHA 等于切片；v1 保持自己的标定源（`31c5e598…`） | 符合声明；重排问题见 N1 |
| G3 判据 | C01–C10、B05：现场框 0.54×0.57 在 v2 到达、v1 不到达；0.50×0.52 到达；0.499 宽/0.519 高拒绝；centre 0.12 边界正确；`arrived` 无激活参数；规则仍是普通 box | 与第 60 节数值一致；恰好等值浮点脆弱性见 N3 |
| G5 判据 | D01–D11、B06：字段逐字节未改；现场框 target 1.4、覆盖率 0.6552→比值 2.184；50 组随机框两版结果完全相同；hazard/uncertainty 阻断 | 符合声明；覆盖率是第三约束，见 N2 |
| prompt 注入面 | E01–E12、B09–B12：组合式 `v1 prompt + marker + addendum` 精确；标记只在 G3/G5；顶层 `confidence` 被忽略（场景置信度取实体最小值）；`actions/lease_id/role` 被记录为忽略项且不进实体、不产生 motion；`SUCCESS`/`arm` 不进场景；提示由 session 从冻结世界组装，模型载荷不能改提示或世界版本 | 未得到经由模型输出的动作/权限注入 |
| v1 隔离与默认 | A04、A05、B10、B13、B17、B20、B21、G07、G08：v1 世界/提示字节不变；v2 非 addendum 提示与 v1 逐字节相同；19 任务或改 addendum 会被摘要/模型校验拒绝；默认 dry-run/legacy | 符合声明 |
| 报告世界版本复验 | F01–F23：矩阵见上文；`threshold_met` 需 ≥16 且每任务恰一份且无 invalid；混合批次 20 个 SUCCESS 仍 threshold False；v2 下 stability schema 仍校验；CLI 汇总带 world_version/suite_digest | 报告不能跨世界混用或借小规则复验 |
| 候选拒绝与 ARM 门槛 | G01–G06、G09、G10：v5 仍 REQUIRES_FIELD_CALIBRATION；两世界下 exit 2 且发生在 provider/controller 之前；F8/F9 注册与提示未变；v2 计划仍标现场标定 | 门槛未被 v2 绕过；本轮未操作真实 ARM |

## 2. 非阻断观察与最小反例

### N1：交付提交重排了来源切片所钉的字段文档

- 位置：`docs/validation/T08_stability_v5_field.md` 表格分隔行（`| --- | --- |` → `| --- | ----------------- |`），改动在交付提交 `dfb63b2` 内。
- 证据：探针 A13 把 HEAD 与 `HEAD~1` 的该文件按“去空白 + 分隔破折号归一”比较，结果完全相等（语义与数字未变）；A14 用 `git show HEAD~1:docs/validation/T08_stability_v5_field.md` 重算同一切片，SHA 不等于 `91b09394…`，说明切片钉的是重排后的字节，且重排位置落在 `## 2.` 到 `## 4.` 区间内。
- 影响：当前提交内哈希链自洽（世界字段 = 现文件切片），不影响运行语义或现场判据；但若将来有人用交付前的文档快照核对，切片会不匹配。建议今后来源文档的排版改动与切片重新固化分开提交，或在世界 JSON 注释/交接中写明“切片以某提交为准”。

### N2：G5 的 `min_lower_coverage` 是独立的第三条约束

- 位置：`src/vrc_agent/live_navigation/skill.py:27` 起的 `target_size`；规则 `lower_half` 0.6/0.25/0.3。
- 最小反例：框 `[0.2,0.375,0.8,0.625]`（恰好 0.6×0.25）宽高都达标，但下半覆盖 `0.6×0.125/0.5=0.15`，比值 0.5<1 → 未到达。等价地，当 `y1≤0.5` 时到达还要求 `w×h≥0.3`：0.6 宽需约 0.5 高，0.75 宽需约 0.4 高。现场框 0.54×0.57 的 `w×h=0.3078`，通过。
- 判定：该约束继承自 v1、v2 未改（50 组随机框两版结果一致），第 60 节也记录了 0.6552 与 0.328 的口径，因此列非阻断。但文档若只写 0.6/0.25/0.3 三个数字，现场容易误以为“宽高够就到达”；建议在交接中补一句“0.6 宽需配约 0.5 高”。

### N3：恰好等于阈值的浮点边界仍不可靠（继承 v1）

- 位置：同一 `target_size`；比值用浮点除法。
- 最小反例：G1 的 0.80×0.45 等值框在 v1/v2 下 `target_size=0.9999999999999999<1` → 不到达（探针 C10）。v2 新增的 0.50×0.52 等值框恰好通过（C02），说明新边界本身可用，但“恰好等值”不是稳定判据。
- 判定：继承 v1，不是 v2 回归，v2 的判据值没有因此失效；现场/合成判据不应使用“恰好等于阈值”的框，应以略高于阈值的实测框为准。

## 3. 已声明的限界与独立性

- 本轮只做离线复核：没有启动 live、没有真实模型推理、没有本机 ARM/热键、没有付费调用、没有读取密钥；现场帧、延迟、光照/姿态变化、G3 激活前停止、G5 水面误框均未验证。
- 参考 PNG 只能核对“本地文件字节 = 世界声明哈希”，且二者都来自实现者的现场采集；本轮没有独立采集同场景新帧，不能证明截图来源、时间或未被挑选。
- 世界 JSON 的语义摘要由产品反序列化器重算（A02 用独立 canonical-json 复算，A03 记录原始字节口径），并非从原始规格文本独立重建；若实现者与复核者共用同一 schema 理解，语义层同错不在此机制覆盖内。
- 报告复验用的是探针构造的报告，不是真实供应商运行产物；`verified_success` 的证据链、时间序、实体标签等均按产品契约从零合成，只证明“该实现会拒绝/接受哪些形状”，不证明真实现场能产出这些形状。
- 探针没有修改实现者测试；四项门禁由复核者独立运行（见第 4 节），但门禁全绿不替代探针反例。
- 独立性：复核者未参与世界 v2 实现；实现者 Codex 此前实现过 v2/v5 基底，因此独立性针对本次世界 v2 交付件，不宣称对共享代码从未参与。同机同工作树，以文件摘要锚定，无 Git 提交锚点。

## 4. 工件与门禁

- 主探针 `.runtime/verify/v_t08_world_v2_review.py`（SHA256 `b6c88bfbdf9c610e32ce45d72d43ad6176d881c877978bc904babfb073601a3b`），输出 `.runtime/verify/v_t08_world_v2_review_report.json`（SHA256 `4bbe760b69f8a89c6b15d77382f3c37717b8efef694f1852f7f3994d3c416280`）：**106 个条件检查、0 失败**（A16 / B24 / C10 / D11 / E12 / F23 / G10），记录 commit `dfb63b2`、两世界摘要与逐项 detail。
- 门禁日志 `.runtime/verify/t08_world_v2_review_gates.log`（SHA256 `c4e3561cc979f8a01dd9ee10e6901e97ac9ca6579b6876a8922a1d9eb67fb0dc`）。

| 四项门禁（复核者实际运行，项目虚拟环境） | 结果 |
| --- | --- |
| `python -m pytest -q` | **991 passed in 164.59s (0:02:44)** |
| `python -m ruff check src tests` | All checks passed! |
| `python -m ruff format --check src tests` | 133 files already formatted |
| `python -m pip check` | No broken requirements found. |

## 5. 未覆盖（不构成验收）

未采集真实客户端图像；未现场复验 G3 激活前停止、G5 池面/水面排除与周边边界；未用真实供应商确认新 addendum 是否被遵循、是否改变 token/延迟；未验证五组姿态配对、遮罩与目标交叠、多显示器/DPI 缩放；未执行固定 20 次任务，因此没有成功率、接管率或延迟数字。v5 稳定性策略的现场复验（第 59 节）与本次世界 v2 复核相互独立，本轮不追认其实机结果。

## 6. 复现

```powershell
.venv/Scripts/python.exe .runtime/verify/v_t08_world_v2_review.py
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
.venv/Scripts/python.exe -m pip check
```

探针每次重建 `.runtime/verify/v_t08_world_v2_review_report.json`；重定向日志时使用新文件名，保留本轮工件，不覆盖旧复核证据。CLI 子进程在 UTF-8 管道下运行（`PYTHONIOENCODING=utf-8`），否则含中文的计划输出会按 GBK 字节写出而无法解码。

## 7. 下一步

1. N1 可作为文档/提交纪律问题另行处置：再次改字段文档时同步重算并固化来源切片，或明确“切片以某提交为准”。N2/N3 为继承事实，建议写进现场交接而非改判据。
2. 由用户选择并冻结版本组合（`local-rgb-stability-v5` + `vrchat-home-live-v2`）；本轮没有替用户选版或改策略状态。
3. 冻结后做现场复验：G3 激活前停止与木框可见性、G5 排除池面/水面与周边、五组配对静止/移动、延迟测量；确认新 addendum 未引入无关框或漏框。
4. 最后执行固定 20 次真实任务（接管/超时/卡住计失败，至少 16/20），在此之前 T08 保持 IN_PROGRESS，不得用本轮离线复核宣称实机验收。
