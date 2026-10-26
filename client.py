#!/usr/bin/env python3
"""Headless battle client.

Run:
  python client.py --port 5000 --team data/teams/冻陨2.0.json
  python client.py --port 5000 --team data/teams/冻陨2.0.json --auto best
  python client.py --port 5000 --team data/teams/冻陨2.0.json --auto teacher
"""

import argparse
import json
import os
import random
import socket
import sys
from pathlib import Path

from sim.data_loader import validate_team_config


def send_line(conn, obj):
    conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


def read_line(f):
    line = f.readline()
    if not line:
        return None
    return json.loads(line)


LOG_FILES = []
AUTO_MODE = False
MACHINE_MODE = False
MY_SIDE = None
AUTO_AGENT = None
AUTO_ENCODER = None
AUTO_MODEL = None
AUTO_TEACHER = None
AUTO_DEVICE = None
AUTO_MEMORIES = {"A": None, "B": None}

# 元素 0-17 名称（与 sim/enums.Element 一致）
ELEMENT_NAMES = [
    "普通",
    "草",
    "火",
    "水",
    "光",
    "地",
    "冰",
    "龙",
    "电",
    "毒",
    "虫",
    "武",
    "翼",
    "萌",
    "幽",
    "恶",
    "机械",
    "幻",
]
# 首领血脉编号（与 sim/enums.LORD_BLOODLINE 一致）
LORD_BLOODLINE = 18

CATEGORY_NAMES = {
    0: "物攻",
    1: "魔攻",
    2: "防御",
    3: "状态",
}

# buff 分类
BUFF_ALWAYS = {"lifesteal"}
BUFF_DEBUFF = {
    "priority_debuff",
    "poison",
    "burn",
    "dizzy",
    "leech",
    "freeze",
    "cute",
    "lock",
    "lightning",
}
# buff 类型中文名
BUFF_NAMES = {
    "atk": "物攻",
    "spatk": "魔攻",
    "def": "物防",
    "spdef": "魔防",
    "speed": "速度",
    "speed_percent": "速度%",
    "skill_power_percent": "威力%",
    "skill_power_flat": "威力",
    "next_attack_power_percent": "下次攻击威力%",
    "next_attack_power_flat": "下次攻击威力",
    "hit_count_percent": "连击%",
    "hit_count_flat": "连击数",
    "energy_cost": "能耗",
    "lifesteal": "吸血",
    "overload": "过载",
    "priority": "先手",
    "priority_debuff": "先手减益",
    "poison": "中毒",
    "burn": "灼烧",
    "dizzy": "眩晕",
    "leech": "寄生",
    "freeze": "冻结",
    "cute": "萌化",
    "lock": "禁足",
    "lightning": "引电",
    "freeze_immune": "免疫冻结",
    "burn_immune": "免疫灼烧",
    "leech_immune": "免疫寄生",
}
_BUFF_INFO = None


def buff_display_name(btype):
    """buff 类型显示名；按元素威力增益（power_boost_<元素id>）动态映射为'X系技能威力'。"""
    if btype.startswith("power_boost_"):
        el = int(btype.rsplit("_", 1)[1])
        return f"{ELEMENT_NAMES[el]}系技能威力"
    if btype.startswith("energy_cost_boost_"):
        return "被应对技能能耗"
    return BUFF_NAMES.get(btype, btype)


def buff_classify(btype, value):
    """按类型与层数判断 buff/debuff（与引擎一致）。"""
    if btype in BUFF_ALWAYS:
        return "buff"
    if btype in BUFF_DEBUFF:
        return "debuff"
    if btype in ("energy_cost", "attack_energy_cost"):
        return "buff" if value < 0 else "debuff"
    return "buff" if value >= 0 else "debuff"


def buff_info():
    """懒加载 data/buff.json：类型 -> {per_layer, unit}（显示每层数值用）。"""
    global _BUFF_INFO
    if _BUFF_INFO is None:
        path = Path(os.path.dirname(os.path.abspath(__file__))) / "data" / "buff.json"
        with open(path, encoding="utf-8") as f:
            _BUFF_INFO = {e["id"]: e for e in json.load(f)["effects"]}
    return _BUFF_INFO


