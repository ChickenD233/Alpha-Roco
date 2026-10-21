"""状态类技能（category 3，共 157 条）。

与 defense.py / attack.py 同约定：效果全部以代码定义，desc 不参与运行时；
能耗/属性沿用 skills.json。

进度以 ``data/skills.json`` 的 ``done``/``tested`` 标志为准。
"""

from __future__ import annotations

from ... import buffs, skill_utils
from ...data_loader import build_skill_map, load_spirits
from ..ops import (
    AddMark,
    ApplyBuff,
    CanCute,
    ConvertBuffs,
    COUNTER_DEFENSE,
    DispelBuffs,
    DispelMarks,
    DoubleBuffs,
    ENEMY,
    EnemySwitchedThisTurn,
    FIELD,
    GROWTH_ENERGY_COST,
    GainEnergy,
    Heal,
    LEAVE_BOTH,
    LEAVE_SELF,
    LeaveField,
    OpsSkill,
    Overload,
    RandomStatBuffs,
    Reenter,
    Repeat,
    SELF,
    SELF_BACKLINE,
    SkillGrowth,
    When,
    apply_effects,
    hit_count_of,
    per_recorded,
    skill_by_id,
)
from ..registry import register


# ---------------- 纯 buff 状态技（无其他效果） ----------------
class BuffStatus(OpsSkill):
    """纯 buff 状态技：desc 只有"自己/敌方获得 X"，无其他效果。

    数值换算约定（填表人负责）：属性增益 10% = 1 层（get_stat_multiplier =
    1 + 层数×0.1）；速度每层 +10；冻结由 add_buff 强制 PERMANENT（生命变动或获得冻结后
    实时检查斩杀，见 buffs.check_freeze）；其余常规时长的增益离场清除（B28）、
    可被驱散（S26：全部增益/减益，含永久时长；特性自身的展示效果不算增益/减益）。新增同类技能只在对应表加一行。
    """

    def __init__(
        self, skill_id: int, name: str, buff_type: str, value: int, target: str
    ):
        self.skill_id = skill_id
        self.name = name
        self.category = 3
        self.status_effects = (
            ApplyBuff(buff_type=buff_type, value=value, target=target),
        )
        self.implemented = True


# 纯给敌方上 buff/debuff
for _handler in (
    BuffStatus(7090160, "霜降", buffs.BuffType.FREEZE, 4, ENEMY),
    BuffStatus(7120120, "毒孢子", buffs.BuffType.POISON, 5, ENEMY),
    BuffStatus(7040260, "引燃", buffs.BuffType.BURN, 10, ENEMY),
    BuffStatus(7020770, "退化", buffs.BuffType.CUTE, 1, ENEMY),
):
    register(_handler)

# 纯给自己上 buff
for _handler in (
    BuffStatus(7020620, "力量增效", buffs.BuffType.ATK, 10, SELF),
    BuffStatus(7020630, "魔法增效", buffs.BuffType.SPATK, 7, SELF),
    BuffStatus(7050220, "润泽", buffs.BuffType.SPATK, 19, SELF),
    BuffStatus(7150120, "乘风", buffs.BuffType.SPEED, 12, SELF),
    BuffStatus(7150140, "暴风眼", buffs.BuffType.HIT_COUNT_PERCENT, 100, SELF),
):
    register(_handler)


# ---------------- 纯印记状态技（无其他效果） ----------------
class MarkStatus(OpsSkill):
    """纯印记状态技：desc 只有"自己/敌方获得N层X印记"，无其他效果。

    印记以阵营为单位：target 解析到对应阵营后由 marks.add_mark 按印记
    正/负属性写入对应槽位（data/marks.json），单条印记上限 99（B30）。
    新增同类技能只在对应表加一行。
    """

    def __init__(
        self, skill_id: int, name: str, mark_id: int, stacks: int, target: str
    ):
        self.skill_id = skill_id
        self.name = name
        self.category = 3
        self.status_effects = (AddMark(mark_id=mark_id, stacks=stacks, target=target),)
        self.implemented = True


# 纯给自己上印记
for _handler in (
    MarkStatus(7030300, "光合作用", 10, 1, SELF),
    MarkStatus(7050240, "打湿", 11, 1, SELF),
):
    register(_handler)

# 纯给敌方上印记
for _handler in (
    MarkStatus(7090330, "速冻", 3, 2, ENEMY),
    MarkStatus(7020740, "棘刺", 1, 1, ENEMY),
):
    register(_handler)


# ---------------- 7020640 休息回复 ----------------
class RestHeal(OpsSkill):
    """自己回复30%生命。

    回复量 = int(最大生命 × 30%)，经 ``traits.query_heal`` 修正（如"戏耍"可把回复
    转为敌方扣血），不超过最大生命。
    """

    skill_id = 7020640
    name = "休息回复"
    category = 3
    implemented = True

    status_effects = (Heal(percent=0.30),)


