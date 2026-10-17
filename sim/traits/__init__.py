"""特性系统（接口层）。

引擎用法
--------
- 事件：``traits.emit(state, "事件名", scope=..., side=..., subject=..., ...)``
  scope 决定特性所有者集合：all（双方全部，默认）/ side（单方全部）/ active（双方在场）/ self（单个）。
  事件默认广播（scope="all"），handler 用 ctx.side / ctx.subject / ctx.active 判断与自己相关与否。
- 致命伤害判定：``traits.emit_lethal(state, side, defender, damage, ...)``，返回 True 表示特性免死。
- 修正查询：``traits.query_*``（见下），在伤害/能耗/速度等计算处汇总增量。
- 形态变化后：``traits.rebind(pet)`` 重新绑定特性。

实现某个特性：新建 TraitHandler 子类覆写钩子，``register(handler)`` 覆盖 no-op 占位。
"""

from __future__ import annotations

from typing import Optional

from ..models import Action, BattlePet, BattleSkill, BattleState
from .base import TraitContext, TraitHandler
from .registry import get_handler, info, register, report  # noqa: F401

__all__ = [
    "TraitContext",
    "TraitHandler",
    "emit",
    "emit_lethal",
    "rebind",
    "ensure_entry",
    "on_battle_start",
    "on_turn_start",
    "on_round_end",
    "on_buff_gain",
    "query_stat_multiplier",
    "query_power",
    "query_energy_cost",
    "query_speed",
    "query_priority",
    "query_star_consume",
    "query_damage_dealt",
    "query_damage_taken",
    "query_hit_count",
    "query_lifesteal",
    "query_energy_limit",
    "query_heal",
    "query_energy_gain",
    "query_energy_shortfall",
    "query_burn_growth",
    "query_skill_usable",
    "query_skill_element",
    "grant_energy",
    "info",
    "register",
    "report",
]


def _weather_id(state: BattleState) -> Optional[int]:
    if state.weather is None:
        return None
    return state.weather["id"] if isinstance(state.weather, dict) else state.weather


_CTX_FIELDS = set(TraitContext.__dataclass_fields__.keys()) - {
    "state",
    "actor",
    "event",
    "extra",
}


def _make_ctx(state: BattleState, actor: BattlePet, event: str, **kw) -> TraitContext:
    """构建上下文：已知字段进 ctx 对应字段，未知字段（incoming/type_mult/base_heal 等）进 extra。"""
    ctx_kw = {}
    extra = {}
    for k, v in kw.items():
        if k == "extra" and isinstance(v, dict):
            extra.update(v)
        elif k in _CTX_FIELDS:
            ctx_kw[k] = v
        else:
            extra[k] = v
    ctx_kw.setdefault("weather", _weather_id(state))
    return TraitContext(state=state, actor=actor, event=event, extra=extra, **ctx_kw)


def _scope_pets(
    state: BattleState, scope: str, side: str = "", pet: BattlePet | None = None
) -> list:
    order = ["A", "B"]
    if scope == "self":
        return [pet] if pet is not None else []
    if scope == "side":
        return list(state.teams.get(side, []))
    if scope == "active":
        pets = []
        for s in order:
            idx = state.active[s]
            if idx >= 0:
                pets.append(state.teams[s][idx])
        return pets
    return [p for s in order for p in state.teams[s]]


def emit(
    state: BattleState,
    event: str,
    scope: str = "all",
    side: str = "",
    subject: BattlePet | None = None,
    pet: BattlePet | None = None,
    **kw,
) -> None:
    """广播事件。scope: all / side / active / self。"""
    for p in _scope_pets(state, scope, side, pet):
        handler = get_handler(p.trait_id)
        if handler is None:
            continue
        fn = getattr(handler, "on_" + event, None)
        if fn is None:
            continue
        active_idx = state.active[p.side]
        ctx = _make_ctx(
            state,
            p,
            event,
            active=(active_idx >= 0 and state.teams[p.side][active_idx] is p),
            side=side,
            subject=subject,
            **kw,
        )
        fn(ctx)


def emit_lethal(
    state: BattleState, side: str, defender: BattlePet, damage: int, **kw
) -> bool:
    """致命伤害判定：只询问受击精灵自己的特性，返回 True 表示本次伤害被免除（handler 已改血量）。"""
    handler = get_handler(defender.trait_id)
    if handler is None:
        return False
    fn = getattr(handler, "on_lethal", None)
    if fn is None:
        return False
    ctx = _make_ctx(
        state,
        defender,
        "lethal",
        active=True,
        side=side,
        subject=defender,
        damage=damage,
        **kw,
    )
    return bool(fn(ctx))


