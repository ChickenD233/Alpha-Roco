"""入场/离场类特性（on_entry / on_leave）。

- 200149 专注力    入场首回合，获得物攻+100%（首次行动——任意技能或聚能——后结束）。
- 200244 图书守卫者  入场时若己方魔力为1，获得双攻+100%。
- 200126 洁癖     离场后，自己的增益和减益会被更换入场的精灵继承。
- 200161 做噩梦    敌方精灵离场后，更换入场的精灵失去3能量。
- 200162 噼啪！   入场后首次行动，所选技能使用次数+1（迸发：skill_use_count）。
- 200164 电流刺激  入场后获得迸发：本次攻击技能威力+40（attack_power_flat）。
- 200216 珊瑚骨    敌方精灵离场时，自己获得全技能能耗-3（ENERGY_COST buff 叠加）。
- 200102 地脉      初始能量为0，入场前己方精灵每放1次地系技能，回复3能量
                   （场下积累次数，入场时能量=次数×3，初始为0）。
- 200109 守护者    己方其他精灵每有1层萌化，自己入场时全技能能耗-1
                   （入场时统计己方其他精灵萌化总层数 N，加 ENERGY_COST -N）。
- 200110 水翼推进  己方精灵每使用1次水系技能，自己入场时获得全技能能耗-1
                   （全程累计次数 charges，入场时刷新能耗 buff 为当前累计数，不清零不叠加）。
- 200118 结晶水    初始能量为0，入场前己方精灵每放1次冰系技能，回复3能量
                   （场下积累次数，入场时能量=次数×3，初始为0）。
- 200132 虫群鼓舞  队伍中每有1只其他的虫系精灵，自己入场时获得攻防速+10%
                   （入场时统计己方其他存活虫系精灵数 N，攻/防/速各 +10%×N）。
- 200142 瞳中倒影  自己或其他精灵离场时，自己与更换入场的精灵交换血量百分比
                   （任意精灵离场都触发，on_leave）。
- 200147 散热      初始能量为0，入场前己方精灵每放1次火系技能，回复3能量
                   （场下积累次数，入场时能量=次数×3，初始为0）。
- 200153 蒸汽膨胀  己方精灵每使用1次火系技能，自己入场时获得全技能威力+10。
- 200177 慢热型    初始能量为0，入场前己方精灵每成功应对1次，回复5能量。
- 200139 渴求      入场时获得50%吸血（LIFESTEAL 5 层，每层10%）。
- 200220 稀兽花宝  根据自身血脉，入场时对己方/敌方施加不同效果。

语义说明：
- 专注力：buff 为 NORMAL 时长，持续到首次行动（技能或聚能）之后才结束；下场/重新入场重新触发。
- 洁癖：离场事件在引擎清除普通 buff 之前广播，因此能拿到完整的增益/减益列表，全部复制给入场精灵。
- 地脉：on_skill_end 只积累"己方"（ctx.side==actor.side）的地系技能且自己在场下；入场时兑现。
- 特性带来的所有 buff 均带 source_kind="trait" 标记（不算普通"增益"，见 buffs.Buff.is_gain）。
"""

from __future__ import annotations

import copy

from ... import buffs as B
from ... import burst
from ... import marks as M
from ... import skill_utils
from ... import traits as T
from ...enums import Element as E
from ..base import TraitHandler
from ..registry import register

EARTH = E.EARTH  # 5 地
WATER = E.WATER  # 3 水
ICE = E.ICE  # 6 冰
BUG = E.BUG  # 10 虫
FIRE = E.FIRE  # 2 火


