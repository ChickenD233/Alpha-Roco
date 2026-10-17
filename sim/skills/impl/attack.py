"""攻击类技能（category 0 物理 / 1 魔法）。

与 defense.py 同样的约定：效果全部以代码定义，desc 不参与运行时。
威力/属性/能耗沿用 skills.json 的数据；只有"由规则算出"的威力才用
``absolute_power`` 覆盖（见 README 第 1 节列出的 4 条桩值技能）。

进度以 ``data/skills.json`` 的 ``done``/``tested`` 标志为准。

排列规则（新增技能请照此插入，保持文件可扫读）：
1. 家族（参数化声明器 + 登记表）在前：纯攻击基族置首，其余按族内最小技能 id 升序；
2. 家族表内成员与后面的独立技能，统一按三级键排序：
   无连击 → 连击（desc 带"N连击"字样者属连击组，含"1连击"；表中体现为显式
   hits 参数）→ 物攻 → 魔攻 → 技能 id 升序；
3. 独立技能共用同一排序键；共享辅助函数紧随其使用者之前。
"""

from __future__ import annotations

from ... import buffs, traits, weather
from ...data_loader import build_skill_map, skill_group_ids
from ...evolution import can_cute
from ..ops import (
    COUNTER_DEFENSE,
    COUNTER_STATUS,
    ENEMY,
    FIELD,
    GROWTH_ENERGY_COST,
    GROWTH_HIT_COUNT,
    GROWTH_POWER,
    LEAVE_SELF,
    AddMark,
    ApplyBuff,
    CanCute,
    ConvertBuffToMark,
    DispelBuffs,
    DispelMarks,
    EnemySwitchedThisTurn,
    GainEnergy,
    Heal,
    KilledTarget,
    LeaveField,
    LoseEnergy,
    LoseHp,
    Not,
    OpsSkill,
    SkillGrowth,
    SlotIn,
    UsedAttackSkillLastTurn,
    When,
    apply_effects,
    per_any_mark,
    per_missing_hp,
    per_stack,
    skill_by_id,
)
from ..registry import register

STAR_MARK = 7  # 星陨印记（负面）


# ==================== 家族声明与登记表 ====================


# ---------------- 纯攻击技能（无附加效果） ----------------
class PureAttack(OpsSkill):
    """纯攻击技能：desc 只有"造成伤害（，N连击）"，无附加效果、无本次数值修正。

    威力/能耗/属性全部取自 skills.json；固有 N 连击 = 基础 1 + hit_count_flat(N-1)
    （与撕咬/打喷嚏同一条 compute_hit_count 管线，buff/特性修正照常叠加），
    伤害由引擎攻击分支按段数整体结算。handler 仅作"已评审"记录（check()
    核对 name/category 与数据一致）。新增同类技能只在下表加一行。
    """

    def __init__(self, skill_id: int, name: str, category: int, hits: int = 1):
        self.skill_id = skill_id
        self.name = name
        self.category = category
        self.hit_count_flat = hits - 1
        self.implemented = True


for _handler in (
    # 无连击 · 物攻
    PureAttack(7020370, "猛烈撞击", 0),
    PureAttack(7020380, "飞踢", 0),
    PureAttack(7020390, "扫尾", 0),
    PureAttack(7020870, "落星", 0),
    PureAttack(7020930, "压扁", 0),
    PureAttack(7020940, "践踏", 0),
    PureAttack(7030170, "荆棘爪", 0),
    PureAttack(7030180, "仙人掌刺击", 0),
    PureAttack(7030410, "飞叶", 0),
    PureAttack(7030470, "顶端优势", 0),
    PureAttack(7040150, "火焰冲锋", 0),
    PureAttack(7040320, "火云车", 0),
    PureAttack(7040370, "火焰箭", 0),
    PureAttack(7040390, "火焰切割", 0),
    PureAttack(7040410, "炎息", 0),
    PureAttack(7040420, "灼伤", 0),
    PureAttack(7050330, "泡沫", 0),
    PureAttack(7050360, "潮涌", 0),
    PureAttack(7050370, "水幕冲击", 0),
    PureAttack(7060100, "闪光冲击", 0),
    PureAttack(7060180, "光刃", 0),
    PureAttack(7060210, "折线冲击", 0),
    PureAttack(7060230, "透射", 0),
    PureAttack(7070100, "拆卸", 0),
    PureAttack(7080110, "扬沙", 0),
    PureAttack(7080120, "跺地", 0),
    PureAttack(7080180, "地震", 0),
    PureAttack(7080290, "落石", 0),
    PureAttack(7090230, "冰锥", 0),
    PureAttack(7090250, "冰爪", 0),
    PureAttack(7100120, "龙吼", 0),
    PureAttack(7100190, "龙爪", 0),
    PureAttack(7100220, "龙炮", 0),
    PureAttack(7100230, "隼鳞", 0),
    PureAttack(7110010, "导电撞击", 0),
    PureAttack(7110300, "球状闪电", 0),
    PureAttack(7110320, "离子火花", 0),
    PureAttack(7110330, "交叉闪电", 0),
    PureAttack(7120190, "溃烂触碰", 0),
    PureAttack(7120200, "毒沼", 0),
    PureAttack(7120260, "毒誓", 0),
    PureAttack(7130240, "蛰针", 0),
    PureAttack(7130260, "噬心", 0),
    PureAttack(7130270, "尾后针", 0),
    PureAttack(7140250, "影袭", 0),
    PureAttack(7140260, "一拳", 0),
    PureAttack(7150190, "鹰爪", 0),
    PureAttack(7150210, "风矢", 0),
    PureAttack(7150240, "俯冲猛击", 0),
    PureAttack(7160220, "鞭打", 0),
    PureAttack(7160240, "碰爪", 0),
    PureAttack(7160260, "爆米花爆破", 0),
    PureAttack(7170190, "诡刺", 0),
    PureAttack(7170200, "幻象", 0),
    PureAttack(7170290, "撞鬼", 0),
    PureAttack(7180080, "恶能量", 0),
    PureAttack(7180240, "迫害", 0),
    PureAttack(7180270, "诋毁", 0),
    PureAttack(7190210, "念力膨胀", 0),
    PureAttack(7190350, "粒子对撞", 0),
    PureAttack(7190360, "星云漩涡", 0),
    PureAttack(7190370, "双星", 0),
    # 无连击 · 魔攻
    PureAttack(7020410, "音爆", 1),
    PureAttack(7020880, "拍击", 1),
    PureAttack(7020890, "音波弹", 1),
    PureAttack(7020900, "许愿星", 1),
    PureAttack(7020910, "星星撞击", 1),
    PureAttack(7020950, "湮灭", 1),
    PureAttack(7020960, "打鼾", 1),
    PureAttack(7030430, "花香", 1),
    PureAttack(7030440, "棘突", 1),
    PureAttack(7030460, "叶绿光束", 1),
    PureAttack(7040160, "炎枪", 1),
    PureAttack(7040330, "爆裂飞弹", 1),
    PureAttack(7040340, "热气", 1),
    PureAttack(7040360, "火爪", 1),
    PureAttack(7050170, "水弹枪", 1),
    PureAttack(7050180, "气泡", 1),
    PureAttack(7050290, "水光冲击", 1),
    PureAttack(7050320, "水弹", 1),
    PureAttack(7050340, "肥皂泡", 1),
    PureAttack(7050380, "激流", 1),
    PureAttack(7060090, "闪光", 1),
    PureAttack(7060160, "光球", 1),
    PureAttack(7060190, "脉冲光线", 1),
    PureAttack(7060200, "虹光冲击", 1),
    PureAttack(7070130, "金属噪音", 1),
    PureAttack(7070330, "广播", 1),
    PureAttack(7080270, "泥浆", 1),
    PureAttack(7080280, "泥巴喷射", 1),
    PureAttack(7080300, "热砂", 1),
    PureAttack(7080310, "陨石", 1),
    PureAttack(7090240, "冷风", 1),
    PureAttack(7100210, "角击", 1),
    PureAttack(7110190, "触电", 1),
    PureAttack(7110290, "电流", 1),
    PureAttack(7110310, "磁干扰", 1),
    PureAttack(7120180, "毒泡泡", 1),
    PureAttack(7120210, "瘴气喷射", 1),
    PureAttack(7130280, "虫蛊", 1),
    PureAttack(7140210, "气波", 1),
    PureAttack(7140240, "贯手", 1),
    PureAttack(7150180, "鸣叫", 1),
    PureAttack(7150220, "回旋风暴", 1),
    PureAttack(7160130, "飞吻", 1),
    PureAttack(7160230, "魅惑", 1),
    PureAttack(7160350, "可爱", 1),
    PureAttack(7170160, "恐吓", 1),
    PureAttack(7170170, "幽灵爆发", 1),
    PureAttack(7170220, "灵媒", 1),
    PureAttack(7180250, "掠夺", 1),
    PureAttack(7180280, "栽赃", 1),
    PureAttack(7180460, "暴打", 1),
    PureAttack(7190390, "大爆炸", 1),
    # 连击 · 物攻
    PureAttack(7020480, "旋转突击", 0, hits=3),
    PureAttack(7030190, "刺藤", 0, hits=2),
    PureAttack(7030420, "种皮爆裂", 0, hits=2),
    PureAttack(7040190, "双响炮", 0, hits=2),
    PureAttack(7060170, "光之矛", 0, hits=3),
    PureAttack(7140230, "缠丝劲", 0, hits=2),
    PureAttack(7150050, "啄击", 0, hits=2),
    # 连击 · 魔攻
    PureAttack(7020500, "乱打", 1, hits=5),
    PureAttack(7020920, "能量炮", 1, hits=2),
    PureAttack(7040350, "流火", 1, hits=3),
    PureAttack(7050350, "水花四溅", 1, hits=4),
    PureAttack(7090260, "打雪仗", 1, hits=2),
    PureAttack(7130250, "虫刺", 1, hits=3),
    PureAttack(7170210, "午夜噪音", 1, hits=5),
    PureAttack(7180260, "黑手", 1, hits=2),
    PureAttack(7190380, "针状物", 1, hits=3),
):
    register(_handler)


