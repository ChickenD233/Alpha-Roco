"""资源转移类特性（on_heal / on_energy_gain）。

- 200083 腐植循环  每回复1能量，同时回复5%生命。
- 200085 系统发育  获得能量或生命时，会将等量的能量或生命随机分配给场下的精灵。

语义：自己照常获得（回血/回能不变），同时随机选一只场下存活精灵获得等量一份。
满血时获得生命、满能量时获得能量不触发（自己没有实际获得，不分配）。
腐植循环：每回复 1 能量回复 max_hp*5% 生命（回复量=能量回复量×5%最大生命）。
"""

from __future__ import annotations

import random

from ... import traits as T
from ..base import TraitHandler
from ..registry import register


def _bench_pets(ctx):
    """自己场下的存活精灵（非当前出战、hp>0）。"""
    side = ctx.actor.side
    active_idx = ctx.state.active[side]
    return [
        p for i, p in enumerate(ctx.state.teams[side]) if i != active_idx and p.hp > 0
    ]


# ---------------- 200083 腐植循环 ----------------
class HumusCycle(TraitHandler):
    trait_id = 200083
    name = "腐植循环"
    desc = "每回复1能量，同时回复5%生命。"
    implemented = True

    def on_energy_gain(self, ctx):
        if ctx.subject is not ctx.actor or ctx.energy_gain <= 0:
            return
        heal = int(ctx.actor.max_hp * 0.05 * ctx.energy_gain)
        if heal <= 0:
            return
        ctx.actor.hp = min(ctx.actor.max_hp, ctx.actor.hp + heal)


# ---------------- 200085 系统发育 ----------------
class Phylogeny(TraitHandler):
    trait_id = 200085
    name = "系统发育"
    desc = "获得能量或生命时，会将等量的能量或生命随机分配给场下的精灵。"
    implemented = True

    def on_heal(self, ctx):
        if ctx.subject is not ctx.actor or ctx.heal <= 0:
            return
        if ctx.actor.hp >= ctx.actor.max_hp:
            # 满血时获得生命不触发（自己没有实际获得）
            return
        bench = _bench_pets(ctx)
        if not bench:
            return
        target = random.choice(bench)
        target.hp = min(target.max_hp, target.hp + ctx.heal)

    def on_energy_gain(self, ctx):
        if ctx.subject is not ctx.actor or ctx.energy_gain <= 0:
            return
        if ctx.actor.energy >= T.query_energy_limit(ctx.state, ctx.actor):
            # 满能量时获得能量不触发（自己没有实际获得）
            return
        bench = _bench_pets(ctx)
        if not bench:
            return
        target = random.choice(bench)
        limit = T.query_energy_limit(ctx.state, target)
        target.energy = min(limit, target.energy + ctx.energy_gain)

    def display(self, state, pet):
        n = pet.trait_state.get("triggers", 0)
        if n <= 0:
            return None
        return [{"name": "分配", "layers": n, "per": "1", "gain": True}]


# ---------------- 280031 草木苏醒时 ----------------
class FloraAwakening(TraitHandler):
    trait_id = 280031
    name = "草木苏醒时"
    desc = "每回复1能量，物攻和魔攻永久+20%，攻击后重置。"
    implemented = True

    def on_energy_gain(self, ctx):
        if ctx.subject is ctx.actor and ctx.energy_gain > 0:
            ctx.add_state("restored_energy", ctx.energy_gain)

    def modify_stat(self, ctx, stat):
        if ctx.target is not ctx.actor or stat not in ("atk", "spatk"):
            return 0.0
        return 0.2 * ctx.state_of("restored_energy", 0)

    def on_skill_start(self, ctx):
        if (ctx.subject is ctx.actor and ctx.skill is not None
                and ctx.skill.category in (0, 1)):
            ctx.set_state("attack_energy_snapshot", ctx.state_of("restored_energy", 0))

    def on_skill_end(self, ctx):
        # 整次攻击用完旧加成；命中后回能产生的新加成留给下一次攻击。
        if (ctx.subject is ctx.actor and ctx.skill is not None
                and ctx.skill.category in (0, 1)):
            consumed = ctx.state_of("attack_energy_snapshot", 0)
            ctx.set_state("restored_energy", max(0, ctx.state_of("restored_energy", 0) - consumed))
            ctx.set_state("attack_energy_snapshot", 0)

    def display(self, state, pet):
        n = pet.trait_state.get("restored_energy", 0)
        if n <= 0:
            return None
        return [{"name": "双攻", "layers": n, "per": "20%", "gain": True}]


def register_resource() -> None:
    register(HumusCycle())
    register(Phylogeny())
    register(FloraAwakening())


register_resource()
