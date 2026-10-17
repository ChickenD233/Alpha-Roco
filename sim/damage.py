"""Damage calculation for the MVP battle engine."""

from __future__ import annotations

import math

from . import buffs, traits
from .data_loader import round_half_up
from .models import BattlePet, BattleSkill
from .typechart import type_multiplier

LEVEL_COEFF_BASE = 60


def level_coefficient(level: int) -> float:
    return (level * 45 / 100 + 10) / 41


def stab_multiplier(
    attacker: BattlePet, skill: BattleSkill, element: int | None = None
) -> float:
    # 本系加成：技能属性与精灵任一属性相同则 1.25
    eff_element = element if element is not None else skill.element
    return 1.25 if eff_element in getattr(attacker, "attributes", []) else 1.0


def ability_stage(state, pet: BattlePet, stat: str) -> float:
    """Buff and trait ability increments share the same clamped stage pool."""
    stage = buffs.get_buff_value(pet, stat)
    if state is not None:
        stage += traits.query_stat_multiplier(state, pet, stat) * 10
    return max(-99, min(99, stage))


def ability_multiplier(stage: float) -> float:
    stage = max(-99, min(99, stage))
    percent = stage * 10
    return (1 + max(percent, 0) / 100) / (1 + max(-percent, 0) / 100)


def display_ability_multiplier(attack_stage: float, defense_stage: float) -> float:
    attack_percent = attack_stage * 10
    defense_percent = defense_stage * 10
    return (1 + max(attack_percent, 0) / 100 + max(-defense_percent, 0) / 100) / (
        1 + max(-attack_percent, 0) / 100 + max(defense_percent, 0) / 100
    )


def calc_damage(
    attacker: BattlePet,
    defender: BattlePet,
    skill: BattleSkill,
    typechart: dict,
    damage_reduction: float = 0.0,
    extra_power_percent: float = 0.0,
    extra_power_flat: float = 0.0,
    element: int | None = None,
    state=None,
    is_first: bool = False,
    power_override: int | None = None,
) -> dict:
    # power_override：威力由规则算出的技能（S17 绝对覆盖，如闪击/鸣沙按差值查表），
    # 非空时替代 skills.json 的桩值作为基础威力，增量（威力buff/技能修正）照常叠加。
    if skill.category not in (0, 1) or (skill.power is None and power_override is None):
        return {"damage": 0, "display_power": 0, "type_multiplier": 1.0, "stab": 1.0}

    attack_key = "atk" if skill.category == 0 else "spatk"
    defense_key = "def" if skill.category == 0 else "spdef"
    attack_stage = ability_stage(state, attacker, attack_key)
    defense_stage = ability_stage(state, defender, defense_key)
    attack_stat = round_half_up(
        attacker.stats[attack_key] * ability_multiplier(attack_stage)
    )
    defense_stat = defender.stats[defense_key] * ability_multiplier(defense_stage)
    if defense_stage >= 0:
        defense_stat = round_half_up(defense_stat)
    if defense_stat <= 0:
        raise ValueError("Effective defense must be greater than zero")
    dealt_mod = 1.0
    taken_mod = 1.0
    if state is not None:
        dealt_mod = 1.0 + traits.query_damage_dealt(
            state, attacker, defender, skill=skill
        )
        taken_mod = 1.0 + traits.query_damage_taken(
            state, defender, attacker, skill=skill, is_first=is_first
        )
    eff_element = element if element is not None else skill.element
    defender_elements = getattr(defender, "attributes", [])
    stab = stab_multiplier(attacker, skill, element)
    type_mult = type_multiplier(eff_element, defender_elements, typechart)
    power_percent, power_flat = buffs.get_skill_power_modifier(attacker)
    base_power = power_override if power_override is not None else skill.power
    modified_power = (base_power + power_flat + extra_power_flat) * (
        1.0 + (power_percent + extra_power_percent) / 100.0
    )
    actual_power = math.floor(modified_power)
    calculation_power = actual_power * stab * type_mult
    display_power = math.floor(
        calculation_power * display_ability_multiplier(attack_stage, defense_stage)
    )
    coeff = level_coefficient(attacker.level)
    numerator = round_half_up(attack_stat * calculation_power * coeff)
    remaining = 1.0 - max(0.0, min(1.0, damage_reduction))
    one_hit = math.floor(numerator / defense_stat * remaining)
    damage = max(0, math.floor(one_hit * max(0.0, dealt_mod * taken_mod)))
    return {
        "damage": damage,
        "display_power": display_power,
        "type_multiplier": type_mult,
        "stab": stab,
    }


def calc_mark_damage(
    attacker: BattlePet,
    defender: BattlePet,
    category: int,
    base_power: int,
    element: int,
    typechart: dict,
    damage_reduction: float = 0.0,
    extra_power_percent: float = 0.0,
    extra_power_flat: float = 0.0,
    state=None,
) -> int:
    """印记爆炸伤害（星陨）：与 calc_damage 共用能力等级换算与取整顺序。

    差异：威力由印记层数公式给出、不乘本系加成、不产生显示威力、
    不查询造成伤害与受到伤害修正。
    """
    attack_key = "atk" if category == 0 else "spatk"
    defense_key = "def" if category == 0 else "spdef"
    attack_stage = ability_stage(state, attacker, attack_key)
    defense_stage = ability_stage(state, defender, defense_key)
    attack_stat = round_half_up(
        attacker.stats[attack_key] * ability_multiplier(attack_stage)
    )
    defense_stat = defender.stats[defense_key] * ability_multiplier(defense_stage)
    if defense_stage >= 0:
        defense_stat = round_half_up(defense_stat)
    if defense_stat <= 0:
        raise ValueError("Effective defense must be greater than zero")
    power_percent, power_flat = buffs.get_skill_power_modifier(attacker)
    actual_power = math.floor(
        (base_power + power_flat + extra_power_flat)
        * (1.0 + (power_percent + extra_power_percent) / 100.0)
    )
    calculation_power = actual_power * type_multiplier(
        element, getattr(defender, "attributes", []), typechart
    )
    numerator = round_half_up(
        attack_stat * calculation_power * level_coefficient(attacker.level)
    )
    remaining = 1.0 - max(0.0, min(1.0, damage_reduction))
    return max(0, math.floor(numerator / defense_stat * remaining))