def terminal_input(prompt=""):
    if MACHINE_MODE:
        print(prompt, file=sys.stderr, end="", flush=True)
        return input()
    return input(prompt)


def terminal_print(*args, **kwargs):
    if MACHINE_MODE:
        kwargs["file"] = sys.stderr
    print(*args, **kwargs)


def log(msg):
    print(msg, file=sys.stderr if MACHINE_MODE else sys.stdout)
    for f in LOG_FILES:
        f.write(str(msg) + "\n")
        f.flush()


def load_team(team_name):
    """加载队伍 JSON。
    都会优先从 data/teams/ 下查找
    返回完整 JSON 配置（含 team、resonance 等字段）。
    """
    root = Path(os.path.dirname(os.path.abspath(__file__)))
    candidates = []
    if Path(team_name).is_absolute():
        candidates.append(Path(team_name))
    else:
        candidates.append(root / team_name)
        if team_name.endswith(".json"):
            candidates.append(root / "data" / "teams" / team_name)
        else:
            candidates.append(root / "data" / "teams" / f"{team_name}.json")
    for path in candidates:
        if path.is_file():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            validate_team_config(data, source=f"队伍文件 {path}")
            return data
    raise SystemExit(f"找不到队伍文件：{candidates[-1]}")


def choose_lead(team, opponent_team):
    log("\n对方队伍：")
    for i, name in enumerate(opponent_team):
        log(f"  {i + 1}: {name}")
    log("选择首发精灵：")
    for i, cfg in enumerate(team):
        log(f"  {i + 1}: {cfg['spirit']}")
    while True:
        value = terminal_input("请输入首发编号 (1-6): ").strip()
        if value.isdigit() and 1 <= int(value) <= len(team):
            return int(value) - 1
        log("无效编号，请重新输入")


def print_state(state):
    if AUTO_MODE:
        log(
            f"回合 {state['turn']}  魔力 A:{state['magic']['A']} B:{state['magic']['B']}"
        )
        return
    log("\n" + "=" * 56)
    log(f"回合 {state['turn']}  魔力 A:{state['magic']['A']} B:{state['magic']['B']}")
    for side in ("A", "B"):
        active_idx = state["active"][side]
        log(f"[{side}]")
        side_marks = state.get("marks", {}).get(side, {})
        pos = side_marks.get("positive")
        neg = side_marks.get("negative")
        pos_text = f"正:{pos['id']}x{pos['stacks']}" if pos else "正:无"
        neg_text = f"负:{neg['id']}x{neg['stacks']}" if neg else "负:无"
        log(f"  印记 {pos_text} {neg_text}")
        for i, pet in enumerate(state["teams"][side]):
            marker = ">" if i == active_idx else " "
            speed_text = "?"
            if side == MY_SIDE:
                eff = pet.get("eff_speed")
                if eff is not None:
                    speed_text = str(eff)
                else:
                    speed = pet["stats"]["speed"]
                    speed_percent = 0
                    for buff in pet.get("buffs", []):
                        if buff["type"] == "speed":
                            speed += buff["value"] * 10
                        elif buff["type"] == "speed_percent":
                            speed_percent += buff["value"]
                    neg_mark = state.get("marks", {}).get(side, {}).get("negative")
                    if neg_mark is not None and neg_mark["id"] == 3:
                        speed -= 10 * neg_mark["stacks"]
                    speed = int(speed * (1.0 + speed_percent * 0.1))
                    speed_text = str(speed)
            elif pet.get("speed_range"):
                eff_range = pet.get("eff_speed_range")
                if eff_range is not None:
                    speed_text = f"{eff_range[0]}~{eff_range[1]}"
                else:
                    speed_mod = 0
                    speed_percent = 0
                    for buff in pet.get("buffs", []):
                        if buff["type"] == "speed":
                            speed_mod += buff["value"] * 10
                        elif buff["type"] == "speed_percent":
                            speed_percent += buff["value"]
                    neg_mark = state.get("marks", {}).get(side, {}).get("negative")
                    if neg_mark is not None and neg_mark["id"] == 3:
                        speed_mod -= 10 * neg_mark["stacks"]
                    lo = int(
                        (pet["speed_range"][0] + speed_mod)
                        * (1.0 + speed_percent * 0.1)
                    )
                    hi = int(
                        (pet["speed_range"][1] + speed_mod)
                        * (1.0 + speed_percent * 0.1)
                    )
                    speed_text = f"{lo}~{hi}"
            if side == MY_SIDE:
                hp_text = f"HP {pet['hp']}/{pet['max_hp']}"
            else:
                hp_text = f"HP {pet.get('hp_percent', 0)}%"
            log(
                f" {marker} #{i + 1} {pet['name']} {hp_text} 能量 {pet['energy']} 速 {speed_text}"
            )
            # 特性效果行在最前（服务端算好，如 +双攻 * 3 (20%/trait)）
            for eff in pet.get("trait_effects") or []:
                sign = "+" if eff.get("gain", True) else "-"
                log(f"     {sign}{eff['name']} * {eff['layers']} ({eff['per']}/trait)")
            # 普通 buff（特性来源的已由 trait_effects 展示，这里跳过）
            merged = {}
            for buff in pet.get("buffs", []):
                if buff.get("source_kind") == "trait":
                    continue
                btype = buff["type"]
                merged[btype] = merged.get(btype, 0) + buff["value"]
            for line in render_buffs(merged):
                log(f"     {line}")
            for effect in pet.get("bursts", []):
                if effect["type"] == "skill_use_count":
                    log(f"     使用次数 +{effect['value']}")
            for skill in pet["skills"]:
                elem = (
                    ELEMENT_NAMES[skill["element"]]
                    if skill.get("element") is not None
                    else "?"
                )
                if "skill_id" not in skill:
                    log(
                        f"     对方用过: {skill['name']} [{elem}] 能耗{skill['energy_cost']}"
                    )
                    continue
                display = skill.get("display_power")
                display_text = f" 显示威力 {display}" if display is not None else ""
                log(
                    f"     技能{skill['index'] + 1}: {skill['name']} [{elem}] 能耗{skill['energy_cost']}{display_text} "
                    f"{CATEGORY_NAMES[skill['category']]}"
                )
    log("=" * 56)


