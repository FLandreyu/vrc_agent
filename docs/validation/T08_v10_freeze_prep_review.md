# T08 v10 冻结前置增量——ADR-009 定向复核

- 复核对象：`af5ab1e`（“v10 冻结前置：放开状态闭集”，对应 `docs/TASKS.md` 第118节与
  `docs/validation/T08_v10_freeze_prep.md`），父提交 `979e6ee`，其父 `5ae2a68`。
  复核时 `HEAD=af5ab1e`，工作树只有 `docs/validation/T08_v10_freeze_prep.md` 的 3 行
  表格空白对齐差异未提交（不改变任何语义）。
- 复核结论：**声明主体未能证伪、无阻断项**。
- 边界：本轮**不是冻结授权、不是实机验收**。未启动 live、未 ARM、未发付费模型请求、
  未发送真实输入；未改产品代码、策略、控制、配置、世界、提示。复核者只新增本记录、
  第119节与 AGENTS.md 状态胶囊，以及 `.runtime/verify/` 下的探针与日志。

## 1. 结论摘要

| 项 | 声明 | 判定 |
| --- | --- | --- |
| (a) 改动最小性 | 只放宽 v10 `status` 取值集合 | 未能证伪 |
| (b) 闭集完备性 | 围栏外取值与其它任何键篡改仍被拒 | 未能证伪 |
| (c) live 门槛 | 未放松，候选仍拒绝 live | 未能证伪 |
| (d) 测试真实性 | 未删断言，新增用例真跑 loader | 未能证伪 |
| (e) 不变项 | v1–v9 策略/sidecar/世界/配置/提示逐字节未改 | 未能证伪 |
| (f) 独立复跑 | 自建探针 + 四项门禁 | 未能证伪（含 1 次偶发，见第3节） |

## 2. 逐项证据

### 2.1 (a) 改动最小性

`git show af5ab1e --stat` 只有 4 文件 `+126 / −4`：`docs/TASKS.md`（+11）、
`docs/validation/T08_v10_freeze_prep.md`（新增 76 行）、
`src/vrc_agent/live_navigation/stability.py`（9 行）、
`tests/unit/test_path_scan_v10.py`（+34）。`git diff --name-status af5ab1e^..HEAD -- src`
只回 `M src/vrc_agent/live_navigation/stability.py`。

v10 分支新代码原文（`src/vrc_agent/live_navigation/stability.py`）：

```python
expected_v10 = {
    **load_policy("v9")[0],
    "version": "local-rgb-stability-v10-candidate",
    "status": policy.get("status"),
    "path_scan": PATH_SCAN,
}
if (
    type(policy.get("status")) is not str
    or policy["status"] not in {"REQUIRES_FIELD_CALIBRATION", "FROZEN"}
    or json.dumps(policy, sort_keys=True, allow_nan=False)
    != json.dumps(expected_v10, sort_keys=True)
):
    raise ValueError("invalid v10 path scan candidate declaration")
```

同一“取版本主体 → 覆盖版本名/status/path_scan → 严格相等 + 闭集”的三段式已是 v6/v8/v9
的既有做法（同文件 `stability.py` 第 35/51/67/97 行各有闭集）；v7 分支仍把 status 钉为
常量（v7 从未冻结）。**唯一被放宽的是“status 的取值集合”**：其余 29 个键仍与 v9 主体
逐键严格相等，`path_scan` 仍与模块常量 `PATH_SCAN` 相等，loader 白名单仍
`{"v1"…"v10"}`、sidecar 摘要比对仍在严格相等之前。

### 2.2 (b) 闭集完备性

自建探针（`.runtime/verify/t08v10_freeze_prep_probe.py`，59 项 0 失败）实测：

- 15 个非法 status 全部被拒：`"FROZEN_APPROVED"`、`"frozen"`、`" Frozen"`、`"FROZEN "`、
  `""`、`"REQUIRES_FIELD_CALIBRATION "`、`"REQUIRES-FIELD-CALIBRATION"`、`None`、`1`、`0`、
  `1.0`、`True`、`False`、`["FROZEN"]`、`{"value": "FROZEN"}`。说明：探针只断言“被拒”这一
  结果；`None`/数字/布尔/容器在集合成员判断中本就不属于 `{REQUIRES_FIELD_CALIBRATION,
  FROZEN}`，故 `type is not str` 属纵深防御而非唯一拦截点（不据此项宣称双重独立拦截）。
- 两个合法值 `REQUIRES_FIELD_CALIBRATION` 与 `FROZEN` 都能加载，且 `load_policy` 返回的
  digest 与重算值一致；缺 `status` 键被拒。
