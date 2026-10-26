"""Resonance magic system."""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import buffs, counter, evolution, traits
from .enums import LORD_BLOODLINE
from .data_loader import load_typechart, round_half_up
from .models import BattleSkill
from .damage import level_coefficient
from .typechart import type_multiplier

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESONANCE_PATH = ROOT / "data" / "resonance.json"

WISH = 0
EVOLVE = 1
HEAL = 2


def load_magics():
    with open(RESONANCE_PATH, encoding="utf-8") as f:
        return json.load(f)["magics"]


def magic_limit(magic_id: int) -> int:
    for magic in load_magics():
        if magic["id"] == magic_id:
            if magic_id == WISH:
                return 2
            return 1
    return 1


def can_use(state, side: str, magic_id: int) -> bool:
    used = state.resonance_usage[side].get(magic_id, 0)
    if used >= magic_limit(magic_id):
        return False
    if state.resonance_cooldown[side].get(magic_id, 0) > 0:
        return False
    return True


def _heal(state, side: str) -> None:
    pet = state.teams[side][state.active[side]]
    heal = int(pet.max_hp * 0.15)
    pet.hp = min(pet.max_hp, pet.hp + heal)
    pet.light_heal_rounds = 3
    state.log.append(f"{side} {pet.name} 使用光合治愈")


def _evolve(state, side: str, branch: int = 0) -> bool:
    pet = state.teams[side][state.active[side]]
    if evolution.lordize(pet, branch):
        state.log.append(f"{side} {pet.name} 使用进化之力")
        traits.emit(state, "evolution", scope="self", side=side, subject=pet, pet=pet)
        # 首领化视作一次入场：重置入场状态并广播入场事件——新形态的特性重新绑定后，
        # 其"入场时"效果（如雪影冰灵 280012 抓到你了给敌方冻结）在双方行动前立即触发。
        # 不是换人：不做印记/迸发结算，不触发迅捷。
        pet.has_acted_since_entry = False
        pet.entry_turn = state.turn
        traits.emit(state, "entry", scope="all", side=side, subject=pet)
        return True
    state.log.append(f"{side} {pet.name} 无法首领化")
    return False


def use_magic(
    state, side: str, magic_id: int, opponent_is_status: bool = False, branch: int = 0
) -> bool:
    if not can_use(state, side, magic_id):
        return False
    if magic_id == HEAL:
        _heal(state, side)
    elif magic_id == WISH:
        pet = state.teams[side][state.active[side]]
        category = 0 if pet.stats["atk"] >= pet.stats["spatk"] else 1
        # 愿力冲击属性 = 精灵血脉；首领血脉（18）不是元素，退回主属性
        blood = pet.bloodline if pet.bloodline != LORD_BLOODLINE else None
        element = (
            blood if blood is not None else (pet.attributes[0] if pet.attributes else 0)
        )
        wish_skill = BattleSkill(
            skill_id=-1,
            name="愿力冲击",
            element=element,
            category=category,
            power=200 if opponent_is_status else 80,
            energy_cost=2,
            desc="应对状态：本次威力+150%",
        )
        wish_skill.counter_target = "status"
        pet.wish_original_skill = pet.skills[0]
        pet.skills[0] = wish_skill
        state.log.append(f"{side} {pet.name} 使用愿力冲击")
    elif magic_id == EVOLVE:
        if not _evolve(state, side, branch):
            return False
    state.resonance_usage[side][magic_id] = (
        state.resonance_usage[side].get(magic_id, 0) + 1
    )
    if magic_id == WISH:
        state.resonance_cooldown[side][magic_id] = 3
    return True


def on_round_end(state) -> None:
    for side in ("A", "B"):
        for pet in state.teams[side]:
            if pet.light_heal_rounds > 0 and pet.hp > 0:
                heal = int(pet.max_hp * 0.15)
                pet.hp = min(pet.max_hp, pet.hp + heal)
                pet.light_heal_rounds -= 1
            if pet.wish_original_skill is not None:
                pet.skills[0] = pet.wish_original_skill
                pet.wish_original_skill = None
        for magic_id in list(state.resonance_cooldown[side].keys()):
            state.resonance_cooldown[side][magic_id] -= 1
            if state.resonance_cooldown[side][magic_id] <= 0:
                del state.resonance_cooldown[side][magic_id]