# ---------------- 7060270 色散 ----------------
class DispersionAttack(OpsSkill):
    """魔法攻击；目标为混血精灵时威力 +50%。

    混血判定使用目标的血脉：血脉不在目标自身属性中即算混血；
    双属性中包含血脉属性时不算混血，首领血脉同样属于混血血脉。
    """

    skill_id = 7060270
    name = "色散"
    category = 1
    implemented = True

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        target = ctx.target
        if target is not None and target.bloodline is not None:
            if target.bloodline not in target.attributes:
                percent += 50
        return (percent, flat)


register(DispersionAttack())


# ---------------- 7110230 落雷 ----------------
class ThunderStrike(OpsSkill):
    """魔法攻击；每次入场后本技能威力永久 +40。"""

    skill_id = 7110230
    name = "落雷"
    category = 1
    implemented = True

    def on_entry(self, ctx):
        ctx.add_state("entry_power", 40)

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        return (percent, flat + int(ctx.state_of("entry_power", 0) or 0))


register(ThunderStrike())


# ---------------- 攻击后自己回复能量 ----------------
class GainEnergyAttack(OpsSkill):
    """ "造成X伤，自己回复N能量"类：命中后自己回复固定能量。

    同一语义仅回复数值不同（藤绞 5，其余 1）
    新增同类技能只在下表加一行。
    """

    def __init__(self, skill_id: int, name: str, category: int, gain: int = 1):
        self.skill_id = skill_id
        self.name = name
        self.category = category
        self.hit_effects = (GainEnergy(value=gain),)
        self.implemented = True


for _handler in (
    # 物攻
    GainEnergyAttack(7020360, "抓挠", 0),
    GainEnergyAttack(7030160, "种子弹", 0),
    GainEnergyAttack(7030200, "藤绞", 0, gain=5),
    GainEnergyAttack(7040170, "火苗", 0),
    GainEnergyAttack(7140050, "寸拳", 0),
    GainEnergyAttack(7180070, "魔爪", 0),
    GainEnergyAttack(7190250, "偷师", 0),
    # 魔攻
    GainEnergyAttack(7050160, "甩水", 1),
    GainEnergyAttack(7090100, "风吹雪", 1),
    GainEnergyAttack(7130140, "虫网", 1),
    GainEnergyAttack(7170090, "鬼火", 1),
):
    register(_handler)


# ---------------- 攻击 + 吸血 ----------------
class LifestealAttack(OpsSkill):
    """ "造成X伤，并吸血100%"类。

    吸血走统一查询（`query_lifesteal`）：按本次**实际造成伤害**的比例回复生命
    （经 traits.query_heal 的治疗修正与上限处理，与其它吸血同一管线）。
    新增同类技能只在下表加一行。
    """

    def __init__(self, skill_id: int, name: str, category: int, lifesteal: float = 1.0):
        self.skill_id = skill_id
        self.name = name
        self.category = category
        self.lifesteal = lifesteal
        self.implemented = True


for _handler in (
    # 无连击 · 物攻
    LifestealAttack(7180090, "蝙蝠", 0),
    # 无连击 · 魔攻
    LifestealAttack(7030340, "汲取", 1),
):
    register(_handler)


# ---------------- 应对状态：本次威力变为N倍 ----------------
class CounterPowerUp(OpsSkill):
    """ "造成X伤，（迅捷，）应对状态：本次技能威力变为N倍"类。

    应对效果作用于**本次行动自身**（威力倍数在伤害计算时就需要），是 B38
    延迟结算的例外——经 modify_power 在伤害计算时即时生效（ctx.is_counter
    由排序期的应对判定给出），不走 STAGE_COUNTER 的回合末登记。
    倍数按增量语义折算进威力百分比（P11："变为N倍" = +(N-1)×100%）。
    新增同类技能只在下表加一行。
    """

    def __init__(
        self, skill_id: int, name: str, category: int, mult: float, swift: bool = False
    ):
        self.skill_id = skill_id
        self.name = name
        self.category = category
        self.counter_target = COUNTER_STATUS
        self.implemented = True
        self.mult = mult
        self.swift = swift

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        if ctx.is_counter:
            percent += (self.mult - 1) * 100
        return (percent, flat)


for _handler in (
    # 物攻
    CounterPowerUp(7020490, "偷袭", 0, 3),
    CounterPowerUp(7040180, "闪燃", 0, 4),
    CounterPowerUp(7140080, "无影脚", 0, 2),
    CounterPowerUp(7140120, "技巧打击", 0, 10),
    CounterPowerUp(7140220, "爆冲", 0, 5),
    CounterPowerUp(7150110, "龙卷风", 0, 1.5, swift=True),
    # 魔攻
    CounterPowerUp(7020450, "突袭", 1, 3),
):
    register(_handler)


# ---------------- 使用后本技能能耗永久增减 ----------------
class AfterUseEnergyGrowthAttack(OpsSkill):
    """ "（造成X伤，）每次使用后，本技能能耗永久±N"类。

    成长经 SkillGrowth(GROWTH_ENERGY_COST, delta) 写入 skill_state，
    能耗查询/支付/显示同走 true_energy_cost，最后统一钳制到 0（B18）。
    delta 正为增加（重击）、负为减少（水炮）。新增同类技能只在下表加一行。
    """

    def __init__(self, skill_id: int, name: str, category: int, delta: int):
        self.skill_id = skill_id
        self.name = name
        self.category = category
        self.implemented = True
        self.after_use_effects = (SkillGrowth(field=GROWTH_ENERGY_COST, delta=delta),)


for _handler in (
    # 物攻
    AfterUseEnergyGrowthAttack(7020540, "重击", 0, 1),
    # 魔攻
    AfterUseEnergyGrowthAttack(7050190, "水炮", 1, -1),
):
    register(_handler)


# ---------------- 击败触发攻击技能 ----------------
class KillRewardAttack(OpsSkill):
    """ "造成X伤，（N连击，）（每次/若使用本技能）击败敌方，X"类。

    击败判定读命中登记时的快照（伤害结算后对方 hp≤0，B39 延迟到回合末结算
    也不失真）；效果经 KilledTarget 条件门，未击败一律不执行；目标需要
    "被击败的那只"的效果（中毒转化等）经 ctx.extra["hit_target"] 锚定。
    hits 为固有连击数。新增同类技能只在下表加一行。
    """

    def __init__(
        self,
        skill_id: int,
        name: str,
        category: int,
        hits: int = 1,
        on_kill: tuple = (),
    ):
        self.skill_id = skill_id
        self.name = name
        self.category = category
        self.hit_count_flat = hits - 1
        self.implemented = True
        self.hit_effects = (When(condition=KilledTarget(), effects=tuple(on_kill)),)