def render_buffs(merged):
    """把普通 buff（type -> 层数）渲染成显示行：统计类联合显示、中文名、格式 +物攻 * 6 (10%/buff)。"""
    info_map = buff_info()
    lines = []
    # 统计类联合：层数相同的 atk/spatk/def/spdef 合并为 双攻/双防/物攻&物防 等
    stat_family = ("atk", "spatk", "def", "spdef")
    stats = {t: v for t, v in merged.items() if t in stat_family}
    if len(stats) >= 2 and len(set(stats.values())) == 1:
        value = next(iter(stats.values()))
        types = tuple(sorted(stats))
        if types == ("atk", "spatk"):
            name = "双攻"
        elif types == ("def", "spdef"):
            name = "双防"
        elif types == ("atk", "def"):
            name = "物攻&物防"
        elif types == ("spatk", "spdef"):
            name = "魔攻&魔防"
        else:
            name = "&".join(BUFF_NAMES[t] for t in types)
        lines.append((name, value, types[0]))
        merged = {t: v for t, v in merged.items() if t not in stats}
    for btype, value in sorted(merged.items()):
        lines.append((buff_display_name(btype), value, btype))
    out = []
    for name, value, ctype in lines:
        tag = buff_classify(ctype, value)
        if ctype in ("energy_cost", "attack_energy_cost"):
            sign = "+" if value > 0 else "-"
        else:
            sign = "+" if tag == "buff" else "-"
        info = info_map.get(ctype, {})
        per = info.get("per_layer")
        if per is None:
            per_text = ""
        elif info.get("unit") == "percent":
            per_text = f"{per}%"
        else:
            per_text = str(per)
        out.append(f"{sign}{name} * {abs(value)} ({per_text}/{tag})")
    return out


def can_use_resonance(state):
    magic_id = state.get("resonance_magic", {}).get(MY_SIDE)
    if magic_id is None:
        return False
    key = str(magic_id)
    used = state.get("resonance_usage", {}).get(MY_SIDE, {}).get(key, 0)
    limit = 2 if magic_id == 0 else 1
    if used >= limit:
        return False
    cooldown = state.get("resonance_cooldown", {}).get(MY_SIDE, {}).get(key, 0)
    return cooldown <= 0


