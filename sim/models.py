"""Core data models for the headless battle simulator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SkillData:
    id: int
    name: str
    element: int
    category: int
    energy_cost: int
    power: Optional[int]
    desc: str
    effects: list = field(default_factory=list)


@dataclass
class BattleSkill:
    skill_id: int
    name: str
    element: int
    category: int
    power: Optional[int]
    energy_cost: int
    desc: str
    have_counter: bool = False
    counter_target: str = ""
    counter_interrupt: bool = False  # 打断：应对成功时被应对的技能/聚能行动作废
    drive: int = 0  # 传动值：回合开始时技能向下移动的格数
    swift: bool = False  # 迅捷标记：主动换人入场时自动释放
    # 真正的巧变：技能使用后替换/临时技能用后还原
    morph_pool: list = field(default_factory=list)  # 巧变随机池：BattleSkill 模板列表
    morph_origin: Optional["BattleSkill"] = None  # 巧变临时技能记录的原技能
    # 每回合替换（借用/取念/复写）：独立生命周期，入场/回合末抽取，离场还原
    round_replace_origin: Optional["BattleSkill"] = None
    windup: bool = False  # 蓄力技能标记：需要先蓄力1回合才能释放
    choice: bool = False  # 选择标记：本技能含「明/暗」两个效果，使用时二选一
    usable: bool = (
        True  # False = 无法主动使用（技能代码定义，如"使用3次翼系技能后自动使用"）
    )
    # 技能自身的持久运行时状态（永久成长值/使用计数等），由 sim.skills 的效果原语读写
    skill_state: dict = field(default_factory=dict)


@dataclass
class BattlePet:
    side: str
    spirit_id: int
    name: str
    level: int
    stats: dict
    hp: int
    max_hp: int
    energy: int
    skills: list
    attributes: list = field(default_factory=list)
    speed_range: list = field(default_factory=lambda: [0, 0])
    defense_reduction: float = 0.0
    buffs: list = field(default_factory=list)
    overload_next: dict = field(default_factory=dict)
    overload_current: dict = field(default_factory=dict)
    ivs: dict = field(default_factory=dict)
    nature: int | None = None
    bloodline: int | None = (
        None  # 血脉编号：0-17 元素血脉；18=首领血脉（enums.LORD_BLOODLINE）。一只精灵只有一种血脉
    )
    skill_cooldowns: set = field(default_factory=set)
    skill_cooldowns_pending: set = field(default_factory=set)
    has_acted_since_entry: bool = False
    entry_turn: int = (
        0  # 最近一次入场的回合（返场免疫判定：本回合入场的精灵免疫返场效果）
    )
    light_heal_rounds: int = 0  # 光合治愈持续回合数（回合结束回复）
    windup_skill: Optional[BattleSkill] = None  # 当前正在蓄力的技能
    cute_history: list[int] = field(default_factory=list)  # 每次萌化前的精灵形态 ID，恢复时后进先出
    # 技能侧免死（血气"应对攻击：本回合受到致命伤害时，保留1生命值"）：
    # 记录生效回合；跨回合自动失效，无需回合末清理。
    survive_lethal_turn: int = 0
    bursts: list = field(default_factory=list)
    wish_original_skill: Optional[BattleSkill] = None
    trait_id: Optional[int] = None
    trait_state: dict = field(default_factory=dict)
    # 应对统计（按精灵）：{"count": 累计次数, "types": {"attack": n, "defense": n, "status": n}}
    # 由 counter 模块记录，供"每应对成功N次"类特性读取
    counter_stats: dict = field(default_factory=lambda: {"count": 0, "types": {}})
    # 本场战斗释放过的技能：{技能 id: 使用次数}（插入序即首次使用顺序，键按技能
    # 去重——疾风连袭的重放集合按键过滤；"每使用1次X技能"类按次数读取；记录实时
    # 更新，含迅捷入场自动释放与巧变临时技能）
    used_skill_counts: dict = field(default_factory=dict)
    revealed_skills: set = field(default_factory=set)
    # 上一次使用的技能 id（"若上次使用攻击技"类判定；聚能/换人不改变，
    # 疾风连袭的重放不算新的使用；力竭/离场后保留记录）
    last_used_skill_id: Optional[int] = None
    # "上回合是否使用攻击技能"类判定（绵里藏针"若自己上回合未使用攻击技能"）：
    # 回合开始时由引擎把 this 平移到 last 并清零；**蓄力回合不算使用攻击技能**
    # （用户口径：蓄力算作未使用攻击技能），释放回合照常算；重放出的攻击技能也算。
    attack_used_this_turn: bool = False
    attack_used_last_turn: bool = False

    def __setattr__(self, name, value) -> None:
        object.__setattr__(self, name, value)
        # 生命写入统一入口：所有伤害、回复、代价和比例交换都实时检查冻结。
        # 构造阶段 hp 先于 buffs 赋值，此时尚无冻结状态，不执行规则查询。
        if name == "hp" and "buffs" in self.__dict__:
            from .buffs import check_freeze
            check_freeze(self)

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def speed(self) -> int:
        return self.stats["speed"]


@dataclass
class BattleState:
    teams: dict = field(default_factory=dict)
    active: dict = field(default_factory=lambda: {"A": 0, "B": 0})
    magic: dict = field(default_factory=lambda: {"A": 4, "B": 4})
    marks: dict = field(
        default_factory=lambda: {
            "A": {"positive": None, "negative": None},
            "B": {"positive": None, "negative": None},
        }
    )
    round_end_order: list = field(default_factory=lambda: ["A", "B"])
    trait_round_end_turn: int = -1  # 防止脱离续接重复执行本回合特性阶段
    weather: int | None = None
    resonance_magic: dict = field(
        default_factory=lambda: {
            "A": None,
            "B": None,
        }
    )
    resonance_usage: dict = field(
        default_factory=lambda: {
            "A": {},
            "B": {},
        }
    )
    resonance_cooldown: dict = field(
        default_factory=lambda: {
            "A": {},
            "B": {},
        }
    )
    turn: int = 0
    log: list = field(default_factory=list)
    winner: Optional[str] = None
    entry_done: bool = False
    # 特性请求的换人（如警惕：回合结束能量为0时脱离）——服务端发 choose_replacement 由玩家选人
    pending_switch: dict = field(default_factory=lambda: {"A": False, "B": False})
    # 返场请求（精灵离场并立即入场，如特性安可 200290、技能过载回路"回合结束自己返场"）
    # ——引擎在回合末自动执行，无需玩家输入
    pending_reenter: dict = field(default_factory=lambda: {"A": False, "B": False})
    # 返场成功后给予的迸发，记录请求者，避免发给被替换后的其它精灵。
    pending_reenter_bursts: dict = field(default_factory=lambda: {"A": [], "B": []})
    # 技能驱动的脱离请求（如移花接木"随后脱离"、吓退"敌方脱离"）：该侧行动结算后立即
    # 离场并重新选人。与 pending_switch 的区别：它在**回合中**生效并跳过离场者的回合末结算。
    pending_action_leave: dict = field(default_factory=lambda: {"A": False, "B": False})
    # 技能脱离附带"替换入场的精灵回复N能量"（如加大功率）：与 pending_action_leave
    # 同生命周期登记，离场暂停时转入 paused_turn 上下文，由 resume_after_leave 发给入场精灵。
    pending_entry_energy: dict = field(default_factory=lambda: {"A": 0, "B": 0})
    # 击鼓传花"自己脱离，下个入场精灵继承自己增益"：登记（consume）→ 执行离场时
    # 在清除 NORMAL buff 前快照到 pending_entry_buffs（元素
    # (buff_type, value, duration, source_side, source_pet, source_kind)，只含增益），
    # 由 resume_after_leave 发给本侧下一只入场精灵；脱离未执行则一并作废。
    pending_entry_inherit: dict = field(default_factory=lambda: {"A": False, "B": False})
    pending_entry_buffs: dict = field(default_factory=lambda: {"A": [], "B": []})
    # 回合因脱离暂停：非 None 时 step 已中断等待选人，含续接上下文与应选人的一侧（leave_side）
    paused_turn: Optional[dict] = None
    # 应对奖励从句的登记（应对判定成功时记入）：延迟到回合末结算的第一步统一生效（B38）。
    # 元素：{"pet", "skill", "skill_index", "is_first", "counter_category",
    #        "energy_cost", "damage_dealt", "hit_count"}
    pending_counters: list = field(default_factory=list)

    @property
    def pets(self) -> dict:
        return {side: self.teams[side][self.active[side]] for side in ("A", "B")}


@dataclass
class Action:
    kind: str
    skill_index: Optional[int] = None
    pet_index: Optional[int] = None
    magic_id: Optional[int] = None
    magic_branch: int = 0
    choice_branch: int = 0  # 选择技能的分支：0=明，1=暗
