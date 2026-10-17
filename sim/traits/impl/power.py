"""第 1 批-a：技能威力修正（modify_power）。

- 200075 目空       携带的非光系技能，威力+25%。
- 200077 勇敢       携带的能耗大于3的技能，威力+40%。
- 200088 挺起胸脯    携带的能耗为1的技能，威力+50%。
- 200098 共鸣       携带的「虫鸣」技能威力+20。
- 200106 观星       敌方每有1层星陨印记，自己的地系技能威力+20%。
- 200117 冰钻       敌方携带技能总能耗每有1点，自己攻击时威力+10%。
- 200120 冻土       每携带1个冰系技能进入战斗，地系技能威力+10%。
- 200124 顺风       若先于敌方攻击，本次技能威力+50%。（原第 0 批测试队伍，归入此类）
- 200192 绒粉星光    攻击时，若敌方血脉是非本系的系别血脉，技能威力+100%。
- 200193 月光审判    攻击时，若敌方血脉是首领血脉，技能威力+100%。
- 200243 变形活画    行动时，敌方每有1层增益，本次行动技能威力+10%，速度+5（modify_speed 也在本文件）。
- 200251 血型吸引    敌方每携带1种系别的技能，自己攻击时威力+10（固定值）。
- 200267 涂鸦       使用非本系技能时威力+50%。
- 280009 夺目       额外获得三个未携带系别的随机技能，且非光系技能威力+25%。
- 280011 坠星       敌方每有1层星陨印记，自己的技能威力+20%。
- 280023 破空       若先于敌方攻击，本次技能威力+75%。

语义说明：除注明"敌方"的判定外，其余都是自身效果（handler 内用 ctx.target is ctx.actor 判定）。
绒粉星光"非本系的系别血脉"= 敌方血脉是元素且不在自己属性中（首领血脉 18 不算）；月光审判=敌方血脉为首领血脉（bloodline==18）。
变形活画数的是敌方"增益"层数（Buff.is_gain()，特性来源不算）。
"""

from __future__ import annotations

import random

from ... import buffs as B
from ...data_loader import load_skills, load_spirits
from ...enums import LORD_BLOODLINE, Element as E
from ...models import BattleSkill
from ..registry import register
from ..base import TraitHandler

LIGHT = E.LIGHT  # 4 光
EARTH = E.EARTH  # 5 地
WATER = E.WATER  # 3 水
FIGHT = E.FIGHT  # 11 武
POISON = E.POISON  # 9 毒
CUTE = E.CUTE  # 13 萌
ICE = E.ICE  # 6 冰
BLIZZARD = 1  # 天气：暴风雪
STAR_MARK = 7  # 星陨印记
CHIRP_SKILL_ID = 7130160  # 虫鸣


def _self(ctx):
    return ctx.target is ctx.actor


def _enemy_star_stacks(ctx) -> int:
    """敌方星陨印记层数（敌方=ctx.subject）。"""
    enemy = ctx.subject
    if enemy is None:
        enemy = ctx.opponent()
    if enemy is None:
        return 0
    from ... import marks

    return marks.get_stacks(ctx.state, enemy.side, STAR_MARK)


def _enemy_gain_layers(pet) -> int:
    return sum(b.value for b in pet.buffs if b.is_gain())


# ---------------- 200124 顺风 ----------------
class ShunFeng(TraitHandler):
    trait_id = 200124
    name = "顺风"
    desc = "若先于敌方攻击，本次技能威力+50%。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx):
            return (0.0, 0.0)
        if ctx.is_first:
            return (50.0, 0.0)
        return (0.0, 0.0)


# ---------------- 200075 目空 ----------------
class MuKong(TraitHandler):
    trait_id = 200075
    name = "目空"
    desc = "携带的非光系技能，威力+25%。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return (0.0, 0.0)
        if ctx.skill.element == LIGHT:
            return (0.0, 0.0)
        return (25.0, 0.0)


