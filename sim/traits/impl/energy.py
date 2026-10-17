"""第 1 批-c：能耗/能量修正（modify_energy_cost / modify_energy_limit / modify_energy_shortfall）。

- 200108 缩壳       携带的防御技能能耗-2。
- 200112 倾轧       携带的技能受能耗变化效果的影响翻倍。
- 200113 对流       自己的能耗增加变为能耗降低；能耗降低变为能耗增加。
- 200123 冰封       在场时，敌方全技能能耗+1（敌方光环，query 会查敌方在场精灵）。
- 200104 石天平     若使用技能能耗高于敌方，回合结束敌方失去能耗之差的能量。
- 200114 洄游       每次进入蓄力状态，获得全技能能耗永久-2（PERMANENT，下场不消失）。
- 200224 消波块     每携带1个水系技能进入战斗，地系技能能耗-1。
- 200252 留学生     自己全技能能耗+2（"可以学习全部攻击技能石"未建模，暂不实现）。
- 200215 多人宿舍   自己的能量可以超过能量上限（上限+999，视为无实际限制）。
- 200100 石头大餐   能量不足时，消耗5%最大生命，代替1能量。
- 280030 盛宴       能量不足时，消耗5%最大生命，代替1能量；生命低于50%时，获得吸血100%（modify_lifesteal）。
                   两者共用 _pay_hp（转换比 = max(1, int(max_hp×5%))/能量，与当前生命无关）。

语义说明：能耗修正查询会额外查敌方在场精灵特性（冰封），因此 handler 用
ctx.target.side != ctx.actor.side 判断是否对敌方生效；自身类用 ctx.target is ctx.actor。
倾轧/对流基于自身当前 ENERGY_COST buff 总值翻转/翻倍（含特性来源，特性也是"能耗变化效果"）。
"""

from __future__ import annotations

from ... import buffs as B
from ... import skill_utils
from ...enums import Element as E
from ..registry import register
from ..base import TraitHandler

WATER = E.WATER  # 3 水
EARTH = E.EARTH  # 5 地
FANTASY = E.FANTASY  # 17 幻
NO_LIMIT = 999  # 多人宿舍：能量上限视为无实际限制


def _self(ctx):
    return ctx.target is ctx.actor


def _pay_hp(ctx, need, dry_run: bool = False):
    """每1能量消耗最大生命的5%（石头大餐 200100 / 盛宴 280030），返回补充的能量数。

    转换比固定：cost = max(1, int(max_hp × 5%))，与当前生命无关（与盛宴同一套）。
    dry_run=True（选择校验）：不支付，只判断当前生命是否够付且付完不死
    （hp > need×cost）；不够则不可选。
    dry_run=False（结算）：不做死亡保护，全额支付 need×cost；支付后生命 ≤0
    即直接力竭（技能不再结算，力竭由引擎在付费处统一处理）。
    """
    if not _self(ctx):
        return 0
    cost = max(1, int(ctx.actor.max_hp * 0.05))
    if dry_run:
        return need if ctx.actor.hp > need * cost else 0
    ctx.actor.hp -= need * cost
    return need


