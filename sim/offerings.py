"""奉献：队伍级资源（SPEC B45）。

一次奉献是下列五种效果之一。同类按次累加，不消耗，整场保留，只强化该队
全部「啃咬」「虫群」技能（含场下精灵），由这些技能自己取用 ``bonus``。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

POWER = "power"  # 威力+20
HIT = "hit"  # 连击数+1
COST = "cost"  # 能耗-2
POISON = "poison"  # 敌方获得2层中毒
LIFESTEAL = "lifesteal"  # 获得10%吸血

KINDS = (POWER, HIT, COST, POISON, LIFESTEAL)

VALUES = {POWER: 20, HIT: 1, COST: 2, POISON: 2, LIFESTEAL: 10}

LABELS = {
    POWER: "威力+20",
    HIT: "连击数+1",
    COST: "能耗-2",
    POISON: "敌方获得2层中毒",
    LIFESTEAL: "获得10%吸血",
}

# 受奉献影响的技能：啃咬 7130100、虫群 7130130
AFFECTED_SKILL_IDS = frozenset({7130100, 7130130})


@dataclass(frozen=True)
class Bonus:
    """奉献给啃咬/虫群带来的强化（不含技能自身的永久成长）。"""

    power: int = 0
    hit_count: int = 0
    energy_cost: int = 0
    poison: int = 0
    lifesteal: float = 0.0


def grant(state, side: str, kind: str, times: int = 1) -> None:
    if times <= 0:
        return
    counts = state.offerings[side]
    counts[kind] = counts.get(kind, 0) + times


def grant_random(state, side: str, times: int = 1) -> None:
    """随机奉献：每次在五种效果中均匀抽取。"""
    for _ in range(times):
        grant(state, side, random.choice(KINDS))


def count(state, side: str, kind: str) -> int:
    return state.offerings[side].get(kind, 0)


def total(state, side: str) -> int:
    return sum(state.offerings[side].values())


def affects(skill_id: int) -> bool:
    return skill_id in AFFECTED_SKILL_IDS


def bonus(state, side: str) -> Bonus:
    return Bonus(
        power=count(state, side, POWER) * VALUES[POWER],
        hit_count=count(state, side, HIT) * VALUES[HIT],
        energy_cost=-count(state, side, COST) * VALUES[COST],
        poison=count(state, side, POISON) * VALUES[POISON],
        lifesteal=count(state, side, LIFESTEAL) * VALUES[LIFESTEAL] / 100,
    )


def summary(state, side: str) -> list:
    """展示行：种类、次数与单次文本（供状态序列化）。"""
    return [
        {"kind": kind, "name": LABELS[kind], "times": count(state, side, kind)}
        for kind in KINDS
        if count(state, side, kind) > 0
    ]
