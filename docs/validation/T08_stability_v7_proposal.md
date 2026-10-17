# T08 稳定性 v7 候选提案（乙，2026-10-18）

**这是提案，不是冻结，不是实机验收。** 负责人 Codex/root；依据 TASKS 第81节及用户明确任务，产出隔离的候选实现与离线证据，等待另派复核、现场复验和用户冻结。生产配置仍选择 v6/dry-run；v1–v6文件、世界与提示不改，不涉及第82节/T13客户端提案。

## 1. 现场证据与动机

第81节 pilot4：after_inference 0 px通过，before_submit仅protected_changed=2px即中止，1次推理3.04s且没有动作。旧pilot2在1/20/28px拒绝后有重试恢复，故甲单独恢复三次有界重试。

现场5对原始BGRA静止帧：未遮罩变化均值12.4px/对、单对最大54px；浣熊框均值0.4px。地板forward_clearance外扩32px后约2008×584=1,172,672px，约占2,848,256保留像素的41%。将如此大的证据区要求零变化，真实渲染抖动会频繁拒绝。JPEG往返确实放大噪声，但产品比较始终用原始BGRA，编码器不是本问题的因果来源；不能把改JPEG参数当作判据修复。

## 2. 规则原文与明确范围

用户授权的规则：

> stable 条件 = 非黑帧 ∧ 全局 ≤5% ∧ 目标实体保护区变化=0 ∧ 非目标证据框变化 ≤ 申明预算；报告逐框记录 changed 计数与归属标签，禁止用全局数值冒充。

候选策略以冻结v6原字节为底，仅改version/status并新增显式字段：`zero_tolerance_labels=["home_raccoon"]`、`evidence_changed_ppm=1000`。metric名称保留v6原值；比较器按严格策略版本显式分派v7，禁止将新键或新判据回填v6。5%/Δ2/遮罩/预热2对/4000ms/180s/40步/motion_profiles保持不变，没有隐式容差默认。

逐实体先取原bbox与外扩32px保护框，沿用exclude_masked语义：只数保护框内未遮罩像素；每个完全不可见原框或保护框拒绝。目标label命中清单时changed必须0；其他每个框分别满足 `changed*1_000_000 <= visible_px*1000`，采用整数比较，不向上舍入。重叠区域分别计入各框，命中目标保护框的变化仍触发零容差，不能借地板容差放行。

当前目标零容差清单仅home_raccoon，故只针对G2候选：**不能声称G1/G3/G4/G5目标也保留零容差**。若未来扩展目标清单，须新的显式策略版本/摘要与用户授权，不能隐式把任意任务label当作目标改写策略。

## 3. 0.1%的依据、代价与限界

约1.17Mpx的地板框预算约1172px，超过本次静止样本最大54px约21.7倍，给渲染抖动和框轻微差异留工程余量；目标框仍0px，避免用地板大面积稀释导航目标变化。0.1%是本轮用户指定的工程候选值，**5对静止样本不能证明其最优、普适或安全**，不能据此承诺成功率。

代价：非目标框内小于预算的真实变化可能放行，小障碍、窄边缘或运动物体都可能落入这个盲区；保护区外仍有5%面积盲区，遮罩内无稳定性保证。像素稳定不等于路径无障碍，现有模型语义/clearance安全门槛与有限动作、急停监管仍必需，且不能弥补所有小障碍风险。浣熊框均值0.4也不是始终零，重试仍有必要。

## 4. 比较与报告契约

v7使用独立schema10，禁止冒充v6/schema9。逐框报告包含track_id、label、原框、保护框、可见像素、changed、适用预算及零容差归属。为能重算逐框数值，报告保存原始BGRA经Δ2比较后的二值变化位置的规范半开区间changed_runs；不是RGB颜色或图像上传。校验器从区间重算全局及每框计数、遮罩剔除、整数预算，再与推理源实体重算的标签/几何逐项核对；不能仅检查自报changed≤global。

差分位置证据仍可能暴露轮廓/活动位置，属于本地敏感几何证据；不得宣传为匿名或无隐私风险。本轮只用合成帧生成，候选拒绝live，未对真人现场帧生成/发送此报告。未来冻结前须评估保留策略、大小/耗时和用户知情，不能把位置证据当作无损原图或物理事件签名。报告只证明与给定差分证据一致；全套像素/摘要/证据一致重写不具密码学认证。

## 5. 必须现场复验的事项

- G2静止/移动/小障碍与目标变化的对照，至少覆盖远近、边缘、地板大框与浣熊小框；比较重试后的成功率、误拒与盲区，不能沿用五对样本作验收。
- G1/G3/G5遮罩与保护区交互，尤其部分遮罩、完全不可见原框、框外扩与重叠；当前候选不声明这些目标的零容差覆盖。
- 目标与地板重叠、最大54px及预算恰等/超1px、真实变化被0.1%放行的界限；新矩形大小变化会改变绝对预算。
- 新逐框计数/差分区间的比较与报告耗时/大小，180s/40步与30s基础租约＋仅人工续租下的闭环表现；不得自动ARM或自动续租以让试点通过。