register(RestHeal())


# ---------------- 7020840 借用 ----------------
class Borrow(OpsSkill):
    """每回合随机变成己方队伍中其他精灵的技能。

    每回合替换由引擎统一处理（battle._apply_round_replacements_for_pet）：入场即抽取、
    在场时每回合末重随、离场还原为本体。池 = 己方其他**存活**精灵携带的技能，
    按 id 从 skills.json 重建**原始状态**——不带对方的运行时成长
    （对方的水炮已成长到 3 费时，抽到的是原始 5 费）。
    本技能被直接使用时无效果。
    """

    skill_id = 7020840
    name = "借用"
    category = 3
    implemented = True
    round_replace = True

    def round_replace_pool(self, state, pet, skill):
        skill_map = build_skill_map()
        return [
            skill_by_id(s.skill_id)
            for p in state.teams[pet.side]
            if p is not pet and p.hp > 0
            for s in p.skills
            if s.skill_id in skill_map and s.skill_id != 9999999
        ]


register(Borrow())


# ---------------- 7020850 取念 ----------------
class ThoughtSteal(OpsSkill):
    """每回合随机变成敌方任意精灵的技能，且该技能能耗-2。

    池 = 敌方队伍中**存活**精灵携带的技能，同样按 id 从 skills.json 重建
    原始状态（不含对方的运行时成长）；临时技能能耗 -2
    （round_replace_cost_delta）。其余同借用。
    """

    skill_id = 7020850
    name = "取念"
    category = 3
    implemented = True
    round_replace = True
    round_replace_cost_delta = -2

    def round_replace_pool(self, state, pet, skill):
        enemy_side = "B" if pet.side == "A" else "A"
        skill_map = build_skill_map()
        return [
            skill_by_id(s.skill_id)
            for p in state.teams[enemy_side]
            if p.hp > 0
            for s in p.skills
            if s.skill_id in skill_map and s.skill_id != 9999999
        ]


register(ThoughtSteal())


# ---------------- 7060150 镜像反射复制当前技能属性的辅助 ----------------
# 复制逻辑由 MirrorReflection 在防御应对阶段执行；该注释保留技能分组位置。


# ---------------- 7020860 复写 ----------------
class Rewrite(OpsSkill):
    """每回合随机变成自己未携带的技能，且该技能能耗-2。

    池 = 本精灵可习得技能（spirits.json 的 normal 池 + 血脉池中血脉编号与
    当前血脉一致的条目）减去当前携带的技能——不能从血脉技能池中抽取
    非当前血脉的技能（用户口径）；临时技能能耗 -2。其余同借用。
    """

    skill_id = 7020860
    name = "复写"
    category = 3
    implemented = True
    round_replace = True
    round_replace_cost_delta = -2

    def round_replace_pool(self, state, pet, skill):
        spirit = next(
            (sp for sp in load_spirits() if sp["id"] == pet.spirit_id), None
        )
        if spirit is None:
            return []
        carried = {s.skill_id for s in pet.skills}
        skill_map = build_skill_map()
        pool_ids = [
            sid for sid in spirit["skills"]["normal"]
            if sid not in carried and sid in skill_map and sid != 9999999
        ]
        # 血脉技能池：只取血脉编号与当前血脉一致的条目
        for entry in spirit["skills"].get("bloodline", []):
            sid = entry["skillId"]
            if (
                entry["bloodline"] == pet.bloodline
                and sid not in carried
                and sid in skill_map
                and sid != 9999999
            ):
                pool_ids.append(sid)
        return [skill_by_id(sid) for sid in pool_ids]


register(Rewrite())


# ---------------- 7030510 富养化 ----------------
class Eutrophication(OpsSkill):
    """为场下每个精灵回复3能量。

    目标是己方场下精灵（SELF_BACKLINE，不含使用者自身）；回复经
    traits.grant_energy 走能量上限修正与 energy_gain 广播。
    """

    skill_id = 7030510
    name = "富养化"
    category = 3
    implemented = True

    status_effects = (GainEnergy(value=3, target=SELF_BACKLINE),)


register(Eutrophication())


# ---------------- 7110270 加大功率 ----------------
class PowerUp(OpsSkill):
    """自己脱离，替换入场的精灵回复8能量。

    脱离走 LeaveField 登记（B36：行动照常结算，行动后立即离场、暂停等待
    选人）；entry_energy 随暂停上下文传递给续接流程，玩家选定的替补入场
    结算后经 traits.grant_energy 回复（含能量上限与 energy_gain 广播）。
    无存活替补 / 蓄力免疫时脱离不生效，能量也不发。
    """

    skill_id = 7110270
    name = "加大功率"
    category = 3
    implemented = True

    status_effects = (LeaveField(mode=LEAVE_SELF, entry_energy=8),)


register(PowerUp())