def show_resonance_info(state):
    magic_id = state.get("resonance_magic", {}).get(MY_SIDE)
    if magic_id is None:
        log("当前未携带共鸣魔法")
        return
    names = {0: "愿力冲击", 1: "进化之力", 2: "光合治愈"}
    limits = {0: 2, 1: 1, 2: 1}
    key = str(magic_id)
    used = state.get("resonance_usage", {}).get(MY_SIDE, {}).get(key, 0)
    cooldown = state.get("resonance_cooldown", {}).get(MY_SIDE, {}).get(key, 0)
    log(
        f"共鸣魔法: {names.get(magic_id, magic_id)} 剩余 {max(0, limits.get(magic_id, 1) - used)} 次 冷却 {cooldown}"
    )


def show_bench(state):
    log("场下精灵：")
    for i, pet in enumerate(state["teams"][MY_SIDE]):
        if i == state["active"][MY_SIDE]:
            continue
        status = "存活" if pet["hp"] > 0 else "无法战斗"
        log(f"  {i + 1}: {pet['name']} HP {pet['hp']}/{pet['max_hp']} {status}")


def choose_lord_branch(state):
    pet = state["teams"][MY_SIDE][state["active"][MY_SIDE]]
    spirits = json.load(
        open(
            Path(os.path.dirname(os.path.abspath(__file__))) / "data" / "spirits.json",
            encoding="utf-8",
        )
    )["spirits"]
    spirit = next((x for x in spirits if x["id"] == pet["spirit_id"]), None)
    if spirit is None:
        return 0
    branches = []
    for chain in spirit.get("evolution") or []:
        for b in chain.get("lordBranches") or []:
            branches.append(b["name"])
    if not branches:
        return 0
    terminal_print("可选择的首领形态：")
    for i, name in enumerate(branches):
        terminal_print(f"  {i + 1}: {name}")
    while True:
        value = terminal_input("请输入编号: ").strip()
        if value.isdigit() and 1 <= int(value) <= len(branches):
            return int(value) - 1
        terminal_print("无效编号")


def choose_branch(skill):
    """选择技能（choice=True）的分支提示。

    返回 0=明 / 1=暗；无默认，必须显式选择。
    直接回车返回 None，表示返回去改选其他技能。
    """
    while True:
        value = terminal_input(
            f"选择 {skill.get('name', '技能')} 的效果 (0=明, 1=暗, 回车=返回): "
        ).strip()
        if value == "":
            return None
        if value in ("0", "1"):
            return int(value)
        terminal_print("无效选择，请输入 0/1 或直接回车返回")


