# T08 JPEG q85 编码交付与离线验证

2026-10-17，Codex / root，实现方交付；TASKS 第64/66节，独立提交②。基于冻结提交 `3846191`，本次不修改稳定性策略或世界。

## 1. 配置与依赖

新增运行依赖 `Pillow==13.0.0`，在 pyproject 与 requirements.lock 精确锁定；其余锁定版本不变。官方来源：[Pillow 13.0.0](https://pypi.org/project/pillow/13.0.0/)；质量与采样参数参考 [Pillow JPEG 文档](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#jpeg)。本机安装 CPython3.14 Windows wheel，实际 importlib.metadata 和完整锁清单逐项核对一致，pip check 通过。镜像未提供候选包时显式使用官方 PyPI 安装，不修改用户 pip 配置；editable 元数据用现有锁定 setuptools `--no-deps --no-build-isolation` 更新，未升级其他依赖。

现场 `configs/navigation-live.toml` 的 `[perception]` 新增：

```toml
image_format = "jpeg"
jpeg_quality = 85
```

`image_format` 只允许 `png`/`jpeg`，`jpeg_quality` 为严格整数1–95（拒绝bool/字符串/浮点），默认85。通用 PerceptionConfig 默认PNG，以保持旧配置行为；选择JPEG须显式配置。两项配置传入provider工厂，同时由doctor摘要展示（无图像、密钥、请求正文）。现场模式仍dry-run，navigation_design仍stable-inference-v5；显式live、授权、本机ARM、lease/epoch与原控制通道不改。

## 2. 编码和边界

`providers/images.py` 新增 `bgra_jpeg(data, size, quality=85)` 和 `encode_bgra`。JPEG按BGRX读取BGRA，忽略alpha、保留RGB及原尺寸，固定4:2:0、非渐进、无优化遍历；只用内存BytesIO，不读写画面文件，不添加EXIF/ICC/备注。JPEG有损，不声称与第64节.NET q85编码器字节等价，识别精度不能由文件解码成功推出。

`encode_bgra` 返回编码字节与对应MIME。legacy原始BGRA由provider按配置编码；稳定性路径在现有worker内按相同配置预编码，传入`ModelInput(image_media_type="image/jpeg")`，provider直接附`data:image/jpeg;base64,…`，不二次压缩。PNG开关仍使用原bgra_png；已编码输入尊重其原MIME，不按配置重编码。发送授权关闭时provider不附图、不编码原始BGRA；JPEG失败不会自动回退PNG。

原始Frame、reference和稳定性proof仍使用原始BGRA；JPEG不会进入稳定性像素比较。1000ms编码预算、8000ms推理预算、每30ms授权检查、1500ms新观测上限、保护区/黑帧/预热判据、提示、世界v1/v2、所有安全门槛均未修改。回归包含保护区单像素变化拒绝，防止有损编码吞掉变化后错误放行。

## 3. 本机参考帧测量（仅离线编码）

探针 `.runtime/verify/t08_jpeg_probe.py`，输出 `t08_jpeg_delivery.json`；无网络调用，不保存JPEG图像。核对第59/60节两张本机PNG的原始哈希，原尺寸2160×1440，每格式编码三次，编码时间为perf_counter测量的本机值（与完整测试并行，不能作为稳定性能基线）。

| 参考帧 | PNG字节 | JPEG q85字节 | JPEG/PNG | PNG编码中位ms | JPEG编码中位ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| G3 激活前 | 5900447 | 366753 | 6.22% | 142.37 | 21.25 |
| G5 站立 | 5491746 | 430166 | 7.83% | 119.26 | 16.19 |

G3原PNG SHA256 `41b2d90236fa8fb8b79ba2cf699275539626e6ecf5bfb5b4296ce7cba0c09a6b`，本次JPEG `2c7f84bfa2b1263707c4bb629acc574dc8929ebcf6288505f43f7fa74a36222f`。

G5原PNG SHA256 `51a7858833c66280a2fae457f4af4220ef5246c2ffbbcf0d17efafd36d27145c`，本次JPEG `e55fa62b4744034fcd4a9ad1ba26d2cb64e670a77d04a42a2df0112f42b2cccb`。

本轮没有真实模型推理，不验证G3/G5框或语义。第64节.NET对照是前次现场证据，不能当成本实现的真实供应商延迟/准确率结果；仍须以新实现生产同款请求重测p95<8000ms并复验G3激活前停止、G5水面排除及新提示遵从，之后做固定20次任务。

## 4. 哈希与不变项审计

交付审计JSON字节SHA256：`bcc820a4ebd3c6d1cf24c84cefe4e7bf254b6d591d952dd5ede77e58a693f4ab`。其中保留完整测量、各生产文件工作树字节哈希、12份旧策略/世界文件哈希和锁清单核对结果。

| 文件 | 本次工作树字节SHA256 |
| --- | --- |
| providers/images.py | `5748820ee858d5ea3a26f424483894fe48d83b834a9632af746a7416a50fb680` |
| providers/openai_compat.py | `ca7525a1a336430ceac65550fa645a64a627b64d0a61a27ecb841f0e9363fb80` |
| providers/factory.py | `fc6d41bd70a902b0140f00dacec5f20b4bf097c345983f9afc1d16cbe0a40bf1` |
| config.py | `140f0763ff4a5f23e802339a9062afc8fa338336f092d8b812a637240f5d448c` |
| live_navigation/stable_runtime.py | `3346d7f3ff77645c444b2bb5c209b9efd5d5bee07f87f20feb6a9197d02eba24` |
| configs/navigation-live.toml | `139e4d448163fbeb701999a361a3309906d8923fbba51d158ba9a6798677b7cd` |
| pyproject.toml | `f7cfb183f564134d979f3919c872e42c000d1319273e4d3ca7ba9f141277cc6a` |
| requirements.lock | `345aaacfbb6aa8365460391660d4dd6f897a57c24e3df9adbc912c713e621e2f` |

源码前五行的路径以`src/vrc_agent/`为根。源码工作树换行可能受Git autocrlf影响，表中是本轮交付时的字节值；策略摘要由sidecar按原始文件校验。冻结v5摘要仍 `2e21c734ec902c44b3111de8f6f62f06626fbbe9410ff9a0b959469248eb4faa`；v5与冻结前比较仍只有status不同。v1–v4及世界v1/v2 JSON/sidecar12文件与冻结前逐字节一致，提示与判据源码没有修改；世界v2语义摘要仍 `b65b3dab51ef720c44b942d7ed67a975fbc52ed2bc1c65610718dc47b23d1ac0`。

## 5. 回归与门禁

新增30项回归：`tests/unit/test_jpeg_images.py`23项检查真实解码颜色/尺寸/alpha/质量/采样/元数据、坏配置与帧长、工厂接线、两格式MIME及预编码透传、不发送未授权图像、编码失败不回退；`tests/integration/test_jpeg_navigation.py`6项覆盖实际JPEG请求体与BGRA reference同源、闭环合成成功/动作结束归零、推理迟到/急停、编码迟到/急停均不发模型请求，以及原始保护区单像素变化拒绝。另更新T05隐私静态规则：PIL仅在images.py精确允许Image，save仅允许bgra_jpeg内同一with绑定的Image.frombytes与BytesIO，属性open仍禁用，其他禁盘/禁网规则保留。新增1项动态隐私测试，在磁盘/网络/图片open哨兵下成功编码8×8和2160×1440，实测save目标仅BytesIO且已关闭。三组定向运行共38 passed in9.15s。

初次完整测试1018通过、2项旧隐私静态规则失败（PIL/save一律禁用），记录保留`t08_jpeg_initial_pytest.log`；收窄至上述内存编码例外并补动态哨兵后，最终完整四项门禁如下（项目虚拟环境，从项目根执行），日志位于 `.runtime/verify/`：

| 命令 | 结果 | 日志 |
| --- | --- | --- |
| `python -m pytest -q` | **1021 passed in169.86s** | `t08_jpeg_pytest.log` |
| `python -m ruff check src tests` | All checks passed | `t08_jpeg_ruff.log` |
| `python -m ruff format --check src tests` | 135 files already formatted | `t08_jpeg_format.log` |
| `python -m pip check` | No broken requirements found | `t08_jpeg_pip.log` |

只读CLI计划 `python -m vrc_agent navigate-live --task G3-1 --world-version v2 --config configs/navigation-live.toml` 退出0，未传mode live，输出plan-only而不创建模型/控制器；日志`t08_jpeg_plan.log`。冻结记录同步校正为“世界v2装配”，不扩大第65节测试证据范围。

GraphFlow工具不可用，未伪造context/plan/index。本交付不等于ADR-009独立复核或现场验收，T08保持IN_PROGRESS；未启动live、未ARM、未付费调用、未发真实输入。
