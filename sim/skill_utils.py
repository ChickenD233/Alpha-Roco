"""Skill inspection helpers."""

from __future__ import annotations

from . import buffs

ATTACK = "attack"
DEFENSE = "defense"
STATUS = "status"
NON_SKILL = "non_skill"


def category_of(skill) -> str:
    if skill is None:
        return NON_SKILL
    if skill.category in (0, 1):
        return ATTACK
    if skill.category == 2:
        return DEFENSE
    if skill.category == 3:
        return STATUS
    return NON_SKILL


def raw_energy_cost(skill) -> int:
    return skill.energy_cost if skill is not None else 0


def modified_energy_cost(pet, skill) -> int:
    """仅 buff 修正的能耗（不含印记/天气/特性修正，且中途钳制）。

    需要"真实能耗"时请用 true_energy_cost（含全部修正 + 最后钳制）。
    """
    if skill is None:
        return 0
    return max(0, skill.energy_cost + buffs.get_energy_cost_modifier(pet))


def actual_energy_cost(pet, skill, mark_energy_bonus: int = 0) -> int:
    """buff + 指定印记修正的能耗（不含天气/特性修正，且中途钳制）。

    需要"真实能耗"时请用 true_energy_cost（含全部修正 + 最后钳制）。
    """
    if skill is None:
        return 0
    return max(
        0, skill.energy_cost + buffs.get_energy_cost_modifier(pet) + mark_energy_bonus
    )


def energy_modifier_before_traits(state, pet, skill) -> int:
    """特性修正之前的能耗修正总量（buff + 印记(蓄势/湿润) + 天气）。

    对流 200113 用它把"全部能耗变化"翻转为反向（返回 -2×本值）。
    """
    if skill is None:
        return 0
    from . import marks, weather

    bonus = weather.sandstorm_energy_modifier(state.weather, skill.element)
    positive = marks.get_mark(state, pet.side, marks.POSITIVE)
    if positive is not None and positive["id"] == 2:
        bonus += positive["stacks"]
    if positive is not None and positive["id"] == 11:
        bonus -= positive["stacks"]
    # 攻击技能能耗（恶意逃离类）：只对攻击技能生效的带符号修正
    if category_of(skill) == ATTACK:
        bonus += buffs.get_buff_value(pet, buffs.BuffType.ATTACK_ENERGY_COST)
    # 按技能的能耗修正（"被应对技能能耗+N"类，如冰天雪地）
    bonus += buffs.get_skill_energy_boost(pet, skill.skill_id)
    return buffs.get_energy_cost_modifier(pet) + bonus


def true_energy_cost(state, pet, skill, skill_index: int | None = None,
                     choice_branch: int = 0) -> int:
    """技能的真实能耗：含天气、印记(蓄势/湿润)、buff、特性与技能自身修正。

    与 battle.current_skill_cost 同一套算法（该函数也复用本函数），
    供特性在需要"真实能耗"判定时调用（如快锤 200129 判定能耗<3）。
    所有修正叠加后**最后钳制一次**到 0：中途钳制会让"降低类"修正被截断，
    使对流（200113，翻转全部能耗变化为 -M）等翻转类特性算错
    （例：原始1 + 沙暴-2 → 中间钳制 0 → 对流 +4 = 4；正确应为 1-2+4 = 3）。
    """
    if skill is None:
        return 0
    from . import skills, traits

    if skill_index is None:
        skill_index = next(
            (i for i, candidate in enumerate(pet.skills) if candidate is skill),
            -1,
        )

    total = (
        skill.energy_cost
        + energy_modifier_before_traits(state, pet, skill)
        + traits.query_energy_cost(state, pet, skill)
        + skills.query_energy_cost(state, pet, skill, skill_index=skill_index,
                                   choice_branch=choice_branch)
    )
    return max(0, total)


def is_same_attribute(skill, pet) -> bool:
    if skill is None:
        return False
    return skill.element in pet.attributes


def attributes_equal(skill_a, skill_b) -> bool:
    if skill_a is None or skill_b is None:
        return False
    return skill_a.element == skill_b.element


def counter_target_of(skill) -> str:
    if skill is None:
        return ""
    return skill.counter_target


def has_counter_effect(skill) -> bool:
    return bool(counter_target_of(skill))


def is_skill_on_cooldown(pet, skill_id: int) -> bool:
    """判断技能是否处于当前回合冷却。"""
    return skill_id in pet.skill_cooldowns


def schedule_skill_cooldown(pet, skill_id: int) -> None:
    """安排任意技能在下一回合进入冷却。"""
    pet.skill_cooldowns_pending.add(skill_id)


def settle_skill_cooldowns(pet) -> None:
    """回合结束统一结算技能冷却：下一回合的冷却 = 本回合安排的冷却。"""
    pet.skill_cooldowns = set(pet.skill_cooldowns_pending)
    pet.skill_cooldowns_pending.clear()


def is_swift(skill) -> bool:
    """判断技能是否带有迅捷效果。

    迅捷是技能的结构化运行时属性，不从技能描述文本推断。
    后续技能效果代码化后，只需在构造/效果绑定阶段维护 skill.swift，
    入场机制无需感知具体技能名称或描述。
    """
    return bool(getattr(skill, "swift", False))