def _double_current_power(ctx) -> int:
    """阳火增辉"威力永久翻倍"：SkillGrowth 的增量 = 当前总威力（桩值+已累计成长）。"""
    return (ctx.skill.power or 0) + int(ctx.state_of(GROWTH_POWER, 0) or 0)


for _handler in (
    # 无连击 · 物攻
    KillRewardAttack(7020590, "吞噬", 0, on_kill=(GainEnergy(value=6),)),
    KillRewardAttack(
        7040210, "流星火雨", 0, on_kill=(SkillGrowth(field=GROWTH_POWER, delta=85),)
    ),
    # 无连击 · 魔攻
    KillRewardAttack(
        7040620,
        "阳火增辉",
        1,
        on_kill=(SkillGrowth(field=GROWTH_POWER, delta=_double_current_power),),
    ),
    KillRewardAttack(
        7120110,
        "感染病",
        1,
        on_kill=(ConvertBuffToMark(buff_type=buffs.BuffType.POISON, mark_id=4),),
    ),
    KillRewardAttack(
        7190230,
        "坍缩",
        1,
        on_kill=(ApplyBuff(buff_type=buffs.BuffType.SPATK, value=7),),
    ),
    # 连击 · 物攻
    KillRewardAttack(
        7180340,
        "趁火打劫",
        0,
        hits=2,
        on_kill=(SkillGrowth(field=GROWTH_HIT_COUNT, delta=2),),
    ),
):
    register(_handler)


# ---------------- 攻击+自己立即脱离 ----------------
class AttackAndLeave(OpsSkill):
    """ "造成X伤，（N连击，）自己脱离"类：伤害照常结算，命中时登记脱离
    （LeaveField 是 deferrable=False 的登记类效果，B39 例外）并立即按 B36
    流程执行——使用技能后立即脱离、回合中暂停换人，新精灵本回合即上场
    （面对本回合后续行动）。无存活替补时脱离不生效。hits 为固有连击数。
    新增同类技能只在下表加一行。
    """

    def __init__(self, skill_id: int, name: str, category: int, hits: int = 1):
        self.skill_id = skill_id
        self.name = name
        self.category = category
        self.hit_count_flat = hits - 1
        self.implemented = True
        self.hit_effects = (LeaveField(mode=LEAVE_SELF),)


for _handler in (
    # 无连击 · 魔攻
    AttackAndLeave(7040240, "高温回火", 1),
    # 连击 · 物攻
    AttackAndLeave(7110220, "闪击折返", 0, hits=2),
):
    register(_handler)


# ---------------- 应对状态后本技能永久降能耗 ----------------
class CounterStatusEnergyReductionAttack(OpsSkill):
    """应对状态成功后，本技能能耗永久降低的攻击技能。

    统一使用 ``SkillGrowth`` 写入本技能的 skill_state，能耗查询、实际支付和
    显示继续走同一条管线。新增同类技能只需声明技能 ID、名称、类别和每次
    应对成功的降幅。
    """

    def __init__(self, skill_id: int, name: str, category: int, reduction: int):
        self.skill_id = skill_id
        self.name = name
        self.category = category
        self.counter_target = COUNTER_STATUS
        self.implemented = True
        self.counter_effects = (
            SkillGrowth(field=GROWTH_ENERGY_COST, delta=-reduction),
        )


# 物攻
register(CounterStatusEnergyReductionAttack(7050200, "水刃", 0, 3))
# 魔攻
register(CounterStatusEnergyReductionAttack(7050210, "天洪", 1, 6))


# ---------------- 槽位加成攻击技能（机械传动族） ----------------
class SlotBonusAttack(OpsSkill):
    """ "造成X伤，（N连击，）本技能位于M号位时威力+K，传动D"类。

    槽位按使用时位置判定（复用 SlotIn 条件，1-based）；传动重排每回合改变
    槽位，加成随之变化。威力加成走 modify_power 条件增量（与 CuteAttack 同一
    管线），攻击伤害与显示威力都计入；drive 为静态属性，构造期绑定。
    新增同族技能只在下表加一行。
    """

    def __init__(
        self,
        skill_id: int,
        name: str,
        category: int,
        hits: int = 1,
        drive: int = 0,
        slots: tuple = (),
        power_flat: int = 0,
        slot_hit_count_flat: int = 0,
        slot_energy_cost_delta: int = 0,
    ):
        self.skill_id = skill_id
        self.name = name
        self.category = category
        self.hit_count_flat = hits - 1
        self.drive = drive
        self.slot_power_flat = power_flat
        self.slot_hit_count_flat = slot_hit_count_flat
        self.slot_energy_cost_delta = slot_energy_cost_delta
        self._slot_cond = SlotIn(slots=tuple(slots))
        self.implemented = True

    def _in_slot(self, ctx):
        return self._slot_cond.test(ctx)

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        if self.slot_power_flat and self._in_slot(ctx):
            flat += self.slot_power_flat
        return (percent, flat)

    def modify_hit_count(self, ctx):
        fixed, percent, forced = super().modify_hit_count(ctx)
        if self.slot_hit_count_flat and self._in_slot(ctx):
            fixed += self.slot_hit_count_flat
        return (fixed, percent, forced)

    def modify_energy_cost(self, ctx):
        delta = super().modify_energy_cost(ctx)
        if self.slot_energy_cost_delta and self._in_slot(ctx):
            delta += self.slot_energy_cost_delta
        return delta


for _handler in (
    # 无连击 · 物攻
    SlotBonusAttack(7070010, "械斗", 0, drive=1, slots=(1,), power_flat=60),
    SlotBonusAttack(7070040, "钢铁洪流", 0, drive=2, slots=(1,), power_flat=90),
    SlotBonusAttack(
        7070150, "齿轮切开", 0, drive=1, slots=(1, 3), slot_energy_cost_delta=-2
    ),
    # 无连击 · 魔攻
    SlotBonusAttack(7070110, "离子震荡", 1, drive=1, slots=(3,), power_flat=40),
    SlotBonusAttack(7070140, "磁暴", 1, drive=1, slots=(1, 3), power_flat=30),
    # 连击 · 物攻
    SlotBonusAttack(
        7070120, "传感器", 0, hits=2, drive=1, slots=(1, 3), slot_hit_count_flat=1
    ),
):
    register(_handler)


# ---------------- 应对状态：额外打断被应对技能 ----------------
class CounterStatusInterruptAttack(OpsSkill):
    """ "造成X伤，应对状态：额外打断被应对技能"类。

    打断是**静态属性**（`counter_interrupt`，SPEC B40）：应对判定成功时立即作废
    被应对方本次的技能/聚能行动，不走 B38 回合末从句（那时对方技能已结算过）。
    能耗按"取消即未支付"处理，不额外发能量；若打断的是蓄力技能的**释放段**
    （费用已在蓄力回合付过、本次为 0 费），不返还能量且蓄力状态保留。
    **蓄力不是状态行动**（SPEC B41）：对方"开始蓄力"不会被本类技能应对。
    新增同类技能只在下表加一行。
    """

    def __init__(self, skill_id: int, name: str, category: int):
        self.skill_id = skill_id
        self.name = name
        self.category = category
        self.counter_target = COUNTER_STATUS
        self.counter_interrupt = True
        self.implemented = True


for _handler in (
    # 无连击 · 物攻
    CounterStatusInterruptAttack(7080130, "地刺", 0),
    CounterStatusInterruptAttack(7140090, "斩断", 0),
    # 无连击 · 魔攻
    CounterStatusInterruptAttack(7020520, "阻断", 1),
):
    register(_handler)