# ---------------- 7180190 恶意逃离 ----------------
class MaliciousEscape(OpsSkill):
    """脱离，应对防御：额外使敌方攻击技能能耗+4。

    本体脱离走 LeaveField 登记（B36：行动照常结算、行动后立即离场暂停选人）。
    应对从句延迟到回合末结算（B38）——离场不影响结算（登记先于离场）。
    "攻击技能能耗+4"是只对攻击技能生效的 debuff（BuffType.ATTACK_ENERGY_COST），
    经 skill_utils.energy_modifier_before_traits 计入真实能耗（付费/校验/显示
    同管线），常规时长、离场清除；描述未写持续回合，故不设回合数。
    """

    skill_id = 7180190
    name = "恶意逃离"
    category = 3
    counter_target = COUNTER_DEFENSE
    implemented = True

    status_effects = (LeaveField(mode=LEAVE_SELF),)
    counter_effects = (
        ApplyBuff(buff_type=buffs.BuffType.ATTACK_ENERGY_COST, value=4, target=ENEMY),
    )


register(MaliciousEscape())


# ---------------- 7150260 飞羽 ----------------
class SwiftFeather(OpsSkill):
    """迅捷，驱散敌方1种增益。

    迅捷是静态属性（构造期由 bind_skill 写入 ``skill.swift``）：主动换人入场时
    若能量足够则自动释放，不强制先手。驱散范围涵盖**全部**增益（含永久时长）；
    特性自身的展示效果不是增益，不受影响（S26）。
    """

    skill_id = 7150260
    name = "飞羽"
    category = 3
    swift = True
    implemented = True

    status_effects = (DispelBuffs(kind="buff", count=1, target=ENEMY),)


register(SwiftFeather())


# ---------------- 7021100 晒太阳 ----------------
class Sunbath(OpsSkill):
    """驱散敌方所有增益。

    count=None 表示全部驱散；**全部**增益都在范围内——常规时长与永久时长的
    增益同样会被剥掉。特性自身的展示效果不是增益（另一个类别），不受影响（S26）。
    """

    skill_id = 7021100
    name = "晒太阳"
    category = 3
    implemented = True

    status_effects = (DispelBuffs(kind="buff", count=None, target=ENEMY),)


register(Sunbath())


# ---------------- 7030290 移花接木 ----------------
class Switcheroo(OpsSkill):
    """自己回复15%生命，随后脱离。

    先回复（Heal），随后立即脱离：脱离不取消本次行动——回复照常结算，
    随后立即离场、跳过自己的回合末结算，并由己方重新选择上场精灵
    （引擎回合中暂停、服务端 choose_replacement 续接）。无存活替补时脱离不生效。
    """

    skill_id = 7030290
    name = "移花接木"
    category = 3
    implemented = True

    status_effects = (
        Heal(percent=0.15),
        LeaveField(mode=LEAVE_SELF),
    )


register(Switcheroo())


# ---------------- 7030490 花炮 ----------------
class FlowerCannon(OpsSkill):
    """2连击，每次连击自己获得魔攻+60%。

    分 2 次各施加 6 层魔攻（Repeat），而不是一次施加 12 层——"每次连击"是
    N 次独立的施加，"获得增益时追加"类特性按次数分别响应（同打喷嚏）。
    固有 2 连击 = 基础 1 + hit_count_flat 1，走统一连击查询（hit_count_of）。
    """

    skill_id = 7030490
    name = "花炮"
    category = 3
    hit_count_flat = 1  # 固有 2 连击 = 基础 1 + 1
    implemented = True

    status_effects = (
        Repeat(
            count=hit_count_of,
            effects=(
                ApplyBuff(buff_type=buffs.BuffType.SPATK, value=6),
            ),
        ),
    )


register(FlowerCannon())


# ---------------- 7080190 流沙 ----------------
class Quicksand(OpsSkill):
    """敌方获得3回合禁足，应对防御：敌方获得双防-60%。

    禁足（LOCK）的层数 = 剩余回合数：回合末逐层衰减（buffs._apply_lock），
    3 层即 3 回合无法主动换人（buffs.can_switch）。应对防御（状态克制防御）
    的从句由引擎在 STAGE_COUNTER 回合末结算（B38）；双防-60% = 物防/魔防
    各 -6 层（属性增减益 10%/层）。
    """

    skill_id = 7080190
    name = "流沙"
    category = 3
    counter_target = COUNTER_DEFENSE
    implemented = True

    status_effects = (
        ApplyBuff(buff_type=buffs.BuffType.LOCK, value=3, target=ENEMY),
    )
    counter_effects = (
        ApplyBuff(buff_type=buffs.BuffType.DEF, value=-6, target=ENEMY),
        ApplyBuff(buff_type=buffs.BuffType.SPDEF, value=-6, target=ENEMY),
    )


register(Quicksand())


