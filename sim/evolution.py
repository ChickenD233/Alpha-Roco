"""Evolution-chain helpers, including cute degeneration."""

from __future__ import annotations

from .data_loader import (
    calc_all_stats,
    calc_speed_range,
    get_attributes,
    load_spirits,
    round_half_up,
)
from .enums import LORD_BLOODLINE
from .traits import rebind


def get_spirit_by_id(spirit_id: int):
    for spirit in load_spirits():
        if spirit["id"] == spirit_id:
            return spirit
    return None


def get_previous_stage(spirit):
    if not spirit.get("evolution"):
        return None
    for chain in spirit["evolution"]:
        stages = chain.get("chain", [])
        for i, stage in enumerate(stages):
            if stage["id"] == spirit["id"] and i > 0:
                return get_spirit_by_id(stages[i - 1]["id"])
    return None


def get_next_stage(spirit):
    if not spirit.get("evolution"):
        return None
    for chain in spirit["evolution"]:
        stages = chain.get("chain", [])
        for i, stage in enumerate(stages):
            if stage["id"] == spirit["id"] and i + 1 < len(stages):
                return get_spirit_by_id(stages[i + 1]["id"])
    return None


def has_lord_form(spirit) -> bool:
    if not spirit.get("evolution"):
        return False
    for chain in spirit["evolution"]:
        if chain.get("lordBranches"):
            return True
    return False


def get_lord_form(spirit):
    if not spirit.get("evolution"):
        return None
    for chain in spirit["evolution"]:
        branches = chain.get("lordBranches") or []
        if branches:
            return get_spirit_by_id(branches[0]["id"])
    return None


def evolve(pet) -> bool:
    spirit = get_spirit_by_id(pet.spirit_id)
    if spirit is None:
        return False
    # 萌化恢复优先沿实际退化记录返回，不能重新选默认进化分支。
    restoring_cute = bool(pet.cute_history)
    nxt = (
        get_spirit_by_id(pet.cute_history[-1])
        if restoring_cute
        else get_next_stage(spirit)
    )
    if nxt is None:
        return False
    if restoring_cute:
        pet.cute_history.pop()
    pet.spirit_id = nxt["id"]
    pet.name = nxt["name"]
    pet.attributes = get_attributes(nxt)
    new_stats = calc_all_stats(nxt["stats"], ivs=pet.ivs, nature=pet.nature)
    pet.speed_range = calc_speed_range(nxt["stats"]["speed"])
    old_max_hp = pet.max_hp
    old_hp = pet.hp
    pet.stats = new_stats
    pet.max_hp = new_stats["hp"]
    pet.hp = min(pet.max_hp, round_half_up(old_hp * pet.max_hp / old_max_hp))
    rebind(pet)
    return True


def get_lord_forms(spirit) -> list:
    if not spirit.get("evolution"):
        return []
    forms = []
    for chain in spirit["evolution"]:
        for branch in chain.get("lordBranches") or []:
            form = get_spirit_by_id(branch["id"])
            if form is not None:
                forms.append(form)
    return forms


def can_lordize(pet) -> bool:
    if pet.bloodline != LORD_BLOODLINE:
        return False
    spirit = get_spirit_by_id(pet.spirit_id)
    if spirit is None:
        return False
    if not has_lord_form(spirit):
        return False
    return get_lord_form(spirit) is not None


def lordize(pet, branch: int = 0) -> bool:
    if not can_lordize(pet):
        return False
    # 萌化后再次首领化：只恢复上一阶段普通形态，不进入首领形态
    cute_buffs = [b for b in pet.buffs if b.buff_type == "cute"]
    if cute_buffs:
        if not evolve(pet):
            return False
        buff = cute_buffs[0]
        buff.value -= 1
        if buff.value <= 0:
            pet.buffs.remove(buff)
        return True
    spirit = get_spirit_by_id(pet.spirit_id)
    lords = get_lord_forms(spirit)
    if not lords:
        return False
    lord = lords[branch] if 0 <= branch < len(lords) else lords[0]
    pet.spirit_id = lord["id"]
    pet.name = lord["name"]
    pet.attributes = get_attributes(lord)
    new_stats = calc_all_stats(lord["stats"], ivs=pet.ivs, nature=pet.nature)
    pet.speed_range = calc_speed_range(lord["stats"]["speed"])
    old_max_hp = pet.max_hp
    old_hp = pet.hp
    pet.stats = new_stats
    pet.max_hp = new_stats["hp"]
    pet.hp = min(pet.max_hp, round_half_up(old_hp * pet.max_hp / old_max_hp))
    rebind(pet)
    return True


def can_cute(pet) -> bool:
    spirit = get_spirit_by_id(pet.spirit_id)
    if spirit is None:
        return False
    return get_previous_stage(spirit) is not None


def apply_cute(pet) -> bool:
    if not can_cute(pet):
        return False
    spirit = get_spirit_by_id(pet.spirit_id)
    prev = get_previous_stage(spirit)
    if prev is None:
        return False

    # 使用精灵自身字段保存，特性 rebind 清理 trait_state 不会丢失退化链。
    pet.cute_history.append(pet.spirit_id)
    pet.spirit_id = prev["id"]
    pet.name = prev["name"]
    pet.attributes = get_attributes(prev)
    new_stats = calc_all_stats(prev["stats"], ivs=pet.ivs, nature=pet.nature)
    pet.speed_range = calc_speed_range(prev["stats"]["speed"])
    old_max_hp = pet.max_hp
    old_hp = pet.hp
    pet.stats = new_stats
    pet.max_hp = new_stats["hp"]
    pet.hp = min(pet.max_hp, round_half_up(old_hp * pet.max_hp / old_max_hp))
    rebind(pet)
    return True
