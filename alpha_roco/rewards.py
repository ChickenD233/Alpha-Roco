from __future__ import annotations

from alpha_roco.losses import terminal_rewards

TEAM_SIZE = 6


def _hp_ratio(pet: dict, own: bool) -> float:
    if not own and "hp_percent" in pet:
        return pet["hp_percent"] / 100.0
    max_hp = pet.get("max_hp")
    return pet.get("hp", max_hp) / max_hp if max_hp else 1.0


def _team_hp(pets: list[dict], own: bool) -> float:
    if not pets:
        return 0.0
    return sum(_hp_ratio(pet, own) for pet in pets) / min(len(pets), TEAM_SIZE)


def _team_alive(pets: list[dict]) -> int:
    return sum(pet.get("hp", 0) > 0 or pet.get("hp_percent", 0) > 0 for pet in pets)


def state_summary(state: dict, side: str) -> dict:
    other = "B" if side == "A" else "A"
    teams = state.get("teams", {})
    own = teams.get(side, [])
    enemy = teams.get(other, [])
    return {
        "own_hp": _team_hp(own, True),
        "enemy_hp": _team_hp(enemy, False),
        "own_alive": _team_alive(own),
        "enemy_alive": _team_alive(enemy),
    }


def potential(summary: dict) -> float:
    return (
        0.5 * (summary["enemy_hp"] - summary["own_hp"])
        + 0.1 * (summary["enemy_alive"] - summary["own_alive"]) / TEAM_SIZE
    )


def assign_rewards(episode: list[dict], winner: str, gamma: float = 1.0) -> None:
    reward = terminal_rewards(winner)[0]
    episode[0]["reward"] = reward
    episode[0]["terminal"] = True
    battle_entries = [entry for entry in episode if entry["phase"] != "lead"]
    for index, entry in enumerate(battle_entries):
        following = (
            gamma * battle_entries[index + 1]["potential"]
            if index + 1 < len(battle_entries)
            else 0.0
        )
        entry["reward"] = following - entry["potential"]
        entry["terminal"] = index + 1 == len(battle_entries)
    if battle_entries:
        battle_entries[-1]["reward"] += reward


def effective_bonus(type_mult: float) -> float:
    return min(0.01 * (type_mult - 1.0), 0.03) if type_mult > 1.0 else 0.0


def counter_bonus(matched: bool) -> float:
    return 0.02 if matched else 0.0


def apply_event_bonus(episode: list[dict], cap: float = 0.1) -> None:
    remaining = cap
    for entry in episode:
        applied = min(max(entry.get("event_bonus", 0.0), 0.0), remaining)
        entry["reward"] = entry.get("reward", 0.0) + applied
        remaining -= applied