# ---------------- 7150270 风隐 ----------------
class WindHide(OpsSkill):
    """敌方和自己均脱离，先手-1。

    双方均脱离 = LeaveField(LEAVE_BOTH)：行动照常结算（B36），**结算后立即**进入
    收集阶段——双方先各自盲选替补（服务端在此期间不下发对方的选择），集齐后再按
    先手顺序"先退先入、后退后入"（SPEC B36a）。本技能先手-1，非特殊情况下本回合
    最后一个行动；若对方也有先手-1（如双方都用风隐），则按有效速度拼速、同速随机，
    此时先手方的换人先完成，**对方尚未结算的行动直接作废**（不结算）。
    先手-1 = priority_delta（同后发制人）。
    """

    skill_id = 7150270
    name = "风隐"
    category = 3
    priority_delta = -1
    implemented = True

    status_effects = (LeaveField(mode=LEAVE_BOTH),)


register(WindHide())


# ---------------- 7090470 打喷嚏 ----------------
class Sneeze(OpsSkill):
    """3连击，每次连击敌方获得1层冻结。

    分 N 次各施加 1 层冻结（Repeat），而不是一次施加 N 层——"每次连击"是 N 次独立的
    施加，"敌方获得冻结时追加"类特性（加个雪球 200122）会按次数分别响应。
    连击数走统一查询（hit_count_of = 基础1 + 技能修正 + buff + 特性）：
    固有 3 连击用既有的 hit_count_flat 增量字段给出（+2）。
    """

    skill_id = 7090470
    name = "打喷嚏"
    category = 3
    hit_count_flat = 2  # 固有 3 连击 = 基础 1 + 2
    implemented = True

    status_effects = (
        Repeat(
            count=hit_count_of,
            effects=(
                ApplyBuff(buff_type=buffs.BuffType.FREEZE, value=1, target=ENEMY),
            ),
        ),
    )


register(Sneeze())


# ---------------- 7150300 羽化加速 ----------------
class FeatherAccel(OpsSkill):
    """自己获得全技能威力+20，迅捷。

    威力+20 是固定值增益（SKILL_POWER_FLAT，calc_damage 的威力固定值通道），
    与"全技能威力+N%"的百分比增益（SKILL_POWER_PERCENT）是两条独立 buff——
    描述无 %，按固定值。迅捷是静态属性（同飞羽）：主动换人入场时能量足够
    则自动释放。
    """

    skill_id = 7150300
    name = "羽化加速"
    category = 3
    swift = True
    implemented = True

    status_effects = (ApplyBuff(buff_type=buffs.BuffType.SKILL_POWER_FLAT, value=20),)


register(FeatherAccel())


# ---------------- 7140140 化劲 ----------------
class NeutralizeForce(OpsSkill):
    """自己获得全技能威力+40。

    与羽化加速同一条通道：描述无 %，按固定值增益（SKILL_POWER_FLAT=40），
    不做"40% 威力"的百分比换算（SKILL_POWER_PERCENT 是另一条 buff）。
    """

    skill_id = 7140140
    name = "化劲"
    category = 3
    implemented = True

    status_effects = (ApplyBuff(buff_type=buffs.BuffType.SKILL_POWER_FLAT, value=40),)


register(NeutralizeForce())


# ---------------- 7040500 焚烧烙印 ----------------
class BurnBrand(OpsSkill):
    """驱散双方所有印记，每驱散1层，敌方获得5层灼烧。

    驱散覆盖双方阵营的正/负印记（DispelMarks target=FIELD）；驱散的总层数
    经 record_as 写入 ctx.extra，灼烧层数 = 5 × 总层数，一次施加（一个事件，
    与霜降"获得4层"同粒度）。驱散 0 层时灼烧为 0，add_buff 不生效。
    """

    skill_id = 7040500
    name = "焚烧烙印"
    category = 3
    implemented = True

    status_effects = (
        DispelMarks(count=None, target=FIELD, record_as="dispelled_marks"),
        ApplyBuff(
            buff_type=buffs.BuffType.BURN,
            value=per_recorded("dispelled_marks", 5),
            target=ENEMY,
        ),
    )


register(BurnBrand())


# ---------------- 7090400 冰点 ----------------
class FreezingPoint(OpsSkill):
    """敌方获得5层冻结，应对防御：额外获得5层。

    本体（STAGE_STATUS）施加 5 层；应对防御成功（对方本回合使用防御行动）
    时 STAGE_COUNTER 再追加 5 层（合计 10 层，两次独立施加、两个事件）。
    冻结的永久性与实时斩杀判定同霜降（buffs.check_freeze）。
    """

    skill_id = 7090400
    name = "冰点"
    category = 3
    counter_target = COUNTER_DEFENSE
    implemented = True

    status_effects = (
        ApplyBuff(buff_type=buffs.BuffType.FREEZE, value=5, target=ENEMY),
    )
    counter_effects = (
        ApplyBuff(buff_type=buffs.BuffType.FREEZE, value=5, target=ENEMY),
    )


