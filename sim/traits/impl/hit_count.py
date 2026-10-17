"""第 1 批-e：连击修正（modify_hit_count / force_hit_count）+ 嫁祸。

- 200143 嫁祸         自己每失去25%生命，连击数+2（累计失去量，回复不清零）。
- 200183 自由飘       自己每有1层萌化，获得连击数+3。
- 200204 侵蚀         敌方每有1层中毒效果，自己获得连击数+1。
- 200209 无差别过滤    在场时，所有精灵连击数固定为2（全场光环）。
- 280035 强制过滤     在场时，所有精灵连击数固定为1（全场光环）。

语义说明：连击查询会额外查敌方在场精灵特性（全场光环类），
无差别过滤/强制过滤用 ctx.is_active() 判断"在场时"，对双方都强制；
多个"固定为N"光环同时在场时取较大值（无差别过滤固定2 优先于强制过滤固定1），
由 traits.query_hit_count 统一处理。
嫁祸累计实际失去的生命（on_take_damage 攻击伤害 + on_dot_damage 中毒等），
每满 25% 最大生命给 2 层 HIT_COUNT_FLAT。
"""

from __future__ import annotations

from ... import buffs as B
from ..registry import register
from ..base import TraitHandler


def _self(ctx):
    return ctx.target is ctx.actor


# ---------------- 200143 嫁祸 ----------------
class FrameUp(TraitHandler):
    trait_id = 200143
    name = "嫁祸"
    desc = "自己每失去25%生命，连击数+2。"
    implemented = True

    def on_take_damage(self, ctx):
        self._track_loss(ctx)

    def on_dot_damage(self, ctx):
        self._track_loss(ctx)

    def _track_loss(self, ctx):
        if ctx.subject is not ctx.actor or ctx.damage <= 0:
            return
        threshold = int(ctx.actor.max_hp * 0.25)
        if threshold <= 0:
            return
        lost = ctx.state_of("lost", 0) + ctx.damage
        ctx.set_state("lost", lost)
        new_count = lost // threshold
        old_count = ctx.state_of("count", 0)
        for _ in range(new_count - old_count):
            B.grant_buff(ctx.actor, B.BuffType.HIT_COUNT_FLAT, 2, source_kind="trait")
        ctx.set_state("count", new_count)


# ---------------- 200183 自由飘 ----------------
class FreeFloat(TraitHandler):
    trait_id = 200183
    name = "自由飘"
    desc = "自己每有1层萌化，获得连击数+3。"
    implemented = True

    def modify_hit_count(self, ctx):
        if not _self(ctx):
            return (0, 0)
        cute = B.get_buff_value(ctx.actor, B.BuffType.CUTE)
        return (3 * cute, 0)

    def display(self, state, pet):
        n = B.get_buff_value(pet, B.BuffType.CUTE)
        if n <= 0:
            return None
        return [{"name": "连击数", "layers": n, "per": "3", "gain": True}]


# ---------------- 200204 侵蚀 ----------------
class Erosion(TraitHandler):
    trait_id = 200204
    name = "侵蚀"
    desc = "敌方每有1层中毒效果，自己获得连击数+1。"
    implemented = True

    def modify_hit_count(self, ctx):
        if not _self(ctx):
            return (0, 0)
        enemy = ctx.subject if ctx.subject is not None else ctx.opponent()
        if enemy is None:
            return (0, 0)
        poison = B.get_buff_value(enemy, B.BuffType.POISON)
        return (poison, 0)


# ---------------- 200209 无差别过滤 ----------------
class NoDiscrimination(TraitHandler):
    trait_id = 200209
    name = "无差别过滤"
    desc = "在场时，所有精灵连击数固定为2。"
    implemented = True

    def force_hit_count(self, ctx):
        if not ctx.is_active():
            return None
        return 2


# ---------------- 280035 强制过滤 ----------------
class ForcedFilter(TraitHandler):
    trait_id = 280035
    name = "强制过滤"
    desc = "在场时，所有精灵连击数固定为1。"
    implemented = True

    def force_hit_count(self, ctx):
        if not ctx.is_active():
            return None
        return 1


def register_batch1_hitcount() -> None:
    for cls in (FrameUp, FreeFloat, Erosion, NoDiscrimination, ForcedFilter):
        register(cls())


register_batch1_hitcount()