def rebind(pet: BattlePet) -> None:
    """形态变化（进化/首领化/萌化）后重新绑定特性并重置特性运行时状态。"""
    from ..data_loader import load_spirits

    tid = None
    for sp in load_spirits():
        if sp["id"] == pet.spirit_id:
            tid = (sp.get("feature") or {}).get("id")
            break
    pet.trait_id = tid if tid is not None else None
    pet.trait_state = {}


def ensure_entry(state: BattleState) -> None:
    """战斗首回合为初始在场精灵补发入场事件（首发也视为入场）。"""
    if state.entry_done:
        return
    state.entry_done = True
    for side in ("A", "B"):
        idx = state.active[side]
        if idx >= 0:
            emit(state, "entry", scope="all", side=side, subject=state.teams[side][idx])


def on_battle_start(state: BattleState) -> None:
    """战斗创建完成后的初始化事件。"""
    emit(state, "battle_start", scope="all")


def on_turn_start(
    state: BattleState, action_a: Action | None = None, action_b: Action | None = None
) -> None:
    emit(state, "turn_start", scope="all", action_a=action_a, action_b=action_b)


def describe(state: BattleState, pet: BattlePet) -> list:
    """当前精灵特性的显示行（供序列化，客户端 buff 区展示）。"""
    handler = get_handler(pet.trait_id)
    if handler is None:
        return []
    result = handler.display(state, pet)
    return list(result) if result else []


def on_round_end(state: BattleState, order: list | None = None) -> None:
    """按速度顺序结算双方特性；单个特性触发的返场在 handler 内完整执行。"""
    if order is None:
        from ..battle import _effective_speed
        import random
        a_speed = _effective_speed(state, "A")
        b_speed = _effective_speed(state, "B")
        order = (["A", "B"] if a_speed > b_speed else ["B", "A"]
                 if b_speed > a_speed else random.sample(["A", "B"], 2))
    # 保留全场额外触发/禁止触发的汇总语义，不将光环缩小成仅影响自身。
    repeats = 1
    for side in order:
        idx = state.active[side]
        if idx < 0:
            continue
        owner = state.teams[side][idx]
        handler = get_handler(owner.trait_id)
        if handler is not None:
            ctx = _make_ctx(state, owner, "round_end_repeats", active=True)
            repeats += handler.modify_round_end_repeats(ctx)
    for side in order:
        for _ in range(max(0, repeats)):
            emit(state, "round_end", scope="side", side=side)


def on_buff_gain(
    state: BattleState,
    pet: BattlePet,
    buff_type: str,
    value: int,
    source_side: str = "",
    source_pet: str = "",
    source_kind: str = "",
    **kw,
) -> None:
    """精灵获得 buff（含特性施加）：广播 buff_gain，供增益/冻结响应类特性使用。

    引擎在 buffs.add_buff 调用处（battle.py / weather.py）调用；
    ctx.extra 携带 buff_type/value/source_side/source_pet/source_kind。
    """
    emit(
        state,
        "buff_gain",
        scope="all",
        side=pet.side,
        subject=pet,
        buff_type=buff_type,
        value=value,
        source_side=source_side,
        source_pet=source_pet,
        source_kind=source_kind,
        **kw,
    )


# ==================== 修正查询 ====================


def _modifier_pets(
    state: BattleState, pet: BattlePet, include_enemy: bool = False
) -> list:
    """修正查询涉及的特性所有者。

    默认只查被查询精灵自身（绝大多数特性是自身效果）。
    include_enemy=True 时额外查敌方在场精灵——仅用于存在"敌方光环"类的查询：
    能耗（冰封：在场时敌方全技能能耗+1）、连击（无差别过滤/强制过滤：全场固定）。
    """
    pets = [pet]
    if include_enemy:
        opp_side = "B" if pet.side == "A" else "A"
        idx = state.active[opp_side]
        if idx >= 0:
            pets.append(state.teams[opp_side][idx])
    return pets


def _query(state: BattleState, pet: BattlePet, method: str, **kw):
    total = 0.0
    for owner in _modifier_pets(state, pet):
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        fn = getattr(handler, method, None)
        if fn is None:
            continue
        kw2 = dict(kw)
        kw2.setdefault("target", pet)
        ctx = _make_ctx(state, owner, method, **kw2)
        total += fn(ctx)
    return total