# ---------------- 入场后首次行动的能耗迸发 ----------------
class FirstActionBurstAttack(OpsSkill):
    """攻击技能：入场后的首次行动中获得指定的迸发效果。"""

    def __init__(
        self,
        skill_id: int,
        name: str,
        category: int,
        *,
        energy_cost_reduction: int = 0,
    ):
        self.skill_id = skill_id
        self.name = name
        self.category = category
        self.energy_cost_reduction = energy_cost_reduction
        self.implemented = True

    def modify_energy_cost(self, ctx):
        delta = super().modify_energy_cost(ctx)
        if not ctx.actor.has_acted_since_entry:
            delta -= self.energy_cost_reduction
        return delta


# 无连击 · 魔攻
register(FirstActionBurstAttack(7110200, "超导", 1, energy_cost_reduction=2))


# ---------------- "获得萌化：X"族攻击 ----------------
class CuteAttack(OpsSkill):
    """ "造成X伤，（N连击。）自己获得萌化：X"类（萌化族攻击技能）。

    前置条件是**能获得萌化**（存在前一进化阶段）：不能萌化时本次威力加成与
    附加效果、萌化一律不给。萌化在技能结算之后施加（STAGE_HIT 登记、回合末
    结算，B39），本次伤害按退化前形态结算。
    hits 固有连击数；power_on_cute 为能萌化时本次威力加成（固定值增量）；
    bonus_effects 为能萌化时附加的效果（与萌化同门，When+CanCute 条件门）。
    新增同族技能只在下表加一行。
    """

    def __init__(
        self,
        skill_id: int,
        name: str,
        category: int,
        hits: int = 1,
        power_on_cute: int = 0,
        bonus_effects: tuple = (),
    ):
        self.skill_id = skill_id
        self.name = name
        self.category = category
        self.hit_count_flat = hits - 1
        self.implemented = True
        self.power_on_cute = power_on_cute
        self.hit_effects = (
            When(
                condition=CanCute(),
                effects=(
                    tuple(bonus_effects)
                    + (ApplyBuff(buff_type=buffs.BuffType.CUTE, value=1),)
                ),
            ),
        )

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        if self.power_on_cute and buffs.can_gain_buff(
            ctx.actor, buffs.BuffType.CUTE, 1
        ):
            flat += self.power_on_cute
        return (percent, flat)


for _handler in (
    # 无连击 · 物攻
    CuteAttack(7160140, "超级糖果", 0, power_on_cute=60),
    # 连击 · 魔攻
    CuteAttack(
        7160250,
        "撒娇",
        1,
        hits=3,
        bonus_effects=(
            ApplyBuff(
                buff_type=buffs.BuffType.SKILL_POWER_FLAT,
                value=10,
                duration=buffs.DurationKind.PERMANENT,
            ),
        ),
    ),
):
    register(_handler)


# ==================== 独立技能（同一排序键） ====================


# ---------------- 无连击 · 物攻 ----------------


# 7020530 穿膛
class PiercingShot(OpsSkill):
    """造成物伤；若敌方能量不高于 2，本次技能威力变为 5 倍。

    普通系（element 0）物攻，威力 65、能耗 2。能量条件在
    **使用技能时**实时读取（伤害计算时），选择技能时的能量不作数；
    "5 倍"按增量语义折算为 +400%（P11）。
    """

    skill_id = 7020530
    name = "穿膛"
    category = 0
    implemented = True

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        if ctx.target is not None and ctx.target.energy <= 2:
            percent += 400.0
        return (percent, flat)


register(PiercingShot())


# 7020970 先发制人
class Preemptive(OpsSkill):
    """造成物理伤害，先手+1。

    普通系（element 0）物攻，威力 55、能耗 2。先手+1 是
    技能侧静态增量（priority_delta），参与行动排序：应对强制先手之下、
    有效速度之上（B10）——先手值高者先动，相等才比速度。
    """

    skill_id = 7020970
    name = "先发制人"
    category = 0
    priority_delta = 1
    implemented = True


register(Preemptive())


# 7020980 天旋地转
class Whirlwind(OpsSkill):
    """造成物理伤害，先手+1，迸发：本次技能威力+30。

    普通系（element 0）物攻，威力 60、能耗 3。
    先手+1 = 技能侧静态增量（priority_delta，同先发制人）。
    迸发 = 入场后首次行动生效：ctx.actor.has_acted_since_entry 为 False 时
    威力 +30（modify_power 固定值增量，攻击伤害与显示威力同计入；
    触发条件与超导一致，效果由技能自身声明）。
    """

    skill_id = 7020980
    name = "天旋地转"
    category = 0
    priority_delta = 1
    implemented = True

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        if not ctx.actor.has_acted_since_entry:
            flat += 30
        return (percent, flat)


register(Whirlwind())


# 7021020 后发制人
class AfterStrike(OpsSkill):
    """造成物理伤害，先手-1。

    普通系（element 0）物攻，威力 155、能耗 3。先手-1 是
    技能侧静态增量（priority_delta），参与行动排序：应对强制先手之下、
    有效速度之上（B10）——先手值低者后动，相等才比速度（先发制人的镜像）。
    """

    skill_id = 7021020
    name = "后发制人"
    category = 0
    priority_delta = -1
    implemented = True


register(AfterStrike())


# 7030480 筛管奔流
class SieveSurge(OpsSkill):
    """造成物伤，自己生命大于80%时，本次技能威力+75。

    草系（element 1）物攻，威力 80、能耗 3。"大于80%"按
    整数比较（hp×5 > max_hp×4，恰好 80% 不加）；威力加成走 modify_power
    固定值增量，攻击伤害与显示威力同计入。
    """

    skill_id = 7030480
    name = "筛管奔流"
    category = 0
    implemented = True

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        if ctx.actor.hp * 5 > ctx.actor.max_hp * 4:
            flat += 75
        return (percent, flat)


register(SieveSurge())


# 7040200 吹火
class FireBreath(OpsSkill):
    """造成物伤，每次使用后，本技能威力永久+20。

    火系（element 2）物攻，威力 50、能耗 1。威力成长经
    SkillGrowth(GROWTH_POWER, +20) 写入 skill_state（与水炮能耗成长同一管线），
    威力查询/伤害/显示同走 query_power，与威力 buff/特性增量叠加。
    """

    skill_id = 7040200
    name = "吹火"
    category = 0
    implemented = True
    after_use_effects = (SkillGrowth(field=GROWTH_POWER, delta=20),)


register(FireBreath())


# 7070020 齿轮扭矩
class GearTorque(OpsSkill):
    """造成物伤，每回合位置发生变化时，本技能威力永久+15。

    机械系（element 16）物攻，威力 70、能耗 3。位置变化由引擎
    在传动重排后广播（STAGE_SLOT_CHANGE，覆盖开局排序 / 回合末重排 / 换人后的
    重排，只结算在场精灵）；每次变化经 SkillGrowth(GROWTH_POWER, +15) 永久成长，
    与威力 buff/特性增量叠加。
    """

    skill_id = 7070020
    name = "齿轮扭矩"
    category = 0
    implemented = True
    slot_change_effects = (SkillGrowth(field=GROWTH_POWER, delta=15),)


register(GearTorque())


# ---------------- 7070220 轮班 ----------------
class ShiftAttack(OpsSkill):
    """物理攻击；明支路按 1 号位加威力，暗支路本回合额外传动。"""

    skill_id = 7070220
    name = "轮班"
    category = 0
    drive = 1
    choice = True
    implemented = True

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        if ctx.choice_branch == 0 and ctx.skill_index == 0:
            flat += 65
        return (percent, flat)

    def on_after_use(self, ctx):
        if ctx.choice_branch == 1:
            ctx.skill.skill_state["extra_drive"] = (
                int(ctx.skill.skill_state.get("extra_drive", 0) or 0) + 1
            )


register(ShiftAttack())


# ---------------- 7080320 鸣沙陷阱 / 7150250 闪击（共用差值威力表） ----------------
# 速度/物防差 → 威力（闪击与鸣沙共用）：差 ≤0 为 60（数据里的桩值），
# 1-30→80，31-60→100，61-90→120，91-120→140，121-150→150，151-180→160，
# 181-210→170，211-240→180，241-270→190，≥271→200。
_DIFF_POWER_TABLE = (
    (30, 80),
    (60, 100),
    (90, 120),
    (120, 140),
    (150, 150),
    (180, 160),
    (210, 170),
    (240, 180),
    (270, 190),
)