- 除 status 外仍有 19 个单键篡改被拒（`max_changed_ppm`、`pixel_tolerance`、
  `task_timeout_s`、`inference_ms`、`max_actions`、`freshness_ms`、
  `max_inference_attempts`、`frame_size`、`protected_margin`、`black_threshold`、`metric`、
  `version`、`path_scan.enabled/max_scans_per_task/yaw_step/duration_ms/
  trigger_denied_attempts/alternate_direction/post_scan_requires_new_observation`），
  另有增键（`extra_key`）与把 `warmup_pairs` 改为 0 被拒；删键未单独构造，但严格相等
  逐键比较同样会拒。即：**放开 status 没有顺带放宽任何判据键**。
- 重复 `status` 键（`{"status":"FROZEN", <候选原文>}`）经 `json.loads` 后者优先，解析回
  候选值，仍走候选语义；不构成绕过（若换成前者优先，则会变成“原文被覆盖”，两种行为都
  不改变“必须严格等于 v9 主体 + 3 键”的约束）。

### 2.3 (c) live 门槛未放松

两道运行期门槛都仍在 `status == "FROZEN"` 上：

- `src/vrc_agent/live_navigation/stable_runtime.py:38`（基类 `StableNavigation.__init__`，
  V10 先 `super().__init__` 故必经）：
  `if self.client.config.mode == "live" and self.policy["status"] != "FROZEN":
  raise ValueError("candidate stability policy cannot enable live navigation")`。
- `src/vrc_agent/live_navigation/session.py:83`（在 build_provider / ControllerProcess /
  live_capture 之前）：
  `if stable and load_policy(policy_version)[0]["status"] != "FROZEN":
  raise ValueError("stability policy requires field calibration and a newly frozen version;
  candidate policies cannot enable live navigation")`。

探针用真实入口验证：monkeypatch `load_config` 为 v10 live 配置、把
`build_provider`/`ControllerProcess`/`live_capture`/`Perception` 全换成 `pytest.fail` 后，
`session.navigation_entry(...)` 抛 `ValueError(match="field calibration")` 且未创建任何
资源；dry-run 装配 v10 后把 `client.config.mode` 改为 `live` 再构造，抛
`ValueError(match="candidate")`，calls 与 messages 均空。全仓 `["status"]` 消费点枚举：
`session.py:83`、`stable_runtime.py:38`、`stability_report.py:271`（报告层：
`policy["status"] != "FROZEN"` 且 `mode == "live"` 或 `sends_live_input is True` → 拒）、
`world.py:143/168`（世界状态，独立维度）；`path_scan.py` 内零处引用 status。
`attempts.py:12-15`、`parameters.py:59-61` 的 `"…v10-candidate"` 只是版本名字符串
（v5/v6/v8/v9 冻结后都保留 `-candidate` 名），不是状态判据。

### 2.4 (d) 测试真实性

`tests/unit/test_path_scan_v10.py` 的 diff 只有两处：

1. `@pytest.mark.parametrize("fault", [...])` 的**参数列表未变**（仍是
   `missing/bool/limit/yaw/status/threshold` 六项），只把 `fault == "status"` 的故障值由
   `"FROZEN"` 改为 `"CANDIDATE"`——旧假设（FROZEN 必被拒）因闭集放开而不再成立，改成
   闭集外取值仍是“必须被拒”，不是删断言。
2. 新增 `test_frozen_v10_declaration_loads_without_relaxing_other_keys`：deepcopy 策略 →
   `status="FROZEN"` → 把 `src/vrc_agent/live_navigation/stability_v*.*` 全部复制到
   `tmp_path` → 用 `hashlib.sha256(raw).hexdigest()` 重写 sidecar →
   `monkeypatch.setattr(stability, "files", lambda _: tmp_path)` → `stability.load_policy("v10")`；
   断言 `status`/digest、**与 v9 的差异集合仍严格等于 `{"version", "path_scan"}`**、
   `frozen["status"] == v9["status"] == "FROZEN"`，并把 status 改成 `"FROZEN_APPROVED"`
   断言仍 `ValueError(match="v10")`。即新增用例是“真造冻结副本 + 重算摘要 + 真跑 loader +
   仍拒绝闭集外值”，不是把预期改成通过。

自跑：`tests/unit/test_path_scan_v10.py` **19 passed in 2.37s**（collect 可见
`test_candidate_loader_rejects_mutated_declarations[status]` 与
`test_frozen_v10_declaration_loads_without_relaxing_other_keys`）；
`tests/integration/test_path_scan_v10_runtime.py` **25 passed in 63.80s**（含真实
`ControllerProcess` 预算、schema13 重放、候选 live 拒绝）——放开闭集未造成端到端回归。

### 2.5 (e) 不变项

- `git diff --name-status af5ab1e^..HEAD -- src`：只 `M stability.py`。
- `git diff --name-status 336fb50^..HEAD -- 'stability_v*.json' 'stability_v*.sha256'`：
  只有 `A stability_v10.json`、`A stability_v10.sha256`；v1–v9 的 18 个策略/sidecar 未动。