# ---------------- 200149 专注力 ----------------
class Focus(TraitHandler):
    trait_id = 200149
    name = "专注力"
    desc = "入场首回合，获得物攻+100%。"
    implemented = True

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        # 重新入场/复活时清除可能残留的旧 buff 引用
        old = ctx.state_of("buff")
        if old is not None and old in ctx.actor.buffs:
            ctx.actor.buffs.remove(old)
        buff = B.Buff(
            buff_type=B.BuffType.ATK,
            value=10,
            duration=B.DurationKind.NORMAL,
            source_kind="trait",
        )
        ctx.actor.buffs.append(buff)
        ctx.set_state("buff", buff)

    def on_skill_end(self, ctx):
        # 首次使用技能后结束
        self._end(ctx)

    def on_charge(self, ctx):
        # 聚能（蓄能技能）同样视为使用技能，也消耗
        self._end(ctx)

    def on_leave(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        ctx.set_state("buff", None)

    def _end(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        buff = ctx.state_of("buff")
        if buff is None:
            return
        ctx.set_state("buff", None)
        if buff in ctx.actor.buffs:
            ctx.actor.buffs.remove(buff)

    def display(self, state, pet):
        if pet.trait_state.get("buff") is None:
            return None
        return [{"name": "物攻", "layers": 1, "per": "100%", "gain": True}]


# ---------------- 200177 慢热型 ----------------
class SlowWarm(TraitHandler):
    trait_id = 200177
    name = "慢热型"
    desc = "初始能量为0，入场前己方精灵每成功应对1次，回复5能量。"
    implemented = True

    def on_battle_start(self, ctx):
        ctx.actor.energy = 0
        ctx.set_state("counter_charges", 0)

    def on_counter(self, ctx):
        # 只积累自己入场前、己方其他精灵成功应对的次数。
        if ctx.side != ctx.actor.side or ctx.active:
            return
        ctx.add_state("counter_charges", 1)

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        charges = ctx.state_of("counter_charges", 0)
        ctx.actor.energy = min(10, charges * 5)
        ctx.set_state("counter_charges", 0)


# ---------------- 200244 图书守卫者 ----------------
class LibraryGuardian(TraitHandler):
    trait_id = 200244
    name = "图书守卫者"
    desc = "入场时，若自己魔力值为1，自己获得双攻+100%。"
    implemented = True

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        if ctx.state.magic[ctx.actor.side] != 1:
            return
        B.grant_buff(ctx.actor, B.BuffType.ATK, 10, source_kind="trait")
        B.grant_buff(ctx.actor, B.BuffType.SPATK, 10, source_kind="trait")

    def display(self, state, pet):
        layers = sum(b.value for b in pet.buffs
                     if b.buff_type == B.BuffType.ATK and b.source_kind == "trait")
        if layers <= 0:
            return None
        return [{"name": "双攻", "layers": layers, "per": "10%", "gain": True}]


# ---------------- 200245 构装契约者 ----------------
class ConstructContractor(TraitHandler):
    trait_id = 200245
    name = "构装契约者"
    desc = "入场时，若敌方魔力值为1，自己获得双防+100%。"
    implemented = True

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        enemy_side = "B" if ctx.actor.side == "A" else "A"
        if ctx.state.magic[enemy_side] != 1:
            return
        B.grant_buff(ctx.actor, B.BuffType.DEF, 10, source_kind="trait")
        B.grant_buff(ctx.actor, B.BuffType.SPDEF, 10, source_kind="trait")

    def display(self, state, pet):
        layers = sum(b.value for b in pet.buffs
                     if b.buff_type == B.BuffType.DEF and b.source_kind == "trait")
        if layers <= 0:
            return None
        return [{"name": "双防", "layers": layers, "per": "10%", "gain": True}]


# ---------------- 200126 洁癖 ----------------
class Cleanliness(TraitHandler):
    trait_id = 200126
    name = "洁癖"
    desc = "离场后，自己的增益和减益会被更换入场的精灵继承。"
    implemented = True

    def on_leave(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        incoming = ctx.extra.get("incoming")
        inherited = [b for b in ctx.actor.buffs if b.is_gain() or b.is_loss()]
        if incoming is None:
            # 技能脱离时替补尚未选定；在普通状态被清除前保存快照。
            ctx.state.pending_entry_buffs[ctx.actor.side].extend(
                (b.buff_type, b.value, b.duration, b.source_side, b.source_pet, b.source_kind)
                for b in inherited
            )
            return
        for buff in inherited:
            B.grant_buff(
                incoming, buff.buff_type, buff.value, buff.duration, ctx.state.turn,
                buff.source_side, buff.source_pet, buff.source_kind,
            )


# ---------------- 200160 搜刮 ----------------
class Scavenge(TraitHandler):
    trait_id = 200160
    name = "搜刮"
    desc = "敌方每使用1次「聚能」技能或更换精灵，自己入场时获得魔攻+20%。"
    implemented = True

    def _enemy(self, ctx):
        return ctx.subject is not None and ctx.subject.side != ctx.actor.side

    def on_charge(self, ctx):
        # 敌方聚能（场下也计数，charge 广播覆盖场下）
        if self._enemy(ctx):
            ctx.add_state("charges", 1)

    def on_leave(self, ctx):
        # 敌方更换精灵（换入者与离场者不同才算"更换"；返场 incoming 是自己，不算）
        if not self._enemy(ctx):
            return
        incoming = ctx.extra.get("incoming")
        if incoming is None or incoming is ctx.subject:
            return
        ctx.add_state("charges", 1)

    def on_faint(self, ctx):
        # 敌方力竭（随后换入新精灵 = 更换精灵）
        if self._enemy(ctx):
            ctx.add_state("charges", 1)

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        # 按累计次数刷新魔攻 buff（2层/次 = +20%/次）；次数保留、不随入场清零
        old = ctx.state_of("buff")
        if old is not None and old in ctx.actor.buffs:
            ctx.actor.buffs.remove(old)
        charges = ctx.state_of("charges", 0)
        if charges <= 0:
            ctx.set_state("buff", None)
            return
        buff = B.Buff(
            buff_type=B.BuffType.SPATK,
            value=charges * 2,
            duration=B.DurationKind.NORMAL,
            source_kind="trait",
        )
        ctx.actor.buffs.append(buff)
        ctx.set_state("buff", buff)

    def display(self, state, pet):
        n = pet.trait_state.get("charges", 0)
        if n <= 0:
            return None
        return [{"name": "魔攻", "layers": n, "per": "20%", "gain": True}]


# ---------------- 280036 扫荡 ----------------
class Raid(Scavenge):
    trait_id = 280036
    name = "扫荡"
    desc = "敌方每使用1次「聚能」技能或更换精灵，自己入场时获得魔攻+20%和魔防+10%。"
    implemented = True

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        # 计数逻辑继承自搜刮；入场时刷新魔攻（2层/次）与魔防（1层/次）
        old = ctx.state_of("buffs") or []
        for b in old:
            if b in ctx.actor.buffs:
                ctx.actor.buffs.remove(b)
        charges = ctx.state_of("charges", 0)
        if charges <= 0:
            ctx.set_state("buffs", None)
            return
        new_buffs = [
            B.Buff(buff_type=B.BuffType.SPATK, value=charges * 2,
                   duration=B.DurationKind.NORMAL, source_kind="trait"),
            B.Buff(buff_type=B.BuffType.SPDEF, value=charges,
                   duration=B.DurationKind.NORMAL, source_kind="trait"),
        ]
        ctx.actor.buffs.extend(new_buffs)
        ctx.set_state("buffs", new_buffs)

    def display(self, state, pet):
        n = pet.trait_state.get("charges", 0)
        if n <= 0:
            return None
        return [
            {"name": "魔攻", "layers": n, "per": "20%", "gain": True},
            {"name": "魔防", "layers": n, "per": "10%", "gain": True},
        ]


# ---------------- 200156 下黑手 ----------------
class SuckerPunch(TraitHandler):
    trait_id = 200156
    name = "下黑手"
    desc = "敌方精灵离场后，更换入场的精灵获得5层中毒。"
    implemented = True

    def on_leave(self, ctx):
        # 敌方精灵离场时，给更换入场的精灵施加 5 层中毒（debuff 标记，含毒系免疫检查）
        if ctx.subject is None or ctx.subject.side == ctx.actor.side:
            return
        incoming = ctx.extra.get("incoming")
        if incoming is None:
            return
        B.grant_buff(
            incoming,
            B.BuffType.POISON,
            5,
            source_side=ctx.actor.side,
            source_pet=ctx.actor.name,
            source_kind="debuff",
        )


# ---------------- 200325 旧玩具 ----------------
class OldToy(TraitHandler):
    trait_id = 200325
    name = "旧玩具"
    desc = "己方精灵每使用过1个不同系别的技能，自己入场时获得双攻+10%。"
    implemented = True

    def on_skill_end(self, ctx):
        # 己方精灵（含自己，场下时队友使用也计入）使用技能：按系别去重累计
        if ctx.skill is None:
            return
        if ctx.side != ctx.actor.side:
            return
        elements = ctx.state_of("elements", None)
        if elements is None:
            elements = set()
            ctx.set_state("elements", elements)
        elements.add(ctx.skill.element)

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        n = len(ctx.state_of("elements", set()))
        # 刷新为当前不同系别数：移除本特性的双攻 buff 再按 n 重加（不叠加）
        ctx.actor.buffs = [
            b
            for b in ctx.actor.buffs
            if not (
                b.buff_type in (B.BuffType.ATK, B.BuffType.SPATK)
                and b.source_kind == "trait"
            )
        ]
        if n <= 0:
            return
        B.grant_buff(ctx.actor, B.BuffType.ATK, n, source_kind="trait")
        B.grant_buff(ctx.actor, B.BuffType.SPATK, n, source_kind="trait")

    def display(self, state, pet):
        n = len(pet.trait_state.get("elements", set()))
        if n <= 0:
            return None
        return [{"name": "双攻", "layers": n, "per": "10%", "gain": True}]


# ---------------- 200161 做噩梦 ----------------
class Nightmare(TraitHandler):
    trait_id = 200161
    name = "做噩梦"
    desc = "敌方精灵离场后，更换入场的精灵失去3能量。"
    implemented = True

    def on_leave(self, ctx):
        # 敌方精灵离场（subject=离场精灵，incoming=入场精灵）；自己/己方离场不触发
        if ctx.subject is None or ctx.subject.side == ctx.actor.side:
            return
        incoming = ctx.extra.get("incoming")
        if incoming is None:
            return
        incoming.energy = max(0, incoming.energy - 3)


# ---------------- 200162 噼啪！ ----------------
class Crackle(TraitHandler):
    trait_id = 200162
    name = "噼啪！"
    desc = "入场后首次行动，所选技能使用次数+1。"
    implemented = True

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        burst.add_burst(ctx.actor, "skill_use_count", 1)


# ---------------- 200164 电流刺激 ----------------
class CurrentStimulus(TraitHandler):
    trait_id = 200164
    name = "电流刺激"
    desc = "携带的攻击技能获得迸发：威力+40。"
    implemented = True

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        burst.add_burst(ctx.actor, "attack_power_flat", 40)


# ---------------- 200216 珊瑚骨 ----------------
class CoralBone(TraitHandler):
    trait_id = 200216
    name = "珊瑚骨"
    desc = "敌方精灵离场时，自己获得全技能能耗-3。"
    implemented = True

    def on_leave(self, ctx):
        # 自己必须在场；敌方精灵离场（subject=离场精灵）；己方离场不触发
        if not ctx.is_active():
            return
        if ctx.subject is None or ctx.subject.side == ctx.actor.side:
            return
        B.grant_buff(ctx.actor, B.BuffType.ENERGY_COST, -3, source_kind="trait")
        ctx.add_state("triggers", 1)

    def display(self, state, pet):
        n = pet.trait_state.get("triggers", 0)
        if n <= 0:
            return None
        return [{"name": "能耗", "layers": n, "per": "3", "gain": False}]


# ---------------- 200169 蓄电池 ----------------
class Battery(TraitHandler):
    trait_id = 200169
    name = "蓄电池"
    desc = "每入场1次，永久获得双攻+30%。"
    implemented = True

    def on_entry(self, ctx):
        # 每次入场（首发/主动换人/力竭替换/脱离换人/返场）都叠加双攻+30%（PERMANENT）
        if ctx.subject is not ctx.actor:
            return
        B.grant_buff(ctx.actor, B.BuffType.ATK, 3, B.DurationKind.PERMANENT,
                     source_kind="trait")
        B.grant_buff(ctx.actor, B.BuffType.SPATK, 3, B.DurationKind.PERMANENT,
                     source_kind="trait")
        ctx.add_state("entries", 1)

    def display(self, state, pet):
        n = pet.trait_state.get("entries", 0)
        if n <= 0:
            return None
        return [{"name": "双攻", "layers": n, "per": "30%", "gain": True}]


# ---------------- 200102 地脉 ----------------
class EarthVein(TraitHandler):
    trait_id = 200102
    name = "地脉"
    desc = "初始能量为0，入场前己方精灵每放1次地系技能，回复3能量。"
    implemented = True

    def on_skill_end(self, ctx):
        # 场下积累：己方（side==actor.side）精灵使用地系技能；自己入场后不再积累
        if ctx.skill is None or ctx.skill.element != EARTH:
            return
        if ctx.side != ctx.actor.side:
            return
        if ctx.is_active():
            return
        ctx.add_state("charges", 1)

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        # 入场兑现：能量 = 积累次数 × 3（初始能量为0，入场时从0起只加积累）
        charges = ctx.state_of("charges", 0)
        ctx.actor.energy = charges * 3
        ctx.set_state("charges", 0)

    def display(self, state, pet):
        n = pet.trait_state.get("charges", 0)
        if n <= 0:
            return None
        return [{"name": "积累", "layers": n, "per": "3", "gain": True}]


# ---------------- 200109 守护者 ----------------
class Guardian(TraitHandler):
    trait_id = 200109
    name = "守护者"
    desc = "己方其他精灵每有1层萌化，自己入场时全技能能耗-1。"
    implemented = True

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        n = 0
        for i, pet in enumerate(ctx.state.teams[ctx.actor.side]):
            if pet is ctx.actor:
                continue
            if pet.hp <= 0:
                continue  # 已死亡的精灵不计入
            n += B.get_buff_value(pet, B.BuffType.CUTE)
        if n <= 0:
            return
        B.grant_buff(ctx.actor, B.BuffType.ENERGY_COST, -n, source_kind="trait")
        ctx.set_state("layers", n)

    def display(self, state, pet):
        n = pet.trait_state.get("layers", 0)
        if n <= 0:
            return None
        return [{"name": "能耗", "layers": n, "per": "1", "gain": False}]


# ---------------- 200110 水翼推进 ----------------
class HydroPropulsion(TraitHandler):
    trait_id = 200110
    name = "水翼推进"
    desc = "己方精灵每使用1次水系技能，自己入场时获得全技能能耗-1。"
    implemented = True

    def on_skill_end(self, ctx):
        # 全程累计：己方精灵使用水系技能（自己在场与否都累计）
        if ctx.skill is None or ctx.skill.element != WATER:
            return
        if ctx.side != ctx.actor.side:
            return
        ctx.add_state("charges", 1)

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        charges = ctx.state_of("charges", 0)
        if charges <= 0:
            return
        # 刷新为当前累计数：先移除旧的特性能耗 buff，再加新的（不叠加）
        ctx.actor.buffs = [
            b
            for b in ctx.actor.buffs
            if not (b.buff_type == B.BuffType.ENERGY_COST and b.source_kind == "trait")
        ]
        B.grant_buff(ctx.actor, B.BuffType.ENERGY_COST, -charges, source_kind="trait")

    def display(self, state, pet):
        n = pet.trait_state.get("charges", 0)
        if n <= 0:
            return None
        return [{"name": "积累", "layers": n, "per": "1", "gain": True}]


# ---------------- 280005 水翼飞升 ----------------
class HydroAscend(TraitHandler):
    trait_id = 280005
    name = "水翼飞升"
    desc = "己方精灵每使用1次水系技能，自己入场时获得全技能能耗-1，且能耗为0的技能威力+30%。"
    implemented = True

    def on_skill_end(self, ctx):
        # 全程累计：己方精灵使用水系技能（自己在场与否都累计）
        if ctx.skill is None or ctx.skill.element != WATER:
            return
        if ctx.side != ctx.actor.side:
            return
        ctx.add_state("charges", 1)

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        charges = ctx.state_of("charges", 0)
        if charges <= 0:
            return
        # 刷新为当前累计数：先移除旧的特性能耗 buff，再加新的（不叠加）
        ctx.actor.buffs = [
            b
            for b in ctx.actor.buffs
            if not (b.buff_type == B.BuffType.ENERGY_COST and b.source_kind == "trait")
        ]
        B.grant_buff(ctx.actor, B.BuffType.ENERGY_COST, -charges, source_kind="trait")

    def modify_power(self, ctx):
        # 真实能耗为 0 的技能威力+30%（含被本特性降到 0 的技能）
        if ctx.target is not ctx.actor or ctx.skill is None:
            return (0.0, 0.0)
        if skill_utils.true_energy_cost(ctx.state, ctx.actor, ctx.skill) != 0:
            return (0.0, 0.0)
        return (30.0, 0.0)

    def display(self, state, pet):
        n = pet.trait_state.get("charges", 0)
        if n <= 0:
            return None
        return [{"name": "积累", "layers": n, "per": "1", "gain": True}]


# ---------------- 200118 结晶水 ----------------
class CrystalWater(TraitHandler):
    trait_id = 200118
    name = "结晶水"
    desc = "初始能量为0，入场前己方精灵每放1次冰系技能，回复3能量。"
    implemented = True

    def on_skill_end(self, ctx):
        # 场下积累：己方精灵使用冰系技能；自己入场后不再积累
        if ctx.skill is None or ctx.skill.element != ICE:
            return
        if ctx.side != ctx.actor.side:
            return
        if ctx.is_active():
            return
        ctx.add_state("charges", 1)

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        charges = ctx.state_of("charges", 0)
        ctx.actor.energy = charges * 3
        ctx.set_state("charges", 0)

    def display(self, state, pet):
        n = pet.trait_state.get("charges", 0)
        if n <= 0:
            return None
        return [{"name": "积累", "layers": n, "per": "3", "gain": True}]


# ---------------- 200132 虫群鼓舞 ----------------
class SwarmCheer(TraitHandler):
    trait_id = 200132
    name = "虫群鼓舞"
    desc = "队伍中每有1只其他的虫系精灵，自己入场时获得攻防速+10%。"
    implemented = True

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        n = 0
        for i, pet in enumerate(ctx.state.teams[ctx.actor.side]):
            if pet is ctx.actor:
                continue
            if BUG in pet.attributes:
                n += 1
        if n <= 0:
            return
        B.grant_buff(ctx.actor, B.BuffType.ATK, n, source_kind="trait")
        B.grant_buff(ctx.actor, B.BuffType.DEF, n, source_kind="trait")
        B.grant_buff(ctx.actor, B.BuffType.SPEED_PERCENT, n, source_kind="trait")
        ctx.set_state("count", n)

    def display(self, state, pet):
        n = pet.trait_state.get("count", 0)
        if n <= 0:
            return None
        return [{"name": "攻防速", "layers": n, "per": "10%", "gain": True}]


# ---------------- 200142 瞳中倒影 ----------------
class MirrorReflection(TraitHandler):
    trait_id = 200142
    name = "瞳中倒影"
    desc = "自己或其他精灵离场时，自己与更换入场的精灵交换血量百分比。"
    implemented = True

    def on_leave(self, ctx):
        # 任意精灵（含敌方）离场都触发；与 incoming 交换血量百分比
        incoming = ctx.extra.get("incoming")
        if incoming is None:
            return
        own_pct = ctx.actor.hp / ctx.actor.max_hp
        inc_pct = incoming.hp / incoming.max_hp
        ctx.actor.hp = round(ctx.actor.max_hp * inc_pct)
        incoming.hp = round(incoming.max_hp * own_pct)


# ---------------- 200253 吉利丁片 ----------------
class GelatinSheet(TraitHandler):
    trait_id = 200253
    name = "吉利丁片"
    desc = "离场后，更换入场的精灵获得双防+20%且免疫冻结。"
    implemented = True

    def on_leave(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        incoming = ctx.extra.get("incoming")
        effects = (
            (B.BuffType.DEF, 2, ""),
            (B.BuffType.SPDEF, 2, ""),
            (B.BuffType.FREEZE_IMMUNE, 1, "trait"),
        )
        if incoming is None:
            ctx.state.pending_entry_buffs[ctx.actor.side].extend(
                (buff_type, value, B.DurationKind.NORMAL, ctx.actor.side, ctx.actor.name, source_kind)
                for buff_type, value, source_kind in effects
            )
            return
        for buff_type, value, source_kind in effects:
            B.grant_buff(
                incoming, buff_type, value, source_side=ctx.actor.side,
                source_pet=ctx.actor.name, source_kind=source_kind,
            )


# ---------------- 200254 美拉德反应 ----------------
class MaillardReaction(TraitHandler):
    trait_id = 200254
    name = "美拉德反应"
    desc = "离场后，更换入场的精灵获得双攻+20%且免疫灼烧。"
    implemented = True

    def on_leave(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        incoming = ctx.extra.get("incoming")
        if incoming is None:
            return
        B.grant_buff(incoming, B.BuffType.ATK, 2)
        B.grant_buff(incoming, B.BuffType.SPATK, 2)
        B.grant_buff(
            incoming, B.BuffType.BURN_IMMUNE, 1,
            source_side=ctx.actor.side, source_pet=ctx.actor.name, source_kind="trait",
        )


# ---------------- 200202 茶多酚 ----------------
class TeaPolyphenol(TraitHandler):
    trait_id = 200202
    name = "茶多酚"
    desc = "离场后，更换入场的精灵回复20%生命且免疫寄生。"
    implemented = True

    def on_leave(self, ctx):
        # 自己离场时，更换入场的精灵回复20%最大生命并获得免疫寄生
        if ctx.subject is not ctx.actor:
            return
        incoming = ctx.extra.get("incoming")
        if incoming is None:
            return
        heal = int(incoming.max_hp * 0.2)
        incoming.hp = min(incoming.max_hp, incoming.hp + heal)
        B.grant_buff(
            incoming, B.BuffType.LEECH_IMMUNE, 1,
            source_side=ctx.actor.side, source_pet=ctx.actor.name, source_kind="trait",
        )


# ---------------- 200163 快充 ----------------
class QuickCharge(TraitHandler):
    trait_id = 200163
    name = "快充"
    desc = "离场时回复10能量。"
    implemented = True

    def on_leave(self, ctx):
        # 自己离场时回复 10 能量（受能量上限修正，用 grant_energy 统一）
        if ctx.subject is not ctx.actor:
            return
        T.grant_energy(ctx.state, ctx.actor, 10)


# ---------------- 200153 蒸汽膨胀 ----------------
class SteamExpansion(TraitHandler):
    trait_id = 200153
    name = "蒸汽膨胀"
    desc = "己方精灵每使用1次火系技能，自己入场时获得全技能威力+10。"
    implemented = True

    def on_skill_end(self, ctx):
        if ctx.skill is None or ctx.skill.element != FIRE:
            return
        if ctx.side != ctx.actor.side:
            return
        ctx.add_state("charges", 1)

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        old = ctx.state_of("buff")
        if old is not None and old in ctx.actor.buffs:
            ctx.actor.buffs.remove(old)
        charges = ctx.state_of("charges", 0)
        if charges <= 0:
            ctx.set_state("buff", None)
            return
        buff = B.Buff(
            buff_type=B.BuffType.SKILL_POWER_FLAT,
            value=charges * 10,
            duration=B.DurationKind.NORMAL,
            source_kind="trait",
        )
        ctx.actor.buffs.append(buff)
        ctx.set_state("buff", buff)

    def display(self, state, pet):
        n = pet.trait_state.get("charges", 0)
        if n <= 0:
            return None
        return [{"name": "技能威力", "layers": n, "per": "10", "gain": True}]


# ---------------- 200147 散热 ----------------
class HeatDissipation(TraitHandler):
    trait_id = 200147
    name = "散热"
    desc = "初始能量为0，入场前己方精灵每放1次火系技能，回复3能量。"
    implemented = True

    def on_skill_end(self, ctx):
        # 场下积累：己方精灵使用火系技能；自己入场后不再积累
        if ctx.skill is None or ctx.skill.element != FIRE:
            return
        if ctx.side != ctx.actor.side:
            return
        if ctx.is_active():
            return
        ctx.add_state("charges", 1)

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        charges = ctx.state_of("charges", 0)
        ctx.actor.energy = charges * 3
        ctx.set_state("charges", 0)

    def display(self, state, pet):
        n = pet.trait_state.get("charges", 0)
        if n <= 0:
            return None
        return [{"name": "积累", "layers": n, "per": "3", "gain": True}]


# ---------------- 200139 渴求 ----------------
class Thirst(TraitHandler):
    trait_id = 200139
    name = "渴求"
    desc = "入场时获得50%吸血。"
    implemented = True

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        B.grant_buff(ctx.actor, B.BuffType.LIFESTEAL, 5, source_kind="trait")
        ctx.set_state("active", True)

    def display(self, state, pet):
        if not pet.trait_state.get("active"):
            return None
        return [{"name": "吸血", "layers": 5, "per": "10%", "gain": True}]


# ---------------- 200220 稀兽花宝 ----------------
class RareBeastFlower(TraitHandler):
    # 兽花蕾的特性给的 buff 不用标记 source_kind = "trait"
    trait_id = 200220
    name = "稀兽花宝"
    desc = "根据自己的血脉，入场时获得不同效果。"
    implemented = True

    def _self_buff(self, ctx, buff_type, value):
        B.grant_buff(ctx.actor, buff_type, value)

    def _enemy_buff(self, ctx, target, buff_type, value):
        B.grant_buff(
            target,
            buff_type,
            value,
            source_side=ctx.actor.side,
            source_pet=ctx.actor.name,
            source_kind="debuff",
        )

    def on_entry(self, ctx):
        if ctx.subject is not ctx.actor:
            return
        target = ctx.opponent()
        if target is None:
            return

        bloodline = ctx.actor.bloodline
        if bloodline is None:
            bloodline = ctx.actor.attributes[0] if ctx.actor.attributes else None
        if bloodline is None:
            return

        display = []
        if bloodline == E.NORMAL:
            self._self_buff(ctx, B.BuffType.SKILL_POWER_FLAT, 40)
            display.append({"name": "技能威力", "layers": 1, "per": "40", "gain": True})
        elif bloodline == E.EARTH:
            self._enemy_buff(ctx, target, B.BuffType.SPEED, -6)
            self._enemy_buff(ctx, target, B.BuffType.HIT_COUNT_FLAT, -3)
        elif bloodline == E.ICE:
            B.grant_buff(
                target,
                B.BuffType.FREEZE,
                2,
                source_side=ctx.actor.side,
                source_pet=ctx.actor.name,
                source_kind="debuff",
            )
        elif bloodline == E.CUTE:
            self._enemy_buff(ctx, target, B.BuffType.ATK, -6)
            self._enemy_buff(ctx, target, B.BuffType.SPATK, -6)
        elif bloodline == E.FIRE:
            self._enemy_buff(ctx, target, B.BuffType.BURN, 6)
        elif bloodline == E.DRAGON:
            self._enemy_buff(ctx, target, B.BuffType.SPDEF, -8)
        elif bloodline == E.MECH:
            self._self_buff(ctx, B.BuffType.DEF, 6)
            self._self_buff(ctx, B.BuffType.SPDEF, 6)
            display.append({"name": "双防", "layers": 6, "per": "10%", "gain": True})
        elif bloodline == E.WATER:
            self._self_buff(ctx, B.BuffType.ENERGY_COST, -2)
            display.append({"name": "能耗", "layers": 2, "per": "1", "gain": False})
        elif bloodline == E.ELECTRIC:
            self._self_buff(ctx, B.BuffType.SPEED, 10)
            display.append({"name": "速度", "layers": 10, "per": "10", "gain": True})
        elif bloodline == E.FANTASY:
            M.add_mark(ctx.state, target.side, 7, 2)
        elif bloodline == E.GRASS:
            heal = int(ctx.actor.max_hp * 0.2)
            ctx.actor.hp = min(ctx.actor.max_hp, ctx.actor.hp + heal)
            display.append({"name": "回复", "layers": 20, "per": "%", "gain": True})
        elif bloodline == E.POISON:
            self._enemy_buff(ctx, target, B.BuffType.POISON, 2)
        elif bloodline == E.LIGHT:
            self._self_buff(ctx, B.BuffType.SPATK, 8)
            display.append({"name": "魔攻", "layers": 8, "per": "10%", "gain": True})
        elif bloodline == E.BUG:
            self._enemy_buff(ctx, target, B.BuffType.DEF, -8)
        elif bloodline == E.DARK:
            self._self_buff(ctx, B.BuffType.LIFESTEAL, 5)
            display.append({"name": "吸血", "layers": 5, "per": "10%", "gain": True})
        elif bloodline == E.FIGHT:
            self._self_buff(ctx, B.BuffType.ATK, 8)
            display.append({"name": "物攻", "layers": 8, "per": "10%", "gain": True})
        elif bloodline == E.GHOST:
            target.energy = max(0, target.energy - 2)
        elif bloodline == E.WING:
            self._self_buff(ctx, B.BuffType.HIT_COUNT_FLAT, 3)
            display.append({"name": "连击数", "layers": 3, "per": "1", "gain": True})

        ctx.set_state("bloodline", int(bloodline))
        ctx.set_state("display", display)

    def display(self, state, pet):
        idx = state.active[pet.side]
        if idx < 0 or state.teams[pet.side][idx] is not pet:
            return None
        return pet.trait_state.get("display")


def register_entry() -> None:
    for cls in (
        Focus,
        SlowWarm,
        LibraryGuardian,
        ConstructContractor,
        Cleanliness,
        Nightmare,
        Crackle,
        CurrentStimulus,
        CoralBone,
        EarthVein,
        Guardian,
        HydroPropulsion,
        SteamExpansion,
        CrystalWater,
        SwarmCheer,
        MirrorReflection,
        HeatDissipation,
        Thirst,
        RareBeastFlower,
        GelatinSheet,
        MaillardReaction,
        TeaPolyphenol,
        QuickCharge,
        Scavenge,
        Raid,
        Battery,
        SuckerPunch,
        HydroAscend,
        OldToy,
    ):
        register(cls())


register_entry()
