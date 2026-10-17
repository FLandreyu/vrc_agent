# T08 v5 冻结与 JPEG q85 定向确认（独立复核）

复核者：DSH / Flash。实现者：Codex / root。复核对象：[TASKS 第 65/66/67 节](../TASKS.md)（提交 `3846191` 冻结、`43aee0f` JPEG、`8878b06` 现场冒烟），基线 HEAD `35a8a51`，工作区干净。本轮按用户指令只做**定向确认**，覆盖三条：① FROZEN 门槛；② JPEG 链路；③ 隐私/发送面。

不启动 live、不 ARM、不调用付费模型、不发送真实输入、不读密钥、不改产品文件/策略/世界/配置。

## 0. 结论

- 第 65/66/67 节的三组声明**主体未能证伪，未发现阻断项**：候选状态仍拒绝 live、v1–v4 与 12 份旧工件字节不变；JPEG 无静默回退、media 与字节同源、稳定性比较仍用原始 BGRA、预编码不二次压缩；完整帧不进盘/不进网、密钥只来自环境变量。
- 复核者自建探针 **36 项断言 0 失败**（A 冻结门槛 13、B JPEG 链路 13、C 隐私/发送面 10）；独立复跑四项门禁 **1021 passed in 154.26s**，ruff check / ruff format --check（135 files）/ pip check 均通过。
- 两项**非阻断观察**：N1 稳定性摘要按“工作树字节（CRLF）”口径记录，用 git blob 直接复算会得到不同摘要；N2 缺密钥错误文案会写出配置声明的环境变量名（既有行为，非本轮引入）。两条都不影响冻结门槛或隐私结论。
- 边界：这是**离线定向确认，不是实机验收**。v5 放行路径只验证到“通过状态门槛并到达 provider 构造哨兵”，未跨进程、未真实采集/推理；T08 仍 IN_PROGRESS，固定 20 次任务仍未执行。

## 1. 逐项对照

### 1.1 FROZEN 门槛（A 组）

| 声明 | 独立证据 | 结论 |
| --- | --- | --- |
| v5 字节 `2e21c734…`、sidecar 一致、`status=FROZEN`、版本名不变 | A01：工作树读字节复算 `2e21c734ec90…` 与 sidecar 相同；`version=local-rgb-stability-v5-candidate`、`metric=rgb_tolerance_visible_guard_v5` | 未能证伪 |
| 冻结只改 status，其余字段不变 | A03：`3ae0e40` 与当前 JSON 的键差异恰 `{status}`；`REQUIRES_FIELD_CALIBRATION → FROZEN` | 未能证伪 |
| v1–v4 仍为候选、sidecar 自洽 | A02：四份策略 `status=REQUIRES_FIELD_CALIBRATION`，sha256 与 sidecar 逐字节一致 | 未能证伪 |
| 12 份旧策略/世界工件逐字节不变 | A04：v1–v4 的 json+sha256 与 `vrchat_home_live_v1/v2.json+sha256` 共 12 份，`git show 251b5a1:path` 与 `git show HEAD:path` 的 blob 完全相同 | 未能证伪 |
| 候选 live 在资源创建前拒绝 | A05：v1–v4 设计逐一生效，`navigation_entry` 抛 `ValueError`（含 "field calibration" 与 "cannot enable live navigation"），`build_provider`/`Perception`/`ControllerProcess`/`live_capture` 哨兵均未触发；A11：state 目录与报告目录都未创建 | 未能证伪 |
| CLI 同样拒绝 | A13：`navigate-live --mode live` 以候选设计退出 2 | 未能证伪 |
| 只有 FROZEN 能通过状态门槛 | A06：现场配置（`stable-inference-v5`/FROZEN）越过状态门槛并到达 provider 构造哨兵；A05 的反向用例证明非 FROZEN 不能 | 未能证伪 |
| 既有 legacy 路径行为不变 | A10：`navigation_design=legacy` 时不查询策略状态，直接走到 provider 构造；与第 65 节“只改状态检查”一致 | 未能证伪 |
| 现场配置与默认值 | A08：现场配置 `design=v5`、`mode=dry-run`、`jpeg/85`、`send_images=true`、`disable_thinking=true`、capture exact/unoccluded/DPI；A09：默认 `legacy`、`dry-run`、`png/85`、不发送；A07：未知 design 被配置层拒绝 | 未能证伪 |
| 世界选择未受影响 | A12：`navigate-live --world-version v2` 计划模式退出 0（只读，不触发控制） | 未能证伪 |

### 1.2 JPEG 链路（B 组）

