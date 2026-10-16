"""受可信世界配置约束的短动作技能；不访问控制器或传输。"""

from .navigation import NavigationSkill
from .world import WorldProfile, load_first_world

__all__ = ["NavigationSkill", "WorldProfile", "load_first_world"]
