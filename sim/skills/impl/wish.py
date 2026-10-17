"""愿力冲击技能 handler（特性替换版，skill_id=-2）。

共鸣魔法的愿力冲击是 skill_id=-1 的运行时虚拟技能（静态威力 80/应对状态 200，
回合结束还原）；特性（280025 裁决 / 280026 滋养 / 280027 点燃 / 280028 净化）替换
首个技能所用的愿力冲击是**永久替换、不还原**的另一份虚拟技能（skill_id=-2），
威力 80、应对状态成功时经本 handler 的 modify_power 变为 250%（即 200），
与共鸣魔法版本互不干扰。
"""

from __future__ import annotations

from ..base import SkillHandler
from ..ops import COUNTER_STATUS
from ..registry import register

WISH_TRAIT_SKILL_ID = -2  # 特性替换版愿力冲击（共鸣魔法版为 -1）


class WishImpact(SkillHandler):
    """愿力冲击（特性替换版）：威力 80，应对状态成功时本次威力+150%。"""

    skill_id = WISH_TRAIT_SKILL_ID
    name = "愿力冲击"
    category = 0
    counter_target = COUNTER_STATUS
    implemented = True

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        if ctx.is_counter:
            percent += 150.0
        return (percent, flat)


register(WishImpact())
