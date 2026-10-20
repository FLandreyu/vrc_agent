"""严格的 TOML 配置；此阶段只有 fake provider，不读取或保存任何密钥。"""

import math
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from vrc_agent.contracts import Contract, Scalar


class AvatarParameter(Contract):
    type: Literal["bool", "int", "float"]
    reset_value: Scalar
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def validate_spec(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum exceeds maximum")
        if not self.accepts(self.reset_value):
            raise ValueError("reset_value must match declared type and bounds")
        return self

    def accepts(self, value: Scalar) -> bool:
        if type(value) is not {"bool": bool, "int": int, "float": float}[self.type]:
            return False
        if self.type == "int" and not -(2**31) <= value < 2**31:
            return False
        if self.type == "float" and (not math.isfinite(value) or abs(value) > 3.4028234e38):
            return False
        return (self.minimum is None or value >= self.minimum) and (
            self.maximum is None or value <= self.maximum
        )


class OscConfig(Contract):
    host: Literal["127.0.0.1"] = "127.0.0.1"
    send_port: Annotated[int, Field(ge=1, le=65535)] = 9000
    receive_port: Annotated[int, Field(ge=1, le=65535)] = 9001
    avatar_parameters: dict[str, AvatarParameter] = Field(default_factory=dict, max_length=64)

    @model_validator(mode="after")
    def valid_parameters(self):
        import re

        if self.send_port == self.receive_port:
            raise ValueError("OSC input and output ports must differ")
        if any(not re.fullmatch(r"[A-Za-z0-9_]{1,64}", n) for n in self.avatar_parameters):
            raise ValueError("invalid avatar parameter name")
        return self


class ControlConfig(Contract):
    tick_hz: Annotated[int, Field(ge=20, le=100)] = 50
    max_motion_ms: Annotated[int, Field(ge=1, le=500)] = 500
    heartbeat_interval_ms: Annotated[int, Field(ge=20, le=200)] = 100
    heartbeat_timeout_ms: Annotated[int, Field(ge=100, le=1000)] = 500
    observation_max_age_ms: Annotated[int, Field(ge=100, le=5000)] = 1500
    lease_ms: Annotated[int, Field(ge=500, le=60000)] = 30000
    lease_extension_ms: Annotated[int, Field(ge=1, le=30000)] = 30000
    lease_extension_max: Annotated[int, Field(ge=0, le=5)] = 0
    chat_interval_ms: Annotated[int, Field(ge=1500, le=60000)] = 2000
    max_actions_per_session: Annotated[int, Field(ge=10, le=100000)] = 10000
    window_title: Annotated[str, Field(min_length=1, max_length=256)] = "VRChat"
    enable_motion_probe: bool = False

    @model_validator(mode="after")
    def valid_timing(self):
        if self.heartbeat_timeout_ms < 3 * self.heartbeat_interval_ms:
            raise ValueError("heartbeat timeout must be at least three intervals")
        return self


class CaptureConfig(Contract):
    """T04 窗口采集。默认值按「宁可采集失败，也不把不可信画面交给下游」取。"""

    #: None 表示继承 control.window_title。
    window_title: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    window_class: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    window_match: Literal["exact", "prefix", "contains"] = "exact"
    #: 进程不是 per-monitor DPI 感知时拒采（否则拿到的是被系统缩放的错误分辨率画面）。
    require_dpi_awareness: bool = True
    #: 目标窗口被别的窗口盖住时拒采（GDI BitBlt 会静默返回遮挡窗口的像素）。
    require_unoccluded: bool = True
    occlusion_samples: Annotated[int, Field(ge=1, le=64)] = 5
    occlusion_min_ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0
    detect_black_frames: bool = True
    black_max_channel: Annotated[int, Field(ge=0, le=250)] = 8
    max_width: Annotated[int, Field(ge=16, le=16384)] = 3840
    max_height: Annotated[int, Field(ge=16, le=16384)] = 2160
    max_pixels: Annotated[int, Field(ge=256, le=33_177_600)] = 8_294_400
    #: 只在内存里保留最近若干帧；capture/ 永远不写文件。
    retain_frames: bool = True
    frames_in_memory: Annotated[int, Field(ge=1, le=256)] = 8


class PerceptionConfig(Contract):
    """T05 感知与模型供应商。配置里**永远没有密钥**，只存环境变量名。

    默认是离线假模型：免费、确定性、不出网。切到真实供应商必须同时给出预算，
    否则加载配置就失败（ARCHITECTURE 第 9 节：未设置付费预算时只启用 fake）。
    """

    provider: Literal["fake", "openai_compat"] = "fake"
    model: Annotated[str, Field(min_length=1, max_length=128)] = "fake-scene-v1"
    base_url: Annotated[str, Field(min_length=1, max_length=512)] = "https://api.openai.com/v1"
    #: 只存变量名；密钥本身在处理请求时从环境读入内存，不进配置、日志或遥测。
    api_key_env: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")] = "OPENAI_API_KEY"
    timeout_ms: Annotated[int, Field(ge=100, le=120_000)] = 20_000
    max_retries: Annotated[int, Field(ge=0, le=3)] = 1
    backoff_ms: Annotated[int, Field(ge=0, le=5000)] = 200
    max_output_tokens: Annotated[int, Field(ge=16, le=8192)] = 1024
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.0
    json_mode: bool = True
    disable_thinking: bool = False
    #: 默认**不把画面发给供应商**；开启前必须由本机用户明确决定。
    send_images: bool = False
    #: 通用默认保持 PNG；现场配置显式选择 JPEG q85。只影响发给模型的图像。
    image_format: Literal["png", "jpeg"] = "png"
    jpeg_quality: Annotated[int, Field(ge=1, le=95)] = 85
    #: 离线故障注入，只对假模型有效（ok/garbage/error/timeout/refused/cancelled）。
    fake_behaviour: str = "ok"
    #: 付费预算（美元）。0 表示不启用付费供应商。
    budget_usd: Annotated[float, Field(ge=0.0, le=1000.0)] = 0.0
    #: 价格表：两项必须同时给出；缺价格时费用记录为 unknown，绝不显示 0。
    price_input_per_mtok_usd: Annotated[float, Field(ge=0.0, le=10_000.0)] | None = None
    price_output_per_mtok_usd: Annotated[float, Field(ge=0.0, le=10_000.0)] | None = None
    price_currency: Literal["USD"] = "USD"
    price_as_of: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None
    #: 单个场景最多保留多少个实体；多出来的丢弃并记入 uncertainties。
    max_scene_entities: Annotated[int, Field(ge=1, le=256)] = 64
    #: 观测超过这个年龄就拒绝解读（看不清就别编），0 表示不做年龄检查。
    max_observation_age_ms: Annotated[int, Field(ge=0, le=10_000)] = 1500

    @model_validator(mode="after")
    def valid_provider(self):
        from vrc_agent.providers.fake import FAKE_BEHAVIOURS
        from vrc_agent.providers.openai_compat import validate_base_url

        if (self.price_input_per_mtok_usd is None) != (self.price_output_per_mtok_usd is None):
            raise ValueError("input and output prices must be configured together")
        # 价格表必须带生效日期：拿一个不知道什么时候生效的价格算出来的钱，
        # 比「不知道花了多少」更危险，所以配置阶段就拒绝。
        if self.price_input_per_mtok_usd is not None and self.price_as_of is None:
            raise ValueError("a configured price table must state price_as_of (YYYY-MM-DD)")
        if self.price_as_of is not None:
            from datetime import date

            try:
                date.fromisoformat(self.price_as_of)
            except ValueError as exc:
                raise ValueError(f"price_as_of is not a real date: {self.price_as_of!r}") from exc
        validate_base_url(self.base_url)
        if self.fake_behaviour not in FAKE_BEHAVIOURS:
            # 无论当前用哪个供应商都要校验：配置里留一个拼错的故障名，
            # 等到切回假模型时才炸，是最难查的那类错误。
            raise ValueError(f"unknown fake behaviour: {self.fake_behaviour!r}")
        if self.provider != "fake" and self.budget_usd <= 0:
            raise ValueError(
                f"provider {self.provider!r} is paid: set a positive budget_usd before enabling it"
            )
        return self


class DialogueConfig(Contract):
    """T07 交流。默认**不打开任何音频设备**：离线跑队列与半双工，真机播放要显式开启。

    设备按名字子串选择（索引会随驱动变化）；`None` 表示取该方向的第一台可用设备。
    真实路由（Voicemeeter 虚拟输入/输出）属于运行说明里的用户可执行步骤，代码不自动安装驱动。
    """

    enabled: bool = False
    output_device: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    input_device: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    output_hostapi: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    input_hostapi: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    sample_rate: Annotated[int, Field(ge=8000, le=192_000)] = 48_000
    playback_channels: Annotated[int, Field(ge=1, le=2)] = 1
    capture_channels: Annotated[int, Field(ge=1, le=2)] = 1
    #: 一个音频块的长度；它就是「本机停止」能生效的最坏延迟。
    block_ms: Annotated[int, Field(ge=5, le=100)] = 20
    max_pending: Annotated[int, Field(ge=1, le=64)] = 8
    queue_ttl_ms: Annotated[int, Field(ge=500, le=600_000)] = 20_000
    #: 与 control.chat_interval_ms 同一个量级：控制层还会再拦一次 RATE_LIMITED。
    min_interval_ms: Annotated[int, Field(ge=0, le=60_000)] = 2_000
    max_utterance_ms: Annotated[int, Field(ge=100, le=15_000)] = 15_000
    listen_ms: Annotated[int, Field(ge=20, le=5_000)] = 300
    turns: Annotated[int, Field(ge=1, le=200)] = 20
    gate_while_speaking: bool = True
    vad_threshold: Annotated[float, Field(gt=0.0, le=1.0)] = 0.01
    echo_tone_hz: Annotated[float, Field(gt=100.0, lt=10_000.0)] = 880.0
    echo_probe_ms: Annotated[int, Field(ge=20, le=5_000)] = 300
    echo_amplitude: Annotated[float, Field(gt=0.0, le=0.5)] = 0.08
    echo_min_ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 0.25
    echo_min_rms: Annotated[float, Field(gt=0.0, le=1.0)] = 0.002
    #: 离线合成/识别的实现选择；真实引擎属于用户可执行步骤，不在代码里安装。
    tts: Literal["fake"] = "fake"
    asr: Literal["fake", "energy"] = "fake"

    @model_validator(mode="after")
    def valid_timing(self):
        if self.block_ms > self.listen_ms:
            raise ValueError("block_ms must not exceed listen_ms")
        return self


class AppConfig(Contract):
    # 显式选用新策略；默认保持既有 live 时效策略。候选版须现场重新确认 HUD 区域。
    navigation_design: Literal[
        "legacy",
        "stable-inference-v1",
        "stable-inference-v2",
        "stable-inference-v3",
        "stable-inference-v4",
        "stable-inference-v5",
        "stable-inference-v6",
        "stable-inference-v7",
        "stable-inference-v8",
        "stable-inference-v9",
        "stable-inference-v10",
    ] = "legacy"
    navigation_motion_profile: Literal["cautious", "field"] = "cautious"
    mode: Literal["dry-run", "live"] = "dry-run"
    #: 动作提议的来源（T01 的固定示例）；与 [perception].provider 是不同的东西。
    provider: Literal["fake"] = "fake"
    osc: OscConfig = Field(default_factory=OscConfig)
    control: ControlConfig = Field(default_factory=ControlConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    perception: PerceptionConfig = Field(default_factory=PerceptionConfig)
    dialogue: DialogueConfig = Field(default_factory=DialogueConfig)

    @model_validator(mode="after")
    def navigation_parameters(self):
        if self.control.lease_extension_max:
            if self.navigation_design not in {
                "stable-inference-v6",
                "stable-inference-v7",
                "stable-inference-v8",
                "stable-inference-v9",
                "stable-inference-v10",
            }:
                raise ValueError("local lease extensions require v6")
            from vrc_agent.live_navigation.stability import load_policy

            budget = (
                load_policy(self.navigation_design.rsplit("-", 1)[-1])[0]["task_timeout_s"] * 1000
            )
            if (
                self.control.lease_ms
                + self.control.lease_extension_ms * self.control.lease_extension_max
                > budget
            ):
                raise ValueError("lease base plus extensions exceeds task_timeout_s")
        if self.navigation_design not in {
            "stable-inference-v6",
            "stable-inference-v7",
            "stable-inference-v8",
            "stable-inference-v9",
            "stable-inference-v10",
        } and (
            self.navigation_motion_profile != "cautious"
            or self.control.observation_max_age_ms > 1500
        ):
            raise ValueError("extended navigation parameters require v6")
        if self.navigation_design in {
            "stable-inference-v6",
            "stable-inference-v7",
            "stable-inference-v8",
            "stable-inference-v9",
            "stable-inference-v10",
        }:
            required = 500 if self.navigation_motion_profile == "field" else 200
            if self.control.max_motion_ms < required:
                raise ValueError(
                    "control motion limit is smaller than selected navigation envelope"
                )
        return self

    def capture_window_title(self) -> str:
        return self.capture.window_title or self.control.window_title


def load_config(path: Path | None = None) -> AppConfig:
    if path is None:
        return AppConfig()
    with path.open("rb") as stream:
        return AppConfig.model_validate(tomllib.load(stream))
