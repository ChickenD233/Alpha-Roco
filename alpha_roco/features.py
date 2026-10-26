from __future__ import annotations

import hashlib
import json
import math
import re

import torch

from sim.data_loader import DATA_DIR, calc_all_stats, calc_speed_range, calc_stat
from sim.skills import impl as _skills_impl  # noqa: F401
from sim.typechart import type_multiplier

SPECIAL = ("<PAD>", "<UNK>", "<NONE>")
EVENT_FEATURES = 11


def _vocabulary(values):
    return {
        value: index + len(SPECIAL) for index, value in enumerate(sorted(set(values)))
    }


def _index(value, vocab):
    if isinstance(value, dict):
        value = value.get("id")
    if value is None:
        return 2
    return vocab.get(value, vocab.get(str(value), 1))


def _number(value, scale=1.0):
    if isinstance(value, dict):
        value = value.get("id")
    return (
        float(value) / scale
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else 0.0
    )


def _scalar(value):
    if isinstance(value, dict):
        value = value.get("id")
    return (
        value
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def _log_mult(mult):
    return min(max(math.log2(max(mult, 1e-6)), -2.0), 2.0) / 2


def _load(name):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


class ObservationEncoder:
    def __init__(self):
        self.spirits = _load("spirits.json")["spirits"]
        self.skills = _load("skills.json")["skills"]
        self.buffs = _load("buff.json")["effects"]
        self.spirit_by_name = {item["name"]: item for item in self.spirits}
        self.spirit_by_id = {str(item["id"]): item for item in self.spirits}
        self.skill_by_id = {str(item["id"]): item for item in self.skills}
        self.skill_by_name = {item["name"]: item for item in self.skills}
        from sim.skills.registry import all_handlers, static_of

        self.choice_skills = {
            handler.skill_id for handler in all_handlers() if handler.choice
        }
        self.static_of = static_of
        self.typechart = _load("typechart.json")
        self.spirit_vocab = _vocabulary(item["id"] for item in self.spirits)
        self.skill_vocab = _vocabulary(item["id"] for item in self.skills)
        self.effect_vocab = _vocabulary(item["id"] for item in self.buffs)
        self.element_vocab = _vocabulary(range(19))
        self.nature_vocab = _vocabulary(
            item["id"] for item in _load("natures.json")["natures"]
        )
        self.category_vocab = _vocabulary(range(4))
        self.weather_vocab = _vocabulary(
            item["id"] for item in _load("weather.json").get("weathers", [])
        )
        self.mark_vocab = _vocabulary(
            item["id"] for item in _load("marks.json").get("marks", [])
        )
        self.action_vocab = _vocabulary(
            ("lead", "skill", "charge", "switch", "flee", "unknown_skill")
        )
        self.phase_vocab = _vocabulary(("lead", "action", "replacement"))
        self.event_vocab = _vocabulary(
            (
                "unknown",
                "log",
                "private",
                "skill",
                "switch",
                "charge",
                "damage",
                "status",
                "entry",
                "round",
            )
        )
        self.side_vocab = _vocabulary(("own", "enemy"))
        self.vocabularies = {
            "spirit": self.spirit_vocab,
            "skill": self.skill_vocab,
            "effect": self.effect_vocab,
            "element": self.element_vocab,
            "nature": self.nature_vocab,
            "category": self.category_vocab,
            "weather": self.weather_vocab,
            "mark": self.mark_vocab,
            "action": self.action_vocab,
            "phase": self.phase_vocab,
            "event": self.event_vocab,
            "side": self.side_vocab,
        }
        self.vocab_sizes = {
            name: len(vocab) + len(SPECIAL) for name, vocab in self.vocabularies.items()
        }
        signature = json.dumps(
            {"schema_version": 2, "vocabularies": self.vocabularies},
            ensure_ascii=False,
            sort_keys=True,
        )
        self.vocab_signature = hashlib.sha256(signature.encode("utf-8")).hexdigest()

    def _skill(self, raw):
        fields = raw if isinstance(raw, dict) else {}
        if isinstance(raw, dict):
            item = self.skill_by_id.get(str(raw.get("skill_id", raw.get("id", ""))))
            if item is None:
                item = self.skill_by_name.get(raw.get("name", ""), {})
        elif isinstance(raw, int) or (isinstance(raw, str) and raw.isdigit()):
            item = self.skill_by_id.get(str(raw), {})
        else:
            item = self.skill_by_name.get(str(raw), {})
        skill_id = item.get("id", raw if isinstance(raw, (int, str)) else None)
        static = self.static_of(
            skill_id
            if isinstance(skill_id, int)
            else (
                int(skill_id)
                if isinstance(skill_id, str) and skill_id.isdigit()
                else None
            )
        )
        power = fields.get("display_power", fields.get("power", item.get("power")))
        energy = fields.get(
            "energy_cost", fields.get("energyCost", item.get("energyCost"))
        )
        category = fields.get("category", item.get("category"))
        element = (
            static["element"]
            if static["element"] is not None
            else fields.get("element", item.get("element"))
        )
        counter = static["counter_target"]
        return {
            "id": _index(skill_id, self.skill_vocab),
            "category": _index(category, self.category_vocab),
            "element": _index(element, self.element_vocab),
            "numeric": [
                _number(power, 200),
                _number(energy, 10),
                float(power is not None),
                float(energy is not None),
                float(bool(item)),
                float(counter == "attack"),
                float(counter == "defense"),
                float(counter == "status"),
                float(static["counter_interrupt"]),
                static["drive"] / 3,
                float(static["swift"]),
                float(static["windup"]),
                float(static["usable"]),
                float(static["choice"]),
            ],
            "mask": True,
            "power_raw": _scalar(power),
            "energy_raw": _scalar(energy),
            "category_raw": _scalar(category),
            "element_raw": _scalar(element),
            "static": static,
        }

    def _public_own(self, raw):
        name = raw.get("name", raw.get("spirit", ""))
        species = self.spirit_by_name.get(name, {})
        max_hp = raw.get("max_hp", 0)
        hp = raw.get("hp", max_hp)
        return {
            "name": name,
            "spirit_id": raw.get("spirit_id", species.get("id")),
            "hp_percent": raw.get(
                "hp_percent", round(hp / max_hp * 100) if max_hp else 100
            ),
            "energy": raw.get("energy"),
            "eff_speed_range": raw.get("eff_speed_range") or raw.get("speed_range"),
            "buffs": raw.get("buffs", []),
            "skills": [],
        }

    def _pet(self, raw, is_own, nature=None):
        name = raw.get("name", raw.get("spirit", ""))
        nature = nature if nature is not None else raw.get("nature")
        species = self.spirit_by_id.get(
            str(raw.get("spirit_id", ""))
        ) or self.spirit_by_name.get(name, {})
        stats = raw.get("stats", {}) if is_own else {}
        if is_own and not stats and species and "ivs" in raw:
            stats = calc_all_stats(species["stats"], raw["ivs"], raw.get("nature"))
        hp = raw.get("hp", raw.get("hp_percent", 100))
        max_hp = raw.get("max_hp", 100)
        hp_ratio = (
            _number(hp, max_hp)
            if is_own and "hp_percent" not in raw
            else _number(hp, 100) if "hp_percent" in raw else 0.0
        )
        speed_range = raw.get("eff_speed_range") or raw.get("speed_range")
        if not speed_range and species:
            speed_range = calc_speed_range(species["stats"]["speed"])
        speed_range = speed_range or [None, None]
        values = [
            hp_ratio,
            _number(raw.get("energy"), 10),
            _number(raw.get("level", 60), 100) if is_own else 0.0,
            *[
                _number(stats.get(key), 500)
                for key in ("hp", "atk", "def", "spatk", "spdef", "speed")
            ],
            _number(raw.get("eff_speed"), 500) if is_own else 0.0,
            _number(speed_range[0], 500),
            _number(speed_range[-1], 500),
            _number(raw.get("bloodline"), 18) if is_own else 0.0,
        ]
        numeric_mask = [
            "hp" in raw or "hp_percent" in raw or is_own,
            "energy" in raw,
            "level" in raw and is_own,
            *([bool(stats)] * 6),
            "eff_speed" in raw and is_own,
            speed_range[0] is not None,
            speed_range[-1] is not None,
            "bloodline" in raw and is_own,
        ]
        for key in ("atk", "def", "spatk", "spdef"):
            base = species.get("stats", {}).get(key)
            if stats:
                values.extend([_number(stats.get(key), 500)] * 2)
                known = key in stats
            else:
                values.extend(
                    [calc_stat(base, 0, 0.9) / 500, calc_stat(base, 10, 1.2) / 500]
                    if base is not None
                    else [0.0, 0.0]
                )
                known = base is not None
            numeric_mask.extend([known, known])
        skills = [self._skill(skill) for skill in raw.get("skills", [])]
        effects = [
            {
                "id": _index(effect.get("type"), self.effect_vocab),
                "numeric": [
                    _number(effect.get("value"), 100),
                    _number(effect.get("duration"), 10),
                    1.0,
                ],
                "mask": True,
            }
            for effect in raw.get("buffs", [])
        ]
        return {
            "id": _index(species.get("id", raw.get("spirit_id")), self.spirit_vocab),
            "element": _index(species.get("mainElement"), self.element_vocab),
            "sub_element": (
                _index(species["subElement"], self.element_vocab)
                if species.get("subElement") is not None
                else 0
            ),
            "nature": _index(nature, self.nature_vocab) if nature is not None else 0,
            "numeric": values,
            "numeric_mask": numeric_mask,
            "skills": skills,
            "effects": effects,
            "mask": True,
        }

    def _team_nature(self, team, index, raw):
        if raw.get("nature") is not None:
            return raw["nature"]
        name = raw.get("name", raw.get("spirit", ""))
        if (
            index < len(team)
            and team[index].get("name", team[index].get("spirit", "")) == name
        ):
            return team[index].get("nature")
        return next(
            (
                pet.get("nature")
                for pet in team
                if pet.get("name", pet.get("spirit", "")) == name
            ),
            None,
        )

    def _lord_branches(self, species):
        return [
            branch
            for chain in species.get("evolution", [])
            for branch in chain.get("lordBranches", [])
        ]

    def opponent_candidates(self, request):
        state = request.get("state", {})
        side = request.get("side", "A")
        enemy_side = "B" if side == "A" else "A"
        enemies = state.get("teams", {}).get(enemy_side, [])
        if request.get("phase") == "lead":
            names = request.get("opponent_team", []) or [
                pet.get("name", "") for pet in enemies
            ]
            return [
                {"kind": "lead", "lead": index} for index, _ in enumerate(names)
            ] or [{"kind": "unknown_skill"}]
        if not enemies:
            enemies = [{"name": name} for name in request.get("opponent_team", [])]
        active_index = state.get("active", {}).get(enemy_side, -1)
        if request.get("phase") == "replacement":
            return [
                {"kind": "switch", "pet_index": index}
                for index, raw in enumerate(enemies)
                if index != active_index and raw.get("hp_percent", 100) > 0
            ] or [{"kind": "unknown_skill"}]
        active = enemies[active_index] if 0 <= active_index < len(enemies) else {}
        species = self.spirit_by_name.get(
            active.get("name", "")
        ) or self.spirit_by_id.get(str(active.get("spirit_id", "")))
        skill_ids = list(species.get("skills", {}).get("normal", [])) if species else []
        skill_ids.extend(
            skill.get(
                "skill_id", self.skill_by_name.get(skill.get("name", ""), {}).get("id")
            )
            for skill in active.get("skills", [])
            if isinstance(skill, dict)
        )
        pool = []
        for skill_id in dict.fromkeys(skill_ids):
            if skill_id is None or str(skill_id) not in self.skill_by_id:
                continue
            for choice_branch in ((0, 1) if skill_id in self.choice_skills else (0,)):
                pool.append(
                    {
                        "kind": "skill",
                        "skill_id": skill_id,
                        "pet_index": active_index,
                        "choice_branch": choice_branch,
                    }
                )
        pool.extend(
            {"kind": "switch", "pet_index": index}
            for index, raw in enumerate(enemies)
            if index != active_index and raw.get("hp_percent", 100) > 0
        )
        pool.extend(({"kind": "charge"}, {"kind": "unknown_skill"}))
        magic_id = state.get("resonance_magic", {}).get(enemy_side)
        usage = state.get("resonance_usage", {}).get(enemy_side, {})
        cooldown = state.get("resonance_cooldown", {}).get(enemy_side, {})
        if (
            magic_id is not None
            and usage.get(magic_id, usage.get(str(magic_id), 0))
            < (2 if magic_id == 0 else 1)
            and cooldown.get(magic_id, cooldown.get(str(magic_id), 0)) <= 0
        ):
            branches = (
                len(self._lord_branches(species))
                if magic_id == 1 and species
                else (1 if magic_id != 1 else 0)
            )
            for candidate in tuple(pool):
                if candidate["kind"] == "unknown_skill":
                    continue
                for branch in range(branches):
                    pool.append(
                        {**candidate, "magic_id": magic_id, "magic_branch": branch}
                    )
        unique = {action_key(candidate): candidate for candidate in pool}
        return list(unique.values()) or [{"kind": "unknown_skill"}]

    def _event_features(self, event, line, side, state, order, is_private, actor_side):
        participants = sorted(
            (
                (pet.get("name", ""), team_side, index)
                for team_side, team in state.get("teams", {}).items()
                for index, pet in enumerate(team)
                if pet.get("name") and pet["name"] in line
            ),
            key=lambda item: (item[1] != actor_side, item[2]),
        )
        source = participants[0] if participants else ("", "", -1)
        target = next(
            (value for value in participants[1:] if value[1:] != source[1:]),
            ("", "", -1),
        )
        relative = lambda team_side: (
            1.0 if team_side == side else -1.0 if team_side else 0.0
        )
        matches = re.findall(
            r"(?:造成|伤害|回复|恢复|获得|失去|损失|扣除)[^0-9-]*(-?\d+(?:\.\d+)?)",
            line,
        )
        raw_value = next(
            (
                event.get(key)
                for key in ("value", "damage", "healing")
                if isinstance(event, dict) and isinstance(event.get(key), (int, float))
            ),
            None,
        )
        amount = (
            float(raw_value) / 1000
            if raw_value is not None
            else float(matches[0]) / 1000 if matches else 0.0
        )
        known = raw_value is not None or bool(matches)
        numeric = [
            min(order, 1000) / 1000,
            amount,
            float(known),
            relative(source[1]),
            max(source[2], -1) / 6,
            relative(target[1]),
            max(target[2], -1) / 6,
            len(line) / 1000,
            0.0,
            0.0,
            0.0,
        ]
        if is_private and isinstance(event, dict):
            numeric[8] = _number(event.get("skill_index", -1), 10)
            numeric[9] = _number(event.get("magic_id", -1), 3)
            numeric[10] = _number(event.get("choice_branch", 0), 1)
        return numeric

    def encode(self, request):
        state = request.get("state", {})
        side = request.get("side", "A")
        enemy_side = "B" if side == "A" else "A"
        phase = request.get("phase", "action")
        own_raw = state.get("teams", {}).get(side, [])
        enemy_raw = state.get("teams", {}).get(enemy_side, [])
        if phase == "lead" and not own_raw:
            own_raw = request.get("team", [])
        team = request.get("team", [])
        own_pets = [
            self._pet(raw, True, self._team_nature(team, index, raw))
            for index, raw in enumerate(own_raw)
        ]
        enemy_pets = [self._pet(raw, False) for raw in enemy_raw]
        public_state = request.get("public_state")
        if public_state is not None:
            public_own_raw = public_state.get("teams", {}).get(side, [])
            public_enemy_raw = public_state.get("teams", {}).get(enemy_side, [])
            public_log = "\n".join(str(line) for line in public_state.get("log", []))
            public_own = []
            for raw in public_own_raw:
                public_raw = self._public_own(raw)
                public_raw["skills"] = [
                    skill
                    for skill in raw.get("skills", [])
                    if (
                        skill.get("name", "") if isinstance(skill, dict) else str(skill)
                    )
                    in public_log
                ]
                public_own.append(self._pet(public_raw, False))
        else:
            public_own = [self._pet(self._public_own(raw), False) for raw in own_raw]
            public_enemy_raw = enemy_raw or [
                {"name": name} for name in request.get("opponent_team", [])
            ]
        public_pets = public_own + [self._pet(raw, False) for raw in public_enemy_raw]
        events = []
        for is_private, values in (
            (False, request.get("events", [])),
            (True, request.get("private_events", [])),
        ):
            for index, event in enumerate(values):
                line = event if isinstance(event, str) else event.get("line", "")
                event_phase = (
                    event.get("phase", phase) if isinstance(event, dict) else phase
                )
                event_kind = (
                    event.get("kind", "private" if is_private else "log")
                    if isinstance(event, dict)
                    else ("private" if is_private else "log")
                )
                action_kind = (
                    event.get("kind")
                    if is_private and isinstance(event, dict)
                    else None
                )
                skill_id = (
                    event.get("skill_id")
                    if is_private and isinstance(event, dict)
                    else None
                )
                if is_private and action_kind not in self.action_vocab:
                    action_kind = "unknown_skill"
                if skill_id is None and is_private and action_kind == "skill":
                    active_index = state.get("active", {}).get(side, -1)
                    own_active = (
                        own_raw[active_index]
                        if 0 <= active_index < len(own_raw)
                        else {}
                    )
                    skill_index = event.get("skill_index", -1)
                    own_skills = own_active.get("skills", [])
                    if 0 <= skill_index < len(own_skills):
                        skill = own_skills[skill_index]
                        skill_id = (
                            skill.get("skill_id", skill.get("name"))
                            if isinstance(skill, dict)
                            else skill
                        )
                private_skill = self._skill(skill_id) if skill_id is not None else None
                public_skill = (
                    next(
                        (
                            self._skill(name)
                            for name in self.skill_by_name
                            if name in line
                        ),
                        None,
                    )
                    if not is_private
                    else None
                )
                actor_side = side
                if not is_private:
                    for public_side in (side, enemy_side):
                        if any(
                            pet.get("name") and pet["name"] in line
                            for pet in state.get("teams", {}).get(public_side, [])
                        ):
                            actor_side = public_side
                            break
                event_features = self._event_features(
                    event, str(line), side, state, index, is_private, actor_side
                )
                event_features[1] = float(event_features[1])
                events.append(
                    {
                        "kind": _index(
                            "private" if is_private else event_kind, self.event_vocab
                        ),
                        "phase": _index(event_phase, self.phase_vocab),
                        "action": _index(action_kind, self.action_vocab),
                        "skill": (
                            private_skill["id"]
                            if private_skill
                            else public_skill["id"] if public_skill else 0
                        ),
                        "numeric": event_features,
                        "mask": True,
                        "public": not is_private,
                    }
                )
        fields = []
        weather = state.get("weather")
        fields.append(
            {
                "id": 0,
                "numeric": [
                    _number(weather),
                    _number(
                        weather.get("remaining") if isinstance(weather, dict) else 0
                    ),
                ],
                "mask": weather is not None,
            }
        )
        for field_id, mark_side in enumerate((side, enemy_side), 1):
            for polarity in ("positive", "negative"):
                mark = state.get("marks", {}).get(mark_side, {}).get(polarity)
                fields.append(
                    {
                        "id": field_id + (0 if polarity == "positive" else 2),
                        "numeric": [
                            _number(mark),
                            _number(
                                mark.get("stacks") if isinstance(mark, dict) else 0
                            ),
                        ],
                        "mask": mark is not None,
                    }
                )
        for field_id, key in enumerate(("magic", "resonance_magic"), 5):
            values = state.get(key, {})
            fields.append(
                {
                    "id": field_id,
                    "numeric": [
                        _number(values.get(side)),
                        _number(values.get(enemy_side)),
                    ],
                    "mask": bool(values),
                }
            )
        fields.append(
            {
                "id": 7,
                "numeric": [_number(state.get("turn", 0), 1000), 0.0],
                "mask": True,
            }
        )
        active = state.get("active", {}).get(side, 0)
        enemy_active = state.get("active", {}).get(enemy_side, -1)
        own_candidates = selectable_candidates(request)
        if phase == "lead" and not own_candidates:
            own_candidates = [
                {"kind": "lead", "lead": index} for index in range(len(own_raw))
            ]
        opponent_candidates = self.opponent_candidates(request)
        return {
            "phase": _index(phase, self.phase_vocab),
            "phase_is_lead": phase == "lead",
            "side": _index("own", self.side_vocab),
            "turn": _number(state.get("turn", 0), 1000),
            "weather": _index(weather, self.weather_vocab),
            "own_pets": own_pets,
            "enemy_pets": enemy_pets,
            "public_pets": public_pets,
            "fields": fields,
            "events": events,
            "own_candidates": [
                self._candidate(action, active, own_raw, enemy_active, enemy_raw)
                for action in own_candidates
            ],
            "opponent_candidates": [
                self._candidate(action, enemy_active, enemy_raw, active, own_raw)
                for action in opponent_candidates
            ],
            "action_mask": [
                bool(action.get("mask", True)) for action in own_candidates
            ],
            "opponent_mask": [True] * len(opponent_candidates),
        }
        return {
            key: value.squeeze(0) for key, value in collate_inputs([encoded]).items()
        }

    def _species(self, raw):
        return self.spirit_by_id.get(
            str(raw.get("spirit_id", ""))
        ) or self.spirit_by_name.get(raw.get("name", raw.get("spirit", "")), {})

    def _elements(self, raw):
        attributes = raw.get("attributes")
        if attributes:
            return list(attributes)
        species = self._species(raw)
        return [
            element
            for element in (species.get("mainElement"), species.get("subElement"))
            if element is not None
        ]

    def _stats(self, raw):
        if raw.get("stats"):
            return raw["stats"]
        species = self._species(raw)
        return calc_all_stats(species["stats"]) if species.get("stats") else {}

    def _speed(self, raw):
        speed = raw.get("eff_speed")
        if isinstance(speed, (int, float)):
            return float(speed)
        speed_range = raw.get("eff_speed_range") or raw.get("speed_range")
        if speed_range:
            return (speed_range[0] + speed_range[-1]) / 2
        speed = (raw.get("stats") or {}).get("speed")
        return float(speed) if isinstance(speed, (int, float)) else None

    def _speed_range(self, raw):
        speed = raw.get("eff_speed")
        if isinstance(speed, (int, float)):
            return [float(speed), float(speed)]
        speed_range = raw.get("eff_speed_range") or raw.get("speed_range")
        if speed_range:
            return [speed_range[0], speed_range[-1]]
        speed = (raw.get("stats") or {}).get("speed")
        return (
            [float(speed), float(speed)]
            if isinstance(speed, (int, float))
            else [None, None]
        )

    def _initiative(self, speed, speed_range):
        low, high = speed_range
        if speed is None or low is None or high is None:
            return 0.5
        if high <= low:
            return 0.5 if speed == low else float(speed > low)
        return min(max((speed - low) / (high - low), 0.0), 1.0)

    def _current_hp(self, raw, stats):
        hp = raw.get("hp")
        if isinstance(hp, (int, float)):
            return float(hp)
        percent = raw.get("hp_percent", 100)
        return (
            float(percent) / 100 * stats.get("hp", 0)
            if isinstance(percent, (int, float))
            else 0.0
        )

    def _hp_ratio(self, raw):
        if "hp_percent" in raw:
            return _number(raw["hp_percent"], 100)
        return _number(raw.get("hp"), raw.get("max_hp", 100)) if "hp" in raw else 0.0

    def _best_mult(self, raw, defender_elements):
        if not defender_elements:
            return 1.0
        skills = raw.get("skills") or self._species(raw).get("skills", {}).get(
            "normal", []
        )
        mults = [
            type_multiplier(skill["element_raw"], defender_elements, self.typechart)
            for skill in map(self._skill, skills)
            if skill["category_raw"] in (0, 1) and skill["element_raw"] is not None
        ]
        return max(mults, default=1.0)

    def _estimated_damage(self, skill, attacker, defender):
        category = skill["category_raw"]
        power = skill["power_raw"]
        if category not in (0, 1) or not isinstance(power, (int, float)) or power <= 0:
            return 0
        attacker_stats = self._stats(attacker)
        defender_stats = self._stats(defender)
        attack = attacker_stats.get("atk" if category == 0 else "spatk")
        defense = defender_stats.get("def" if category == 0 else "spdef")
        if not isinstance(attack, (int, float)) or not isinstance(
            defense, (int, float)
        ):
            return 0
        element = skill["element_raw"]
        stab = (
            1.25 if element is not None and element in self._elements(attacker) else 1.0
        )
        mult = (
            type_multiplier(element, self._elements(defender), self.typechart)
            if element is not None
            else 1.0
        )
        level = attacker.get("level")
        level = level if isinstance(level, (int, float)) else 60
        return int(
            attack
            * power
            * stab
            * mult
            * (level * 45 / 100 + 10)
            / 41
            / max(defense, 1)
        )

    def _skill_derived(self, skill, attacker, defender):
        element = skill["element_raw"]
        attacker_elements = self._elements(attacker)
        mult = (
            type_multiplier(element, self._elements(defender), self.typechart)
            if element is not None
            else 1.0
        )
        damage = self._estimated_damage(skill, attacker, defender)
        defender_hp = self._current_hp(defender, self._stats(defender))
        static = skill["static"]
        counter = static["counter_target"]
        energy = attacker.get("energy")
        energy = float(energy) if isinstance(energy, (int, float)) else 0.0
        cost = skill["energy_raw"]
        cost = float(cost) if isinstance(cost, (int, float)) else 0.0
        return [
            _log_mult(mult),
            float(element is not None and element in attacker_elements),
            min(damage / defender_hp, 2) / 2 if defender_hp > 0 else 0.0,
            (
                min(math.ceil(defender_hp / damage), 10) / 10
                if damage > 0 and defender_hp > 0
                else 1.0
            ),
            self._initiative(self._speed(attacker), self._speed_range(defender)),
            min(max(energy - cost, 0.0), 10.0) / 10,
            float(energy >= cost),
            float(counter == "attack"),
            float(counter == "defense"),
            float(counter == "status"),
            float(static["counter_interrupt"]),
            min(max(static["drive"] / 3, 0.0), 1.0),
            float(static["swift"]),
            float(static["windup"]),
            float(static["usable"]),
            float(static["choice"]),
        ]

    def _switch_derived(self, team, pet_index, enemy_team, enemy_index):
        if not 0 <= pet_index < len(team):
            return [0.0] * 4
        entry = team[pet_index]
        defender = enemy_team[enemy_index] if 0 <= enemy_index < len(enemy_team) else {}
        return [
            _log_mult(self._best_mult(entry, self._elements(defender))),
            _log_mult(self._best_mult(defender, self._elements(entry))),
            self._hp_ratio(entry),
            self._initiative(self._speed(entry), self._speed_range(defender)),
        ]

    def _candidate(
        self, action, active_index=None, team=None, enemy_index=None, enemy_team=None
    ):
        kind = action.get("kind", "unknown_skill")
        team = team or []
        enemy_team = enemy_team or []
        active_index = active_index if isinstance(active_index, int) else -1
        enemy_index = enemy_index if isinstance(enemy_index, int) else -1
        skill_id = action.get("skill_id")
        skill_index = action.get("skill_index", -1)
        pet_index = action.get(
            "pet_index", action.get("lead", active_index if active_index >= 0 else 0)
        )
        pet_index = int(pet_index if pet_index is not None else 0)
        skills = team[pet_index].get("skills", []) if 0 <= pet_index < len(team) else []
        raw_skill = skills[skill_index] if 0 <= skill_index < len(skills) else None
        if raw_skill is None and skill_id is not None:
            raw_skill = next(
                (
                    skill
                    for skill in skills
                    if isinstance(skill, dict)
                    and str(skill.get("skill_id", skill.get("id"))) == str(skill_id)
                ),
                None,
            )
        if skill_id is None and raw_skill is not None:
            skill_id = (
                raw_skill.get("skill_id", raw_skill.get("name"))
                if isinstance(raw_skill, dict)
                else raw_skill
            )
        skill_data = (
            self._skill(raw_skill if raw_skill is not None else skill_id)
            if raw_skill is not None or skill_id is not None
            else self._skill(None)
        )
        attacker = team[pet_index] if 0 <= pet_index < len(team) else {}
        defender = enemy_team[enemy_index] if 0 <= enemy_index < len(enemy_team) else {}
        derived = [0.0] * 24
        if kind == "skill":
            derived[:16] = self._skill_derived(skill_data, attacker, defender)
        elif kind == "switch":
            derived[16:20] = self._switch_derived(
                team, pet_index, enemy_team, enemy_index
            )
        derived[20] = float(kind == "skill")
        derived[21] = float(kind == "switch")
        derived[22] = float(kind == "charge")
        derived[23] = float(kind == "lead")
        return {
            "kind": _index(kind, self.action_vocab),
            "pet_index": pet_index,
            "skill": skill_data["id"],
            "magic_id": int(
                action.get("magic_id", -1) if action.get("magic_id") is not None else -1
            ),
            "branches": [
                int(action.get("magic_branch", 0)),
                int(action.get("choice_branch", 0)),
            ],
            "numeric": skill_data["numeric"][:2],
            "skill_index": int(skill_index),
            "derived": derived,
            "mask": bool(action.get("mask", True)),
        }


def selectable_candidates(request):
    return [
        candidate
        for candidate in request.get("candidates", [])
        if candidate.get("kind") != "flee"
    ]


def action_key(action):
    kind = action.get("kind", "unknown_skill")
    if kind == "lead":
        return (
            "lead",
            int(action.get("lead", action.get("pet_index", 0))),
            action.get("magic_id"),
            int(action.get("magic_branch", 0)),
        )
    if kind in ("switch", "flee"):
        identity = int(action.get("pet_index", -1)) if kind == "switch" else None
        return (
            kind,
            identity,
            action.get("magic_id"),
            int(action.get("magic_branch", 0)),
        )
    if kind == "skill":
        skill_id = action.get("skill_id")
        if skill_id is None and "skill_index" in action:
            skill_id = ("slot", int(action["skill_index"]))
        return (
            "skill",
            skill_id,
            action.get("magic_id"),
            int(action.get("magic_branch", 0)),
            int(action.get("choice_branch", 0)),
        )
    if kind == "charge":
        return ("charge", action.get("magic_id"), int(action.get("magic_branch", 0)))
    if kind == "unknown_skill":
        return ("unknown_skill", action.get("skill_id"), action.get("pet_index"))
    return (kind, action.get("skill_id"), action.get("pet_index"))


def collate_inputs(items, device="cpu"):
    if not items:
        return {}
    if all(
        all(isinstance(value, torch.Tensor) for value in item.values())
        for item in items
    ):
        result = {}
        for key in items[0]:
            tensors = [item[key] for item in items]
            if tensors[0].ndim == 0:
                result[key] = torch.stack(tensors).to(device)
            else:
                shape = tuple(
                    max(tensor.shape[axis] for tensor in tensors)
                    for axis in range(tensors[0].ndim)
                )
                batch = torch.zeros(
                    (len(items), *shape), dtype=tensors[0].dtype, device=device
                )
                for index, tensor in enumerate(tensors):
                    slices = tuple(slice(0, size) for size in tensor.shape)
                    batch[(index, *slices)] = tensor.to(device)
                result[key] = batch
        return result
    batch_size = len(items)
    max_pets = max(
        1,
        max(
            max(len(item[key]) for key in ("own_pets", "enemy_pets", "public_pets"))
            for item in items
        ),
    )
    max_skills = max(
        1,
        max(
            (
                len(pet["skills"])
                for item in items
                for key in ("own_pets", "enemy_pets")
                for pet in item[key]
            ),
            default=0,
        ),
    )
    max_effects = max(
        1,
        max(
            (
                len(pet["effects"])
                for item in items
                for key in ("own_pets", "enemy_pets")
                for pet in item[key]
            ),
            default=0,
        ),
    )
    max_fields = max(1, max(len(item["fields"]) for item in items))
    max_events = max(1, max(len(item["events"]) for item in items))
    max_actions = max(1, max(len(item["own_candidates"]) for item in items))
    max_opponents = max(1, max(len(item["opponent_candidates"]) for item in items))
    result = {
        "phase": torch.tensor(
            [item["phase"] for item in items], dtype=torch.long, device=device
        ),
        "phase_is_lead": torch.tensor(
            [item["phase_is_lead"] for item in items], dtype=torch.bool, device=device
        ),
        "side": torch.tensor(
            [item["side"] for item in items], dtype=torch.long, device=device
        ),
        "turn": torch.tensor(
            [item["turn"] for item in items], dtype=torch.float32, device=device
        ),
        "weather": torch.tensor(
            [item["weather"] for item in items], dtype=torch.long, device=device
        ),
    }
    for prefix in ("own", "enemy", "public"):
        result[f"{prefix}_pets"] = torch.zeros(
            batch_size, max_pets, 42, dtype=torch.float32, device=device
        )
        result[f"{prefix}_pet_ids"] = torch.zeros(
            batch_size, max_pets, dtype=torch.long, device=device
        )
        result[f"{prefix}_pet_elements"] = torch.zeros(
            batch_size, max_pets, dtype=torch.long, device=device
        )
        result[f"{prefix}_pet_sub_elements"] = torch.zeros(
            batch_size, max_pets, dtype=torch.long, device=device
        )
        result[f"{prefix}_pet_natures"] = torch.zeros(
            batch_size, max_pets, dtype=torch.long, device=device
        )
        result[f"{prefix}_pet_mask"] = torch.zeros(
            batch_size, max_pets, dtype=torch.bool, device=device
        )
    for prefix in ("own", "enemy"):
        result[f"{prefix}_skills"] = torch.zeros(
            batch_size, max_pets, max_skills, 14, dtype=torch.float32, device=device
        )
        result[f"{prefix}_skill_ids"] = torch.zeros(
            batch_size, max_pets, max_skills, dtype=torch.long, device=device
        )
        result[f"{prefix}_skill_elements"] = torch.zeros(
            batch_size, max_pets, max_skills, dtype=torch.long, device=device
        )
        result[f"{prefix}_skill_categories"] = torch.zeros(
            batch_size, max_pets, max_skills, dtype=torch.long, device=device
        )
        result[f"{prefix}_skill_mask"] = torch.zeros(
            batch_size, max_pets, max_skills, dtype=torch.bool, device=device
        )
        result[f"{prefix}_effects"] = torch.zeros(
            batch_size, max_pets, max_effects, 3, dtype=torch.float32, device=device
        )
        result[f"{prefix}_effect_ids"] = torch.zeros(
            batch_size, max_pets, max_effects, dtype=torch.long, device=device
        )
        result[f"{prefix}_effect_mask"] = torch.zeros(
            batch_size, max_pets, max_effects, dtype=torch.bool, device=device
        )
    result.update(
        {
            "fields": torch.zeros(
                batch_size, max_fields, 5, dtype=torch.float32, device=device
            ),
            "field_mask": torch.zeros(
                batch_size, max_fields, dtype=torch.bool, device=device
            ),
            "events": torch.zeros(
                batch_size,
                max_events,
                EVENT_FEATURES,
                dtype=torch.float32,
                device=device,
            ),
            "event_kinds": torch.zeros(
                batch_size, max_events, dtype=torch.long, device=device
            ),
            "event_phases": torch.zeros(
                batch_size, max_events, dtype=torch.long, device=device
            ),
            "event_mask": torch.zeros(
                batch_size, max_events, dtype=torch.bool, device=device
            ),
            "event_public_mask": torch.zeros(
                batch_size, max_events, dtype=torch.bool, device=device
            ),
            "event_action_kinds": torch.zeros(
                batch_size, max_events, dtype=torch.long, device=device
            ),
            "event_skill_ids": torch.zeros(
                batch_size, max_events, dtype=torch.long, device=device
            ),
            "own_candidates": torch.zeros(
                batch_size, max_actions, 33, dtype=torch.float32, device=device
            ),
            "action_mask": torch.zeros(
                batch_size, max_actions, dtype=torch.bool, device=device
            ),
            "opponent_candidates": torch.zeros(
                batch_size, max_opponents, 33, dtype=torch.float32, device=device
            ),
            "opponent_mask": torch.zeros(
                batch_size, max_opponents, dtype=torch.bool, device=device
            ),
        }
    )
    for batch_index, item in enumerate(items):
        for prefix in ("own", "enemy", "public"):
            for pet_index, pet in enumerate(item[f"{prefix}_pets"]):
                result[f"{prefix}_pets"][batch_index, pet_index] = torch.tensor(
                    [*pet["numeric"], *pet["numeric_mask"]],
                    dtype=torch.float32,
                    device=device,
                )
                result[f"{prefix}_pet_ids"][batch_index, pet_index] = pet["id"]
                result[f"{prefix}_pet_elements"][batch_index, pet_index] = pet[
                    "element"
                ]
                result[f"{prefix}_pet_sub_elements"][batch_index, pet_index] = pet[
                    "sub_element"
                ]
                result[f"{prefix}_pet_natures"][batch_index, pet_index] = pet["nature"]
                result[f"{prefix}_pet_mask"][batch_index, pet_index] = pet["mask"]
                if prefix == "public":
                    continue
                for skill_index, skill in enumerate(pet["skills"]):
                    result[f"{prefix}_skills"][batch_index, pet_index, skill_index] = (
                        torch.tensor(
                            skill["numeric"], dtype=torch.float32, device=device
                        )
                    )
                    result[f"{prefix}_skill_ids"][
                        batch_index, pet_index, skill_index
                    ] = skill["id"]
                    result[f"{prefix}_skill_elements"][
                        batch_index, pet_index, skill_index
                    ] = skill["element"]
                    result[f"{prefix}_skill_categories"][
                        batch_index, pet_index, skill_index
                    ] = skill["category"]
                    result[f"{prefix}_skill_mask"][
                        batch_index, pet_index, skill_index
                    ] = skill["mask"]
                for effect_index, effect in enumerate(pet["effects"]):
                    result[f"{prefix}_effects"][
                        batch_index, pet_index, effect_index
                    ] = torch.tensor(
                        effect["numeric"], dtype=torch.float32, device=device
                    )
                    result[f"{prefix}_effect_ids"][
                        batch_index, pet_index, effect_index
                    ] = effect["id"]
                    result[f"{prefix}_effect_mask"][
                        batch_index, pet_index, effect_index
                    ] = effect["mask"]
        for field_index, field in enumerate(item["fields"]):
            result["fields"][batch_index, field_index] = torch.tensor(
                [field["id"] / 10, *field["numeric"], float(field["mask"]), 0],
                dtype=torch.float32,
                device=device,
            )
            result["field_mask"][batch_index, field_index] = field["mask"]
        for event_index, event in enumerate(item["events"]):
            result["events"][batch_index, event_index] = torch.tensor(
                event["numeric"], dtype=torch.float32, device=device
            )
            result["event_kinds"][batch_index, event_index] = event["kind"]
            result["event_phases"][batch_index, event_index] = event["phase"]
            result["event_mask"][batch_index, event_index] = event["mask"]
            result["event_public_mask"][batch_index, event_index] = event["public"]
            result["event_action_kinds"][batch_index, event_index] = event["action"]
            result["event_skill_ids"][batch_index, event_index] = event["skill"]
        for key, mask_key in (
            ("own_candidates", "action_mask"),
            ("opponent_candidates", "opponent_mask"),
        ):
            for candidate_index, candidate in enumerate(item[key]):
                result[key][batch_index, candidate_index] = torch.tensor(
                    [
                        candidate["kind"],
                        candidate["pet_index"],
                        candidate["skill"],
                        candidate["magic_id"],
                        *candidate["branches"],
                        *candidate["numeric"],
                        candidate["skill_index"],
                        *candidate["derived"],
                    ],
                    dtype=torch.float32,
                    device=device,
                )
                result[mask_key][batch_index, candidate_index] = candidate["mask"]
    return result
