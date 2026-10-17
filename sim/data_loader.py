"""Load JSON data and build battle entities."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

from .models import BattlePet, BattleSkill, SkillData

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = ROOT / "data"


def load_json(name: str):
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def load_spirits():
    return load_json("spirits.json")["spirits"]


def load_skills():
    return load_json("skills.json")["skills"]


def load_skill_groups() -> dict:
    """技能语义分组（data/skill_groups.json，人工校对的策划分组），返回 id -> 条目。"""
    return {g["id"]: g for g in load_json("skill_groups.json")["groups"]}


def skill_group_ids(group_id: str) -> list:
    """某语义分组的技能 id 列表。"""
    return list(load_skill_groups()[group_id]["skill_ids"])


def load_typechart():
    return load_json("typechart.json")


def load_natures():
    return load_json("natures.json")["natures"]


IV_KEYS = ("hp", "atk", "def", "spatk", "spdef", "speed")
VALID_RESONANCE_IDS = (0, 1, 2)


def _type_name(value) -> str:
    if isinstance(value, bool):
        return "bool"
    return type(value).__name__


def validate_team_config(data, source: str = "队伍配置") -> None:
    """校验队伍 JSON 结构；不合法时抛出带具体原因的 ValueError。"""
    if not isinstance(data, dict):
        raise ValueError(
            f"{source} 格式错误：顶层必须是 JSON 对象，实际是 {_type_name(data)}"
        )

    team = data.get("team")
    if team is None:
        raise ValueError(f"{source} 缺少 team 字段")
    if not isinstance(team, list):
        raise ValueError(f"{source} 的 team 字段必须是数组，实际是 {_type_name(team)}")
    if not team:
        raise ValueError(f"{source} 的 team 数组不能为空")

    for i, pet in enumerate(team):
        label = f"{source} 第 {i + 1} 只精灵"
        if not isinstance(pet, dict):
            raise ValueError(f"{label} 必须是 JSON 对象，实际是 {_type_name(pet)}")

        if "spirit" not in pet:
            raise ValueError(f"{label} 缺少 spirit 字段")
        if not isinstance(pet["spirit"], str) or not pet["spirit"].strip():
            raise ValueError(
                f"{label} 的 spirit 必须是非空字符串，实际是 {_type_name(pet['spirit'])}"
            )

        if "nature" in pet and pet["nature"] is not None:
            if not isinstance(pet["nature"], int) or isinstance(pet["nature"], bool):
                raise ValueError(
                    f"{label} 的 nature 必须是整数或 null，实际是 {_type_name(pet['nature'])}"
                )

        if "bloodline" in pet and pet["bloodline"] is not None:
            if not isinstance(pet["bloodline"], int) or isinstance(
                pet["bloodline"], bool
            ):
                raise ValueError(
                    f"{label} 的 bloodline 必须是整数或 null，实际是 {_type_name(pet['bloodline'])}"
                )

        if "ivs" in pet and pet["ivs"] is not None:
            ivs = pet["ivs"]
            if not isinstance(ivs, dict):
                raise ValueError(
                    f"{label} 的 ivs 必须是对象或 null，实际是 {_type_name(ivs)}"
                )
            for key in IV_KEYS:
                if key not in ivs:
                    raise ValueError(f"{label} 的 ivs 缺少 {key}")
            for key, value in ivs.items():
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(
                        f"{label} 的 ivs.{key} 必须是整数，实际是 {_type_name(value)}"
                    )

        if "skills" in pet and pet["skills"] is not None:
            skills = pet["skills"]
            if not isinstance(skills, list):
                raise ValueError(
                    f"{label} 的 skills 必须是数组或 null，实际是 {_type_name(skills)}"
                )
            for j, skill in enumerate(skills):
                if not isinstance(skill, str):
                    raise ValueError(
                        f"{label} 的 skills 第 {j + 1} 项必须是字符串，实际是 {_type_name(skill)}"
                    )

    if "resonance" in data and data["resonance"] is not None:
        resonance = data["resonance"]
        if (
            not isinstance(resonance, int)
            or isinstance(resonance, bool)
            or resonance not in VALID_RESONANCE_IDS
        ):
            raise ValueError(
                f"{source} 的 resonance 必须是 0、1、2 或 null，实际是 {_type_name(resonance)}"
            )


DEFAULT_IVS = {
    "hp": 10,
    "atk": 0,
    "def": 0,
    "spatk": 10,
    "spdef": 0,
    "speed": 10,
}

NATURE_UP_MULTIPLIER = 1.2
NATURE_DOWN_MULTIPLIER = 0.9


def get_nature_multipliers(nature):
    if nature is None or nature == -1:
        return {stat: 1.0 for stat in DEFAULT_IVS}
    for item in load_natures():
        if item["id"] == nature:
            mults = {stat: 1.0 for stat in DEFAULT_IVS}
            mults[item["up"]] = NATURE_UP_MULTIPLIER
            mults[item["down"]] = NATURE_DOWN_MULTIPLIER
            return mults
    return {stat: 1.0 for stat in DEFAULT_IVS}


def build_skill_map() -> dict:
    return {s["id"]: s for s in load_skills()}


def make_skill(raw: dict) -> BattleSkill:
    """由 skills.json 原始数据构造 BattleSkill（公共构造函数）。"""
    return BattleSkill(
        skill_id=raw["id"],
        name=raw["name"],
        element=raw["element"],
        category=raw["category"],
        power=raw.get("power"),
        energy_cost=raw.get("energyCost", 0),
        desc=raw.get("desc", ""),
    )


def skills_by_element(element: int) -> list:
    """全游戏中指定元素（系别）的全部技能模板（不限类别）。"""
    return [make_skill(raw) for raw in load_skills() if raw["element"] == element]


# 元素 -> 技能模板列表缓存（巧变随机池等使用，避免重复构造）
_SKILL_POOL_CACHE: dict = {}


def skill_pool_for_element(element: int) -> list:
    """带缓存的按元素技能池（返回模板列表的浅拷贝，元素级复用）。"""
    if element not in _SKILL_POOL_CACHE:
        _SKILL_POOL_CACHE[element] = skills_by_element(element)
    return list(_SKILL_POOL_CACHE[element])


def find_spirit(name: str, spirits=None):
    if spirits is None:
        spirits = load_spirits()
    for sp in spirits:
        if sp["name"] == name:
            return sp
    return None


def round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def calc_stat(base_stat: int, iv: int, nature_multiplier: float = 1.0) -> int:
    return (
        round_half_up(
            (round_half_up(1.1 * (base_stat + 3 * iv)) + 10) * nature_multiplier
        )
        + 50
    )


def calc_speed_range(base_speed: int) -> list:
    """按当前形态种族速度计算对外显示的基础速度范围。"""
    return [calc_stat(base_speed, 0, 0.9), calc_stat(base_speed, 10, 1.2)]


def calc_hp(base_hp: int, iv: int, nature_multiplier: float = 1.0) -> int:
    growth = 0.01 * base_hp + 0.005 * iv * 6
    return round_half_up(round_half_up(70 + growth * 170) * nature_multiplier) + 100


def calc_all_stats(base_stats: dict, ivs=None, nature=None) -> dict:
    if ivs is None:
        ivs = DEFAULT_IVS
    mults = get_nature_multipliers(nature)
    return {
        "hp": calc_hp(base_stats["hp"], ivs["hp"], mults["hp"]),
        "atk": calc_stat(base_stats["atk"], ivs["atk"], mults["atk"]),
        "spatk": calc_stat(base_stats["spatk"], ivs["spatk"], mults["spatk"]),
        "def": calc_stat(base_stats["def"], ivs["def"], mults["def"]),
        "spdef": calc_stat(base_stats["spdef"], ivs["spdef"], mults["spdef"]),
        "speed": calc_stat(base_stats["speed"], ivs["speed"], mults["speed"]),
    }


def make_battle_pet(
    spirit,
    side: str,
    level: int = 60,
    ivs=None,
    nature=None,
    skill_names=None,
    skill_limit: int = 4,
    bloodline=None,
) -> BattlePet:
    skill_map = build_skill_map()
    skill_ids = [sid for sid in spirit["skills"].get("normal", [])]
    if skill_names is None:
        selected_ids = [
            sid for sid in skill_ids if skill_map.get(sid, {}).get("name") != "蓄能"
        ]
        selected_ids = selected_ids[:skill_limit]
    else:
        by_name = {raw["name"]: raw for raw in load_skills()}
        selected_ids = []
        for name in skill_names:
            raw = by_name.get(name)
            if raw is not None:
                selected_ids.append(raw["id"])
    skills = []
    for sid in selected_ids:
        raw = skill_map.get(sid)
        if raw is None:
            continue
        skills.append(
            BattleSkill(
                skill_id=sid,
                name=raw["name"],
                element=raw["element"],
                category=raw["category"],
                power=raw.get("power"),
                energy_cost=raw.get("energyCost", 0),
                desc=raw.get("desc", ""),
            )
        )
    stats = calc_all_stats(spirit["stats"], ivs=ivs, nature=nature)
    speed_range = calc_speed_range(spirit["stats"]["speed"])
    trait_id = None
    feature = spirit.get("feature") or {}
    if feature.get("id") is not None:
        trait_id = feature["id"]
    return BattlePet(
        side=side,
        spirit_id=spirit["id"],
        name=spirit["name"],
        level=level,
        stats=stats,
        hp=stats["hp"],
        max_hp=stats["hp"],
        energy=10,
        skills=skills,
        attributes=get_attributes(spirit),
        speed_range=speed_range,
        ivs=dict(ivs) if ivs is not None else dict(DEFAULT_IVS),
        nature=nature,
        bloodline=bloodline if bloodline is not None else spirit.get("mainElement"),
        trait_id=trait_id,
    )


def get_attributes(spirit) -> list:
    elements = []
    if spirit.get("mainElement") is not None:
        elements.append(spirit["mainElement"])
    if spirit.get("subElement") is not None:
        elements.append(spirit["subElement"])
    return elements


def create_default_pets() -> dict:
    spirits = load_spirits()
    a = make_battle_pet(find_spirit("岚鸟", spirits), "A")
    b = make_battle_pet(find_spirit("奇丽花", spirits), "B")
    return {"A": a, "B": b}