- 十个 sidecar 与字节 sha256 逐一相等：v1 `e9350468…`、v2 `5487965f…`、v3 `333739e3…`、
  v4 `e4dcb100…`、v5 `2e21c734…`、v6 `e46e0c28…`、v7 `fbebd9cb…`、v8 `11b98adf…`、
  v9 `c9fb45ee…`、v10 `437849cf…`。
- `stability_v10.json` 在 `af5ab1e` 内字节未改，仍 `status="REQUIRES_FIELD_CALIBRATION"`、
  摘要 `437849cfee57cc157cc01cb76a0bc56eb8ab5eade7510a31bff47d4a5c14bff3`；v9 仍
  `c9fb45ee3295766e62f3ceda2c4a744aab9dd45e580d36385168a59f97e700a2`。
- `.gitattributes` 未再改（v10 json/sha256 的 `-text` 两行在 `336fb50` 已加）；
  `configs/`、`world/`、`prompts/`、`control/`、`skills/`、`acceptance_v8.py` 零改动。

### 2.6 (f) 独立复跑

自建探针 `.runtime/verify/t08v10_freeze_prep_probe.py`：**59 passed in 4.06s**，覆盖
(a)(b)(c)(d)(e) 全部要点（含 19 个单键篡改、15 个非法 status、真实 `navigation_entry`
拒绝、冻结副本可加载、十份摘要、闭集外值与 `CANDIDATE` 各自独立被拒、冻结后差异集合、
`STABLE_NAVIGATION_CLASSES["v10"] is StableNavigationV10` 等）。

四项门禁两次独立进程（同日同树 `af5ab1e`）：

| 轮次 | pytest | ruff | format | pip |
| --- | --- | --- | --- | --- |
| 第 1 次 | `1 failed, 1503 passed in 565.28s`（PYTEST_EXIT=1，见第3节） | All checks passed (0) | 166 files already formatted (0) | No broken requirements found. (0) |
| 第 2 次复跑 | `1504 passed in 648.19s (0:10:48)`（0） | All checks passed (0) | 166 files already formatted (0) | No broken requirements found. (0) |

日志：`.runtime/verify/review_v10_freeze_gates.log`、`.runtime/verify/review_v10_freeze_gates2.log`。
第 1 次的 ruff/format/pip 结果在作业输出中观察为全绿，收尾时以同树（仅文档有未提交改动、
`src`/`tests` 未变）复跑并追加到同一日志（`2026-10-20T09:18:37Z`，`RUFF_EXIT=0`、
`FMT_EXIT=0`、`PIP_EXIT=0`）以便追溯；第 2 次的四项结果直接在同一日志内。
`1504` = 第117节基线 `1503` + 1 个新增冻结声明用例，与第118节自报口径一致（交付方自报
`1504 passed in 572.35s`，量级相符）。

## 3. 第 1 次门禁的偶发失败定性（N1）

失败用例：`tests/integration/test_jpeg_navigation.py:108`
`test_jpeg_encoding_timeout_or_estop_prevents_model_call[estop-MANUAL_STOP]`，断言
`assert report["outcome"] == outcome and not calls and not report["actions"]`，报错串为
`assert ('MANUAL_STOP' == 'MANUAL_STOP' and not ['99bf779b22504bf09288dd946deef2d5'])`
——`outcome` 正确为 `MANUAL_STOP`，但 `calls`（测试替身 `http()` 里按请求体记下的
observation id）非空。由于 Python 短路，`not report["actions"]` 这一项**未被求值**，
不能据此断言当时记了动作。

机制（代码级）：

- `src/vrc_agent/live_navigation/stable_runtime.py:145-158` 的 `observe()` 用
  `await self.bounded(asyncio.to_thread(encode_bgra, …), ms=1000,
  reason="IMAGE_ENCODING_TIMEOUT")` 把编码放进 worker；
- `src/vrc_agent/live_navigation/runtime.py:122-134` 的 `bounded()` 以
  `await self.check_authority()` + `asyncio.wait({task}, timeout=min(0.03, …))` 每 ≤30ms
  轮询一次授权；`check_authority`（`runtime.py:90-102`）在 state 不在
  `{ARMED_IDLE, EXECUTING}` 时 `raise RuntimeHalt(reason_code)`；
- 该用例的 estop 是在 worker 线程内 `client.core.stop("MANUAL_STOP", emergency=True)`
  设置的，于是“授权轮询先取到 estop 后的快照”与“编码任务先返回、provider 继续”之间
  存在调度竞态：`check_authority` 的 status 快照若早于 estop 写入，编码结果会被采用，
  随后 provider 的 `_build_request` 在 `asyncio.to_thread` 中（`prepare_in_thread=True`）
  先到达 transport，就记下了一次 `calls`。