def _power_by_diff(diff: int) -> int:
    """按速度/物防差查威力（闪击/鸣沙共用表）。"""
    if diff <= 0:
        return 60
    for hi, power in _DIFF_POWER_TABLE:
        if diff <= hi:
            return power
    return 200


def _effective_speed_of(state, side: str) -> int:
    """在场精灵有效速度（buff/印记/特性修正全计入，同 battle._effective_speed）。

    battle 在模块级导入本包（注册入口），这里只能惰性引用。
    """
    from ...battle import _effective_speed

    return _effective_speed(state, side)


def _effective_def(state, pet) -> float:
    """有效物防：面板 × buff 倍率 × 特性倍率（与伤害公式同一口径）。"""
    return (
        pet.stats["def"]
        * buffs.get_stat_multiplier(pet, "def")
        * (1.0 + traits.query_stat_multiplier(state, pet, "def"))
    )


class SingingSand(OpsSkill):
    """造成物伤，物防比敌方越高，本次技能威力越高。

    地系（element 5）物攻，威力 60 是桩值（物防差 ≤0 时的威力，S17）：实际
    威力按双方**有效**物防差查表（与闪击同表），经 absolute_power 绝对覆盖。
    能耗 4。
    """

    skill_id = 7080320
    name = "鸣沙陷阱"
    category = 0
    implemented = True

    def absolute_power(self, ctx):
        if ctx.target is None:
            return 60
        diff = _effective_def(ctx.state, ctx.actor) - _effective_def(
            ctx.state, ctx.target
        )
        return _power_by_diff(int(diff))


register(SingingSand())


# 7090110 暴风雪
class Blizzard(OpsSkill):
    """造成物伤，敌方获得1层冻结。

    冰系（element 6）物攻，威力 85、能耗 3。命中后
    （STAGE_HIT）敌方获得 1 层冻结（含未造成伤害的情形，S9；一次施加
    一个事件，"获得冻结时追加"类特性响应一次）。
    """

    skill_id = 7090110
    name = "暴风雪"
    category = 0
    implemented = True

    hit_effects = (ApplyBuff(buff_type=buffs.BuffType.FREEZE, value=1, target=ENEMY),)


register(Blizzard())


# 7090130 冰雹
class Hail(OpsSkill):
    """造成物伤，应对状态：额外使敌方获得全技能能耗+3。

    冰系（element 6）物攻，威力 105、能耗 4。应对状态 =
    counter_target 为 status：只有对方本回合使用状态行动时才算应对成功，
    此时由引擎在 STAGE_COUNTER（回合末，B38）结算本条附加效果。
    "全技能能耗+3"是敌方全技能能耗修正（ENERGY_COST 正值 = 减益），
    常规时长、离场清除；付费/校验/显示同走 true_energy_cost 管线。
    """

    skill_id = 7090130
    name = "冰雹"
    category = 0
    counter_target = COUNTER_STATUS
    implemented = True

    counter_effects = (
        ApplyBuff(buff_type=buffs.BuffType.ENERGY_COST, value=3, target=ENEMY),
    )


register(Hail())


# 7100160 龙之利爪
class DragonClaw(OpsSkill):
    """蓄力，造成物伤并吸血50%。

    龙系（element 7）物攻，威力 130、能耗 3。蓄力是静态属性
    （windup=True，同升龙咆哮）：首回合选择时支付能耗进入蓄力，次回合再次选择
    该技能时释放。吸血 50% 走技能侧吸血修正查询（skills.query_lifesteal），
    经引擎伤害结算的吸血管线生效（与 buff/特性吸血叠加）。
    """

    skill_id = 7100160
    name = "龙之利爪"
    category = 0
    windup = True
    lifesteal = 0.50
    implemented = True


register(DragonClaw())


# 7140130 截拳
class InterceptFist(OpsSkill):
    """造成物伤；应对状态：造成打断，回复被打断技能能耗的能量。

    武系（element 11）物攻，威力 85、能耗 3。打断是静态属性
    （counter_interrupt，SPEC B40）：应对判定成功时对方的技能/聚能行动立即作废；
    "回复被打断技能能耗的能量"是应对奖励从句，按 B38 回合末结算——取被应对技能
    当前能耗回复自己（能量上限修正与能量广播同 GainEnergy 管线）。
    """

    skill_id = 7140130
    name = "截拳"
    category = 0
    counter_target = COUNTER_STATUS
    counter_interrupt = True
    implemented = True

    def on_counter(self, ctx):
        incoming = ctx.extra.get("incoming_skill")
        if incoming is None:
            return
        GainEnergy(value=int(incoming.energy_cost or 0)).apply(ctx)


register(InterceptFist())


class FlashStrike(OpsSkill):
    """造成物伤，速度比敌方越高，本次技能威力越高。

    翼系物攻，威力 60 是桩值（速度差 ≤0 时的威力，S17）：实际威力按双方
    **有效**速度差查表（60/80/100/120/140/150/160/170/180/190/200），经
    absolute_power 绝对覆盖；威力增量（buff/技能修正）照常叠加。能耗 4 取自
    skills.json。
    """

    skill_id = 7150250
    name = "闪击"
    category = 0
    implemented = True

    def absolute_power(self, ctx):
        if ctx.target is None:
            return 60
        diff = _effective_speed_of(ctx.state, ctx.actor.side) - _effective_speed_of(
            ctx.state, ctx.target.side
        )
        return _power_by_diff(diff)


register(FlashStrike())


# 7170100 惊吓盒子
class ScareBox(OpsSkill):
    """造成物伤；应对状态：使敌方失去 6 能量。

    幽灵系（element 14）物攻，威力 80、能耗 3。应对奖励从句
    延迟到回合末结算（B38），因此对方本回合的行动不受扣能影响——不会把对方的
    行动"吸"到无法支付。
    """

    skill_id = 7170100
    name = "惊吓盒子"
    category = 0
    counter_target = COUNTER_STATUS
    implemented = True

    counter_effects = (LoseEnergy(value=6, target=ENEMY),)


register(ScareBox())


# 7170120 坟场搏击
class GraveyardBrawl(OpsSkill):
    """造成物伤；敌方每有 1 能量，本次技能威力 -10%。

    幽灵系（element 14）物攻，威力 180、能耗 4。能量在
    **使用技能时**实时读取（伤害计算时），按敌方当前能量逐点 -10%。
    """

    skill_id = 7170120
    name = "坟场搏击"
    category = 0
    implemented = True

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        if ctx.target is not None:
            percent -= 10.0 * ctx.target.energy
        return (percent, flat)


register(GraveyardBrawl())


# 7180130 极限撕裂
class LimitTear(OpsSkill):
    """造成物伤；若自己生命高于 50%，使用后自己获得双攻 -50%。

    恶系（element 15）物攻，威力 135、能耗 4。生命条件在
    **使用技能时**实时判定；满足时使用后给自己物攻/魔攻各 -5 层（属性 10%/层
    = -50%），常规时长、离场清除。
    """

    skill_id = 7180130
    name = "极限撕裂"
    category = 0
    implemented = True

    def on_after_use(self, ctx):
        if ctx.actor.hp * 2 <= ctx.actor.max_hp:
            return
        ApplyBuff(buff_type=buffs.BuffType.ATK, value=-5).apply(ctx)
        ApplyBuff(buff_type=buffs.BuffType.SPATK, value=-5).apply(ctx)


register(LimitTear())


# 7180410 困兽
class CorneredBeast(OpsSkill):
    """造成物伤；若自己生命低于 50%，本次攻击吸血 50%。

    恶系（element 15）物攻，威力 90、能耗 3。生命条件在
    **使用技能时**实时判定（吸血比例查询），由引擎按本次实际伤害结算吸血。
    """

    skill_id = 7180410
    name = "困兽"
    category = 0
    implemented = True

    def modify_lifesteal(self, ctx):
        base = super().modify_lifesteal(ctx)
        if ctx.actor.hp * 2 < ctx.actor.max_hp:
            base += 0.5
        return base


register(CorneredBeast())


