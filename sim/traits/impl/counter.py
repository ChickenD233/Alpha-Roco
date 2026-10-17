"""应对类特性（on_counter）。

- 200148 斗技  应对成功后，获得全技能威力永久+30。
- 200249 野性感官  应对成功后，下次行动先手+1（PRIORITY buff，行动时引擎一次性消耗）。

语义：counter 事件在"应对成功（本精灵强制先手）"时广播（battle.py 先手判定处，
并已用 counter.record_counter 记录到 pet.counter_stats），ctx.subject=强制先手的精灵。
斗技只响应自己应对成功，给自己加 PERMANENT 的 SKILL_POWER_FLAT +30（下场不消失、可叠加）。
应对次数从 pet.counter_stats 读取（供 display；当前应对判定未接入数据，实际不触发）。
"""

from __future__ import annotations

from ... import buffs as B
from ... import counter
from ..base import TraitHandler
from ..registry import register


# ---------------- 200148 斗技 ----------------
class DuelSkill(TraitHandler):
    trait_id = 200148
    name = "斗技"
    desc = "应对成功后，获得全技能威力永久+30。"
    implemented = True

    def on_counter(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        B.grant_buff(
            ctx.actor,
            B.BuffType.SKILL_POWER_FLAT,
            30,
            B.DurationKind.PERMANENT,
            source_kind="trait",
        )

    def display(self, state, pet):
        n = counter.get_counter_count(pet)
        if n <= 0:
            return None
        return [{"name": "威力", "layers": n, "per": "30", "gain": True}]


# ---------------- 200249 野性感官 ----------------
class WildSense(TraitHandler):
    trait_id = 200249
    name = "野性感官"
    desc = "应对成功后，下次行动先手+1。"
    implemented = True

    def on_counter(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        # "下一次行动获得先手+1"：排序时生效，行动开始结算时引擎一次性消耗
        B.grant_buff(ctx.actor, B.BuffType.PRIORITY, 1, source_kind="trait")

    def display(self, state, pet):
        n = B.get_buff_value(pet, B.BuffType.PRIORITY)
        if n <= 0:
            return None
        return [{"name": "先手", "layers": n, "per": "1", "gain": True}]


def register_counter() -> None:
    register(DuelSkill())
    register(WildSense())


register_counter()