# ---------------- 280009 夺目 ----------------
class Dazzle(MuKong):
    trait_id = 280009
    name = "夺目"
    desc = "额外获得三个未携带的随机技能，且非光系技能威力+25%。"
    implemented = True

    def _add_extra_skills(self, ctx):
        if ctx.state_of("extra_skills_added", False):
            return

        carried_elements = {skill.element for skill in ctx.actor.skills}
        carried_ids = {skill.skill_id for skill in ctx.actor.skills}
        spirit = next(
            (raw for raw in load_spirits() if raw.get("id") == ctx.actor.spirit_id),
            None,
        )
        if spirit is None:
            ctx.set_state("extra_skills_added", True)
            return

        # 夺目只从自身技能池的普通技能和技能石技能中抽取。
        pools = spirit.get("skills", {})
        learnable_ids = dict.fromkeys(
            pools.get("normal", []) + pools.get("skillstone", [])
        )
        skill_map = {raw["id"]: raw for raw in load_skills()}
        candidates = [
            skill_map[skill_id]
            for skill_id in learnable_ids
            if skill_id in skill_map
            and skill_id not in carried_ids
            and skill_map[skill_id].get("element") not in carried_elements
            and 0 <= skill_map[skill_id].get("element", -1) <= int(E.FANTASY)
        ]
        for raw in random.sample(candidates, min(3, len(candidates))):
            ctx.actor.skills.append(
                BattleSkill(
                    skill_id=raw["id"],
                    name=raw["name"],
                    element=raw["element"],
                    category=raw["category"],
                    power=raw.get("power"),
                    energy_cost=raw.get("energyCost", 0),
                    desc=raw.get("desc", ""),
                )
            )
        ctx.set_state("extra_skills_added", True)

    def on_evolution(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        self._add_extra_skills(ctx)


# ---------------- 200077 勇敢 ----------------
class Brave(TraitHandler):
    trait_id = 200077
    name = "勇敢"
    desc = "携带的能耗大于3的技能，威力+40%。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return (0.0, 0.0)
        if ctx.skill.energy_cost > 3:
            return (40.0, 0.0)
        return (0.0, 0.0)


# ---------------- 200088 挺起胸脯 ----------------
class PuffChest(TraitHandler):
    trait_id = 200088
    name = "挺起胸脯"
    desc = "携带的能耗为1的技能，威力+50%。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return (0.0, 0.0)
        if ctx.skill.energy_cost == 1:
            return (50.0, 0.0)
        return (0.0, 0.0)


# ---------------- 200098 共鸣 ----------------
class Resonance(TraitHandler):
    trait_id = 200098
    name = "共鸣"
    desc = "携带的「虫鸣」技能威力+20。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return (0.0, 0.0)
        if ctx.skill.skill_id == CHIRP_SKILL_ID:
            return (0.0, 20.0)
        return (0.0, 0.0)


# ---------------- 200106 观星 ----------------
class StarGaze(TraitHandler):
    trait_id = 200106
    name = "观星"
    desc = "敌方每有1层星陨印记，自己的地系技能威力+20%。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return (0.0, 0.0)
        if ctx.skill.element != EARTH:
            return (0.0, 0.0)
        return (20.0 * _enemy_star_stacks(ctx), 0.0)


# ---------------- 200117 冰钻 ----------------
class IceDrill(TraitHandler):
    trait_id = 200117
    name = "冰钻"
    desc = "敌方携带技能总能耗每有1点，自己攻击时威力+10%。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx) or ctx.subject is None:
            return (0.0, 0.0)
        total = sum(s.energy_cost for s in ctx.subject.skills)
        return (10.0 * total, 0.0)


# ---------------- 200120 冻土 ----------------
class FrozenSoil(TraitHandler):
    trait_id = 200120
    name = "冻土"
    desc = "每携带1个冰系技能进入战斗，地系技能威力+10%。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return (0.0, 0.0)
        if ctx.skill.element != EARTH:
            return (0.0, 0.0)
        n_ice = sum(1 for s in ctx.actor.skills if s.element == ICE)
        return (10.0 * n_ice, 0.0)


# ---------------- 200192 绒粉星光 ----------------
class FluffyStarlight(TraitHandler):
    trait_id = 200192
    name = "绒粉星光"
    desc = "攻击时，若敌方血脉是非本系的系别血脉，技能威力+100%。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx) or ctx.subject is None:
            return (0.0, 0.0)
        # "非本系的系别血脉"：血脉是元素且不在自己属性中（首领血脉 18 不是系别血脉，不触发）
        if (
            ctx.subject.bloodline is not None
            and ctx.subject.bloodline != LORD_BLOODLINE
            and ctx.subject.bloodline not in ctx.actor.attributes
        ):
            return (100.0, 0.0)
        return (0.0, 0.0)


# ---------------- 200193 月光审判 ----------------
class MoonlightJudgment(TraitHandler):
    trait_id = 200193
    name = "月光审判"
    desc = "攻击时，若敌方血脉是首领血脉，技能威力+100%。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx) or ctx.subject is None:
            return (0.0, 0.0)
        if ctx.subject.bloodline == LORD_BLOODLINE:
            return (100.0, 0.0)
        return (0.0, 0.0)


# ---------------- 200243 变形活画 ----------------
class LivingCanvas(TraitHandler):
    trait_id = 200243
    name = "变形活画"
    desc = "行动时，敌方每有1层增益，本次行动技能威力+10%，速度+5。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx):
            return (0.0, 0.0)
        enemy = ctx.opponent()
        if enemy is None:
            return (0.0, 0.0)
        return (10.0 * _enemy_gain_layers(enemy), 0.0)

    def modify_speed(self, ctx):
        if not _self(ctx):
            return 0
        enemy = ctx.opponent()
        if enemy is None:
            return 0
        return 5 * _enemy_gain_layers(enemy)


# ---------------- 200251 血型吸引 ----------------
class BloodTypeAttraction(TraitHandler):
    trait_id = 200251
    name = "血型吸引"
    desc = "敌方每携带1种系别的技能，自己攻击时威力+10。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx) or ctx.subject is None:
            return (0.0, 0.0)
        n_types = len({s.element for s in ctx.subject.skills})
        return (0.0, 10.0 * n_types)


# ---------------- 200267 涂鸦 ----------------
class Graffiti(TraitHandler):
    trait_id = 200267
    name = "涂鸦"
    desc = "使用非本系技能时威力+50%。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return (0.0, 0.0)
        if ctx.skill.element not in ctx.actor.attributes:
            return (50.0, 0.0)
        return (0.0, 0.0)


# ---------------- 280011 坠星 ----------------
class FallingStar(TraitHandler):
    trait_id = 280011
    name = "坠星"
    desc = "敌方每有1层星陨印记，自己的技能威力+20%。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx):
            return (0.0, 0.0)
        return (20.0 * _enemy_star_stacks(ctx), 0.0)


# ---------------- 280023 破空 ----------------
class BreakSky(TraitHandler):
    trait_id = 280023
    name = "破空"
    desc = "若先于敌方攻击，本次技能威力+75%。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx):
            return (0.0, 0.0)
        if ctx.is_first:
            return (75.0, 0.0)
        return (0.0, 0.0)


# ---------------- 200311 冰雪魂魄 ----------------
class IceSoul(TraitHandler):
    trait_id = 200311
    name = "冰雪魂魄"
    desc = "天气为暴风雪时，冰系技能威力+100%。"
    implemented = True

    def modify_power(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return (0.0, 0.0)
        if ctx.skill.element != ICE:
            return (0.0, 0.0)
        if ctx.weather != BLIZZARD:
            return (0.0, 0.0)
        return (100.0, 0.0)


# ---------------- 200178 身经百练 ----------------
class BattleHardened(TraitHandler):
    trait_id = 200178
    name = "身经百练"
    desc = "己方精灵每应对1次，自己入场时水系和武系技能威力+20%。"
    implemented = True

    def on_counter(self, ctx):
        # 己方精灵应对成功即累计（含自己；场下也累计）
        if ctx.subject is None or ctx.subject.side != ctx.actor.side:
            return
        ctx.add_state("counters", 1)

    def on_entry(self, ctx):
        # 入场时把累计次数快照为当前生效值
        if ctx.subject is not ctx.actor:
            return
        ctx.set_state("active_counters", ctx.state_of("counters", 0))

    def modify_power(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return (0.0, 0.0)
        if ctx.skill.element not in (WATER, FIGHT):
            return (0.0, 0.0)
        return (20.0 * ctx.state_of("active_counters", 0), 0.0)

    def display(self, state, pet):
        n = pet.trait_state.get("active_counters", 0)
        if n <= 0:
            return None
        return [{"name": "水系武系威力", "layers": n, "per": "20%", "gain": True}]


# ---------------- 200181 拨浪鼓 ----------------
class RattleDrum(TraitHandler):
    trait_id = 200181
    name = "拨浪鼓"
    desc = "己方精灵每使用1次状态技能，自己入场时毒系和萌系技能威力+10。"
    implemented = True

    def on_skill_end(self, ctx):
        # 己方精灵（含自己）使用状态技能即累计（场下也累计）
        if ctx.skill is None or ctx.skill.category != 3:
            return
        if ctx.side != ctx.actor.side:
            return
        ctx.add_state("uses", 1)

    def on_entry(self, ctx):
        # 入场时把累计次数快照为当前生效值
        if ctx.subject is not ctx.actor:
            return
        ctx.set_state("active_uses", ctx.state_of("uses", 0))

    def modify_power(self, ctx):
        if not _self(ctx) or ctx.skill is None:
            return (0.0, 0.0)
        if ctx.skill.element not in (POISON, CUTE):
            return (0.0, 0.0)
        return (0.0, 10.0 * ctx.state_of("active_uses", 0))

    def display(self, state, pet):
        n = pet.trait_state.get("active_uses", 0)
        if n <= 0:
            return None
        return [{"name": "毒系萌系威力", "layers": n, "per": "10", "gain": True}]


def register_batch1_power() -> None:
    for cls in (
        ShunFeng,
        MuKong,
        Dazzle,
        Brave,
        PuffChest,
        Resonance,
        StarGaze,
        IceDrill,
        FrozenSoil,
        FluffyStarlight,
        MoonlightJudgment,
        LivingCanvas,
        BloodTypeAttraction,
        Graffiti,
        FallingStar,
        BreakSky,
        IceSoul,
        BattleHardened,
        RattleDrum,
    ):
        register(cls())


register_batch1_power()