# 7180420 下注
class BetWager(OpsSkill):
    """造成物伤；选择：本次威力 +40 且使用后自己生命 -10%，或生命低于 50% 时本次威力 +100%。

    恶系（element 15）物攻，威力 85、能耗 3。
    明支路：本次威力固定 +40，使用后自己失去 10% 最大生命（代价类，与
    "彗星消耗全部生命"同一 LoseHp 管线）。
    暗支路：生命低于 50% 时本次威力 +100%；**使用技能时**实时判定，条件
    不成立时该支路无效果。
    """

    skill_id = 7180420
    name = "下注"
    category = 0
    choice = True
    implemented = True

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        if ctx.choice_branch == 0:
            flat += 40
        elif ctx.actor.hp * 2 < ctx.actor.max_hp:
            percent += 100.0
        return (percent, flat)

    def on_after_use(self, ctx):
        if ctx.choice_branch == 0:
            LoseHp(percent=0.10).apply(ctx)


register(BetWager())


# ---------------- 无连击 · 魔攻 ----------------


# 7020470 追打
class Pursuit(OpsSkill):
    """造成魔伤，1连击，应对状态：本技能变为3连击。

    普通系（element 0）魔攻，威力 75、能耗 3。基础 1 连击；
    应对状态（对方本回合使用状态行动）成功时，本次连击数**变为 3**（强制值
    覆盖 buff/技能增量，与特性强制值取较大者）。与"应对状态：本次威力N倍"
    同属 B38 延迟结算的例外——连击数在伤害计算时就需要，故经 modify_hit_count
    按 ctx.is_counter 即时判定，不走 STAGE_COUNTER 的回合末登记。
    """

    skill_id = 7020470
    name = "追打"
    category = 1
    counter_target = COUNTER_STATUS
    implemented = True

    def modify_hit_count(self, ctx):
        flat, percent, forced = super().modify_hit_count(ctx)
        if ctx.is_counter:
            return (flat, percent, 3)
        return (flat, percent, forced)


register(Pursuit())


# 7020600 蓄能轰击
class ChargedBlast(OpsSkill):
    """造成魔伤，每使用1次其他普通系技能，本技能能耗永久-2。

    普通系（element 0）魔攻，威力 120、能耗 6。能耗修正按
    使用记录（BattlePet.used_skill_counts）中**其他**普通系技能的使用次数
    逐次 -2——动态计算与"永久"等价：记录只增不减、整场保留，付费/校验/
    显示同步（true_energy_cost 管线，最后统一钳制到 0）。自身使用不计入。
    """

    skill_id = 7020600
    name = "蓄能轰击"
    category = 1
    implemented = True

    def modify_energy_cost(self, ctx):
        skill_map = build_skill_map()
        uses = sum(
            n
            for sid, n in ctx.actor.used_skill_counts.items()
            if sid != self.skill_id
            and sid in skill_map
            and skill_map[sid]["element"] == 0
        )
        return super().modify_energy_cost(ctx) - 2 * uses


register(ChargedBlast())


# 7021060 彗星
class Comet(OpsSkill):
    """造成魔伤，每失去5%生命，本次技能威力-10，使用后消耗全部生命。

    普通系（element 0）魔攻，威力 240、能耗 0。
    威力减益按**自己**已失去生命的 5% 整数步进逐档 -10（per_missing_hp，
    与燃尽同一接口，目标为自己；满血不减）。
    "使用后消耗全部生命"：on_after_use 直接对自己扣血 9999（用户口径：
    是扣血而非置零），由引擎在 STAGE_AFTER_USE 结算后按力竭处理
    （倒下、扣魔力、等待换人）。
    """

    skill_id = 7021060
    name = "彗星"
    category = 1
    implemented = True

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        return (percent, flat + per_missing_hp(-10, step=0.05)(ctx))

    def on_after_use(self, ctx):
        ctx.actor.hp = max(0, ctx.actor.hp - 9999)


register(Comet())


# 7021170 倾泻
class PourOut(OpsSkill):
    """造成魔伤，若本次攻击未被防御技能应对，则驱散双方所有印记。

    "被防御技能应对"= 对方的防御技能本回合应对成功（防御克制攻击；引擎中
    只有防御技能以攻击为应对目标，故 countered_by == "defense" 即被防御
    应对）。排序期判定后随命中登记快照（ctx.extra["countered_by"]），回合末
    按 B39 结算时判定；驱散覆盖双方阵营的正/负印记（DispelMarks target=FIELD）。
    """

    skill_id = 7021170
    name = "倾泻"
    category = 1
    implemented = True

    def on_hit(self, ctx):
        if ctx.extra.get("countered_by", "") == COUNTER_DEFENSE:
            return
        apply_effects(ctx, (DispelMarks(count=None, target=FIELD),))


register(PourOut())


# 7021190 吹散
class DisperseAttack(OpsSkill):
    """造成魔伤；明支路驱散敌方全部增益，暗支路驱散双方各1层印记。

    明支路驱散敌方的**全部**增益（含永久时长）；特性自身的展示效果不是增益，
    不受影响（S26）。
    """

    skill_id = 7021190
    name = "吹散"
    category = 1
    choice = True
    implemented = True

    def on_hit(self, ctx):
        if ctx.choice_branch == 0:
            apply_effects(ctx, (DispelBuffs(kind="buff", count=None, target=ENEMY),))
        else:
            apply_effects(ctx, (DispelMarks(count=1, target=FIELD),))


register(DisperseAttack())


# 7021220 同频
class MagicMorphAttack(OpsSkill):
    """造成魔伤，巧变为随机其他魔攻技能；巧变临时技能能耗 -1。"""

    skill_id = 7021220
    name = "同频"
    category = 1
    implemented = True

    def on_bind(self, pet, skill):
        pool = [
            skill_by_id(sid)
            for sid, raw in build_skill_map().items()
            if raw["category"] == 1 and sid != self.skill_id
        ]
        return {"morph_pool": pool}


register(MagicMorphAttack())


# 7030570 甜蜜陷阱
class SweetTrap(OpsSkill):
    """造成魔伤；自己每有 1 能量，本次技能威力 +10。

    草系（element 1）魔攻，威力 50、能耗 4。能量在**使用技能时**
    实时读取（伤害计算时），按自身当前能量逐点 +10（描述无 % → 固定值增量）。
    """

    skill_id = 7030570
    name = "甜蜜陷阱"
    category = 1
    implemented = True

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        return (percent, flat + 10 * ctx.actor.energy)


register(SweetTrap())


# 7030580 撒花
class FlowerScatter(OpsSkill):
    """造成魔伤；选择：生命高于 80% 时威力 +50，或应对状态时自己回复 35% 生命。

    草系（element 1）魔攻，威力 95、能耗 4。
    明支路：威力固定 +50，条件是**使用技能时**的生命（伤害计算时实时判定）。
    暗支路：本技能应对状态成功时回复 35% 最大生命——应对判定属排序期快照，
    奖励从句走回合末结算（B38）；条件不成立时该支路无效果。
    """

    skill_id = 7030580
    name = "撒花"
    category = 1
    choice = True
    counter_target = COUNTER_STATUS
    implemented = True

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        if ctx.choice_branch == 0 and ctx.actor.hp * 5 > ctx.actor.max_hp * 4:
            flat += 50
        return (percent, flat)

    def on_counter(self, ctx):
        if ctx.choice_branch == 1:
            Heal(percent=0.35).apply(ctx)


register(FlowerScatter())


# 7040220 持续高温
class SustainedHeat(OpsSkill):
    """造成魔伤，应对状态：下次攻击技能威力翻倍。

    火系（element 2）魔攻，威力 70、能耗 2。应对从句延迟到
    回合末结算（B38），故"下次攻击技能"必然指之后的回合。

    "威力翻倍"按 P11 增量语义折算成 +100%（与 CounterPowerUp 的"变为N倍"=
    +(N-1)×100% 同口径），以一次性增益
    （BuffType.NEXT_ATTACK_POWER_PERCENT，真·百分比 1%/层，100 = +100%）挂在
    自己身上：下一次攻击技能行动时由引擎取走并计入本次威力（只作用一次——
    同一行动内多次执行（使用次数+1）时由首次执行消耗）；蓄力回合不算使用
    攻击技能、不消耗，释放回合照常消耗。该增益为常规时长：离场清除、可被
    驱散增益剥掉，与其它常规时长的增益一致。
    """

    skill_id = 7040220
    name = "持续高温"
    category = 1
    counter_target = COUNTER_STATUS
    implemented = True

    counter_effects = (
        # target 缺省即 SELF（attack.py 不引 SELF 常量，同表内其它自身增益写法）
        ApplyBuff(
            buff_type=buffs.BuffType.NEXT_ATTACK_POWER_PERCENT,
            value=100,
        ),
    )


