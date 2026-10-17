"""Mark system: each side has one positive mark slot and one negative mark slot.

Marks persist when a Pokémon leaves the field; the next Pokémon inherits them.
Gaining a new mark replaces the previous mark on the same side.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import buffs
from .data_loader import load_typechart
from .typechart import type_multiplier

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MARKS_PATH = ROOT / "data" / "marks.json"

POSITIVE = "positive"
NEGATIVE = "negative"


def load_marks():
    with open(MARKS_PATH, encoding="utf-8") as f:
        return json.load(f)["marks"]


def mark_side(mark_id: int) -> str:
    for mark in load_marks():
        if mark["id"] == mark_id:
            return mark["side"]
    return POSITIVE


def add_mark(state, side: str, mark_id: int, amount: int = 1) -> None:
    if amount <= 0:
        return
    side_name = mark_side(mark_id)
    current = state.marks[side][side_name]
    if current is not None and current["id"] == mark_id:
        current["stacks"] = min(99, current["stacks"] + amount)
    else:
        state.marks[side][side_name] = {"id": mark_id, "stacks": min(99, amount)}


def remove_mark(state, side: str, mark_id: int) -> None:
    for side_name in (POSITIVE, NEGATIVE):
        current = state.marks[side][side_name]
        if current is not None and current["id"] == mark_id:
            state.marks[side][side_name] = None


def consume_mark_stacks(state, side: str, mark_id: int, amount: int) -> None:
    """消耗印记部分层数（守望星 200210"仅消耗一半"类）；耗尽则移除印记。"""
    if amount <= 0:
        return
    side_name = mark_side(mark_id)
    current = state.marks[side][side_name]
    if current is None or current["id"] != mark_id:
        return
    if amount >= current["stacks"]:
        state.marks[side][side_name] = None
        return
    current["stacks"] -= amount


def clear_side(state, side: str, side_name: str) -> None:
    state.marks[side][side_name] = None


def get_marks(state, side: str) -> dict:
    result = {}
    for side_name, value in state.marks[side].items():
        result[side_name] = dict(value) if value is not None else None
    return result


def get_mark(state, side: str, side_name: str):
    current = state.marks[side].get(side_name)
    if current is None:
        return None
    return dict(current)


def get_stacks(state, side: str, mark_id: int) -> int:
    side_name = mark_side(mark_id)
    current = state.marks[side][side_name]
    if current is not None and current["id"] == mark_id:
        return current["stacks"]
    return 0


# 印记 8：萌芽印记 —— 获得增益时额外获得一层
def amplify_buff_gain(state, side: str, buff_type: str, value: int) -> int:
    if value == 0 or not buffs.is_buff(buff_type, value):
        return value
    positive = state.marks[side]["positive"]
    if positive is not None and positive["id"] == 8:
        return value + positive["stacks"]
    return value


# 回合结束印记结算：中毒印记(4)、光合印记(10)
def on_round_end(state) -> None:
    typechart = load_typechart()
    order = list(getattr(state, "round_end_order", ["A", "B"]))
    for side in order:
        active_idx = state.active[side]
        if active_idx < 0:
            continue
        pet = state.teams[side][active_idx]
        if pet.hp <= 0:
            continue

        positive = state.marks[side]["positive"]
        if positive is not None and positive["id"] == 10:
            gain = positive["stacks"]
            pet.energy = min(10, pet.energy + gain)
            state.log.append(f"{side} {pet.name} 光合印记回复 {gain} 能量")

        negative = state.marks[side]["negative"]
        if negative is None:
            continue
        if negative["id"] == 4:
            mult = type_multiplier(9, pet.attributes, typechart)
            damage = int(pet.max_hp * 0.03 * negative["stacks"] * mult)
            pet.hp = max(0, pet.hp - damage)
            state.log.append(f"{side} {pet.name} 受到中毒印记 {damage} 伤害")
