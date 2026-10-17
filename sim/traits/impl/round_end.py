"""回合结束类特性（on_round_end / on_dot_damage / on_defense）。

- 200080 养分重吸收  回合结束时，回复3能量。
- 200095 双向光速    在场时，双方回合结束时的效果额外触发1次。
- 200203 煤渣草      在场时，所有灼烧的衰减变为增长。
- 200087 生长        回合结束时，回复12%生命。
- 200290 安可        使用光系技能后，在本侧特性回合末阶段完整返场。
- 200189 吸积盘      回合结束时，敌方获得2层星陨印记（marks id=7）。
- 200241 陨落        在场时，双方回合结束时的效果不会触发。
- 200240 耐活王      敌方受到中毒效果伤害时，自己回复等量生命（on_dot_damage）。
"""

from __future__ import annotations

from ... import buffs as B
from ... import marks as M
from ... import traits as T
from ...enums import Element as E
from ...models import BattleSkill
from ..base import TraitHandler
from ..registry import register

LIGHT = E.LIGHT  # 4 光


# ---------------- 200203 煤渣草 ----------------
class CinderGrass(TraitHandler):
    trait_id = 200203
    name = "煤渣草"
    desc = "在场时，所有灼烧的衰减变为增长。"
    implemented = True

    def modify_burn_growth(self, ctx):
        return ctx.is_active()


# ---------------- 200095 双向光速 ----------------
class TwoWayLightSpeed(TraitHandler):
    trait_id = 200095
    name = "双向光速"
    desc = "在场时，双方回合结束时的效果会额外触发1次。"
    implemented = True

    def modify_round_end_repeats(self, ctx):
        if not ctx.is_active():
            return 0
        return 1


# ---------------- 200241 陨落 ----------------
class Fallen(TraitHandler):
    trait_id = 200241
    name = "陨落"
    desc = "在场时，双方回合结束时的效果不会触发。"
    implemented = True

    def modify_round_end_repeats(self, ctx):
        if not ctx.is_active():
            return 0
        return -1


# ---------------- 200080 养分重吸收 ----------------
class NourishReabsorb(TraitHandler):
    trait_id = 200080
    name = "养分重吸收"
    desc = "回合结束时，回复3能量。"
    implemented = True

    def on_round_end(self, ctx):
        if not ctx.is_active():
            return
        T.grant_energy(ctx.state, ctx.actor, 3)


# ---------------- 200087 生长 ----------------
class Growth(TraitHandler):
    trait_id = 200087
    name = "生长"
    desc = "回合结束时，回复12%生命。"
    implemented = True

    def on_round_end(self, ctx):
        if not ctx.is_active():
            return
        heal = int(ctx.actor.max_hp * 0.12)
        ctx.actor.hp = min(ctx.actor.max_hp, ctx.actor.hp + heal)