register(FreezingPoint())


# ---------------- 7160180 赤子之心 ----------------
class InnocentHeart(OpsSkill):
    """自己获得萌化：全技能能耗永久-2。

    "获得萌化：X"族的前置条件是**能获得萌化**（存在前一进化阶段）：已是
    最初阶段时整组效果（含能耗-2）都不执行（When + CanCute 条件门，S18）。
    能萌化时：先给全技能能耗-2（ENERGY_COST 负层、PERMANENT，离场保留 B28），
    萌化在技能结算之后施加（退化一个进化阶段，见 evolution.apply_cute）。
    萌化的 buff_gain 广播自动携带 pre_had（ApplyBuff 的 CUTE 契约）。
    """

    skill_id = 7160180
    name = "赤子之心"
    category = 3
    implemented = True

    status_effects = (
        When(
            condition=CanCute(),
            effects=(
                ApplyBuff(
                    buff_type=buffs.BuffType.ENERGY_COST,
                    value=-2,
                    duration=buffs.DurationKind.PERMANENT,
                ),
                ApplyBuff(buff_type=buffs.BuffType.CUTE, value=1),
            ),
        ),
    )


register(InnocentHeart())


# ---------------- 7170240 嘲弄 ----------------
class Taunt(OpsSkill):
    """自己获得魔攻+90%，若敌方本回合更换精灵，自己获得速度+70。

    属性增益 10%/层 → 魔攻+90% = 9 层；速度每层 +10 → 速度+70 = 7 层。
    换人先于行动结算（step 中 switch 先行），故敌方本回合主动换人在状态
    结算时 entry_turn == 当前回合，EnemySwitchedThisTurn 即刻成立；
    回合中脱离换上（暂停续接，switch_in 刷新 entry_turn）同理成立。
    """

    skill_id = 7170240
    name = "嘲弄"
    category = 3
    implemented = True

    status_effects = (
        ApplyBuff(buff_type=buffs.BuffType.SPATK, value=9),
        When(
            condition=EnemySwitchedThisTurn(),
            effects=(ApplyBuff(buff_type=buffs.BuffType.SPEED, value=7),),
        ),
    )


register(Taunt())


# ---------------- 7130210 贮藏 ----------------
class Storage(OpsSkill):
    """自己获得双攻+50%，每携带1个0能耗技能，额外+50%。

    "0能耗技能"按**实际能耗**判定（true_energy_cost：天气/印记/buff/特性/
    技能自身修正全计入、最后钳制到 0；能耗被修正为非 0 的技能不计入），
    不做 skills.json 原始能耗的字面比较。
    双攻 = 物攻+魔攻各 5 层（属性增益 10%/层），每个 0 能耗技能再各 +5 层。
    """

    skill_id = 7130210
    name = "贮藏"
    category = 3
    implemented = True

    def on_status(self, ctx):
        zero_cost = sum(
            1
            for s in ctx.actor.skills
            if skill_utils.true_energy_cost(ctx.state, ctx.actor, s) == 0
        )
        layers = 5 + 5 * zero_cost
        apply_effects(
            ctx,
            (
                ApplyBuff(buff_type=buffs.BuffType.ATK, value=layers),
                ApplyBuff(buff_type=buffs.BuffType.SPATK, value=layers),
            ),
        )


register(Storage())


# ---------------- 7050400 洗礼 ----------------
class Baptism(OpsSkill):
    """驱散自己的减益，并获得全技能能耗-1。

    驱散自身全部减益（DispelBuffs kind="debuff" count=None；常规时长与永久时长的
    减益同样会被驱散；特性自身的展示效果不是减益，不受影响，S26）；随后获得全技能能耗-1
    （ENERGY_COST 负层 = 降耗增益），常规时长、离场清除。
    """

    skill_id = 7050400
    name = "洗礼"
    category = 3
    implemented = True

    status_effects = (
        DispelBuffs(kind="debuff", count=None, target=SELF),
        ApplyBuff(buff_type=buffs.BuffType.ENERGY_COST, value=-1),
    )


register(Baptism())


