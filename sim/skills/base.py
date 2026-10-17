"""技能系统接口：上下文 + handler 基类。

设计约定（与 sim/traits 对称）
------------------------------
- **描述不参与运行时**。`data/skills.json` 的 `desc` 只用于人工校对与客户端展示，
  引擎任何位置都不得正则/关键词解析它。技能的全部效果以本包的 Python 定义为准。
- **静态属性**在技能构造/重绑时确定（`SkillHandler.on_bind`），写入 BattleSkill 实例：
  传动/迅捷/蓄力/应对类别/可用性/巧变池。这是 `battle._parse_reduction(desc)` 的替代物。
- **修正查询**（`modify_*`）在数值计算处汇总，一律"增量"语义：返回 0 表示无修正。
- **事件钩子**（`on_*`）在技能结算的固定阶段调用，可产生副作用（改血量/能量/buff/印记）。

一次技能结算的阶段顺序（与 battle._resolve_action 的现有分支一一对应）::

    skill_start       → on_use_start     支付能耗后、造成伤害前
    (伤害计算)         → modify_power / modify_hit_count / modify_lifesteal
    伤害结算后         → on_hit
    应对成功时         → on_counter      （攻击/防御/状态三条分支共用）
    防御技能本体       → on_defense      （含 modify_damage_reduction）
    状态技能本体       → on_status
    使用后             → on_after_use    永久成长/巧变/位置/冷却/离场/返场
    回合结束           → on_round_end    技能登记的持续效果
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models import BattlePet, BattleSkill, BattleState


@dataclass
class SkillContext:
    """传给技能 handler 的上下文。actor 永远是技能使用者。

    常用字段（随阶段不同而部分生效）：
      state            战斗状态（可读写）
      actor            使用者精灵
      skill            本次使用的技能实例（BattleSkill，技能阶段必有、非空）
      skill_index      技能槽位（0-based）；静态阶段用于"1号位/3号位"判定
      target           对方在场精灵（无则 None）
      category         "attack"/"defense"/"status"（skill_utils.category_of）
      is_first         是否先手行动
      is_counter       是否应对成功
      choice_branch    选择技能的分支：0=明，1=暗（Action.choice_branch 透传）
      counter_category 被应对技能的类别（is_counter=True 时有意义）
      energy_cost      本次实际能耗
      damage_dealt     本次实际造成伤害
      hit_count        本次连击次数
      weather          当前天气 id（None 表示无天气）
      logs             本次行动的本地日志列表（引擎传入）；缺省为 None 时写 state.log
      extra            阶段专属扩展字段
    """

    state: BattleState
    actor: BattlePet
    skill: BattleSkill
    skill_index: int = -1
    target: Optional[BattlePet] = None
    category: str = ""
    is_first: bool = False
    is_counter: bool = False
    choice_branch: int = 0
    counter_category: str = ""
    energy_cost: int = 0
    damage_dealt: int = 0
    hit_count: int = 1
    weather: Optional[int] = None
    logs: Optional[list] = None
    extra: dict = field(default_factory=dict)

    # ---- 便捷方法 ----

    def log(self, msg: str) -> None:
        """写日志（**技能 handler 不得使用**，见 README S29）。

        与特性侧同一约定：接口保留、默认不使用，技能效果只通过状态变化体现；
        引擎自身可以为技能引起的结果写日志（使用 / 伤害 / 脱离 / 力竭等）。
        """
        (self.logs if self.logs is not None else self.state.log).append(msg)

    def is_active(self) -> bool:
        idx = self.state.active[self.actor.side]
        return idx >= 0 and self.state.teams[self.actor.side][idx] is self.actor

    def opponent(self) -> Optional[BattlePet]:
        opp_side = "B" if self.actor.side == "A" else "A"
        idx = self.state.active[opp_side]
        if idx < 0:
            return None
        return self.state.teams[opp_side][idx]

    # ---- 技能自身运行时状态（BattleSkill.skill_state） ----

    def state_of(self, key, default=None):
        return self.skill.skill_state.get(key, default)

    def set_state(self, key, value) -> None:
        self.skill.skill_state[key] = value

    def add_state(self, key, amount) -> None:
        self.skill.skill_state[key] = self.skill.skill_state.get(key, 0) + amount


class SkillHandler:
    """所有技能 handler 的基类。

    子类覆写需要的钩子，其余保持默认空实现。注册方式::

        class Scratch(SkillHandler):
            skill_id = 7020360
            name = "抓挠"
            category = 0
            implemented = True

            def on_hit(self, ctx):
                GainEnergy(value=1).apply(ctx)

        register(Scratch())

    声明式写法见 ``ops.OpsSkill``（把效果原语挂到各阶段，无需手写钩子）。
    """

    skill_id: int = 0
    name: str = ""
    category: int = -1
    desc: str = ""          # 仅展示/校对；引擎绝不解析
    implemented: bool = False   # 该技能的全部效果都已在代码中定义（与 skills.json 的 done 对应）

    # ================= 静态属性（构造/重绑时确定） =================

    counter_target: str = ""    # 应对类别："attack"/"defense"/"status"；空=不应对
    counter_interrupt: bool = False  # 打断：应对成功时被应对的技能/聚能行动作废
    drive: int = 0              # 传动值
    swift: bool = False         # 迅捷：主动换人入场时自动释放
    windup: bool = False        # 蓄力：需要先蓄力 1 回合
    choice: bool = False          # 选择：本技能含「明/暗」两个效果，使用时二选一
    usable: bool = True         # False = 无法主动使用（如"使用3次翼系技能后自动使用"）
    element: Optional[int] = None   # 固有属性改写（None = 用 skills.json 的属性）

    def on_bind(self, pet: BattlePet, skill: BattleSkill) -> dict:
        """技能构造/重绑时覆盖静态属性。

        返回需要写回 BattleSkill 的字段字典，键取自：
        ``counter_target / drive / swift / windup / usable / element / morph_pool``。

        用于"携带即生效"的静态定义（巧变池、条件传动等）。需要战斗状态的动态
        判定请在下面的 modify_* 查询里做，不要放在这里。
        """
        return {}

    # ================= 修正查询（增量语义） =================

    def modify_power(self, ctx: SkillContext) -> tuple:
        """技能威力增量，返回 ``(百分比, 固定值)``。"""
        return (0.0, 0.0)

    def absolute_power(self, ctx: SkillContext) -> Optional[int]:
        """技能威力的绝对值覆盖；None = 使用 skills.json 的 power。

        用于"威力由规则算出"的技能——这类技能的 power 在数据里只能是桩值：
        「魔能爆」消耗能量越高伤害越高、「钢钻」两侧技能威力和的 1/3、
        「冰锋横扫」敌方技能总能耗×10、「怨力打击」蓄力期间受击则为敌方威力×3。
        引擎查到非 None 时应以此值作为基础威力，再叠加 modify_power 的增量。
        """
        return None

    def modify_hit_count(self, ctx: SkillContext) -> tuple:
        """连击增量，返回 ``(固定值, 百分比, 强制值|None)``；强制值非 None 时覆盖连击数。"""
        return (0, 0, None)

    def adjust_hit_count(self, ctx: SkillContext, count: int) -> int:
        """连击数的最终修正（乘算/翻倍类，如灵光"若敌方本回合更换精灵连击数翻倍"）。

        ``modify_hit_count`` 只给增量，拿不到 buff/特性/成长汇总后的连击数；
        "翻倍"这类乘算在汇总之后由本钩子处理（默认原样返回）。
        """
        return count

    def modify_energy_cost(self, ctx: SkillContext) -> int:
        """技能能耗增量。"""
        return 0

    def modify_other_skill_energy_cost(
        self, ctx: SkillContext, target_skill: BattleSkill, target_index: int
    ) -> int:
        """携带本技能时，对同一精灵另一技能能耗的增量。"""
        return 0

    def modify_energy_shortfall(self, ctx: SkillContext, need: int, dry_run: bool = False) -> int:
        """能量不足时本技能可补充的能量（handler 自行支付代价，如扣血）。

        与特性侧 ``modify_energy_shortfall`` 同一语义：返回补充的能量数，
        代价（扣血等）由 handler 在返回前自行结算。
        dry_run=True 为选择校验探测：不得支付代价，只回答"当前状态能否补足"
        （生命够付且付完不死才算能补足）；结算时（dry_run=False）不做死亡
        保护——全额支付，生命 ≤0 由引擎直接力竭。
        """
        return 0

    def modify_priority(self, ctx: SkillContext) -> int:
        """先手增量（"先手+1"/"先手-1"）。"""
        return 0

    def modify_lifesteal(self, ctx: SkillContext) -> float:
        """吸血比例增量（0.5 = +50%）。"""
        return 0.0

    def modify_damage_reduction(self, ctx: SkillContext) -> float:
        """防御技能减伤（0.7 = 减伤70%）；仅防御技能分支查询。"""
        return 0.0

    def modify_element(self, ctx: SkillContext) -> Optional[int]:
        """本次技能属性改写（如"本技能系别和天气系别相同"）；None = 不改写。"""
        return None

    # ================= 每回合替换（借用/取念/复写） =================

    # 与巧变（morph_pool / morph_origin）完全分离：在场时每回合末重抽，离场还原。
    round_replace: bool = False
    round_replace_cost_delta: int = 0  # 取念/复写为 -2

    def round_replace_pool(self, state: BattleState, pet: BattlePet,
                           skill: BattleSkill) -> list:
        """返回每回合替换的候选技能模板池；空 = 本回合不替换。"""
        return []

    # ================= 事件钩子 =================

    def on_entry(self, ctx: SkillContext) -> None:
        """精灵入场时，对其携带技能广播一次。"""

    def on_slot_change(self, ctx: SkillContext) -> None:
        """传动重排后本技能槽位发生变化时广播。

        ctx.extra 带 ``old_index`` / ``new_index``（0-based），ctx.skill_index 为新槽位。
        """

    def on_use_start(self, ctx: SkillContext) -> None:
        """支付能耗后、造成伤害前（对应引擎的 skill_start 时点）。"""

    def on_hit(self, ctx: SkillContext) -> None:
        """本次伤害结算后（含未造成伤害的情况）；ctx.damage_dealt 可用。"""

    def on_counter(self, ctx: SkillContext) -> None:
        """应对成功（ctx.is_counter=True，ctx.counter_category=被应对技能类别）。

        攻击/防御/状态三条分支共用本钩子；应对奖励按被应对类别区分时读 ctx.counter_category。
        """

    def on_defense(self, ctx: SkillContext) -> None:
        """防御技能本体结算（减伤已通过 modify_damage_reduction 取出）。"""

    def on_status(self, ctx: SkillContext) -> None:
        """状态技能本体结算。"""

    def on_after_use(self, ctx: SkillContext) -> None:
        """使用后：永久成长、巧变、位置变化、技能冷却、脱离/返场等。"""

    def on_round_end(self, ctx: SkillContext) -> None:
        """回合结束（技能登记的持续效果；ctx.actor 为技能所有者）。"""

    def on_take_damage(self, ctx: SkillContext) -> None:
        """自己（ctx.actor）受到攻击伤害后广播到本技能（STAGE_TAKE_DAMAGE）。

        ctx.skill 是本技能自身，ctx.target 是攻击方；本次伤害在
        ``ctx.extra["damage_taken"]``、来招技能在 ``ctx.extra["incoming_skill"]``、
        连击段数在 ctx.hit_count。触发条件（如"本回合应对攻击成功期间"）由
        handler 自行判定——引擎只负责在攻击伤害真正落位后广播。
        """
