"""Legal action enumeration for a single player's current view."""

from __future__ import annotations

import copy
import random

from . import buffs, evolution, resonance
from .battle import can_use_skill


def _replacement_candidates(state, side: str) -> list[dict]:
    current = state.active[side]
    excluded = current
    if state.paused_turn is not None and state.paused_turn.get("leave_side") == side:
        excluded = state.paused_turn.get("leaver_index", current)
    return [
        {"kind": "switch", "pet_index": index, "pet_name": pet.name}
        for index, pet in enumerate(state.teams[side])
        if index != excluded and pet.hp > 0
    ]


def _resonance_previews(state, side: str) -> list[tuple[dict, object]]:
    magic_id = state.resonance_magic[side]
    if magic_id is None or not resonance.can_use(state, side, magic_id):
        return []
    pet = state.teams[side][state.active[side]]
    branches = range(1)
    if magic_id == resonance.EVOLVE:
        forms = evolution.get_lord_forms(evolution.get_spirit_by_id(pet.spirit_id))
        branches = range(max(1, len(forms)))
    previews = []
    for branch in branches:
        preview = copy.deepcopy(state)
        if resonance.use_magic(preview, side, magic_id, branch=branch):
            previews.append(({"magic_id": magic_id, "magic_branch": branch}, preview))
    return previews


def _action_candidates(state, side: str) -> list[dict]:
    if state.active[side] < 0:
        return []
    pet = state.teams[side][state.active[side]]
    variants = [({}, state), *_resonance_previews(state, side)]
    candidates = []
    for magic, preview in variants:
        preview_pet = preview.teams[side][preview.active[side]]
        for index, skill in enumerate(preview_pet.skills):
            branches = (0, 1) if skill.choice else (0,)
            for choice_branch in branches:
                if not can_use_skill(
                    preview,
                    side,
                    skill,
                    skill_index=index,
                    choice_branch=choice_branch,
                ):
                    continue
                candidates.append(
                    {
                        "kind": "skill",
                        "skill_index": index,
                        "skill_id": skill.skill_id,
                        "skill_name": skill.name,
                        **magic,
                        "choice_branch": choice_branch,
                    }
                )
        candidates.append({"kind": "charge", **magic})
        if buffs.can_switch(preview_pet):
            candidates.extend(
                {
                    "kind": "switch",
                    "pet_index": index,
                    "pet_name": target.name,
                    **magic,
                }
                for index, target in enumerate(preview.teams[side])
                if index != preview.active[side] and target.hp > 0
            )
        if not magic:
            candidates.append({"kind": "flee"})
    return candidates


def is_valid_action(state, side: str, action: dict, phase: str = "action") -> bool:
    fields = ("kind", "lead", "pet_index", "skill_index", "magic_id", "magic_branch", "choice_branch")
    return any(
        all(
            candidate.get(key, 0 if key in ("magic_branch", "choice_branch") else None)
            == action.get(key, 0 if key in ("magic_branch", "choice_branch") else None)
            for key in fields
        )
        for candidate in enumerate_candidates(state, side, phase)
    )


def enumerate_candidates(state, side: str, phase: str = "action") -> list[dict]:
    """Return executable choices for one side without mutating the battle or RNG."""
    if side not in ("A", "B"):
        raise ValueError(f"unknown side: {side!r}")
    if phase == "replacement":
        return _replacement_candidates(state, side)
    if phase == "lead":
        return [
            {"kind": "lead", "lead": index, "pet_index": index}
            for index, pet in enumerate(state.teams[side])
        ]
    if phase != "action":
        raise ValueError(f"unknown action phase: {phase!r}")
    if state.active[side] < 0:
        return []
    random_state = random.getstate()
    candidates = _action_candidates(copy.deepcopy(state), side)
    random.setstate(random_state)
    return candidates