# ---------------- 200108 缩壳 ----------------
class ShrinkShell(TraitHandler):
    trait_id = 200108
    name = "缩壳"
    desc = "携带的防御技能能耗-2。"
    implemented = True

    def modify_energy_cost(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return 0
        if ctx.skill.category == 2:
            return -2
        return 0


# ---------------- 200112 倾轧 ----------------
class Overwhelm(TraitHandler):
    trait_id = 200112
    name = "倾轧"
    desc = "携带的技能受能耗变化效果的影响翻倍。"
    implemented = True

    def modify_energy_cost(self, ctx):
        if not _self(ctx):
            return 0
        return B.get_buff_value(ctx.target, B.BuffType.ENERGY_COST)


# ---------------- 200113 对流 ----------------
class Convection(TraitHandler):
    trait_id = 200113
    name = "对流"
    desc = "自己的能耗增加变为能耗降低；能耗降低变为能耗增加。"
    implemented = True

    def modify_energy_cost(self, ctx):
        # 翻转"全部能耗变化"（buff + 印记 蓄势/湿润 + 天气沙暴）：
        # 最终能耗 = 原始 + M + (-2M) = 原始 - M，即 M 的正负号被翻转。
        if not _self(ctx) or ctx.skill is None:
            return 0
        total = skill_utils.energy_modifier_before_traits(
            ctx.state, ctx.target, ctx.skill
        )
        return -2 * total


# ---------------- 200104 石天平 ----------------
class StoneBalance(TraitHandler):
    trait_id = 200104
    name = "石天平"
    desc = "若使用技能能耗高于敌方，回合结束敌方失去能耗之差的能量。"
    implemented = True

    def _skill_cost_of(self, state, side, action):
        if action is None or action.kind != "skill" or action.skill_index is None:
            return 0
        idx = state.active[side]
        if idx < 0:
            return 0
        team = state.teams[side]
        if not (0 <= action.skill_index < len(team[idx].skills)):
            return 0
        return team[idx].skills[action.skill_index].energy_cost

    def on_turn_start(self, ctx):
        # 记录本回合双方行动技能的能耗（聚能/换人记 0）
        my_side = ctx.actor.side
        opp_side = "B" if my_side == "A" else "A"
        my_action = ctx.action_a if my_side == "A" else ctx.action_b
        opp_action = ctx.action_a if opp_side == "A" else ctx.action_b
        ctx.set_state("my_cost", self._skill_cost_of(ctx.state, my_side, my_action))
        ctx.set_state("opp_cost", self._skill_cost_of(ctx.state, opp_side, opp_action))

    def on_round_end(self, ctx):
        if not ctx.is_active():
            return
        diff = ctx.state_of("my_cost", 0) - ctx.state_of("opp_cost", 0)
        if diff <= 0:
            return
        enemy = ctx.opponent()
        if enemy is None:
            return
        enemy.energy = max(0, enemy.energy - diff)


# ---------------- 200123 冰封 ----------------
class IceSeal(TraitHandler):
    trait_id = 200123
    name = "冰封"
    desc = "在场时，敌方全技能能耗+1。"
    implemented = True

    def modify_energy_cost(self, ctx):
        if not ctx.is_active():
            return 0
        if ctx.target.side != ctx.actor.side:
            return 1
        return 0


# ---------------- 200114 洄游 ----------------
class Migration(TraitHandler):
    trait_id = 200114
    name = "洄游"
    desc = "每次进入蓄力状态，获得全技能能耗永久-2。"
    implemented = True

    def on_windup(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        # 永久：PERMANENT 时长（下场不消失），每次进入蓄力叠加一层
        B.grant_buff(
            ctx.actor,
            B.BuffType.ENERGY_COST,
            -2,
            B.DurationKind.PERMANENT,
            source_kind="trait",
        )
        ctx.add_state("windups", 1)

    def display(self, state, pet):
        n = pet.trait_state.get("windups", 0)
        if n <= 0:
            return None
        return [{"name": "能耗", "layers": n, "per": "2", "gain": False}]


# ---------------- 200224 消波块 ----------------
class WaveBreaker(TraitHandler):
    trait_id = 200224
    name = "消波块"
    desc = "每携带1个水系技能进入战斗，地系技能能耗-1。"
    implemented = True

    def modify_energy_cost(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return 0
        if ctx.skill.element != EARTH:
            return 0
        n_water = sum(1 for s in ctx.actor.skills if s.element == WATER)
        return -n_water


# ---------------- 200252 留学生 ----------------
class ExchangeStudent(TraitHandler):
    trait_id = 200252
    name = "留学生"
    desc = "自己全技能能耗+2，可以学习全部攻击技能石。"
    implemented = True

    def modify_energy_cost(self, ctx):
        if not _self(ctx):
            return 0
        return 2  # "攻击技能石"机制未建模，暂只实现能耗部分


# ---------------- 200215 多人宿舍 ----------------
class SharedDorm(TraitHandler):
    trait_id = 200215
    name = "多人宿舍"
    desc = "自己的能量可以超过能量上限。"
    implemented = True

    def modify_energy_limit(self, ctx):
        if not _self(ctx):
            return 0
        return NO_LIMIT


# ---------------- 200100 石头大餐 ----------------
class StoneFeast(TraitHandler):
    trait_id = 200100
    name = "石头大餐"
    desc = "能量不足时，消耗5%生命，代替1能量。"
    implemented = True

    def modify_energy_shortfall(self, ctx, need, dry_run=False):
        return _pay_hp(ctx, need, dry_run=dry_run)


# ---------------- 280030 盛宴 ----------------
class GrandFeast(TraitHandler):
    trait_id = 280030
    name = "盛宴"
    desc = "能量不足时，消耗5%最大生命，代替1能量。生命低于50%时，获得吸血100%。"
    implemented = True

    def modify_energy_shortfall(self, ctx, need, dry_run=False):
        return _pay_hp(ctx, need, dry_run=dry_run)

    def modify_lifesteal(self, ctx):
        if not _self(ctx):
            return 0.0
        if ctx.actor.hp * 2 < ctx.actor.max_hp:
            return 1.0
        return 0.0


# ---------------- 200188 盲从 ----------------
class BlindObedience(TraitHandler):
    trait_id = 200188
    name = "盲从"
    desc = "可以携带多个复写/借用/取念技能，非幻系技能能耗-2。"
    implemented = True

    def modify_energy_cost(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return 0
        if ctx.skill.element == FANTASY:
            return 0
        return -2


def register_batch1_energy() -> None:
    for cls in (
        ShrinkShell,
        Overwhelm,
        Convection,
        StoneBalance,
        Migration,
        IceSeal,
        WaveBreaker,
        ExchangeStudent,
        SharedDorm,
        StoneFeast,
        GrandFeast,
        BlindObedience,
    ):
        register(cls())


register_batch1_energy()