register(SustainedHeat())


# 7040460 燃尽
class BurnOut(OpsSkill):
    """造成魔伤，敌方每失去5%生命，本次技能威力-5。

    火系（element 2）魔攻，威力 155、能耗 4。威力按敌方在场
    精灵已失去生命的 5% 整数步进逐档 -5（固定值增量，满血不减），走引擎的
    技能威力修正查询（query_power，与碎冰冰同一接口），显示威力同步计入。
    """

    skill_id = 7040460
    name = "燃尽"
    category = 1
    implemented = True

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        return (percent, flat + per_missing_hp(-5, step=0.05, target=ENEMY)(ctx))


register(BurnOut())


# 7050300 水波术
class HydroPulse(OpsSkill):
    """造成魔伤，回合结束时，本技能威力永久+20。

    水系（element 3）魔攻，威力 90、能耗 6。
    触发条件（用户口径）：在场上**无条件**成长、回合末结算——不要求本回合
    使用过；场下不成长（引擎只对在场精灵发射 STAGE_ROUND_END，与
    "每回合替换只对在场精灵抽取"同一口径）。成长经 SkillGrowth(GROWTH_POWER, +20)
    写入 skill_state，威力查询/伤害/显示同走 query_power。
    """

    skill_id = 7050300
    name = "水波术"
    category = 1
    implemented = True

    round_end_effects = (SkillGrowth(field=GROWTH_POWER, delta=20),)


register(HydroPulse())


# 7060130 折射
class Refraction(OpsSkill):
    """造成魔伤，携带其他系别技能会给本技能带来不同效果。

    光系（element 4）魔攻，威力 50、能耗 4。每个**其他**携带
    技能按其系别各贡献一次效果（同系别多个技能重复计入）：全部在命中后登记、
    回合末统一结算（B39，紧随应对奖励之后；本次伤害按携带效果生效前的状态
    结算——威力/连击类是给后续的 buff）。效果表见 _ON_HIT（策划口径）。
    """

    skill_id = 7060130
    name = "折射"
    category = 1
    implemented = True

    # 系别 → 命中后效果（属性增益 10%/层；速度每层 10；吸血 10%/层）
    _ON_HIT = {
        0: (
            ApplyBuff(buff_type=buffs.BuffType.SKILL_POWER_FLAT, value=10),
        ),  # 普通：技能威力+10
        1: (Heal(percent=0.15),),  # 草：回复15%
        2: (
            ApplyBuff(buff_type=buffs.BuffType.BURN, value=4, target=ENEMY),
        ),  # 火：灼烧4层
        3: (
            ApplyBuff(buff_type=buffs.BuffType.ENERGY_COST, value=-1),
        ),  # 水：全技能能耗-1
        4: (ApplyBuff(buff_type=buffs.BuffType.SPATK, value=3),),  # 光：魔攻+30%
        5: (
            ApplyBuff(
                buff_type=buffs.BuffType.SPEED, value=-4, target=ENEMY
            ),  # 地：敌方速度-40
            ApplyBuff(buff_type=buffs.BuffType.HIT_COUNT_FLAT, value=-2, target=ENEMY),
        ),  # 连击-2
        6: (
            ApplyBuff(buff_type=buffs.BuffType.FREEZE, value=2, target=ENEMY),
        ),  # 冰：冻结2层
        7: (
            ApplyBuff(buff_type=buffs.BuffType.SPDEF, value=-4, target=ENEMY),
        ),  # 龙：敌方魔防-40%
        8: (ApplyBuff(buff_type=buffs.BuffType.SPEED, value=2),),  # 电：速度+20
        9: (
            ApplyBuff(buff_type=buffs.BuffType.POISON, value=2, target=ENEMY),
        ),  # 毒：中毒2层
        10: (
            ApplyBuff(buff_type=buffs.BuffType.DEF, value=-4, target=ENEMY),
        ),  # 虫：敌方物防-40%
        11: (ApplyBuff(buff_type=buffs.BuffType.ATK, value=3),),  # 武：物攻+30%
        12: (
            ApplyBuff(buff_type=buffs.BuffType.HIT_COUNT_FLAT, value=1),
        ),  # 翼：连击+1
        13: (
            ApplyBuff(
                buff_type=buffs.BuffType.ATK, value=-3, target=ENEMY
            ),  # 萌：敌方双攻-30%
            ApplyBuff(buff_type=buffs.BuffType.SPATK, value=-3, target=ENEMY),
        ),
        14: (LoseEnergy(value=2, target=ENEMY),),  # 幽：敌方能量-2
        15: (ApplyBuff(buff_type=buffs.BuffType.LIFESTEAL, value=3),),  # 恶：吸血+30%
        16: (ApplyBuff(buff_type=buffs.BuffType.DEF, value=3),),  # 机械：物防+30%
        17: (AddMark(mark_id=STAR_MARK, stacks=1),),  # 幻：敌方1层星陨印记
    }

    def _other_elements(self, ctx) -> list:
        """本技能以外的携带技能的系别（按槽位顺序，同系别重复计入）。"""
        return [s.element for s in ctx.actor.skills if s.skill_id != self.skill_id]

    def on_hit(self, ctx):
        for element in self._other_elements(ctx):
            apply_effects(ctx, self._ON_HIT.get(element, ()))


register(Refraction())


# 7060220 天光
class SkyLight(OpsSkill):
    """造成魔伤；本技能系别和天气系别相同。

    光系（element 4）魔攻，威力 95、能耗 3。使用时按当前天气改写
    本技能系别（雨天=水系、暴风雪=冰系、沙暴=地系、雷鸣=电系）：STAB 与属性克制
    都按改写后的系别结算；无天气时不改写，保持光系。
    """

    skill_id = 7060220
    name = "天光"
    category = 1
    implemented = True

    def modify_element(self, ctx):
        return weather.element_of(ctx.weather)


register(SkyLight())


# 7090290 寒风吹
class ColdWind(OpsSkill):
    """造成魔伤，敌方获得魔防-50%。

    冰系（element 6）魔攻，威力 70、能耗 3。命中附加效果在
    STAGE_HIT 登记、回合末统一结算（B39，同寒潮/瘴气喷射），结算时按在场状态
    解析对象。魔防-50% = 5 层（属性增益 10%/层，与贮藏/魔攻+70% 同一换算）。
    """

    skill_id = 7090290
    name = "寒风吹"
    category = 1
    implemented = True

    hit_effects = (ApplyBuff(buff_type=buffs.BuffType.SPDEF, value=-5, target=ENEMY),)


register(ColdWind())


# 7090310 碎冰冰
class IceShard(OpsSkill):
    """造成魔伤，敌方每有1层冻结，本次技能威力+20。

    冰系（element 6）魔攻，威力 40、能耗 3。威力加成按敌方
    在场精灵的冻结层数逐层 +20（固定值增量），走引擎的技能威力修正查询
    （skills.query_power，与特性同一增量语义），显示威力同步计入。
    """

    skill_id = 7090310
    name = "碎冰冰"
    category = 1
    implemented = True

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        return (percent, flat + per_stack(buffs.BuffType.FREEZE, 20)(ctx))


register(IceShard())


# 7090440 寒潮
class ColdSnap(OpsSkill):
    """造成魔伤，敌方获得1层冻结，应对状态：额外获得4层冻结，巧变：冻结相关技能。

    - 命中后（STAGE_HIT）敌方获得 1 层冻结（含未造成伤害的情形，S9）。
    - 应对状态（对方本回合使用状态行动）时，STAGE_COUNTER 再追加 4 层（合计 5 层）。
    - 巧变：用后随机变成 data/skill_groups.json 中 freeze_apply 组的一个技能
      （能耗-1），该临时技能用后还原为寒潮（引擎 _apply_morph 既有规则）。
    """

    skill_id = 7090440
    name = "寒潮"
    category = 1
    counter_target = COUNTER_STATUS
    implemented = True

    hit_effects = (ApplyBuff(buff_type=buffs.BuffType.FREEZE, value=1, target=ENEMY),)
    counter_effects = (
        ApplyBuff(buff_type=buffs.BuffType.FREEZE, value=4, target=ENEMY),
    )

    def on_bind(self, pet, skill):
        # 巧变池 = 施加冻结的技能，不含寒潮自身
        pool = [
            skill_by_id(sid)
            for sid in skill_group_ids("freeze_apply")
            if sid != self.skill_id
        ]
        return {"morph_pool": pool}