| 声明 | 独立证据 | 结论 |
| --- | --- | --- |
| q85、原尺寸、忽略 alpha、仅内存 | B01：输出 `FF D8` 起始、PIL 解码 `format=JPEG`、`size=(2160,1440)`；同一帧 alpha 全清零后字节完全相同；合成渐变帧最大通道误差 4（0–255 标度） | 未能证伪 |
| 默认 PNG 路径不变 | B02：PNG magic `89 50 4E 47`，解码像素与原始 BGRA 通道逐字节一致 | 未能证伪 |
| 字节与 MIME 同源 | B03：`encode_bgra` 单次返回 `(bytes, media)`，`jpeg→image/jpeg`、`png→image/png`；B05：编码器拒绝非 jpeg/png、布尔/字符串/0/96 质量与非整帧长；B04：配置层同样拒绝 `True/"85"/85.0/0/96/94.5/None` 与非闭集格式，85 正常接受 | 未能证伪 |
| 生产路径只编码一次、无静默回退 | B06：`application/x-bgra` 输入触发一次 `encode_bgra`，随后 data URL 使用其字节；B08：注入编码器异常后异常向上传播，transport 未被调用、无 PNG 回退；B07：`image/jpeg` 预编码输入按原字节透传，编码器不再被调用；B09：`send_images=False` 时既无图像 part 也无编码调用；B10：`png` 配置仍发 `image/png` | 未能证伪 |
| 稳定性比较仍用原始 BGRA | B11：运行 5 份 stability proof 全部 stable；最后编码输入与 `runtime.reference` 的原始帧**同一对象**，上传字节等于对该原始帧重新编码的结果（证明上传件与新比较帧同源且比较未使用 JPEG 像素）；报告 `inference_ms=8000` 未被改动 | 未能证伪 |
| 有损编码不能掩盖保护区变化 | B12：把场景换成单保护像素可见变化，仍判 `SCENE_UNSTABLE` 且 `protected_changed>=1`，无动作执行 | 未能证伪 |
| 编码预算仍为 1000 ms | B13：人为让编码超时，在模型请求前判 `IMAGE_ENCODING_TIMEOUT` 并进入 `MANUAL_STOP`；`session.py:84` 的编码预算是 `min(1000, perception.timeout_ms)` | 未能证伪 |

### 1.3 隐私/发送面（C 组）

| 声明 | 独立证据 | 结论 |
| --- | --- | --- |
| 感知/供应商层无盘与网络 | C01：AST 扫描 `perception/` 与 `providers/` 共 12 个模块，无 `open`/写盘属性/网络模块导入；唯一例外是 `providers/images.py` 的 `Image.frombytes` 与同一 `with` 内的 `BytesIO` + `image.save`；C08：全 `src/` 只有 `openai_compat.py` 使用 `urlopen`，无 requests/httpx/aiohttp/websockets/ftplib | 未能证伪 |
| 编码只在内存 | C02：在 open/写盘/网络哨兵全部布防时完成 8×8 与 2160×1440 JPEG 编码，无任何哨兵被触发 | 未能证伪 |
| 密钥只来自环境变量 | C04：配置只声明 `api_key_env`；缺失→`NOT_CONFIGURED`；提供 environ 后 provider 构造成功且取自该映射；C03：配置模型无明文密钥字段；C05：doctor 只输出存在性与变量名，不含值；C06：运行期无 `os.environ` 写入 | 未能证伪 |
| 事件/报告不含密钥或画面字节 | C07：捕获 2 条 provider 事件，序列化后不含哨兵密钥与图像 magic；C09：完整运行后报告 20,403 字节，无 JPEG/PNG magic、无 base64 段 | 未能证伪 |
| T05 隐私规则未被放宽 | C10：`tests/unit/test_t05_privacy.py` 9 项全部通过（含新增精确例外与动态哨兵）；例外只覆盖 `Image.frombytes`/`BytesIO.save`，`Image.open` 等仍禁用 | 未能证伪 |

## 2. 观察与最小反例

**N1（非阻断，口径/可复现性）**：文档与 sidecar 记录的 v5 摘要按**工作树字节**计算。本机 `core.autocrlf=true`，工作树是 CRLF；`git show` 给出的是 LF blob，二者不同。

```text
git show 3ae0e40:src/vrc_agent/live_navigation/stability_v5.json | sha256sum
→ 5cf0a27ed068e84415b9e19c8a17db7a3ebb4ae5c5059d84a5c827c9e7c2a355   # blob LF
# 同一 blob 把 LF 归一为 CRLF 后：
→ 1cc4287a4c944ae898012e016ee21b591041f1eb7000041844312c3ec02de21c   # 文档中的“旧候选字节”
```