# ---------------- 7150320 疾风连袭 ----------------
class GaleFlurry(OpsSkill):
    """释放自己释放过的迅捷技能，其能耗之和的二分之一加至本技能能耗，每次使用后能耗+1。

    翼系状态技，基础能耗 0 取自 skills.json（圣羽翼王专属；飓风特性可让任意
    技能获得迅捷，故重放集合按**当前**迅捷状态实时过滤）。
    - 能耗（动态）= 重放集合（本场释放过的迅捷技能，按技能去重）原始能耗之和
      整除 2 + 使用成长（每次用后 +1，SkillGrowth 写 skill_state）。
    - 效果：付费后按**技能槽位顺序**依次免费重放集合内技能
      （battle.replay_used_swift_skills）——与正常使用同等的基础结算
      （伤害/命中附加/状态效果/减伤），无应对效果；自身永远在集合外（防递归）。
    """

    skill_id = 7150320
    name = "疾风连袭"
    category = 3
    implemented = True

    def modify_energy_cost(self, ctx):
        from ...battle import used_swift_skills

        total = sum(s.energy_cost for s in used_swift_skills(ctx.actor, self.skill_id))
        return super().modify_energy_cost(ctx) + total // 2

    def on_status(self, ctx):
        from ...battle import replay_used_swift_skills

        replay_used_swift_skills(
            ctx.state, ctx.actor, self.skill_id, is_first=ctx.is_first, logs=ctx.logs
        )
        SkillGrowth(field=GROWTH_ENERGY_COST, delta=1).apply(ctx)


register(GaleFlurry())


# ---------------- 7190430 叠加态 ----------------
class Superposition(OpsSkill):
    """自己获得随机16层属性增益，巧变：幻系攻击技能。

    - 随机16层（RandomStatBuffs）：掷16次，每次等概率落到物攻/魔攻/物防/
      魔防/速度之一，按属性汇总后逐种施加（每种属性一次施加、一个事件）。
    - 巧变池 = skills.json 中全部幻系（element 17）攻击技能（category 0/1，
      按结构化字段过滤，不解析 desc，无需人工分组）；用后随机变成其一
      （能耗-1），该临时技能用后还原（引擎 _apply_morph（真正巧变）规则）。
    """

    skill_id = 7190430
    name = "叠加态"
    category = 3
    implemented = True

    status_effects = (RandomStatBuffs(layers=16),)

    def on_bind(self, pet, skill):
        pool = [
            skill_by_id(sid)
            for sid, raw in build_skill_map().items()
            if raw["element"] == 17 and raw["category"] in (0, 1)
        ]
        return {"morph_pool": pool}


register(Superposition())


# ---------------- 7110360 过载回路 ----------------
class OverloadCircuit(OpsSkill):
    """回合结束自己返场，下回合所选技能使用次数+1。

    - 返场成功后获得真实迸发：使用次数 +1，持续到下回合末。由下一次行动消费，
      未消费则到期移除；不使用 overload_next 的隐藏过载计数。
      蓄能也是状态技能行动，使用次数 +1 时执行两次；蓄力技能不参与重复。
    - "回合结束自己返场"：Reenter 登记，引擎在回合末技能结算之后、力竭结算后
      与特性返场同一时点执行（离场+立即入场，完整重跑入场结算）；本回合已入场
      的精灵免疫返场效果（entry_turn == 当前回合）。
    - 回合末结算会对在场精灵的**全部技能**发 STAGE_ROUND_END，故返场用**使用
      回合戳**门控（skill_state，本技能自己的运行时状态）：只有本回合用过本技能
      才返场（与嗜痛"期间"同一手法）。返场后精灵仍在场，戳记不会重复触发。
    """

    skill_id = 7110360
    name = "过载回路"
    category = 3
    implemented = True

    def on_status(self, ctx):
        ctx.set_state("used_turn", ctx.state.turn)

    def on_round_end(self, ctx):
        if ctx.state_of("used_turn") != ctx.state.turn:
            return
        ctx.set_state("used_turn", None)
        apply_effects(ctx, (Reenter(),))
        ctx.extra["reenter_bursts"] = [("skill_use_count", 1)]


register(OverloadCircuit())


# ---------------- 7040440 怒火 ----------------
class Wrath(OpsSkill):
    """自己获得双攻+120%和双防-40%。

    火系（element 2）状态技，能耗 1 取自 skills.json。四条属性增益/减益一次
    结算：双攻各 +12 层、双防各 -4 层（属性 10%/层 = ±10%）。常规时长，故
    离场清除、可被驱散（S26）；减益侧按层数为负，可用"驱散减益"剥掉。
    """

    skill_id = 7040440
    name = "怒火"
    category = 3
    implemented = True

    status_effects = (
        ApplyBuff(buff_type=buffs.BuffType.ATK, value=12, target=SELF),
        ApplyBuff(buff_type=buffs.BuffType.SPATK, value=12, target=SELF),
        ApplyBuff(buff_type=buffs.BuffType.DEF, value=-4, target=SELF),
        ApplyBuff(buff_type=buffs.BuffType.SPDEF, value=-4, target=SELF),
    )


register(Wrath())


# ---------------- 本体给"下一次攻击技能威力"增益 ----------------
def _next_attack_buffs(percent: int = 0, flat: int = 0) -> tuple:
    """把"下一次攻击技能威力"数值包成效果元组（0 值不产生效果）。"""
    effects = []
    if percent:
        effects.append(
            ApplyBuff(buff_type=buffs.BuffType.NEXT_ATTACK_POWER_PERCENT, value=percent)
        )
    if flat:
        effects.append(
            ApplyBuff(buff_type=buffs.BuffType.NEXT_ATTACK_POWER_FLAT, value=flat)
        )
    return tuple(effects)