def prompt_action(conn, state):
    magic_id = None
    magic_branch = 0
    while True:
        current_pet = state["teams"][MY_SIDE][state["active"][MY_SIDE]]
        skill_limit = min(
            7, len(current_pet.get("skills", [])) + (3 if magic_id == 1 else 0)
        )
        action = terminal_input(
            f"输入 (W1=共鸣, X=聚能, 1-{skill_limit}=技能, E<0-9>=换人, esc=逃跑): "
        ).strip()
        lower = action.lower()
        if lower == "w":
            show_resonance_info(state)
            continue
        if lower == "e":
            show_bench(state)
            continue
        if lower == "w1":
            if not can_use_resonance(state):
                log("共鸣魔法不可用")
                continue
            magic_id = state.get("resonance_magic", {}).get(MY_SIDE)
            if magic_id == 1:
                pet = state["teams"][MY_SIDE][state["active"][MY_SIDE]]
                if pet.get("bloodline") != LORD_BLOODLINE:
                    log("当前精灵不是首领血脉，无法首领化")
                    continue
                magic_branch = choose_lord_branch(state)
            log(f"操作: 共鸣魔法 {magic_id}")
            continue
        if lower == "x":
            log("操作: 聚能")
            send_line(
                conn,
                {"kind": "charge", "magic_id": magic_id, "magic_branch": magic_branch},
            )
            return
        if action.isdigit() and 1 <= int(action) <= skill_limit:
            idx = int(action) - 1
            pet = state["teams"][MY_SIDE][state["active"][MY_SIDE]]
            # 进化之力追加的技能尚未出现在本地快照中，冷却检查交由服务端/引擎处理。
            if idx < len(pet["skills"]):
                skill = pet["skills"][idx]
                if (
                    skill.get("skill_id") in pet.get("skill_cooldowns", [])
                    and not (magic_id == 0 and idx == 0)
                ):
                    log(f"技能冷却中：{skill['name']}")
                    continue
            choice_branch = 0
            if (
                idx < len(pet["skills"])
                and pet["skills"][idx].get("choice")
                and not (magic_id == 0 and idx == 0)
            ):
                branch = choose_branch(pet["skills"][idx])
                if branch is None:
                    # 玩家在选择效果时返回，改选其他技能。
                    continue
                choice_branch = branch
            log(f"操作: 技能{int(action)}")
            send_line(
                conn,
                {
                    "kind": "skill",
                    "skill_index": idx,
                    "magic_id": magic_id,
                    "magic_branch": magic_branch,
                    "choice_branch": choice_branch,
                },
            )
            return
        if lower.startswith("e") and not lower.startswith("esc"):
            idx_text = action[1:].strip()
            if idx_text.isdigit() and 1 <= int(idx_text) <= 6:
                idx = int(idx_text) - 1
                target = state["teams"][MY_SIDE][idx]
                if idx == state["active"][MY_SIDE]:
                    log("当前精灵已经在场上")
                    continue
                if target["hp"] <= 0:
                    log(f"该精灵已无法战斗：{target['name']}")
                    continue
                log(f"操作: 换人 {idx_text}")
                send_line(
                    conn,
                    {
                        "kind": "switch",
                        "pet_index": idx,
                        "magic_id": magic_id,
                        "magic_branch": magic_branch,
                    },
                )
                return
        if lower in ("esc", "escape"):
            log("操作: 逃跑")
            send_line(
                conn,
                {"kind": "flee", "magic_id": magic_id, "magic_branch": magic_branch},
            )
            return
        log(f"无效输入，请输入 W1 / X / 1-{skill_limit} / E<0-9> / esc")


def prompt_replacement(conn, state):
    while True:
        action = terminal_input("选择上场的精灵 (E <0-9>): ").strip()
        lower = action.lower()
        if lower.startswith("e"):
            idx_text = action[1:].strip()
        elif action.isdigit():
            idx_text = action
        else:
            idx_text = ""
        if idx_text.isdigit() and 1 <= int(idx_text) <= 6:
            idx = int(idx_text) - 1
            target = state["teams"][MY_SIDE][idx]
            if target["hp"] <= 0:
                log(f"该精灵已无法战斗：{target['name']}")
                continue
            if idx == state["active"][MY_SIDE]:
                log("该精灵已经在场上")
                continue
            log(f"操作: 换人 {idx_text}")
            send_line(conn, {"kind": "switch", "pet_index": idx})
            return
        log("请输入 E<0-9> 或直接输入编号")


def auto_action(conn, state, side):
    idx = state["active"][side]
    if idx < 0:
        return
    pet = state["teams"][side][idx]
    if pet["hp"] / pet["max_hp"] < 0.25:
        candidates = [
            i
            for i, p in enumerate(state["teams"][side])
            if i != idx and p["hp"] > 0 and p["hp"] / p["max_hp"] > 0.5
        ]
        if candidates and random.random() < 0.3:
            target = random.choice(candidates)
            log(f"操作: 换人 {target + 1}")
            send_line(conn, {"kind": "switch", "pet_index": target})
            return
    usable = [
        i
        for i, skill in enumerate(pet["skills"])
        if skill.get("power") is not None
        and skill.get("category") in (0, 1)
        and skill.get("energy_cost", 0) <= pet["energy"]
    ]
    if usable:
        best = max(
            usable,
            key=lambda i: pet["skills"][i].get("display_power")
            or pet["skills"][i].get("power")
            or 0,
        )
        log(f"操作: 技能{best + 1}")
        send_line(conn, {"kind": "skill", "skill_index": best})
    else:
        log("操作: 聚能")
        send_line(conn, {"kind": "charge"})


