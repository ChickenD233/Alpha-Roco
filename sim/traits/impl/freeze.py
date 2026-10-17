"""冰冻增益类特性（on_buff_gain）。

- 200122 加个雪球  使敌方获得冻结时，也会使其获得2层冻结。
- 200116 捉迷藏    使敌方获得冻结时，也会使其获得全技能能耗+1。

接入说明：buff_gain 事件在 buffs.add_buff 调用处由引擎广播
（traits.on_buff_gain），ctx.extra 携带 buff_type/value/source_side/source_pet/source_kind。
触发语义：敌方获得冻结（任何来源，含天气暴风雪）即触发。
给敌方施加的 debuff 标记 source_kind="debuff"（正常显示/参与减益判定）；
特性内部 add_buff 不广播 buff_gain，因此不会自我连锁叠加。
"""

from __future__ import annotations

from ... import buffs as B
from ... import traits as T
from ..base import TraitHandler
from ..registry import register


def _enemy_frozen(ctx):
    """敌方获得冻结（任何来源，含天气）返回受击精灵；否则 None。"""
    if ctx.extra.get("buff_type") != B.BuffType.FREEZE:
        return None
    subject = ctx.subject
    if subject is None or subject.side == ctx.actor.side:
        return None
    if not ctx.is_active():
        return None
    return subject


# ---------------- 200122 加个雪球 ----------------
class AddSnowball(TraitHandler):
    trait_id = 200122
    name = "加个雪球"
    desc = "使敌方获得冻结时，也会使其获得2层冻结。"
    implemented = True

    def on_buff_gain(self, ctx):
        subject = _enemy_frozen(ctx)
        if subject is None:
            return
        B.grant_buff(
            subject,
            B.BuffType.FREEZE,
            2,
            B.DurationKind.PERMANENT,
            source_side=ctx.actor.side,
            source_pet=ctx.actor.name,
            source_kind="debuff",
        )


# ---------------- 200116 捉迷藏 ----------------
class HideAndSeek(TraitHandler):
    trait_id = 200116
    name = "捉迷藏"
    desc = "使敌方获得冻结时，也会使其获得全技能能耗+1。"
    implemented = True

    def on_buff_gain(self, ctx):
        subject = _enemy_frozen(ctx)
        if subject is None:
            return
        B.grant_buff(
            subject,
            B.BuffType.ENERGY_COST,
            1,
            source_side=ctx.actor.side,
            source_pet=ctx.actor.name,
            source_kind="debuff",
        )


# ---------------- 280012 抓到你了 ----------------
class Gotcha(TraitHandler):
    trait_id = 280012
    name = "抓到你了"
    desc = "自己入场时敌方获得2层冻结，使敌方获得冻结时，也会使其获得全技能能耗+1。"
    implemented = True

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        enemy = ctx.opponent()
        if enemy is None:
            return
        gained = B.grant_buff(
            enemy,
            B.BuffType.FREEZE,
            2,
            source_side=ctx.actor.side,
            source_pet=ctx.actor.name,
            source_kind="debuff",
        )
        if gained > 0:
            # 入场施加的冻结也属于"使敌方获得冻结"：广播以触发本特性的能耗+1
            T.on_buff_gain(
                ctx.state, enemy, B.BuffType.FREEZE, gained,
                source_side=ctx.actor.side, source_pet=ctx.actor.name,
                source_kind="debuff",
            )

    def on_buff_gain(self, ctx):
        subject = _enemy_frozen(ctx)
        if subject is None:
            return
        B.grant_buff(
            subject,
            B.BuffType.ENERGY_COST,
            1,
            source_side=ctx.actor.side,
            source_pet=ctx.actor.name,
            source_kind="debuff",
        )


def register_freeze() -> None:
    register(AddSnowball())
    register(HideAndSeek())
    register(Gotcha())


register_freeze()