register(ColdSnap())


# 7100150 升龙咆哮
class DragonRoar(OpsSkill):
    """升龙咆哮：蓄力一回合后释放的龙系魔法攻击。"""

    skill_id = 7100150
    name = "升龙咆哮"
    category = 1
    windup = True
    implemented = True


register(DragonRoar())


# 7100260 绵里藏针
class SilkenThorn(OpsSkill):
    """造成魔伤，若自己上回合未使用攻击技能，本技能威力永久+20。

    - "上回合未使用攻击技能"按**上一回合**的实战记录判定
      （BattlePet.attack_used_last_turn，回合开始时由引擎平移；**蓄力回合算作
      未使用攻击技能**——用户口径；聚能/换人/防御/状态技能同样不算；释放回合
      照常算）。重放出的攻击技能算（那一回合确实打出了攻击技能），与
      used_skill_counts "疾风连袭的重放不算新的使用"是两个不同的问题。
    - "回合末结算"（用户口径）：+20 与其它命中附加效果同走 B39——命中时只登记，
      回合末统一结算写入 skill_state 的永久成长，故不改变本次伤害，从下次使用
      起生效；同一回合内多次使用（使用次数+1）会各结算一次。
    """

    skill_id = 7100260
    name = "绵里藏针"
    category = 1
    implemented = True

    hit_effects = (
        When(
            condition=Not(condition=UsedAttackSkillLastTurn()),
            effects=(SkillGrowth(field=GROWTH_POWER, delta=20),),
        ),
    )


register(SilkenThorn())


# 7130340 拟寄生
class PseudoParasite(OpsSkill):
    """造成魔伤，偷取敌方1层印记。

    虫系（element 10）魔攻，威力 80、能耗 3。偷取 =
    DispelMarks(count=1, steal=True)：敌方阵营正/负印记各被取走 1 层
    转给己方阵营（同 id 印记、同层数）；命中登记、回合末结算（B39）。
    """

    skill_id = 7130340
    name = "拟寄生"
    category = 1
    implemented = True

    hit_effects = (DispelMarks(count=1, steal=True, target=ENEMY),)


register(PseudoParasite())


# 7170110 背袭
class AmbushFromBehind(OpsSkill):
    """造成魔伤；若敌方能量等于 0，本次技能威力变为 20 倍。

    幽灵系（element 14）魔攻，威力 40、能耗 2。能量条件在
    **使用技能时**实时读取（伤害计算时），选择技能时的能量不作数；
    "20 倍"按增量语义折算为 +1900%（P11）。
    """

    skill_id = 7170110
    name = "背袭"
    category = 1
    implemented = True

    def modify_power(self, ctx):
        percent, flat = super().modify_power(ctx)
        if ctx.target is not None and ctx.target.energy == 0:
            percent += 1900.0
        return (percent, flat)


register(AmbushFromBehind())


# 7190240 四维降解
class FourDimDegrade(OpsSkill):
    """造成魔伤，敌方每有1层印记，本技能能耗-1。

    幻系（element 17）魔攻，威力 110、能耗 7。印记按**阵营**
    计：敌方阵营正/负印记层数合计（不限种类），每层能耗 -1。能耗修正走
    引擎统一管线（skill_utils.true_energy_cost 的技能自身修正项），与
    buff/印记/天气/特性修正叠加后最后统一钳制到 0（B18）。
    """

    skill_id = 7190240
    name = "四维降解"
    category = 1
    implemented = True

    def modify_energy_cost(self, ctx):
        return super().modify_energy_cost(ctx) - per_any_mark(1)(ctx)


register(FourDimDegrade())


# 7190270 错乱
class Confusion(OpsSkill):
    """造成魔伤，应对状态：敌方获得3层星陨印记。

    幻系（element 17）魔攻，威力 65、能耗 2。
    "应对状态"= counter_target 为 status：只有对方本回合使用状态行动时才算应对成功，
    此时由引擎在 STAGE_COUNTER 结算本条附加效果。

    星陨印记（7 号，负面）的触发条件是"非幻系攻击"（battle.py 中 `skill.element != 17`），
    所以错乱自己施加的印记不会被本次攻击消耗掉，顺序上不存在自触发问题。
    """

    skill_id = 7190270
    name = "错乱"
    category = 1
    counter_target = COUNTER_STATUS
    implemented = True

    counter_effects = (AddMark(mark_id=STAR_MARK, stacks=3),)


register(Confusion())


# ---------------- 连击 · 物攻 ----------------


# 7140100 反击拳
class CounterPunch(OpsSkill):
    """造成物伤，2连击，若后手攻击，改为3连击。

    武系（element 11）物攻，威力 25、能耗 2。固有 2 连击 =
    基础 1 + hit_count_flat 1；"后手"按本回合行动顺序判定（ctx.is_first，
    由排序期给出，连击数在伤害计算时即需确定）。"改为"按强制值覆盖
    （与追打"变为3连击"同口径）。
    """

    skill_id = 7140100
    name = "反击拳"
    category = 0
    hit_count_flat = 1
    implemented = True

    def modify_hit_count(self, ctx):
        flat, percent, forced = super().modify_hit_count(ctx)
        if not ctx.is_first:
            return (flat, percent, 3)
        return (flat, percent, forced)


register(CounterPunch())


# 7180110 撕咬
class Bite(OpsSkill):
    """造成物伤，3连击，若自己的生命低于50%，本次技能连击数+2。

    恶系（element 15）物攻，威力 20、能耗 3。固有 3 连击 =
    基础 1 + hit_count_flat 2（与打喷嚏同管线）；自己生命**严格低于** 50%
    （整数比较 hp*2 < max_hp，避免浮点边界）时再 +2，合计 5 连击。
    连击数走统一汇总（compute_hit_count = 基础1 + 技能修正 + buff + 特性）。
    """

    skill_id = 7180110
    name = "撕咬"
    category = 0
    hit_count_flat = 2
    implemented = True

    def modify_hit_count(self, ctx):
        flat, percent, forced = super().modify_hit_count(ctx)
        if ctx.actor.hp * 2 < ctx.actor.max_hp:
            flat += 2
        return (flat, percent, forced)


register(Bite())


# ---------------- 连击 · 魔攻 ----------------


# 7140270 叠势
class MomentumStack(OpsSkill):
    """2 连击；每成功应对一次，永久增加本技能 2 连击。"""

    skill_id = 7140270
    name = "叠势"
    category = 1
    counter_target = COUNTER_STATUS
    hit_count_flat = 1  # 基础 1 + 1 = 2 连击
    implemented = True

    counter_effects = (SkillGrowth(field=GROWTH_HIT_COUNT, delta=2),)


register(MomentumStack())


# 7170230 灵光
class SpiritLight(OpsSkill):
    """造成魔伤，3连击，若敌方本回合更换精灵，本次技能连击数翻倍。

    幽系（element 14）魔攻，威力 25、能耗 3。固有 3 连击 =
    基础 1 + hit_count_flat 2。"翻倍"作用在**最终连击数**上（buff/特性/成长
    全部结算之后），故走 adjust_hit_count 钩子做乘算，而不是用 forced 覆盖
    ——覆盖会连带丢掉连击 buff/特性（暴风眼 +100%、特性 +1 等）。
    换人先于行动结算（step 中 switch 先行），故敌方本回合主动换人在状态
    结算时 entry_turn == 当前回合，EnemySwitchedThisTurn 即刻成立。
    """

    skill_id = 7170230
    name = "灵光"
    category = 1
    hit_count_flat = 2
    implemented = True

    def adjust_hit_count(self, ctx, count):
        if EnemySwitchedThisTurn().test(ctx):
            return count * 2
        return count


register(SpiritLight())