def auto_replace(conn, state, side):
    current = state["active"][side]
    alive = [
        i
        for i, pet in enumerate(state["teams"][side])
        if pet["hp"] > 0 and i != current
    ]
    if not alive:
        alive = [i for i, pet in enumerate(state["teams"][side]) if pet["hp"] > 0]
    if alive:
        idx = random.choice(alive)
        log(f"操作: 换人 {idx}")
        send_line(conn, {"kind": "switch", "pet_index": idx})


def init_auto_agent(kind, checkpoint):
    global AUTO_ENCODER, AUTO_MODEL, AUTO_TEACHER, AUTO_DEVICE
    from alpha_roco.features import ObservationEncoder

    AUTO_ENCODER = ObservationEncoder()
    if kind == "teacher":
        from alpha_roco.teacher import Teacher

        AUTO_TEACHER = Teacher(AUTO_ENCODER)
        return
    import torch

    from alpha_roco.arena import load_model

    AUTO_DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    torch.set_num_threads(1)
    AUTO_MODEL = load_model(Path(checkpoint), AUTO_ENCODER, AUTO_DEVICE)


def auto_agent_action(request):
    if AUTO_AGENT == "teacher":
        return AUTO_TEACHER.choose(request)
    if AUTO_AGENT == "random":
        candidates = [
            candidate
            for candidate in request.get("candidates", [])
            if candidate.get("kind") != "flee"
        ]
        return random.choice(candidates)
    from alpha_roco.arena import choose

    action, AUTO_MEMORIES[request["side"]] = choose(
        AUTO_MODEL,
        AUTO_ENCODER,
        request,
        AUTO_MEMORIES[request["side"]],
        AUTO_DEVICE,
        sample=False,
    )
    return action


def auto_lead(team, opponent_team):
    if AUTO_AGENT == "random":
        return random.randrange(len(team))
    request = {
        "side": MY_SIDE,
        "phase": "lead",
        "team": team,
        "state": {"teams": {MY_SIDE: []}},
        "candidates": [
            {"kind": "lead", "lead": index, "pet_index": index}
            for index in range(len(team))
        ],
        "opponent_team": opponent_team,
    }
    action = auto_agent_action(request)
    return int(action.get("lead", action.get("pet_index", 0)))