def query_stat_multiplier(state: BattleState, pet: BattlePet, stat: str) -> float:
    """属性百分比增量（0.2 = +20%），与 buffs.get_stat_multiplier 相加使用。"""
    total = 0.0
    for owner in _modifier_pets(state, pet):
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        ctx = _make_ctx(state, owner, "modify_stat", target=pet)
        total += handler.modify_stat(ctx, stat)
    return total


def query_power(
    state: BattleState,
    pet: BattlePet,
    opponent: BattlePet | None,
    base_percent: float = 0.0,
    base_flat: float = 0.0,
    is_first: bool = False,
    skill: BattleSkill | None = None,
) -> tuple:
    """技能威力修正，返回 (百分比增量, 固定值增量)，在 base 之上叠加。

    ctx 约定：ctx.target=被查询精灵（攻击方），ctx.subject=敌方。
    """
    percent = base_percent
    flat = base_flat
    for owner in _modifier_pets(state, pet):
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        ctx = _make_ctx(
            state,
            owner,
            "modify_power",
            target=pet,
            subject=opponent,
            is_first=is_first,
            skill=skill,
        )
        p, f = handler.modify_power(ctx)
        percent += p
        flat += f
    return percent, flat


def query_energy_cost(
    state: BattleState, pet: BattlePet, skill: BattleSkill | None, base: int = 0
) -> int:
    total = base
    for owner in _modifier_pets(state, pet, include_enemy=True):
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        ctx = _make_ctx(state, owner, "modify_energy_cost", target=pet, skill=skill)
        total += handler.modify_energy_cost(ctx)
    return total


def query_speed(state: BattleState, pet: BattlePet, base: int = 0) -> int:
    total = base
    for owner in _modifier_pets(state, pet):
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        ctx = _make_ctx(state, owner, "modify_speed", target=pet)
        total += handler.modify_speed(ctx)
    return total


def query_priority(state: BattleState, pet: BattlePet, skill: BattleSkill | None,
                   skill_index: int | None = None) -> int:
    """行动先手值增量（"迅捷技能先手+1"类）；ctx.target=被查询精灵。"""
    total = 0
    for owner in _modifier_pets(state, pet):
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        ctx = _make_ctx(state, owner, "modify_priority", target=pet,
                        skill=skill, skill_index=skill_index)
        total += handler.modify_priority(ctx)
    return total


def query_star_consume(state: BattleState, pet: BattlePet, stacks: int) -> int:
    """星陨印记触发时应消耗的层数（默认全部消耗）；ctx.target=触发方（攻击方）。"""
    for owner in _modifier_pets(state, pet):
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        ctx = _make_ctx(state, owner, "modify_star_consume", target=pet)
        r = handler.modify_star_consume(ctx, stacks)
        if r is not None:
            return r
    return stacks


def query_damage_dealt(
    state: BattleState, pet: BattlePet, target: BattlePet | None, **kw
) -> float:
    """造成伤害乘数增量；ctx.target=攻击方（被查询），ctx.subject=受击方。"""
    return _query(state, pet, "modify_damage_dealt", subject=target, **kw)


def query_damage_taken(
    state: BattleState, pet: BattlePet, attacker: BattlePet | None, **kw
) -> float:
    return _query(state, pet, "modify_damage_taken", subject=attacker, **kw)


def query_hit_count(
    state: BattleState, pet: BattlePet, opponent: BattlePet | None
) -> tuple:
    """连击修正，返回 (固定值增量, 百分比增量, 强制值|None)。

    多个"连击数固定为N"光环同时存在时取较大值（无差别过滤固定2 优先于
    强制过滤固定1），保证双方查询结果一致、与遍历顺序无关。
    ctx 约定：ctx.target=被查询精灵，ctx.subject=敌方。"""
    flat = 0
    percent = 0
    forced = None
    for owner in _modifier_pets(state, pet, include_enemy=True):
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        ctx = _make_ctx(state, owner, "modify_hit_count", target=pet, subject=opponent)
        f, p = handler.modify_hit_count(ctx)
        flat += f
        percent += p
        fv = handler.force_hit_count(ctx)
        if fv is not None:
            forced = fv if forced is None else max(forced, fv)
    return flat, percent, forced