# ---------------- 200290 安可 ----------------
class Encore(TraitHandler):
    trait_id = 200290
    name = "安可"
    desc = "使用光系技能后，回合结束时自己返场。"
    implemented = True

    def on_skill_end(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.skill.element != LIGHT:
            return
        ctx.set_state("used_light", True)

    def on_round_end(self, ctx):
        if not ctx.is_active():
            return
        if not ctx.state_of("used_light", False):
            return
        # 清除触发标记后立即完成返场，入场事件也在本侧特性阶段内结算。
        ctx.set_state("used_light", False)
        from ...battle import reenter
        reenter(ctx.state, ctx.actor.side, ctx.state.log)


# ---------------- 200189 吸积盘 ----------------
class AccretionDisk(TraitHandler):
    trait_id = 200189
    name = "吸积盘"
    desc = "回合结束时，敌方获得2层星陨印记。"
    implemented = True

    def on_round_end(self, ctx):
        if not ctx.is_active():
            return
        enemy = ctx.opponent()
        if enemy is None:
            return
        from ... import marks

        marks.add_mark(ctx.state, enemy.side, 7, 2)


# ---------------- 200240 耐活王 ----------------
class HardyKing(TraitHandler):
    trait_id = 200240
    name = "耐活王"
    desc = "敌方受到中毒效果伤害时，自己回复等量生命。"
    implemented = True

    def on_dot_damage(self, ctx):
        # 敌方（subject=受毒击的精灵）受到中毒伤害；自己/己方不触发
        if ctx.subject is None or ctx.subject.side == ctx.actor.side:
            return
        if ctx.extra.get("dot_type") != "poison":
            return
        if ctx.damage <= 0:
            return
        if not ctx.is_active():
            return
        ctx.actor.hp = min(ctx.actor.max_hp, ctx.actor.hp + ctx.damage)


# ---------------- 200187 蚀刻 ----------------
class Etching(TraitHandler):
    trait_id = 200187
    name = "蚀刻"
    desc = "回合结束时，敌方每2层中毒转化为1层中毒印记。"
    implemented = True

    def on_round_end(self, ctx):
        if not ctx.is_active():
            return
        enemy = ctx.opponent()
        if enemy is None:
            return
        poison = B.get_buff_value(enemy, B.BuffType.POISON)
        n = poison // 2
        if n <= 0:
            return
        # 转化 = 消耗中毒 buff（-2n 层）并生成中毒印记（+n 层，negative 槽 id=4）
        B.add_buff(enemy, B.BuffType.POISON, -n * 2)
        M.add_mark(ctx.state, enemy.side, 4, n)


# ---------------- 200275 合拍 ----------------
# 聚能按"0 能耗 / 状态类型 / 属性18"的虚拟技能参与比较（属性18 不与任何常规系别相同，
# 只有双方都聚能时系别才算相同）。
_CHARGE_VIRTUAL = BattleSkill(
    skill_id=0, name="聚能", element=18, category=3, power=None, energy_cost=0, desc=""
)


def _used_skill(pet, action):
    """本回合行动使用的技能对象；聚能返回虚拟技能；其他非技能行动返回 None。"""
    if action is None:
        return None
    if action.kind == "charge":
        return _CHARGE_VIRTUAL
    if action.kind != "skill" or action.skill_index is None:
        return None
    if not (0 <= action.skill_index < len(pet.skills)):
        return None
    return pet.skills[action.skill_index]


class Sync(TraitHandler):
    trait_id = 200275
    name = "合拍"
    desc = "本回合与敌方使用的技能在系别/类型/能耗上每有1项相同，回合结束时获得物攻和物防永久+10%。"
    implemented = True

    def on_turn_start(self, ctx):
        # 快照双方本回合使用的技能（聚能/换人为 None）
        if not ctx.is_active():
            return
        my_action = ctx.action_a if ctx.actor.side == "A" else ctx.action_b
        enemy_action = ctx.action_b if ctx.actor.side == "A" else ctx.action_a
        ctx.set_state("own_skill", _used_skill(ctx.actor, my_action))
        ctx.set_state("enemy_skill", _used_skill(ctx.opponent(), enemy_action))

    def on_round_end(self, ctx):
        own = ctx.state_of("own_skill")
        foe = ctx.state_of("enemy_skill")
        ctx.set_state("own_skill", None)
        ctx.set_state("enemy_skill", None)
        if not ctx.is_active() or own is None or foe is None:
            return
        same = ((1 if own.element == foe.element else 0)
                + (1 if own.category == foe.category else 0)
                + (1 if own.energy_cost == foe.energy_cost else 0))
        if same <= 0:
            return
        # 每项相同：物攻与物防永久各 +10%（1 层）
        B.grant_buff(ctx.actor, B.BuffType.ATK, same, B.DurationKind.PERMANENT,
                     source_kind="trait")
        B.grant_buff(ctx.actor, B.BuffType.DEF, same, B.DurationKind.PERMANENT,
                     source_kind="trait")
        ctx.add_state("stacks", same)

    def display(self, state, pet):
        n = pet.trait_state.get("stacks", 0)
        if n <= 0:
            return None
        return [{"name": "物攻物防", "layers": n, "per": "10%", "gain": True}]


# ---------------- 200297 莫比乌斯 ----------------
class Mobius(TraitHandler):
    trait_id = 200297
    name = "莫比乌斯"
    desc = "回合结束时，若自己的能量为0，则回复10能量。"
    implemented = True

    def on_round_end(self, ctx):
        if not ctx.is_active():
            return
        if ctx.actor.energy != 0:
            return
        T.grant_energy(ctx.state, ctx.actor, 10)


# ---------------- 280013 养分内循环 ----------------
class NutrientInnerCycle(TraitHandler):
    trait_id = 280013
    name = "养分内循环"
    desc = "回合结束时，回复6能量。"
    implemented = True

    def on_round_end(self, ctx):
        if not ctx.is_active():
            return
        T.grant_energy(ctx.state, ctx.actor, 6)


def register_round_end() -> None:
    register(CinderGrass())
    register(TwoWayLightSpeed())
    register(Fallen())
    register(NourishReabsorb())
    register(Growth())
    register(Encore())
    register(AccretionDisk())
    register(HardyKing())
    register(Etching())
    register(Sync())
    register(Mobius())
    register(NutrientInnerCycle())


register_round_end()