**控制安全未破**：`self.actions.append(request)` 在 `runtime.py:303-306`，
先 `authority = await self.check_authority()` 再 append，且 `submit` 由 core 在 ESTOP
下拒绝；任何一次 `bounded()`/submit 前的授权检查都会抛 `RuntimeHalt` 并跳过 append。
所以该竞态的最坏后果是“estop 后的那一次模型 HTTP 请求仍发出（计费/副作用）”，
而不是“estop 后仍派发动作”。

**与本次增量无关**：失败用例走 `stable-inference-v5` 策略分支，用
`tests/integration/test_stability_v5_runtime.py:33 assemble()`（`StableNavigationV5`）；
`af5ab1e` 只改 `load_policy` 的 v10 分支与 v10 单测，`src` 内除
`stability.py` 无任何改动——两者零代码交集。

复现数据（同日同树）：

| 运行方式 | 结果 |
| --- | --- |
| 全量门禁第 1 次 | 1 次失败（含上述用例） |
| 全量门禁第 2 次 | 1504 passed（该用例通过） |
| 交付方全量 | 1504 passed（`.runtime/tmp/v10freeze_pytest.log`） |
| 该文件单独、轻载 | 6/6 passed |
| 该文件 6 进程并发压载各跑 1 次 | 36/36 passed（`.runtime/verify/estop_stress/`） |
| 进程内 40 次新 runtime 复现（`t08_jpeg_estop_repro.py`） | 0/40 失败 |

判定：**既有 v5 JPEG/estop 路径的负载敏感竞态，非阻断**。同类“机器负载高时既有偶发”已在
`docs/TASKS.md` 第92节留档（v3/v4 spawn 用例被判 `STALE_OBSERVATION`），第115.3节的 v10
交付也沿用该记录（首轮 `2 failed, 1499 passed` → 复跑 `1501 passed`）；本轮据第 2 次复跑
定为偶发。记录为 N1，建议后续单独收紧该用例断言（例如断言“若 calls 非空则
`report["actions"]` 必为空、`outcome=="MANUAL_STOP"`”，或把 estop 设在编码任务之前）——
这是测试口径问题，不改控制行为。

## 4. 非阻断观察

- **N1**：见第3节（v5 JPEG/estop 负载敏感偶发；断言 `not calls` 对调度竞态敏感）。
- **N2**：`status` 与 sidecar 摘要必须同改。只改 JSON 的 `status` 而不重算 sidecar 会先
  撞 `ValueError("stability policy digest mismatch")`；反之只改 sidecar 会被严格相等校验
  拒。冻结时两步同一提交完成即可，但**不可只改一处**。
- **N3**：报告的 `navigation_design`（`stable-inference-v10`）里不含 status，读报告本身不能
  判断候选/冻结；判定必须走 `load_policy`。版本名 `local-rgb-stability-v10-candidate` 在
  冻结后仍保留 `-candidate`（与 v5/v6/v8/v9 一致），不要据名字推断状态。
- **N4**：复核时 `docs/validation/T08_v10_freeze_prep.md` 有 3 行表格空白对齐差异未提交
  （`git status` 单条 `M`），不改变语义；本轮未代为实现方提交，建议入库时一并纳入。
- **N5**：`stability_report.py:271` 的 live 声明拒绝只对 v6+ 报告生效（文案为
  “candidate v6 cannot claim live execution”），v10 走同一分支；文案未随版本改写，属既有
  表述，不影响判定。

## 5. 冻结前置（需用户明确授权，另案执行）

1. 本次增量（`af5ab1e`）已由本轮复核判“未能证伪、无阻断项”。
2. 用户授权后：改 `stability_v10.json` 的 `status` 为 `FROZEN` 并重算 sidecar（新摘要），
   两步同一提交（N2）。
3. 切 `configs/` 的 `navigation_design` 到 v10（现场仍保持 dry-run 直至人工确认）。
4. 人在场现场复验：含第117节 N3 的“3–4s 推理下 15 决策约 45–60s 会先撞 30s 基础租约”
   与本机 Ctrl+F9 续租清单。
5. 固定 20 次任务与人工逐条到达确认（`arrival_geometry_met`，不得自动 SUCCESS 替代）。

## 6. 边界

- 本轮不是冻结授权、不是实机验收，不构成对 v10 现场可用性的结论。
- 未改产品代码/策略/控制/配置/世界/提示；未启动 live、未 ARM、未调用付费模型、
  未发送真实输入。
- 结论仅覆盖 `af5ab1e` 相对 `979e6ee` 的增量；v10 主体与预算修正的结论见
  [T08_v10_review.md](T08_v10_review.md)（第117节）。
