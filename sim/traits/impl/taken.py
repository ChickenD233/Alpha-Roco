"""第 1 批-d：受到伤害修正（modify_damage_taken）+ 展翅的属性改写 + 刺肤反击。

- 200074 偏振       受到自己携带技能系别的攻击伤害-40%。
- 200103 刺肤       每受到1次攻击伤害，对攻击自己的精灵造成50威力物理伤害（on_take_damage）。
- 200239 扎手       每受到1次技能攻击（不含连击），敌方获得1层棘刺印记。
- 200127 展翅       在场时，自己携带的普通系技能变为翼系技能；若后于对手行动，自己受到的伤害+25%。
- 200157 惊吓       能量等于0的精灵，无法对自己造成伤害。
- 200232 逐魂鸟     能耗小于等于1的攻击技能，无法对自己造成伤害。
- 200268 绝对秩序   受到非敌方系别的技能攻击时伤害-50%。
- 280010 完全偏振   抵抗自己携带技能系别的攻击伤害（"抵抗"按减半实现，语义待确认）。

语义说明：受击查询只查被查询精灵自身。ctx.skill=攻击技能，ctx.subject=攻击方，ctx.is_first=是否先手。
惊吓/逐魂鸟返回 -1.0（乘数归零=免疫）。
刺肤：on_take_damage 事件只在受到实际伤害（damage>0）时广播；反击伤害按普通系物理 50 威力
计算（吃攻防/克制/减伤修正），直接扣血，不广播 attack/take_damage（特性效果不写日志、不连锁）。
"""

from __future__ import annotations

from ... import buffs as B
from ... import marks as M
from ...damage import calc_damage, power_modifiers
from ...data_loader import load_typechart
from ...enums import Element as E
from ...models import BattleSkill
from ..registry import register
from ..base import TraitHandler

NORMAL = E.NORMAL  # 0 普通
WING = E.WING  # 12 翼


def _self(ctx):
    return ctx.target is ctx.actor


def _own_skill_elements(pet) -> set:
    return {s.element for s in pet.skills}


# ---------------- 200074 偏振 ----------------
class Polarization(TraitHandler):
    trait_id = 200074
    name = "偏振"
    desc = "受到自己携带技能系别的攻击伤害-40%。"
    implemented = True

    def modify_damage_taken(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return 0.0
        if ctx.skill.element in _own_skill_elements(ctx.actor):
            return -0.4
        return 0.0


# ---------------- 200103 刺肤 ----------------
class ThornSkin(TraitHandler):
    trait_id = 200103
    name = "刺肤"
    desc = "每受到1次攻击伤害，对攻击自己的精灵造成50威力物理伤害。"
    implemented = True

    def on_take_damage(self, ctx):
        # 自己受到实际伤害（damage>0 才广播本事件）；攻击者=ctx.target
        if ctx.subject is not ctx.actor or ctx.damage <= 0:
            return
        attacker = ctx.target
        if attacker is None or attacker.hp <= 0 or attacker.side == ctx.actor.side:
            return
        skill = BattleSkill(
            skill_id=-1,
            name="刺肤",
            element=NORMAL,
            category=0,
            power=50,
            energy_cost=0,
            desc="",
        )
        # 连击每段各反伤一次；反伤把攻击者打死则后续段停止
        for _ in range(max(1, ctx.hit_count)):
            if attacker.hp <= 0:
                break
            percent, flat = power_modifiers(ctx.state, ctx.actor, attacker, skill)
            result = calc_damage(
                ctx.actor,
                attacker,
                skill,
                load_typechart(),
                extra_power_percent=percent,
                extra_power_flat=flat,
                state=ctx.state,
            )
            dmg = result["damage"]
            if dmg > 0:
                attacker.hp = max(0, attacker.hp - dmg)


# ---------------- 200239 扎手 ----------------
class ThornTouch(TraitHandler):
    trait_id = 200239
    name = "扎手"
    desc = "每受到1次技能攻击（不含连击），敌方获得1层棘刺印记。"
    implemented = True

    def on_take_damage(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        if ctx.skill is None or ctx.skill.category not in (0, 1):
            return
        attacker = ctx.target
        if attacker is None or attacker.side == ctx.actor.side:
            return
        M.add_mark(ctx.state, attacker.side, 1, 1)


# ---------------- 200324 乌龟塔理论 ----------------
class TurtleTowerTheory(TraitHandler):
    trait_id = 200324
    name = "乌龟塔理论"
    desc = "每受到1次攻击伤害，敌方获得3层星陨印记。"
    implemented = True

    def on_take_damage(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        # 仅技能攻击（category 0/1），不含异常状态伤害（dot 不走 take_damage）
        if ctx.skill is None or ctx.skill.category not in (0, 1):
            return
        attacker = ctx.target
        if attacker is None or attacker.side == ctx.actor.side:
            return
        M.add_mark(ctx.state, attacker.side, 7, 3)


# ---------------- 200127 展翅 ----------------
class SpreadWings(TraitHandler):
    trait_id = 200127
    name = "展翅"
    desc = (
        "在场时，自己携带的普通系技能变为翼系技能；若后于对手行动，自己受到的伤害+25%。"
    )
    implemented = True

    def modify_skill_element(self, ctx, skill):
        if not _self(ctx):
            return None
        if skill.element == NORMAL:
            return WING
        return None

    def modify_damage_taken(self, ctx):
        if not _self(ctx):
            return 0.0
        if not ctx.is_first:
            return 0.25
        return 0.0


# ---------------- 200157 惊吓 ----------------
class Frighten(TraitHandler):
    trait_id = 200157
    name = "惊吓"
    desc = "能量等于0的精灵，无法对自己造成伤害。"
    implemented = True

    def modify_damage_taken(self, ctx):
        if not _self(ctx) or ctx.subject is None:
            return 0.0
        if ctx.subject.energy == 0:
            return -1.0
        return 0.0


# ---------------- 200232 逐魂鸟 ----------------
class SoulChaser(TraitHandler):
    trait_id = 200232
    name = "逐魂鸟"
    desc = "能耗小于等于1的攻击技能，无法对自己造成伤害。"
    implemented = True

    def modify_damage_taken(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return 0.0
        if ctx.skill.category in (0, 1) and ctx.skill.energy_cost <= 1:
            return -1.0
        return 0.0


# ---------------- 200268 绝对秩序 ----------------
class AbsoluteOrder(TraitHandler):
    trait_id = 200268
    name = "绝对秩序"
    desc = "受到非敌方系别的技能攻击时伤害-50%。"
    implemented = True

    def modify_damage_taken(self, ctx):
        if not _self(ctx) or ctx.skill is None or ctx.subject is None:
            return 0.0
        if ctx.skill.element not in ctx.subject.attributes:
            return -0.5
        return 0.0


# ---------------- 280010 完全偏振 ----------------
class FullPolarization(TraitHandler):
    trait_id = 280010
    name = "完全偏振"
    desc = "抵抗自己携带技能系别的攻击伤害。"
    implemented = True

    def modify_damage_taken(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return 0.0
        if ctx.skill.element in _own_skill_elements(ctx.actor):
            return -0.5  # "抵抗"按减半实现
        return 0.0


def register_batch1_taken() -> None:
    for cls in (
        Polarization,
        ThornSkin,
        ThornTouch,
        TurtleTowerTheory,
        SpreadWings,
        Frighten,
        SoulChaser,
        AbsoluteOrder,
        FullPolarization,
    ):
        register(cls())


register_batch1_taken()
