"""技能使用类特性（on_skill_end / on_attack / on_skill_start）。

- 200191 最好的伙伴  造成克制伤害后，获得攻防速+20%，并回复2能量。
- 200107 浸润       使用水系技能后，全技能能耗-1。
- 200146 助燃       使用火系技能后，获得双攻+20%。
- 280003 爆燃       使用火系技能后，获得双攻永久+30%（PERMANENT，下场不消失）。
- 200076 氧循环     使用草系技能后，回复10%生命。
- 280002 深层氧循环   使用草系技能后，回复15%生命。
- 200078 生物碱     使用草系技能时，敌方获得2层中毒。
- 200089 碰瓷       自己使用恶系技能后，敌方失去2能量。
- 200096 鼓气       使用能耗为3的技能时，获得攻防+20%。
- 200111 溶解扩散   每携带1个毒系技能进入战斗，水系技能使敌方获得1层中毒。
- 200115 扩散侵蚀   使用水系技能后，敌方获得中毒，层数=敌方中毒印记层数×2。
- 200171 贪心算法   1号位技能获得传动1（入场时给 skills[0].drive=1），
                   且使用该技能后使敌方获得6层灼烧。
- 200205 月牙雪糕   使用攻击技能时，敌方每有1层冻结，在攻击前使其获得1层星陨印记。
- 200125 咔咔冲刺   若先于敌方行动，行动后获得连击数+1（HIT_COUNT_FLAT buff 叠加）。
- 200131 毒腺       使用能耗小于等于1的技能时，敌方获得4层中毒。
- 200150 灵魂灼伤   冰系技能使敌方获得4层灼烧，火系技能使敌方获得2层冻结。
- 200128 乘风连击   使用翼系技能后，获得连击数+1（HIT_COUNT_FLAT buff 叠加）。
- 200129 快锤       携带的能耗小于3的技能获得迅捷（按真实能耗判断，入场时重算）。
- 200234 翼轴       1号位技能获得迅捷和传动1（战斗开始时给 skills[0] 打标记）。
- 200093 起飞加速   本场战斗首次使用的技能获得迅捷（on_skill_start 打标记）。
- 200173 暴食       携带的龙系技能获得迅捷（on_battle_start 按属性打标记）。
- 280029 相争       首次使用的技能获得迅捷；拥有迅捷效果的技能先手+1（modify_priority）。
- 200300 上锁       对手本回合使用的技能，冷却1回合。
- 200262 哨兵       敌方本回合技能足够击败自己时，速度+50，行动后脱离。
- 280008 高浓生物碱  使用技能时（不限属性），敌方获得2层中毒。

语义说明：
- 最好的伙伴："攻防速"按 双攻/双防 各 +20%（ATK/SPATK/DEF/SPDEF 各 2 层）、速度 +20%
  （SPEED_PERCENT 2 层，百分比速度见 buffs.get_speed_value）。
- 给己方施加的增益带 source_kind="trait"（不算普通"增益"，见 buffs.Buff.is_gain）；
  给敌方施加的 debuff 带 source_kind="debuff"（正常显示/参与减益判定）。
"""

from __future__ import annotations

from ... import buffs as B
from ... import skill_utils
from ... import traits as T
from ...damage import calc_damage
from ...data_loader import load_typechart
from ...enums import Element as E
from ..base import TraitHandler
from ..registry import register

WATER = E.WATER  # 3 水
FIRE = E.FIRE  # 2 火
GRASS = E.GRASS  # 1 草
POISON = E.POISON  # 9 毒
WING = E.WING  # 12 翼
DRAGON = E.DRAGON  # 7 龙
DARK = E.DARK  # 15 恶
ICE = E.ICE  # 6 冰
POISON_MARK = 4  # 中毒印记
STAR_MARK = 7  # 星陨印记