class NextAttackPowerStatus(OpsSkill):
    """ "下一次攻击时，技能威力+N%（/ +N）"类状态技（本体增益）。

    增益为**一次性**（BuffType.NEXT_ATTACK_POWER_PERCENT / _FLAT）：挂在
    自己身上，由引擎在**下一次攻击技能**用时取走并计入该次威力——本次行动
    （含本技能自身）不受影响，故描述里的"下一次"不需要额外门控。描述带 %
    的走百分比通道（100 = 翻倍），不带 % 的按固定值（1 层 = 1 点威力，同化劲）。
    常规时长：离场清除、可被驱散增益剥掉（与其它常规时长的增益一致）。

    新增同类技能只在下表加一行；带应对从句（"应对X：改为…"）的用子类覆写
    on_counter（见下方 WarmUp）。
    """

    def __init__(self, skill_id: int, name: str, percent: int = 0, flat: int = 0):
        self.skill_id = skill_id
        self.name = name
        self.category = 3
        self.status_effects = _next_attack_buffs(percent, flat)
        self.implemented = True


# ---------------- 7040270 热身 ----------------
class WarmUp(NextAttackPowerStatus):
    """下一次攻击技能威力翻倍，应对防御：改为威力变为 4 倍。

    本体先给 +100%（翻倍）；应对防御成功时在回合末（B38 应对从句）把这份增益
    **替换**为 +300%（4 倍）——先 remove 再 apply，故中途被驱散也不影响最终
    数值（是"改为"而非叠加）。
    """

    counter_target = COUNTER_DEFENSE

    def __init__(self):
        super().__init__(7040270, "热身", percent=100)

    def on_counter(self, ctx):
        if ctx.counter_category != COUNTER_DEFENSE:
            return
        buffs.remove_buff(ctx.actor, buffs.BuffType.NEXT_ATTACK_POWER_PERCENT)
        apply_effects(
            ctx,
            _next_attack_buffs(percent=300),
        )


for _handler in (
    NextAttackPowerStatus(7020700, "伺机而动", flat=70),
):
    register(_handler)

register(WarmUp())


# ---------------- 7080200 泥浆铠甲 ----------------
class MudArmor(OpsSkill):
    """自己获得物攻和物防+60%，应对防御：额外使自己的增益翻倍。

    土系（element 5）状态技，能耗 2 取自 skills.json。本体：物攻/物防各 +6 层
    （属性 10%/层 = 60%）。应对防御（对方本回合使用防御行动）成功时，回合末
    （B38 应对从句）再把自己的**全部增益**层数翻倍（DoubleBuffs(kind="buff")）：
    含本技能刚给的物攻/物防，也含此前已有的其它增益；翻倍同样受层数钳制（B29），
    离场清除 / 可被驱散与常规时长的增益一致。
    """

    skill_id = 7080200
    name = "泥浆铠甲"
    category = 3
    counter_target = COUNTER_DEFENSE
    implemented = True

    status_effects = (
        ApplyBuff(buff_type=buffs.BuffType.ATK, value=6, target=SELF),
        ApplyBuff(buff_type=buffs.BuffType.DEF, value=6, target=SELF),
    )
    counter_effects = (DoubleBuffs(kind="buff", target=SELF),)


register(MudArmor())


# ---------------- 7160160 生日蛋糕 ----------------
class BirthdayCake(OpsSkill):
    """驱散自己的减益，自己的增益翻倍。

    萌系（element 13）状态技，能耗 4 取自 skills.json。本体两条效果按序即时
    结算：先驱散自己全部减益（含永久时长；特性自身的展示效果不是减益，不受
    影响，S26），再把自己的**全部增益**层数翻倍（DoubleBuffs(kind="buff")，与泥浆
    铠甲的应对从句同一原语；翻倍受层数钳制 B29）。驱散减益不算增益、不会被
    第二条翻倍。
    """

    skill_id = 7160160
    name = "生日蛋糕"
    category = 3
    implemented = True

    status_effects = (
        DispelBuffs(kind="debuff", count=None, target=SELF),
        DoubleBuffs(kind="buff", target=SELF),
    )


register(BirthdayCake())