独立复核→用户安排现场复验→用户明确冻结授权前，v7一律拒绝live，v6继续可显式live。本轮不启动live、不ARM、不付费调用、不发真实输入、不读密钥。摘要、测试、门禁和文件清单在候选实现完成后补入；GraphFlow工具不可用，未伪造context/plan/index。

## 6. 候选工件与离线验证

v7 SHA256：`fbebd9cbfd6d11988dd783ea83b7a4e8ef38c1b3ce236e435dcd20617cb2caf7`；sidecar为64位小写十六进制＋CRLF（66字节），新增v7的-text属性固定跨checkout字节。v6仍为`e46e0c2816c6f512fbd4fd5e5c60ff1ef3fc4d0560e0e8b29d3b6a6315caa513`。字节审计确认v7恰为v6原字节的两处字符串替换和两个顶层键追加，没有JSON整文件重排。当前loader只登记该候选声明；未来冻结须单独授权并同步冻结状态登记、摘要与门禁测试，本轮不预先接受FROZEN声明。

逐框字段为`track_id/label/entity_rectangle/protected_rectangle/visible_px/changed/zero_tolerance/budget_ppm`，proof另有`changed_runs`。差分区间有序、不重叠/相邻、坐标严格整数且不落遮罩；所有框计数及保护并集计数都重算，重复框不会稀释目标零容差。采用独立枚举像素的测试核对跨行区间/重叠并集计数，不仅用同一函数互相验证。

定向：与v6预算/重试联跑91 passed in 49.88s；候选最终37 passed in 13.59s，覆盖0.1%恰等/超1px、目标/地板重叠、5%边界142412/142413、黑帧、完全遮罩、伪造标签/几何/计数/见证/预算/缺字段、重算sidecar仍篡改声明（全部拒）、候选在资源前拒绝live、v6/schema9隔离和schema10不接受历史attempt预算缺省。schema10合成闭环复用G1测试夹具仅验证协议，不声明G1目标零容差或实机导航成功。

默认只读计划（`navigate-live --task G2-1 --config configs/navigation-live.toml --world-version v2`）退出0，`mode="plan-only"`、`sends_live_input=false`，suite_digest=`b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0`；原始完整输出`.runtime/verify/t08_retry_v7_plan.log`。只读计划不构成live验收；候选拒绝另由无真实资源的集成测试证明。

审计`.runtime/verify/t08_retry_v7_audit.json`为PASS：20个受保护文件字节一致（含v1–v6 JSON/sidecar、世界、现场配置、既有ARCHITECTURE和T13提案），TASKS原始字节前缀完全保留，故第82节未改。TASKS仅追加83/84节，因夹带既有用户未提交内容而不纳入两个提交。

乙四项门禁原始输出（均退出0）：

```text
1214 passed in 351.20s (0:05:51)
All checks passed!
151 files already formatted
No broken requirements found.
```

依次为项目解释器的 `-m pytest -q`、`-m ruff check src tests`、`-m ruff format --check src tests`、`-m pip check`；全量日志`.runtime/verify/t08_v7_candidate_pytest.log`。首次完整运行`1 failed, 1213 passed`，唯一失败为旧工厂闭集/未知v7断言未同步；登记v7、将真正未知v8作为拒绝反例后，原五项工厂回归`5 passed in 1.66s`，再完整重跑得到以上结果。基类对v2–v7的绕行仍全部拒绝，没有用定向结果替代完整门禁。

## 7. 两次提交的文件清单

甲提交`45af68f`（10文件）：

```text
AGENTS.md
docs/validation/T08_bounded_retry.md
src/vrc_agent/control/service.py
src/vrc_agent/live_navigation/attempts.py
src/vrc_agent/live_navigation/parameters.py
src/vrc_agent/live_navigation/stability_report.py
src/vrc_agent/live_navigation/stable_runtime.py
tests/integration/test_local_lease_integration.py
tests/integration/test_navigation_cost.py
tests/integration/test_navigation_retries.py
```

乙候选（19文件，提交号见交付回报/git log）：

```text
.gitattributes
AGENTS.md
docs/validation/T08_stability_v7_proposal.md
src/vrc_agent/config.py
src/vrc_agent/control/service.py
src/vrc_agent/live_navigation/attempts.py
src/vrc_agent/live_navigation/parameters.py
src/vrc_agent/live_navigation/runtime.py
src/vrc_agent/live_navigation/session.py
src/vrc_agent/live_navigation/stability.py
src/vrc_agent/live_navigation/stability_report.py
src/vrc_agent/live_navigation/stability_v7.json
src/vrc_agent/live_navigation/stability_v7.py
src/vrc_agent/live_navigation/stability_v7.sha256
src/vrc_agent/live_navigation/stable_runtime.py
tests/integration/test_stability_v7_runtime.py
tests/unit/test_stability_v3.py
tests/unit/test_stability_v3_review_regressions.py
tests/unit/test_stability_v7.py
```
