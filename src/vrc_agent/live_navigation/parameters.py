"""可信运行时参数闭集；模型只能提议动作，不能选择档位或修改预算。"""

import json
from dataclasses import asdict, dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class MotionEnvelope:
    forward: float = 0.2
    forward_ms: int = 200
    strafe: float = 0.2
    strafe_ms: int = 200
    yaw: float = 1.0
    yaw_ms: int = 200

    def accepts(self, args):
        # 只允许选定档位的一个轴；不能混轴、后退或由模型自定速度/持续时间。
        for axis, value, duration in (
            ("forward", self.forward, self.forward_ms),
            ("strafe", self.strafe, self.strafe_ms),
            ("strafe", -self.strafe, self.strafe_ms),
            ("yaw", self.yaw, self.yaw_ms),
            ("yaw", -self.yaw, self.yaw_ms),
        ):
            expected = dict(forward=0.0, strafe=0.0, yaw=0.0, duration_ms=duration)
            expected[axis] = value
            if args.model_dump() == expected:
                return True
        return False


MOTION_PROFILES = MappingProxyType(
    {
        "cautious": MotionEnvelope(),
        "field": MotionEnvelope(forward=0.4, forward_ms=500),
    }
)


def motion_profile(name):
    try:
        return MOTION_PROFILES[name]
    except (KeyError, TypeError):
        raise ValueError("unknown navigation motion profile") from None


def profile_declarations():
    return {name: asdict(profile) for name, profile in MOTION_PROFILES.items()}


def action_freshness_ms(config, policy=None):
    # 旧策略维持1500ms；只有新版本声明预算可扩到5000ms配置上限。
    ceiling = (
        5000
        if policy
        and policy.get("version")
        in {
            "local-rgb-stability-v6-candidate",
            "local-rgb-stability-v7-candidate",
            "local-rgb-stability-v8-candidate",
        }
        else 1500
    )
    return min(
        config.control.observation_max_age_ms,
        ceiling,
        policy["freshness_ms"] if policy else ceiling,
    )


def validate_run_parameters(parameters, policy):
    if not isinstance(parameters, dict):
        raise ValueError("missing v6 run parameters")
    freshness = parameters.get("freshness_ms")
    if type(freshness) is not int or not 100 <= freshness <= policy["freshness_ms"]:
        raise ValueError("invalid v6 freshness")
    name = parameters.get("motion_profile")
    expected = {
        "motion_profile": name,
        "motion_envelope": asdict(motion_profile(name)),
        "freshness_ms": freshness,
        "task_timeout_s": policy["task_timeout_s"],
        "max_actions": policy["max_actions"],
    }
    if "max_inference_attempts" in parameters:
        expected["max_inference_attempts"] = policy["max_inference_attempts"]
    # JSON严格比较避免True==1.0等数值等价绕过；只比较受信闭集，不回声载荷。
    try:
        matches = json.dumps(parameters, sort_keys=True, allow_nan=False) == json.dumps(
            expected, sort_keys=True
        )
    except (TypeError, ValueError):
        matches = False
    if not matches:
        raise ValueError("invalid v6 run parameters")
    return {**policy, "freshness_ms": freshness}