# ---------------- 7160270 击鼓传花 ----------------
class PassTheParcel(OpsSkill):
    """自己脱离，下个入场精灵继承自己增益。

    萌系（element 13）状态技，能耗 3 取自 skills.json。脱离走 LeaveField 登记
    （B36：行动照常结算，行动后立即离场、暂停等待选人）。
    "继承自己增益"：离场执行时在清除 NORMAL buff 前**快照全部增益**（含
    PERMANENT 时长，按原时长继承；减益不继承），由续接流程发给本侧**下一只**
    入场的精灵，按原类型/层数/时长逐条加给它（add_buff 统一做层数钳制，叠加
    到它已有增益上）。无存活替补 / 蓄力免疫时脱离不生效，快照也随之作废。
    """

    skill_id = 7160270
    name = "击鼓传花"
    category = 3
    implemented = True

    status_effects = (LeaveField(mode=LEAVE_SELF, entry_inherit_buffs=True),)


register(PassTheParcel())


# ---------------- 7070060 轴承支撑 ----------------
class BearingSupport(OpsSkill):
    """左右相邻技能的永久能耗支撑。

    携带时，当前技能列表中轴承支撑左右相邻的技能能耗 -1；每次主动
    使用后，该被动永久额外 -1。相邻关系按当前技能列表循环判定，
    传动重排后自动随技能位置变化：1号位的两侧是4、2号位，4号位的两侧是3、1号位。
    """

    skill_id = 7070060
    name = "轴承支撑"
    category = 3
    drive = 1
    implemented = True

    _PASSIVE_REDUCTION = "bearing_passive_reduction"

    def on_after_use(self, ctx):
        ctx.add_state(self._PASSIVE_REDUCTION, 1)

    def modify_other_skill_energy_cost(self, ctx, target_skill, target_index):
        skills = ctx.actor.skills
        source_index = next(
            (i for i, candidate in enumerate(skills) if candidate is ctx.skill), -1
        )
        if source_index < 0 or target_index == source_index or len(skills) <= 1:
            return 0
        neighbors = {(source_index - 1) % len(skills), (source_index + 1) % len(skills)}
        if target_index not in neighbors:
            return 0
        reduction = 1 + int(ctx.state_of(self._PASSIVE_REDUCTION, 0) or 0)
        return -reduction


register(BearingSupport())


# ---------------- 7140330 马步 ----------------
class HorseStance(OpsSkill):
    """先手-1；选择：生命低于 20% 时回复 60% 生命，或生命高于 80% 时获得物攻 +150%。

    武系（element 11）状态技，能耗 3 取自 skills.json。先手-1 = priority_delta
    （同后发制人）。两个支路的生命条件都在**使用技能时**实时判定（状态本体
    结算时），条件不成立时该支路无效果：
    - 明：生命低于 20% → 回复 60% 最大生命；
    - 暗：生命高于 80% → 物攻 +15 层（属性 10%/层 = +150%）。
    """

    skill_id = 7140330
    name = "马步"
    category = 3
    priority_delta = -1
    choice = True
    implemented = True

    def on_status(self, ctx):
        if ctx.choice_branch == 0:
            if ctx.actor.hp * 5 < ctx.actor.max_hp:
                Heal(percent=0.60).apply(ctx)
        elif ctx.actor.hp * 5 > ctx.actor.max_hp * 4:
            ApplyBuff(buff_type=buffs.BuffType.ATK, value=15).apply(ctx)


register(HorseStance())


# ---------------- 7120130 毒雾 ----------------
class PoisonFog(OpsSkill):
    """将敌方所有增益转化为相同层数的中毒。

    毒系（element 9）状态技，能耗 7 取自 skills.json。"增益"按效果类型与数值
    判定（与时长、来源无关；特性自身的展示效果不是增益、不参与转化，SPEC S26）：
    把敌方身上的**全部**增益层数合计后一次性转化为等量中毒层数（add_buff 统一
    做层数钳制），增益本身被移除。
    """

    skill_id = 7120130
    name = "毒雾"
    category = 3
    implemented = True

    status_effects = (
        ConvertBuffs(
            from_kind="buff",
            to_buff_type=buffs.BuffType.POISON,
            target=ENEMY,
        ),
    )


register(PoisonFog())


# ---------------- 7100170 龙吟 ----------------
class DragonChant(OpsSkill):
    """蓄力：释放时自己获得双攻+150%和速度+80。

    龙系（element 7）状态技，能耗 3 取自 skills.json。蓄力技能：首段"开始蓄力"
    支付能耗并进入蓄力状态（**蓄力不是状态行动**，不参与应对，SPEC B41），
    下一回合再次选择该技能即释放（不再付费）。释放时给自己物攻/魔攻各 +15 层
    （属性 10%/层 = +150%）、速度 +8 层（每层 +10 = +80），常规时长、离场清除。
    """

    skill_id = 7100170
    name = "龙吟"
    category = 3
    windup = True
    implemented = True

    status_effects = (
        ApplyBuff(buff_type=buffs.BuffType.ATK, value=15),
        ApplyBuff(buff_type=buffs.BuffType.SPATK, value=15),
        ApplyBuff(buff_type=buffs.BuffType.SPEED, value=8),
    )


register(DragonChant())
