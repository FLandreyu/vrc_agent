"""T00 人工探针：动作映射与数据契约的兼容性测试。"""

from vrc_agent.contracts import ChatArgs, MotionArgs
from vrc_agent.control.service import PROBE_BUTTONS, PROBE_CHAT_TEXT, PROBE_MOTIONS
from vrc_agent.transport.osc import BUTTONS


def test_probe_motions_cover_all_keys():
    assert set(PROBE_MOTIONS) == {
        "probe_forward",
        "probe_backward",
        "probe_yaw",
        "probe_yaw_left",
        "probe_strafe_left",
        "probe_strafe_right",
    }


def test_probe_motions_fit_motion_contract():
    for args in PROBE_MOTIONS.values():
        motion = MotionArgs.model_validate(args)
        assert 1 <= motion.duration_ms <= 500


def test_probe_motions_stay_within_axis_bounds():
    for args in PROBE_MOTIONS.values():
        for axis in ("forward", "strafe", "yaw"):
            assert -1.0 <= args.get(axis, 0.0) <= 1.0


def test_probe_buttons_are_allowlisted():
    assert set(PROBE_BUTTONS) == {"probe_use", "probe_grab", "probe_drop"}
    for button in PROBE_BUTTONS.values():
        assert button in BUTTONS


def test_probe_chat_text_fits_contract():
    chat = ChatArgs.model_validate({"text": PROBE_CHAT_TEXT})
    assert chat.text == PROBE_CHAT_TEXT