def main():
    parser = argparse.ArgumentParser(description="Headless Roco 6v6 client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--nature", type=int, default=-1)
    parser.add_argument(
        "--auto",
        nargs="?",
        const="best",
        choices=("best", "teacher", "random"),
        help="自动操作：best 使用 logs/az/best.pt，teacher 使用规则 teacher",
    )
    parser.add_argument("--checkpoint", default="logs/az/best.pt")
    parser.add_argument("--machine", action="store_true")
    parser.add_argument("--log-dir")
    parser.add_argument(
        "--team", help="必须指定队伍文件路径或 data/teams 下的名字（如 冻陨2.0）"
    )
    args = parser.parse_args()
    if not args.team:
        parser.error(
            "必须通过 --team 指定队伍文件，例如 --team data/teams/冻陨2.0.json"
        )
    # random.seed(42)
    global AUTO_MODE, MACHINE_MODE, AUTO_AGENT
    AUTO_MODE = bool(args.auto)
    AUTO_AGENT = args.auto
    MACHINE_MODE = args.machine
    if args.auto and args.auto != "random":
        init_auto_agent(args.auto, args.checkpoint)
    team_data = load_team(args.team)
    team = team_data.get("team")
    resonance = team_data.get("resonance")

    conn = socket.create_connection((args.host, args.port))
    f = conn.makefile("r", encoding="utf-8")
    awaiting_replacement = False
    current_state = None
    my_side = None
    last_printed_turn = None
    log(f"connected to {args.host}:{args.port}")

    while True:
        msg = read_line(f)
        if msg is None:
            log("server closed")
            break
        msg_type = msg.get("type")
        if msg_type == "welcome":
            side = msg["side"]
            global MY_SIDE
            MY_SIDE = side
            my_side = side
            if args.machine:
                print(json.dumps({"type": "welcome", "side": side}), flush=True)
            logs_dir = (
                Path(args.log_dir)
                if args.log_dir
                else Path(os.path.dirname(os.path.abspath(__file__))) / "logs" / "battle"
            )
            if args.machine and args.log_dir is None:
                logs_dir = None
            if logs_dir is not None:
                logs_dir.mkdir(parents=True, exist_ok=True)
                log_name = "clientA.log" if side == "A" else "clientB.log"
                client_log = (logs_dir / log_name).open("w", encoding="utf-8")
                LOG_FILES.append(client_log)

            log(f"你是 {side} 方")
            if team is None:
                resonance = None
            # 共鸣魔法不再开局选择，直接从队伍 JSON 的 resonance 字段读取
            send_line(
                conn,
                {
                    "type": "config",
                    "nature": args.nature,
                    "team": team,
                    "resonance": resonance,
                    "auto": args.auto,
                },
            )
        elif msg_type == "choose_lead":
            opponent_team = msg.get("opponent_team") or []
            if team is None:
                lead = 0
            elif args.auto:
                lead = auto_lead(team, opponent_team)
                log(f"自动选择首发：{lead + 1}")
            else:
                lead = choose_lead(team, opponent_team)
            send_line(conn, {"type": "lead", "lead": lead})
        elif msg_type == "decision":
            request = msg["request"]
            if args.machine:
                print(json.dumps({"type": "decision", "request": request}, ensure_ascii=False), flush=True)
            my_side = request["side"]
            MY_SIDE = my_side
            current_state = request["state"]
            team = request["team"]
            if args.auto:
                action = auto_agent_action(request)
                if request["phase"] == "lead":
                    send_line(
                        conn,
                        {
                            "lead": int(
                                action.get("lead", action.get("pet_index", 0))
                            )
                        },
                    )
                else:
                    awaiting_replacement = request["phase"] == "replacement"
                    send_line(conn, action)
            elif request["phase"] == "lead":
                lead_line = terminal_input("选择首发编号 (1-based): ").strip()
                send_line(conn, {"lead": int(lead_line) - 1})
            elif request["phase"] == "replacement":
                awaiting_replacement = True
                prompt_replacement(conn, current_state)
            else:
                awaiting_replacement = False
                if last_printed_turn != current_state["turn"]:
                    print_state(current_state)
                    last_printed_turn = current_state["turn"]
                prompt_action(conn, current_state)
        elif msg_type == "choose_replacement":
            awaiting_replacement = True
            if args.auto and current_state is not None:
                auto_replace(conn, current_state, my_side)
            else:
                prompt_replacement(conn, current_state)
        elif msg_type == "state":
            awaiting_replacement = False
            current_state = msg["state"]
            if args.machine:
                continue
            if current_state.get("winner"):
                continue
            if current_state["active"]["A"] < 0 or current_state["active"]["B"] < 0:
                continue
            if last_printed_turn != current_state["turn"]:
                print_state(current_state)
                last_printed_turn = current_state["turn"]
            if args.auto:
                auto_action(conn, current_state, my_side)
            else:
                prompt_action(conn, current_state)
        elif msg_type == "error":
            if args.machine:
                print(json.dumps({"type": "protocol_error", "side": my_side, "message": msg.get("message", "操作无效")}), flush=True)
                break
            log(msg.get("message", "操作无效，请重新输入"))
            if args.auto:
                if awaiting_replacement and current_state is not None:
                    auto_replace(conn, current_state, my_side)
                else:
                    auto_action(conn, current_state, my_side)
            elif awaiting_replacement:
                prompt_replacement(conn, current_state)
            else:
                prompt_action(conn, current_state)
        elif msg_type == "game_over":
            if args.machine:
                print(json.dumps({"type": "game_over", "winner": msg["winner"]}), flush=True)
            if msg["winner"] == "both_lose":
                log("游戏结束，双方均失败")
            else:
                log(f"游戏结束，胜者：{msg['winner']}" )
            break

    conn.close()
    for f in LOG_FILES:
        f.close()


if __name__ == "__main__":
    main()