# ---------------- 200191 最好的伙伴 ----------------
class BestCompanion(TraitHandler):
    trait_id = 200191
    name = "最好的伙伴"
    desc = "造成克制伤害后，获得攻防速+20%，并回复2能量。"
    implemented = True

    def on_attack(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        if ctx.extra.get("type_mult", 1.0) <= 1.0:
            return
        B.grant_buff(ctx.actor, B.BuffType.ATK, 2, source_kind="trait")
        B.grant_buff(ctx.actor, B.BuffType.SPATK, 2, source_kind="trait")
        B.grant_buff(ctx.actor, B.BuffType.DEF, 2, source_kind="trait")
        B.grant_buff(ctx.actor, B.BuffType.SPDEF, 2, source_kind="trait")
        B.grant_buff(ctx.actor, B.BuffType.SPEED_PERCENT, 2, source_kind="trait")
        T.grant_energy(ctx.state, ctx.actor, 2)
        ctx.add_state("triggers", 1)

    def display(self, state, pet):
        n = pet.trait_state.get("triggers", 0)
        if n <= 0:
            return None
        return [
            {"name": "双攻&双防", "layers": n, "per": "20%", "gain": True},
            {"name": "速度", "layers": n, "per": "20%", "gain": True},
        ]


# ---------------- 200107 浸润 ----------------
class Soak(TraitHandler):
    trait_id = 200107
    name = "浸润"
    desc = "使用水系技能后，全技能能耗-1。"
    implemented = True

    def on_skill_end(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.skill.element != WATER:
            return
        B.grant_buff(ctx.actor, B.BuffType.ENERGY_COST, -1, source_kind="trait")
        ctx.add_state("uses", 1)

    def display(self, state, pet):
        n = pet.trait_state.get("uses", 0)
        if n <= 0:
            return None
        return [{"name": "能耗", "layers": n, "per": "1", "gain": False}]


# ---------------- 200146 助燃 ----------------
class FireFuel(TraitHandler):
    trait_id = 200146
    name = "助燃"
    desc = "使用火系技能后，获得双攻+20%。"
    implemented = True

    def on_skill_end(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.skill.element != FIRE:
            return
        B.grant_buff(ctx.actor, B.BuffType.ATK, 2, source_kind="trait")
        B.grant_buff(ctx.actor, B.BuffType.SPATK, 2, source_kind="trait")
        ctx.add_state("uses", 1)

    def display(self, state, pet):
        n = pet.trait_state.get("uses", 0)
        if n <= 0:
            return None
        return [{"name": "双攻", "layers": n, "per": "20%", "gain": True}]


# ---------------- 280003 爆燃 ----------------
class Detonation(TraitHandler):
    trait_id = 280003
    name = "爆燃"
    desc = "使用火系技能后，获得双攻永久+30%。"
    implemented = True

    def on_skill_end(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.skill.element != FIRE:
            return
        # 永久：PERMANENT 时长（下场不消失）
        B.grant_buff(
            ctx.actor, B.BuffType.ATK, 3, B.DurationKind.PERMANENT, source_kind="trait"
        )
        B.grant_buff(
            ctx.actor,
            B.BuffType.SPATK,
            3,
            B.DurationKind.PERMANENT,
            source_kind="trait",
        )
        ctx.add_state("uses", 1)

    def display(self, state, pet):
        n = pet.trait_state.get("uses", 0)
        if n <= 0:
            return None
        return [{"name": "双攻", "layers": n, "per": "30%", "gain": True}]


# ---------------- 200076 氧循环 ----------------
class OxygenCycle(TraitHandler):
    trait_id = 200076
    name = "氧循环"
    desc = "使用草系技能后，回复10%生命。"
    implemented = True

    def on_skill_end(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.skill.element != GRASS:
            return
        heal = int(ctx.actor.max_hp * 0.1)
        ctx.actor.hp = min(ctx.actor.max_hp, ctx.actor.hp + heal)


# ---------------- 280002 深层氧循环 ----------------
class DeepOxygenCycle(TraitHandler):
    trait_id = 280002
    name = "深层氧循环"
    desc = "使用草系技能后，回复15%生命。"
    implemented = True

    def on_skill_end(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.skill.element != GRASS:
            return
        heal = int(ctx.actor.max_hp * 0.15)
        ctx.actor.hp = min(ctx.actor.max_hp, ctx.actor.hp + heal)


# ---------------- 200078 生物碱 ----------------
class Alkaloid(TraitHandler):
    trait_id = 200078
    name = "生物碱"
    desc = "使用草系技能时，敌方获得2层中毒。"
    implemented = True

    def on_skill_start(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.skill.element != GRASS:
            return
        target = ctx.target
        if target is None or target.side == ctx.actor.side:
            return
        B.grant_buff(
            target,
            B.BuffType.POISON,
            2,
            source_side=ctx.actor.side,
            source_pet=ctx.actor.name,
            source_kind="debuff",
        )


# ---------------- 200096 鼓气 ----------------
class DrumUp(TraitHandler):
    trait_id = 200096
    name = "鼓气"
    desc = "使用能耗为3的技能时，获得攻防+20%。"
    implemented = True

    def on_skill_end(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.skill.energy_cost != 3:
            return
        B.grant_buff(ctx.actor, B.BuffType.ATK, 2, source_kind="trait")
        B.grant_buff(ctx.actor, B.BuffType.DEF, 2, source_kind="trait")
        ctx.add_state("uses", 1)

    def display(self, state, pet):
        n = pet.trait_state.get("uses", 0)
        if n <= 0:
            return None
        return [{"name": "物攻&物防", "layers": n, "per": "20%", "gain": True}]


# ---------------- 200171 贪心算法 ----------------
class GreedyAlgorithm(TraitHandler):
    trait_id = 200171
    name = "贪心算法"
    desc = "1号位技能获得传动1，且使用后使敌方获得6层灼烧。"
    implemented = True

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        if not ctx.actor.skills:
            return
        # 1号位技能获得传动1（重复入场不叠加：只有原 drive=0 时设置）
        skill = ctx.actor.skills[0]
        if skill.drive == 0:
            skill.drive = 1
            ctx.set_state("drive_skill", skill.skill_id)

    def on_skill_end(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        # 仅使用获得传动的 1 号位技能后触发
        if ctx.skill.skill_id != ctx.state_of("drive_skill", -1):
            return
        target = ctx.opponent()
        if target is None:
            return
        B.grant_buff(
            target,
            B.BuffType.BURN,
            6,
            source_side=ctx.actor.side,
            source_pet=ctx.actor.name,
            source_kind="debuff",
        )


# ---------------- 200205 月牙雪糕 ----------------
class CrescentIceCream(TraitHandler):
    trait_id = 200205
    name = "月牙雪糕"
    desc = "使用攻击技能时，敌方每有1层冻结，在攻击前使其获得1层星陨印记。"
    implemented = True

    def on_skill_start(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.skill.category not in (0, 1):
            return  # 仅攻击技能
        target = ctx.target
        if target is None or target.side == ctx.actor.side:
            return
        frozen = B.get_buff_value(target, B.BuffType.FREEZE)
        if frozen <= 0:
            return
        from ... import marks

        marks.add_mark(ctx.state, target.side, STAR_MARK, frozen)


# ---------------- 200125 咔咔冲刺 ----------------
class KaKaDash(TraitHandler):
    trait_id = 200125
    name = "咔咔冲刺"
    desc = "若先于敌方行动，行动后获得连击数+1。"
    implemented = True

    def _grant(self, ctx):
        # 自己行动且先于敌方（is_first）；行动类型不限（技能/聚能/防御/状态）
        if ctx.subject is not ctx.actor:
            return
        if not ctx.is_first:
            return
        B.grant_buff(ctx.actor, B.BuffType.HIT_COUNT_FLAT, 1, source_kind="trait")
        ctx.add_state("uses", 1)

    def on_skill_end(self, ctx):
        self._grant(ctx)

    def on_charge(self, ctx):
        self._grant(ctx)

    def on_defense(self, ctx):
        self._grant(ctx)

    def on_status_skill(self, ctx):
        self._grant(ctx)

    def display(self, state, pet):
        n = pet.trait_state.get("uses", 0)
        if n <= 0:
            return None
        return [{"name": "连击数", "layers": n, "per": "1", "gain": True}]


# ---------------- 200131 毒腺 ----------------
class VenomGland(TraitHandler):
    trait_id = 200131
    name = "毒腺"
    desc = "使用能耗小于等于1的技能时，敌方获得4层中毒。"
    implemented = True

    def on_skill_start(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.skill.energy_cost > 1:
            return
        target = ctx.target
        if target is None or target.side == ctx.actor.side:
            return
        B.grant_buff(
            target,
            B.BuffType.POISON,
            4,
            source_side=ctx.actor.side,
            source_pet=ctx.actor.name,
            source_kind="debuff",
        )


# ---------------- 200150 灵魂灼伤 ----------------
class SoulBurn(TraitHandler):
    trait_id = 200150
    name = "灵魂灼伤"
    desc = "冰系技能使敌方获得4层灼烧，火系技能使敌方获得2层冻结。"
    implemented = True

    def on_skill_start(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        target = ctx.target
        if target is None or target.side == ctx.actor.side:
            return
        if ctx.skill.element == ICE:
            B.grant_buff(
                target,
                B.BuffType.BURN,
                4,
                source_side=ctx.actor.side,
                source_pet=ctx.actor.name,
                source_kind="debuff",
            )
            ctx.add_state("burns", 1)
        elif ctx.skill.element == FIRE:
            B.grant_buff(
                target,
                B.BuffType.FREEZE,
                2,
                B.DurationKind.PERMANENT,
                source_side=ctx.actor.side,
                source_pet=ctx.actor.name,
                source_kind="debuff",
            )


# ---------------- 200111 溶解扩散 ----------------
class DissolveSpread(TraitHandler):
    trait_id = 200111
    name = "溶解扩散"
    desc = "每携带1个毒系技能进入战斗，水系技能使敌方获得1层中毒。"
    implemented = True

    def on_skill_start(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.skill.element != WATER:
            return
        target = ctx.target
        if target is None or target.side == ctx.actor.side:
            return
        n_poison = sum(1 for s in ctx.actor.skills if s.element == POISON)
        if n_poison <= 0:
            return
        B.grant_buff(
            target,
            B.BuffType.POISON,
            n_poison,
            source_side=ctx.actor.side,
            source_pet=ctx.actor.name,
            source_kind="debuff",
        )


# ---------------- 200115 扩散侵蚀 ----------------
class SpreadErosion(TraitHandler):
    trait_id = 200115
    name = "扩散侵蚀"
    desc = "使用水系技能后，敌方获得中毒，获得层数等于中毒印记层数的2倍。"
    implemented = True

    def on_skill_start(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.skill.element != WATER:
            return
        target = ctx.target
        if target is None or target.side == ctx.actor.side:
            return
        from ... import marks

        stacks = marks.get_stacks(ctx.state, target.side, POISON_MARK)
        layers = stacks * 2
        if layers <= 0:
            return
        B.grant_buff(
            target,
            B.BuffType.POISON,
            layers,
            source_side=ctx.actor.side,
            source_pet=ctx.actor.name,
            source_kind="debuff",
        )


# ---------------- 200300 上锁 ----------------
class SkillLock(TraitHandler):
    trait_id = 200300
    name = "上锁"
    desc = "对手本回合使用的技能，冷却1回合。"
    implemented = True

    def on_skill_start(self, ctx):
        if not ctx.is_active() or ctx.subject is None or ctx.skill is None:
            return
        if ctx.subject.side == ctx.actor.side:
            return
        skill_utils.schedule_skill_cooldown(ctx.subject, ctx.skill.skill_id)


# ---------------- 200262 哨兵 ----------------
class Sentinel(TraitHandler):
    trait_id = 200262
    name = "哨兵"
    desc = "回合开始时若敌方技能足够击败自己，自己获得速度+50，行动后脱离。"
    implemented = True

    def _enemy_usable_skills(self, ctx, opponent):
        """敌方当前真正能使用的技能（含能量/冷却/特性限制/蓄力状态）。"""
        # 蓄力状态下敌方只能使用已蓄力的技能、聚能或换人
        windup = opponent.windup_skill
        if windup is not None:
            idx = next((i for i, s in enumerate(opponent.skills) if s is windup), None)
            if idx is None:
                return []  # 已不在技能槽（如被巧变替换）
            if skill_utils.is_skill_on_cooldown(opponent, windup.skill_id):
                return []
            if not T.query_skill_usable(ctx.state, opponent, windup, skill_index=idx):
                return []
            # 释放回合不再校验能量（能量已在蓄力回合支付）
            return [(idx, windup)]
        usable = []
        for i, skill in enumerate(opponent.skills):
            if skill.category not in (0, 1) or skill.power is None:
                continue
            # 蓄力技能首次选择只是进入蓄力，本回合不造成伤害
            if getattr(skill, "windup", False):
                continue
            if skill_utils.is_skill_on_cooldown(opponent, skill.skill_id):
                continue
            if not T.query_skill_usable(ctx.state, opponent, skill, skill_index=i):
                continue
            # 真实能耗（含天气/印记/buff/特性修正）；不探测"能量不足兜底"以免产生副作用
            if (
                skill_utils.true_energy_cost(ctx.state, opponent, skill)
                > opponent.energy
            ):
                continue
            usable.append((i, skill))
        return usable

    def _damage_of(self, ctx, opponent, skill):
        result = calc_damage(
            opponent,
            ctx.actor,
            skill,
            load_typechart(),
            damage_reduction=0.0,
            state=ctx.state,
            is_first=False,
        )
        hit_flat, hit_percent = B.get_hit_count_bonus(opponent)
        trait_flat, trait_percent, forced = T.query_hit_count(
            ctx.state, opponent, ctx.actor
        )
        if forced is not None:
            hit_count = max(1, forced)
        else:
            hit_count = max(
                1, 1 + hit_flat + trait_flat + int((hit_percent + trait_percent) / 100)
            )
        return result["damage"] * hit_count

    def on_turn_start(self, ctx):
        # 每回合重新判断，避免速度加成和行动后脱离标记残留。
        ctx.set_state("sentinel_triggered", False)
        ctx.set_state("sentinel_leave_after_action", False)
        if not ctx.is_active():
            return

        opponent = ctx.opponent()
        if opponent is None:
            return

        # 敌方当前任一可用技能足以击败自己即触发
        for _, skill in self._enemy_usable_skills(ctx, opponent):
            if self._damage_of(ctx, opponent, skill) >= ctx.actor.hp:
                ctx.set_state("sentinel_triggered", True)
                ctx.set_state("sentinel_leave_after_action", True)
                return

    def modify_speed(self, ctx):
        if ctx.target is not ctx.actor:
            return 0
        return 50 if ctx.state_of("sentinel_triggered", False) else 0


# ---------------- 200129 快锤 ----------------
class QuickHammer(TraitHandler):
    trait_id = 200129
    name = "快锤"
    desc = "携带的能耗小于3的技能，获得迅捷。"
    implemented = True

    def _refresh_swift(self, ctx):
        # 用真实能耗判断（含天气/印记/buff/特性修正），每次入场时重算
        for skill in ctx.actor.skills:
            cost = skill_utils.true_energy_cost(ctx.state, ctx.actor, skill)
            skill.swift = cost < 3

    def on_battle_start(self, ctx):
        self._refresh_swift(ctx)

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        self._refresh_swift(ctx)


# ---------------- 200234 翼轴 ----------------
class WingAxis(TraitHandler):
    trait_id = 200234
    name = "翼轴"
    desc = "1号位技能获得迅捷和传动1。"
    implemented = True

    def on_battle_start(self, ctx):
        # 战斗开始时给 1 号位技能打上迅捷与传动1（标记跟着技能对象走）
        if not ctx.actor.skills:
            return
        skill = ctx.actor.skills[0]
        skill.swift = True
        if skill.drive == 0:
            skill.drive = 1


# ---------------- 200173 暴食 ----------------
class Gluttony(TraitHandler):
    trait_id = 200173
    name = "暴食"
    desc = "携带的龙系技能获得迅捷。"
    implemented = True

    def on_battle_start(self, ctx):
        # 携带的龙系技能全部获得迅捷（标记跟着技能对象走；
        # 巧变替换出的临时技能不是"携带的"，不带标记）
        for skill in ctx.actor.skills:
            if skill.element == DRAGON:
                skill.swift = True


# ---------------- 280022 溶解腐蚀 ----------------
class DissolveCorrosion(TraitHandler):
    trait_id = 280022
    name = "溶解腐蚀"
    desc = "每携带1个毒系技能进入战斗，水系技能使敌方获得2层中毒。"
    implemented = True

    def on_battle_start(self, ctx):
        # 战斗开始时统计携带的毒系技能数（巧变临时技能不算"携带的"）
        n = sum(1 for s in ctx.actor.skills if s.element == POISON)
        ctx.set_state("poison_skills", n)

    def on_skill_end(self, ctx):
        # 使用水系技能（不限类别）时，敌方获得 2×N 层中毒（N=携带的毒系技能数）
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.skill.element != WATER:
            return
        n = ctx.state_of("poison_skills", 0)
        target = ctx.target
        if n <= 0 or target is None or target.side == ctx.actor.side:
            return
        B.grant_buff(
            target,
            B.BuffType.POISON,
            2 * n,
            source_side=ctx.actor.side,
            source_pet=ctx.actor.name,
            source_kind="debuff",
        )


# ---------------- 200093 起飞加速 ----------------
class TakeoffBoost(TraitHandler):
    trait_id = 200093
    name = "起飞加速"
    desc = "本场战斗首次使用的技能获得迅捷。"
    implemented = True

    def on_skill_start(self, ctx):
        # 首次使用（任何类别技能，含迅捷自动释放与蓄力首段）时给该技能打迅捷标记，
        # 之后主动换人入场会自动释放它；聚能不是技能，不算使用。
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.state_of("first_used", False):
            return
        ctx.set_state("first_used", True)
        ctx.skill.swift = True


# ---------------- 280029 相争 ----------------
class Rivalry(TraitHandler):
    trait_id = 280029
    name = "相争"
    desc = "本场战斗首次使用的技能获得迅捷。拥有迅捷效果的技能先手+1。"
    implemented = True

    def on_skill_start(self, ctx):
        # 首次使用（任何类别技能）时给该技能打迅捷标记；与 200093 起飞加速同一逻辑
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.state_of("first_used", False):
            return
        ctx.set_state("first_used", True)
        ctx.skill.swift = True

    def modify_priority(self, ctx):
        # 拥有迅捷效果的技能先手+1（含本特性标记的、技能侧自带迅捷的）
        if ctx.target is not ctx.actor or ctx.skill is None:
            return 0
        return 1 if skill_utils.is_swift(ctx.skill) else 0


# ---------------- 200128 乘风连击 ----------------
class WindRideCombo(TraitHandler):
    trait_id = 200128
    name = "乘风连击"
    desc = "使用翼系技能后，获得连击数+1。"
    implemented = True

    def on_skill_end(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.skill.element != WING:
            return
        B.grant_buff(ctx.actor, B.BuffType.HIT_COUNT_FLAT, 1, source_kind="trait")
        ctx.add_state("uses", 1)

    def display(self, state, pet):
        n = pet.trait_state.get("uses", 0)
        if n <= 0:
            return None
        return [{"name": "连击数", "layers": n, "per": "1", "gain": True}]


# ---------------- 280008 高浓生物碱 ----------------
class ConcentratedAlkaloid(TraitHandler):
    trait_id = 280008
    name = "高浓生物碱"
    desc = "使用技能时，敌方获得2层中毒。"
    implemented = True

    def on_skill_start(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        target = ctx.target
        if target is None or target.side == ctx.actor.side:
            return
        B.grant_buff(
            target,
            B.BuffType.POISON,
            2,
            source_side=ctx.actor.side,
            source_pet=ctx.actor.name,
            source_kind="debuff",
        )


# ---------------- 200089 碰瓷 ----------------
class BumpScam(TraitHandler):
    trait_id = 200089
    name = "碰瓷"
    desc = "自己使用恶系技能后，敌方失去2能量。"
    implemented = True

    def on_skill_end(self, ctx):
        if ctx.subject is not ctx.actor or ctx.skill is None:
            return
        if ctx.skill.element != DARK:
            return
        target = ctx.opponent()
        if target is None:
            return
        target.energy = max(0, target.energy - 2)


def register_skill() -> None:
    for cls in (
        BestCompanion,
        Soak,
        FireFuel,
        Detonation,
        OxygenCycle,
        DeepOxygenCycle,
        Alkaloid,
        BumpScam,
        DrumUp,
        KaKaDash,
        VenomGland,
        SoulBurn,
        DissolveSpread,
        SpreadErosion,
        GreedyAlgorithm,
        CrescentIceCream,
        SkillLock,
        Sentinel,
        QuickHammer,
        WindRideCombo,
        WingAxis,
        TakeoffBoost,
        Rivalry,
        Gluttony,
        DissolveCorrosion,
        ConcentratedAlkaloid,
    ):
        register(cls())


register_skill()