冻结后的工作树字节为 `2e21c734…`（A01 一致）。因此现有哈希链在“工作树/autocrlf”口径下自洽；但用 `git archive`、克隆时 `autocrlf=false` 的环境或 LF 检出复核，会得到另一组摘要。建议后续在验证记录中写明口径，或用 `.gitattributes` 固定行尾，避免跨平台复现争议。

**N2（非阻断，既有行为）**：缺少密钥时 `build_provider` 的文案是 `environment variable PROBE_KEY is empty or missing`，包含**配置声明的变量名**（不是密钥值）。该文案在 `251b5a1` 的 `providers/factory.py:70` 已存在，不是第 66 节引入；值不外泄已由 C04 验证。

## 3. 已声明限界

1. 未启动 live：不跨 `ControllerProcess`、不真实采集、不推理、不 ARM、不发送输入。FROZEN 的“放行”只到 provider 构造哨兵。
2. JPEG 精度只用合成渐变帧测通道误差（最大 4），未用现场 G3/G5 帧重测语义与遮罩交叠；第 64 节的两张参考帧结论未在本轮重做。
3. 未测编码耗时、内存峰值或供应商 p95；本轮的 JPEG 编码耗时数字不构成延迟结论。
4. 隐私静态规则是 AST 白名单补集，理论上仍可能漏掉动态构造的文件路径或网络调用；本轮以运行期哨兵补强，但不是形式化证明。
5. Windows 捕获、DPI、窗口标题、F8/F9、租约/epoch 不在本轮范围，沿用既有门禁结论。
6. 探针复用 `tests/integration` 的运行时夹具（`MemoryClient`/`assemble`），不是独立重写的采集与模型栈。

## 4. 探针与门禁

- 探针：`.runtime/verify/v_t08_freeze_jpeg_review.py`，SHA256 `a5422ea2088481ae12f73a8b6ddfe761c300170b684257c019b399806c1a56f7`；36 项断言（A13/B13/C10），**0 失败**；结果 JSON `.runtime/verify/v_t08_freeze_jpeg_review_report.json`，SHA256 `2eaf6fad11e42b5f80ce6dc231008457a31f59c2171108d49819e8ada21450a2`（UTF-8）。
- 复核者独立复跑（不引用实现方日志）：`pytest -q` **1021 passed in 154.26s**；`ruff check src tests` All checks passed!；`ruff format --check src tests` 135 files already formatted；`pip check` No broken requirements found.。日志 `.runtime/verify/t08_freeze_jpeg_review_gates.log`，SHA256 `e8599768ebcfc0612ce29c058c0d5dbc747e398688c3d57ae0f21fd80a3d8ee4`。
- 与实现方数字同量级（第 66 节 1021 passed in 169.86s；第 67 节 155.77s），无新增失败。

## 5. 未覆盖

- 真实供应商请求：JPEG 是否被服务端接受、真实 p95、tokens/费用入账。
- 现场 G3/G5 帧在 JPEG q85 下的语义精度、遮罩交叠变化与到达判据。
- v5 FROZEN 的 live 全链路：F9/F8、急停、租约/epoch、动作到期归零。
- 固定 20 次任务与 ≥16/20。
- 非 autocrlf 平台上的摘要复现；Pillow 13.0.0 之外的环境。
- 真实运行下报告目录的落盘内容（本轮只检查内存中的报告对象）。

## 6. 复现

```bash
cd /d/vrc_agent
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe .runtime/verify/v_t08_freeze_jpeg_review.py
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
.venv/Scripts/python.exe -m pip check
git show 3ae0e40:src/vrc_agent/live_navigation/stability_v5.json | sha256sum   # N1：LF blob ≠ 文档摘要
```

探针只读产品代码并写 `.runtime/verify/`，不触碰 `src/`、`configs/`、世界/策略文件。

## 7. 下一步

1. 现场以 JPEG q85 配置重做 G3/G5 语义复验与延迟复测（含供应商 p95），再进入固定 20 次任务。
2. 完成 20 次真实任务并按 ≥16/20 验收后，才可改动 T08 状态；本轮不构成实机验收。
3. 冻结/授权边界：v5 阈值、遮罩、判据、提示、世界 v2 已冻结，任何改动都需重新冻结并重新复核；JPEG 质量与 `image_format` 同属现场配置，变更后需重跑本轮 B 组与延迟复测。
4. 建议后续验证记录统一注明“摘要按工作树字节”的口径，或在 `.gitattributes` 固定行尾（N1）。
