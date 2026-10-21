"""Buff/debuff system: data structures and round-end resolution.

This module intentionally does not depend on battle flow details.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .data_loader import load_typechart
from .evolution import apply_cute, can_cute
from .typechart import type_multiplier


class BuffType:
    ATK = "atk"  # 物攻
    SPATK = "spatk"  # 魔攻
    DEF = "def"  # 物抗
    SPDEF = "spdef"  # 魔抗
    SKILL_POWER_PERCENT = "skill_power_percent"  # 技能威力_百分比
    NEXT_ATTACK_POWER_PERCENT = "next_attack_power_percent"  # 下一次攻击技能威力_百分比（一次性，攻击技能用时消耗）
    NEXT_ATTACK_POWER_FLAT = "next_attack_power_flat"  # 下一次攻击技能威力_固定值（一次性，攻击技能用时消耗）
    LIFESTEAL = "lifesteal"  # 吸血
    HIT_COUNT_PERCENT = "hit_count_percent"  # 连击数_百分比
    SPEED = "speed"  # 速度_固定值（每层+10）
    SPEED_PERCENT = "speed_percent"  # 速度_百分比（每层+10%）
    SKILL_POWER_FLAT = "skill_power_flat"  # 技能威力_固定值
    HIT_COUNT_FLAT = "hit_count_flat"  # 连击数_固定值
    ENERGY_COST = "energy_cost"  # 能耗
    ATTACK_ENERGY_COST = "attack_energy_cost"  # 攻击技能能耗（仅攻击技能生效）
    OVERLOAD = "overload"  # 超载
    PRIORITY = "priority"  # 先手
    PRIORITY_DEBUFF = "priority_debuff"  # 先手减益
    POISON = "poison"  # 中毒
    BURN = "burn"  # 灼伤
    DIZZY = "dizzy"  # 眩晕
    LEECH = "leech"  # 寄生
    FREEZE = "freeze"  # 冰冻
    FREEZE_IMMUNE = "freeze_immune"  # 免疫冻结（特性给予，如吉利丁片 200253）
    BURN_IMMUNE = "burn_immune"  # 免疫灼烧（特性给予，如美拉德反应 200254）
    LEECH_IMMUNE = "leech_immune"  # 免疫寄生（特性给予，如茶多酚 200202）
    CUTE = "cute"  # 萌化
    LOCK = "lock"  # 禁足
    LIGHTNING = "lightning"  # 引电


ALWAYS_BUFF_TYPES = {
    BuffType.LIFESTEAL,
    BuffType.FREEZE_IMMUNE,
    BuffType.BURN_IMMUNE,
    BuffType.LEECH_IMMUNE,
}

# 真·百分比型 buff（1%/层）：层数钳制放宽到 ±990（即 ±990%），
# 与 10%/层 buff（属性/速度%/吸血，±99 层 = ±990%）的有效上限一致（SPEC B29）。
# 其余 buff 仍按 ±99 层钳制。
PERCENT_LAYER_BUFF_TYPES = frozenset(
    {
        BuffType.SKILL_POWER_PERCENT,
        BuffType.NEXT_ATTACK_POWER_PERCENT,
        BuffType.HIT_COUNT_PERCENT,
    }
)


def layer_clamp_bound(buff_type: str) -> int:
    """单条增减益的层数钳制边界（B29）。"""
    return 990 if buff_type in PERCENT_LAYER_BUFF_TYPES else 99


# 类减益 -> 免疫该减益的 buff 类型（特性给予的免疫，如吉利丁片/美拉德反应/茶多酚）
IMMUNE_BUFF_FOR = {
    BuffType.FREEZE: BuffType.FREEZE_IMMUNE,
    BuffType.BURN: BuffType.BURN_IMMUNE,
    BuffType.LEECH: BuffType.LEECH_IMMUNE,
}


def is_immune_to(pet, buff_type: str) -> bool:
    """该精灵是否免疫指定减益（特性给予的免疫 buff）。"""
    immune_type = IMMUNE_BUFF_FOR.get(buff_type)
    return immune_type is not None and get_buff_value(pet, immune_type) > 0


SIGNED_TYPES = {
    BuffType.ATK,
    BuffType.SPATK,
    BuffType.DEF,
    BuffType.SPDEF,
    BuffType.SKILL_POWER_PERCENT,
    BuffType.NEXT_ATTACK_POWER_PERCENT,
    BuffType.HIT_COUNT_PERCENT,
    BuffType.SPEED,
    BuffType.SPEED_PERCENT,
    BuffType.SKILL_POWER_FLAT,
    BuffType.NEXT_ATTACK_POWER_FLAT,
    BuffType.HIT_COUNT_FLAT,
    BuffType.OVERLOAD,
    BuffType.PRIORITY,
    BuffType.ENERGY_COST,
    BuffType.ATTACK_ENERGY_COST,
}

DEBUFF_TYPES = {
    BuffType.PRIORITY_DEBUFF,
    BuffType.POISON,
    BuffType.BURN,
    BuffType.DIZZY,
    BuffType.LEECH,
    BuffType.FREEZE,
    BuffType.CUTE,
    BuffType.LOCK,
    BuffType.LIGHTNING,
}


def classify_buff(buff_type: str, value: int = 0) -> str:
    if buff_type in ALWAYS_BUFF_TYPES:
        return "buff"
    if buff_type in DEBUFF_TYPES:
        return "debuff"
    if buff_type in SIGNED_TYPES:
        if buff_type in (BuffType.ENERGY_COST, BuffType.ATTACK_ENERGY_COST):
            if value > 0:
                return "debuff"
            if value < 0:
                return "buff"
            return "unknown"
        if value > 0:
            return "buff"
        if value < 0:
            return "debuff"
        return "unknown"
    # 按元素的技能威力增益（"X系技能威力+N%"，如点亮给光系；命名约定 power_boost_<元素id>）
    if buff_type.startswith("power_boost_"):
        return "buff" if value >= 0 else "debuff"
    # 按技能的能耗修正（"被应对技能能耗+N"；命名约定 energy_cost_boost_<技能id>）
    if buff_type.startswith("energy_cost_boost_"):
        return "debuff" if value > 0 else "buff"
    return "unknown"


def element_power_buff_type(element: int) -> str:
    """按元素的技能威力增益的 buff 类型名（"X系技能威力+N%"类）。"""
    return f"power_boost_{element}"


def get_element_power_percent(pet, element: int) -> int:
    """该精灵对指定元素技能的威力百分比增益（"X系技能威力+N%"类 buff 合计）。"""
    return get_buff_value(pet, element_power_buff_type(element))


def skill_energy_boost_type(skill_id: int) -> str:
    """按技能的能耗修正的 buff 类型名（"被应对技能能耗+N"类）。"""
    return f"energy_cost_boost_{skill_id}"


def get_skill_energy_boost(pet, skill_id: int) -> int:
    """该精灵对指定技能（按 id）的能耗修正（"被应对技能能耗+N"类 buff 合计）。"""
    return get_buff_value(pet, skill_energy_boost_type(skill_id))


def is_buff(buff_type: str, value: int = 0) -> bool:
    return classify_buff(buff_type, value) in ("buff", "both")


def is_debuff(buff_type: str, value: int = 0) -> bool:
    return classify_buff(buff_type, value) in ("debuff", "both")


class DebuffType:
    POISON = BuffType.POISON
    BURN = BuffType.BURN
    DIZZY = BuffType.DIZZY
    LEECH = BuffType.LEECH
    FREEZE = BuffType.FREEZE
    CUTE = BuffType.CUTE
    LOCK = BuffType.LOCK
    LIGHTNING = BuffType.LIGHTNING


class DurationKind:
    PERMANENT = "permanent"  # 永久
    NORMAL = "normal"  # 常规
    TEMPORARY = "temporary"  # 临时


@dataclass
class Buff:
    buff_type: str
    value: int
    duration: str = DurationKind.NORMAL
    source_side: str = ""
    source_pet: str = ""
    expire_turn: int | None = None
    source_kind: str = ""  # "" = 技能/常规来源；"debuff" = 特性给敌方施加的减益；
    # "trait" = 特性自身的展示效果（不是增益/减益，另一类别）

    def is_gain(self) -> bool:
        """是否属于"增益"。特性自身的展示效果（source_kind="trait"）不是增益。"""
        if self.source_kind == "trait":
            return False
        return classify_buff(self.buff_type, self.value) in ("buff", "both")

    def is_loss(self) -> bool:
        """是否属于"减益"。特性自身的展示效果（source_kind="trait"）不是减益。"""
        if self.source_kind == "trait":
            return False
        return classify_buff(self.buff_type, self.value) in ("debuff", "both")


def _allows_cute_reapplication(pet) -> bool:
    """已有萌化且当前特性允许再获得萌化时，不要求还能退化。"""
    if not has_buff(pet, BuffType.CUTE):
        return False
    from .traits.registry import get_handler

    handler = get_handler(pet.trait_id)
    return handler is not None and handler.allow_cute_reapplication(pet)


def _allows_cute_beyond_limit(pet) -> bool:
    """特性允许萌化层数超过可退化次数（无忧无虑 200182）。"""
    from .traits.registry import get_handler

    handler = get_handler(pet.trait_id)
    return handler is not None and handler.allow_cute_beyond_limit(pet)


def can_gain_buff(pet, buff_type: str, value: int) -> bool:
    """获得规则探测，不修改状态；必须在调用 add_buff 前执行。"""
    if value == 0 or (buff_type == BuffType.LIFESTEAL and value < 0):
        return False
    if is_immune_to(pet, buff_type):
        return False
    elemental_immunity = {
        BuffType.FREEZE: 6,
        BuffType.BURN: 2,
        BuffType.POISON: 9,
        BuffType.LEECH: 1,
        BuffType.LIGHTNING: 8,
    }
    element = elemental_immunity.get(buff_type)
    if element is not None and element in pet.attributes:
        return False
    if buff_type == BuffType.LOCK and has_buff(pet, BuffType.LOCK):
        return False
    if buff_type == BuffType.CUTE:
        return value > 0 and (
            _allows_cute_reapplication(pet)
            or _allows_cute_beyond_limit(pet)
            or can_cute(pet)
        )
    return True


def grant_buff(
    pet,
    buff_type: str,
    value: int,
    duration: str = DurationKind.NORMAL,
    current_turn: int | None = None,
    source_side: str = "",
    source_pet: str = "",
    source_kind: str = "",
) -> int:
    """规则层入口：先拦截/执行即时效果，再调用纯状态写入；返回实际获得层数。

    不广播 buff_gain；调用方只在返回值非零时广播，特性内部仍可不广播。
    """
    if not can_gain_buff(pet, buff_type, value):
        return 0
    if source_kind != "trait" and is_buff(buff_type, value):
        from .traits.registry import get_handler

        handler = get_handler(pet.trait_id)
        if handler is not None:
            extra_layers = handler.modify_buff_gain_layers(pet, buff_type, value)
            value += extra_layers if value > 0 else -extra_layers
    if buff_type == BuffType.CUTE:
        applied = 0
        for _ in range(value):
            # 再获得萌化响应类特性：只获得状态并交给 buff_gain 处理，
            # 不先退化，否则会重绑定特性并丢失本次解除萌化的响应者。
            if _allows_cute_reapplication(pet):
                applied += 1
                continue
            if can_cute(pet):
                apply_cute(pet)
                applied += 1
                continue
            # 萌化层数不受可退化次数限制的特性（无忧无虑）：形态不变，层数照加
            if _allows_cute_beyond_limit(pet):
                applied += 1
                continue
            break
        value = applied
        if value == 0:
            return 0
    before = get_buff_value(pet, buff_type)
    add_buff(
        pet,
        buff_type,
        value,
        duration,
        current_turn,
        source_side,
        source_pet,
        source_kind,
    )
    gained = get_buff_value(pet, buff_type) - before
    if gained and buff_type == BuffType.FREEZE:
        check_freeze(pet)
    if gained and buff_type == BuffType.LIGHTNING:
        if get_buff_value(pet, BuffType.LIGHTNING) >= 2:
            _trigger_lightning(pet)
    return gained


def add_buff(
    pet,
    buff_type: str,
    value: int,
    duration: str = DurationKind.NORMAL,
    current_turn: int | None = None,
    source_side: str = "",
    source_pet: str = "",
    source_kind: str = "",
) -> None:
    if value == 0:
        return

    if buff_type != BuffType.LOCK and buff_type != BuffType.CUTE:
        current = get_buff_value(pet, buff_type)
        bound = layer_clamp_bound(buff_type)
        new_total = max(-bound, min(bound, current + value))
        if new_total == 0:
            remove_buff(pet, buff_type)
            return
        if new_total == current:
            return
        value = new_total - current

    if buff_type == BuffType.LOCK:
        pet.buffs.append(
            Buff(
                buff_type=buff_type,
                value=value,
                duration=duration,
                source_side=source_side,
                source_pet=source_pet,
                source_kind=source_kind,
            )
        )
        return

    if buff_type in (BuffType.FREEZE, BuffType.CUTE):
        duration = DurationKind.PERMANENT
    expire_turn = (
        current_turn + 1
        if duration == DurationKind.TEMPORARY and current_turn is not None
        else None
    )
    pet.buffs.append(
        Buff(
            buff_type=buff_type,
            value=value,
            duration=duration,
            source_side=source_side,
            source_pet=source_pet,
            expire_turn=expire_turn,
            source_kind=source_kind,
        )
    )


def remove_buff(pet, buff_type: str) -> None:
    pet.buffs = [b for b in pet.buffs if b.buff_type != buff_type]


def get_buff_value(pet, buff_type: str) -> int:
    return sum(b.value for b in pet.buffs if b.buff_type == buff_type)


def has_buff(pet, buff_type: str) -> bool:
    return any(b.buff_type == buff_type for b in pet.buffs)


def clear_normal_buffs(pet) -> None:
    pet.buffs = [b for b in pet.buffs if b.duration != DurationKind.NORMAL]


def expire_temporary_buffs(pet, current_turn: int) -> None:
    pet.buffs = [
        b
        for b in pet.buffs
        if b.duration != DurationKind.TEMPORARY
        or b.expire_turn is None
        or b.expire_turn > current_turn
    ]


def can_act(pet) -> bool:
    return get_buff_value(pet, BuffType.DIZZY) <= 0


def can_switch(pet) -> bool:
    return not has_buff(pet, BuffType.LOCK)


def add_overload(pet, skill_id: int, amount: int = 1) -> None:
    pet.overload_next[skill_id] = pet.overload_next.get(skill_id, 0) + amount


def get_overload(pet, skill_id: int) -> int:
    return pet.overload_current.get(skill_id, 0) + pet.overload_next.get(skill_id, 0)


def consume_overload(pet, skill_id: int) -> int:
    if pet.overload_current.get(skill_id, 0) > 0:
        pet.overload_current[skill_id] -= 1
        return 1
    if pet.overload_next.get(skill_id, 0) > 0:
        pet.overload_next[skill_id] -= 1
        return 1
    return 0


def start_turn_overload(pet) -> None:
    pet.overload_current = pet.overload_next
    pet.overload_next = {}


def _immune(pet, element: int) -> bool:
    return element in pet.attributes


def _apply_damage(pet, damage: int, current_turn: int | None = None) -> None:
    """持续伤害结算。血气类"本回合受到致命伤害保留1血"对回合末持续伤害
    （中毒/灼烧/寄生）同样生效；冰冻力竭是阈值判定（非伤害），不受其保护。
    """
    if (
        current_turn is not None
        and damage >= pet.hp
        and getattr(pet, "survive_lethal_turn", 0) == current_turn
    ):
        pet.hp = 1
        return
    pet.hp = max(0, pet.hp - damage)


def _find_source_pet(state, source_side: str, source_pet: str):
    if not source_side or not source_pet:
        return None
    for pet in state.teams.get(source_side, []):
        if pet.name == source_pet:
            return pet
    return None


def _trigger_lightning(pet) -> None:
    if _immune(pet, 8):
        remove_buff(pet, BuffType.LIGHTNING)
        return
    typechart = load_typechart()
    mult = type_multiplier(8, pet.attributes, typechart)
    damage = int(pet.max_hp * 0.25 * mult)
    _apply_damage(pet, damage)
    remove_buff(pet, BuffType.LIGHTNING)


def on_round_end(state) -> None:
    typechart = load_typechart()
    order = list(getattr(state, "round_end_order", ["A", "B"]))

    for side in order:
        for pet in state.teams[side]:
            if pet.hp <= 0:
                continue

            _apply_poison(pet, typechart, state)
            _apply_burn(pet, typechart, state)
            _apply_leech(pet, state, typechart)
            _apply_dizzy(pet)
            _apply_lock(pet)
            expire_temporary_buffs(pet, state.turn)


def _apply_poison(pet, typechart, state=None) -> None:
    layers = get_buff_value(pet, BuffType.POISON)
    if layers <= 0 or _immune(pet, 9):
        return
    mult = type_multiplier(9, pet.attributes, typechart)
    damage = int(pet.max_hp * 0.03 * layers * mult)
    _apply_damage(pet, damage, state.turn if state is not None else None)
    if damage > 0 and state is not None:
        state.log.append(f"{pet.side} {pet.name} 受到中毒 {damage} 伤害")
        from . import traits  # 延迟导入，避免与 traits.impl 循环依赖

        traits.emit(
            state,
            "dot_damage",
            scope="all",
            side=pet.side,
            subject=pet,
            damage=damage,
            dot_type="poison",
        )


def _apply_burn(pet, typechart, state=None) -> None:
    layers = get_buff_value(pet, BuffType.BURN)
    if layers <= 0 or _immune(pet, 2):
        return
    # 免疫灼烧（特性给予）：不受灼烧效果
    if is_immune_to(pet, BuffType.BURN):
        return
    mult = type_multiplier(2, pet.attributes, typechart)
    damage = int(pet.max_hp * 0.02 * layers * mult)
    _apply_damage(pet, damage, state.turn if state is not None else None)
    if damage > 0 and state is not None:
        state.log.append(f"{pet.side} {pet.name} 受到灼烧 {damage} 伤害")
        from . import traits  # 延迟导入，避免与 traits.impl 循环依赖

        traits.emit(
            state,
            "dot_damage",
            scope="all",
            side=pet.side,
            subject=pet,
            damage=damage,
            dot_type="burn",
        )
    if state is not None:
        from . import traits  # 延迟导入，避免与 traits.impl 循环依赖

        if traits.query_burn_growth(state, pet):
            new_layers = layers * 2
        else:
            new_layers = layers // 2
    else:
        new_layers = layers // 2
    remove_buff(pet, BuffType.BURN)
    if new_layers > 0:
        pet.buffs.append(
            Buff(
                buff_type=BuffType.BURN, value=new_layers, duration=DurationKind.NORMAL
            )
        )


def _apply_leech(pet, state, typechart) -> None:
    layers = get_buff_value(pet, BuffType.LEECH)
    if layers <= 0 or _immune(pet, 1):
        return
    # 免疫寄生（特性给予）：不受寄生效果
    if is_immune_to(pet, BuffType.LEECH):
        return
    mult = type_multiplier(1, pet.attributes, typechart)
    damage = int(pet.max_hp * 0.02 * layers * mult)
    _apply_damage(pet, damage, state.turn if state is not None else None)
    if damage > 0:
        state.log.append(f"{pet.side} {pet.name} 受到寄生 {damage} 伤害")
        from . import traits  # 延迟导入，避免与 traits.impl 循环依赖

        traits.emit(
            state,
            "dot_damage",
            scope="all",
            side=pet.side,
            subject=pet,
            damage=damage,
            dot_type="leech",
        )
    source = _find_source_pet(
        state,
        pet.buffs[0].source_side if pet.buffs else "",
        pet.buffs[0].source_pet if pet.buffs else "",
    )
    if source is not None and source.hp > 0:
        source.hp = min(source.max_hp, source.hp + damage)


def check_freeze(pet) -> None:
    """实时冻结斩杀判定：生命变动或获得冻结后立即检查，不属于伤害结算。"""
    if pet.hp <= 0:
        return
    layers = get_buff_value(pet, BuffType.FREEZE)
    if layers <= 0 or _immune(pet, 6):
        return
    # 免疫冻结（特性给予）：不受冻结效果
    if is_immune_to(pet, BuffType.FREEZE):
        return
    threshold = int(pet.max_hp * 0.05 * layers)
    if threshold >= pet.hp:
        # 避免 hp 写入入口递归触发；冻结不是伤害，不走免死/减伤。
        object.__setattr__(pet, "hp", 0)


def _apply_dizzy(pet) -> None:
    layers = get_buff_value(pet, BuffType.DIZZY)
    if layers <= 0:
        return
    remove_buff(pet, BuffType.DIZZY)
    if layers > 1:
        pet.buffs.append(
            Buff(
                buff_type=BuffType.DIZZY, value=layers - 1, duration=DurationKind.NORMAL
            )
        )


def _apply_lock(pet) -> None:
    if not has_buff(pet, BuffType.LOCK):
        return
    for buff in pet.buffs:
        if buff.buff_type == BuffType.LOCK:
            buff.value -= 1
            if buff.value <= 0:
                pet.buffs = [b for b in pet.buffs if b is not buff]
            return


def get_speed_value(pet, mark_speed_bonus: int = 0) -> int:
    base = (
        pet.stats["speed"] + get_buff_value(pet, BuffType.SPEED) * 10 + mark_speed_bonus
    )
    percent = get_buff_value(pet, BuffType.SPEED_PERCENT)
    return int(base * (1.0 + percent * 0.1))


def get_stat_multiplier(pet, stat: str) -> float:
    value = get_buff_value(pet, stat)
    return 1.0 + value * 0.1


def get_skill_power_modifier(pet) -> tuple:
    percent = get_buff_value(pet, BuffType.SKILL_POWER_PERCENT)
    flat = get_buff_value(pet, BuffType.SKILL_POWER_FLAT)
    return percent, flat


def consume_next_attack_power(pet) -> tuple:
    """ "下一次攻击技能威力"一次性增益的取用（攻击技能用时调用）。

    返回增量 ``(百分比, 固定值)``（P11 增量语义：百分比 100 = 翻倍；固定值
    1 层 = 1 点威力，同 SKILL_POWER_FLAT）；百分比/固定值两条通道一并清空，
    无增益时返回 ``(0, 0)`` 且不动任何状态。常规时长（NORMAL）——离场清除、
    可被驱散增益剥掉，故换人后不再保留（与其它常规时长的增益一致）。
    """
    percent = get_buff_value(pet, BuffType.NEXT_ATTACK_POWER_PERCENT)
    flat = get_buff_value(pet, BuffType.NEXT_ATTACK_POWER_FLAT)
    if percent:
        remove_buff(pet, BuffType.NEXT_ATTACK_POWER_PERCENT)
    if flat:
        remove_buff(pet, BuffType.NEXT_ATTACK_POWER_FLAT)
    return percent, flat


def get_hit_count_bonus(pet) -> tuple:
    flat = get_buff_value(pet, BuffType.HIT_COUNT_FLAT)
    percent = get_buff_value(pet, BuffType.HIT_COUNT_PERCENT)
    return flat, percent


def get_energy_cost_modifier(pet) -> int:
    return get_buff_value(pet, BuffType.ENERGY_COST)


def get_priority_bonus(pet) -> int:
    return get_buff_value(pet, BuffType.PRIORITY)


def get_lifesteal(pet) -> float:
    return get_buff_value(pet, BuffType.LIFESTEAL) * 0.1