def query_lifesteal(
    state: BattleState,
    pet: BattlePet,
    opponent: BattlePet | None,
    skill: BattleSkill | None = None,
) -> float:
    """吸血增量；ctx.target=被查询精灵，ctx.subject=敌方。"""
    return _query(state, pet, "modify_lifesteal", subject=opponent, skill=skill)


def query_energy_limit(state: BattleState, pet: BattlePet, base: int = 10) -> int:
    total = base
    for owner in _modifier_pets(state, pet):
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        ctx = _make_ctx(state, owner, "modify_energy_limit", target=pet)
        total += handler.modify_energy_limit(ctx)
    return total


def query_heal(state: BattleState, pet: BattlePet, amount: int, **kw) -> int:
    total = amount
    for owner in _modifier_pets(state, pet):
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        ctx = _make_ctx(state, owner, "modify_heal", target=pet, heal=amount, **kw)
        total += handler.modify_heal(ctx)
    return max(0, total)


def query_energy_gain(state: BattleState, pet: BattlePet, amount: int, **kw) -> int:
    total = amount
    for owner in _modifier_pets(state, pet):
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        ctx = _make_ctx(
            state, owner, "modify_energy_gain", target=pet, energy_gain=amount, **kw
        )
        total += handler.modify_energy_gain(ctx)
    return max(0, total)


def query_energy_shortfall(
    state: BattleState, pet: BattlePet, need: int, skill: BattleSkill | None,
    dry_run: bool = False, **kw,
) -> int:
    """能量不足时特性可补充的能量（handler 自行支付代价，如扣血）。
    dry_run=True 为选择校验探测：不支付代价，只回答能否补足。"""
    total = 0
    for owner in _modifier_pets(state, pet):
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        ctx = _make_ctx(
            state,
            owner,
            "modify_energy_shortfall",
            target=pet,
            skill=skill,
            extra={"need": need, **kw},
        )
        total += handler.modify_energy_shortfall(ctx, need, dry_run=dry_run)
    return max(0, total)


def grant_energy(state: BattleState, pet: BattlePet, amount: int, **kw) -> int:
    """给精灵回复能量（含能量上限修正），返回实际回复量并广播 energy_gain。
    供引擎（battle._add_energy）与特性 handler 共用，保证能量上限一致。"""
    limit = query_energy_limit(state, pet)
    before = pet.energy
    pet.energy = min(limit, pet.energy + amount)
    gained = pet.energy - before
    if gained > 0:
        emit(
            state,
            "energy_gain",
            scope="all",
            side=pet.side,
            subject=pet,
            energy_gain=gained,
            **kw,
        )
    return gained


def query_skill_usable(
    state: BattleState,
    pet: BattlePet,
    skill: BattleSkill,
    skill_index: int | None = None,
) -> bool:
    """技能是否可用：任一相关特性返回强制值即生效；默认 True。"""
    for owner in _modifier_pets(state, pet):
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        ctx = _make_ctx(
            state,
            owner,
            "is_skill_usable",
            target=pet,
            skill=skill,
            skill_index=skill_index,
        )
        r = handler.is_skill_usable(ctx, skill)
        if r is not None:
            return r
    return True


def query_windup_any(state: BattleState, pet: BattlePet) -> bool:
    """蓄力状态下是否允许使用任意携带技能（嫉妒 200174）；默认 False。"""
    for owner in _modifier_pets(state, pet):
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        ctx = _make_ctx(state, owner, "allow_any_skill_in_windup", target=pet)
        if handler.allow_any_skill_in_windup(ctx):
            return True
    return False


def query_burn_growth(state: BattleState, pet: BattlePet) -> bool:
    """查询当前在场特性是否将目标精灵的灼烧衰减改为增长。"""
    for side in ("A", "B"):
        idx = state.active[side]
        if idx < 0:
            continue
        owner = state.teams[side][idx]
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        fn = getattr(handler, "modify_burn_growth", None)
        if fn is None:
            continue
        ctx = _make_ctx(state, owner, "modify_burn_growth", target=pet)
        if fn(ctx):
            return True
    return False


def query_skill_element(state: BattleState, pet: BattlePet, skill: BattleSkill) -> int:
    """技能实际属性（特性改写后），默认 skill.element。"""
    for owner in _modifier_pets(state, pet):
        handler = get_handler(owner.trait_id)
        if handler is None:
            continue
        ctx = _make_ctx(state, owner, "modify_skill_element", target=pet, skill=skill)
        r = handler.modify_skill_element(ctx, skill)
        if r is not None:
            return r
    return skill.element
