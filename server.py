#!/usr/bin/env python3
"""Headless battle server.

Run:
  python server.py --port 5000

Then run two clients:
  python client.py --port 5000
  python client.py --port 5000
"""

import argparse
import json
import os
import socket
from pathlib import Path

from sim.battle import (
    apply_drive,
    can_use_skill,
    create_team_battle,
    resume_after_leave,
    state_to_dict,
    step,
    switch_in,
    trait_leave_switch,
)
from sim.data_loader import (
    find_spirit,
    load_spirits,
    make_battle_pet,
    validate_team_config,
)
from sim.models import Action


def send_line(conn, obj):
    conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


def recv_line(f):
    line = f.readline()
    if not line:
        return None
    return json.loads(line)


LOG_FILES = []


def log(msg):
    print(msg)
    for f in LOG_FILES:
        f.write(str(msg) + "\n")
        f.flush()


def is_valid_switch(raw, state, side):
    if raw is None or raw.get("kind") != "switch":
        return False
    idx = raw.get("pet_index")
    if idx is None:
        return False
    team = state.teams[side]
    if not (0 <= idx < len(team)):
        return False
    return team[idx].hp > 0


def read_action_with_switch(f, conn, state, side):
    while True:
        raw = recv_line(f)
        if raw is None:
            return None
        if state.active[side] >= 0 or is_valid_switch(raw, state, side):
            return raw
        send_line(
            conn,
            {"type": "error", "message": "请选择一只存活的上场精灵 (switch <0-5>)"},
        )


def read_replacement(f, conn, state, side):
    while True:
        raw = recv_line(f)
        if raw is None:
            return None
        if is_valid_switch(raw, state, side):
            state.active[side] = raw["pet_index"]
            pet = state.teams[side][state.active[side]]
            state.log.append(f"{side} 换上 {pet.name}")
            log(f"{side} 换上 {pet.name}")
            switch_in(state, side, pet, state.log, thorn=False, active_switch=False)
            apply_drive(state, side)
            return raw
        send_line(
            conn,
            {"type": "error", "message": "请选择一只存活的上场精灵 (switch <0-5>)"},
        )


def read_trait_replacement(f, conn, state, side):
    """特性请求的脱离换人（警惕等）：玩家必须选一只与当前不同的存活精灵。"""
    while True:
        raw = recv_line(f)
        if raw is None:
            return None
        if is_valid_switch(raw, state, side) and raw["pet_index"] != state.active[side]:
            incoming = state.teams[side][raw["pet_index"]]
            trait_leave_switch(state, side, incoming, state.log)
            apply_drive(state, side)
            log(f"{side} 因特性脱离换上 {incoming.name}")
            return raw
        send_line(
            conn,
            {
                "type": "error",
                "message": "请选择一只存活且不同的上场精灵 (switch <0-5>)",
            },
        )


def read_leave_replacement(f, conn, state, side):
    """技能脱离的换人（移花接木/吓退等）：只读取并校验玩家选择，不执行换人。

    真正的入场与回合续接由 battle.resume_after_leave 完成。
    必须选一只存活、且不是刚离场那只（paused_turn["leaver_index"]）的精灵。
    """
    leaver_index = (
        state.paused_turn.get("leaver_index", -1) if state.paused_turn else -1
    )
    while True:
        raw = recv_line(f)
        if raw is None:
            return None
        if is_valid_switch(raw, state, side) and raw["pet_index"] != leaver_index:
            return raw
        send_line(
            conn,
            {"type": "error", "message": "请选择一只存活的上场精灵 (switch <0-5>)"},
        )


def is_valid_normal_switch(raw, state, side):
    if raw is None or raw.get("kind") != "switch":
        return True
    idx = raw.get("pet_index")
    if idx is None or state.active[side] < 0:
        return False
    team = state.teams[side]
    if not (0 <= idx < len(team)):
        return False
    if idx == state.active[side]:
        return False
    return team[idx].hp > 0


def is_valid_skill_energy(raw, state, side):
    if raw is None or raw.get("kind") != "skill":
        return True
    idx = raw.get("skill_index")
    if idx is None or state.active[side] < 0:
        return False
    team = state.teams[side]
    pet = team[state.active[side]]
    if not (0 <= idx < len(pet.skills)):
        # 进化之力在本回合行动前生效，允许校验尚未出现在当前状态快照中的新增3个技能。
        if raw.get("magic_id") == 1 and 0 <= idx < len(pet.skills) + 3:
            return True
        return False
    skill = pet.skills[idx]
    return can_use_skill(state, side, skill, skill_index=idx)


def read_action_with_energy(f, conn, state, side):
    while True:
        raw = recv_line(f)
        if raw is None:
            return None
        if not isinstance(raw, dict) or raw.get("kind") not in (
            "skill",
            "charge",
            "switch",
            "flee",
        ):
            send_line(
                conn, {"type": "error", "message": "必须显式提供有效行动类型 kind"}
            )
            continue
        if raw["kind"] == "skill" and type(raw.get("skill_index")) is not int:
            send_line(
                conn, {"type": "error", "message": "技能行动必须提供整数 skill_index"}
            )
            continue
        if raw["kind"] == "switch" and type(raw.get("pet_index")) is not int:
            send_line(
                conn, {"type": "error", "message": "换人行动必须提供整数 pet_index"}
            )
            continue
        if is_valid_skill_energy(raw, state, side) and is_valid_normal_switch(
            raw, state, side
        ):
            return raw
        send_line(conn, {"type": "error", "message": "行动不可用，请重新选择"})


def make_team(side, team_config, spirits):
    team = []
    for cfg in team_config:
        spirit = find_spirit(cfg["spirit"], spirits)
        if spirit is None:
            raise SystemExit(f"找不到精灵：{cfg['spirit']}")
        team.append(
            make_battle_pet(
                spirit,
                side,
                ivs=cfg.get("ivs"),
                nature=cfg.get("nature", -1),
                skill_names=cfg.get("skills"),
                bloodline=cfg.get("bloodline"),
            )
        )
    return team


def main():
    parser = argparse.ArgumentParser(description="Headless Roco 6v6 server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)

    args = parser.parse_args()

    logs_dir = Path(os.path.dirname(os.path.abspath(__file__))) / "logs" / "battle"
    logs_dir.mkdir(parents=True, exist_ok=True)
    server_log = open(logs_dir / "server.log", "w", encoding="utf-8")
    LOG_FILES.extend([server_log])

    spirits = load_spirits()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(2)
    log(f"server listening on {args.host}:{args.port}")

    log("waiting for client A...")
    conn_a, addr_a = srv.accept()
    f_a = conn_a.makefile("r", encoding="utf-8")
    send_line(conn_a, {"type": "welcome", "side": "A"})
    log(f"client A connected: {addr_a}")

    log("waiting for client B...")
    conn_b, addr_b = srv.accept()
    f_b = conn_b.makefile("r", encoding="utf-8")
    send_line(conn_b, {"type": "welcome", "side": "B"})
    log(f"client B connected: {addr_b}")

    log("waiting for client configs...")
    raw_config_a = recv_line(f_a)
    raw_config_b = recv_line(f_b)

    team_a = raw_config_a.get("team") if raw_config_a else None
    team_b = raw_config_b.get("team") if raw_config_b else None
    resonance_a = raw_config_a.get("resonance") if raw_config_a else None
    resonance_b = raw_config_b.get("resonance") if raw_config_b else None

    if team_a is not None:
        validate_team_config(
            {"team": team_a, "resonance": resonance_a}, source="A 方队伍配置"
        )
    if team_b is not None:
        validate_team_config(
            {"team": team_b, "resonance": resonance_b}, source="B 方队伍配置"
        )

    if not team_a or not team_b:
        raise SystemExit("双方客户端都必须提交队伍配置")

    pets_a = make_team("A", team_a, spirits)
    pets_b = make_team("B", team_b, spirits)

    send_line(
        conn_a, {"type": "choose_lead", "opponent_team": [p.name for p in pets_b]}
    )
    send_line(
        conn_b, {"type": "choose_lead", "opponent_team": [p.name for p in pets_a]}
    )
    raw_lead_a = recv_line(f_a)
    raw_lead_b = recv_line(f_b)
    lead_a = raw_lead_a.get("lead", 0) if raw_lead_a else 0
    lead_b = raw_lead_b.get("lead", 0) if raw_lead_b else 0

    state = create_team_battle(pets_a, pets_b, active_a=lead_a, active_b=lead_b)
    state.resonance_magic["A"] = resonance_a
    state.resonance_magic["B"] = resonance_b

    send_line(conn_a, {"type": "state", "state": state_to_dict(state, view_side="A")})
    send_line(conn_b, {"type": "state", "state": state_to_dict(state, view_side="B")})

    while state.winner is None:
        # 死亡后的换人不占用回合：先处理所有需要上场的替换
        replaced = False
        for side, conn, f in (("A", conn_a, f_a), ("B", conn_b, f_b)):
            if state.active[side] < 0:
                send_line(conn, {"type": "choose_replacement"})
                raw = read_replacement(f, conn, state, side)
                if raw is None:
                    log("client disconnected")
                    break
                replaced = True
        else:
            if replaced:
                payload_a = {
                    "type": "state",
                    "state": state_to_dict(state, view_side="A"),
                }
                payload_b = {
                    "type": "state",
                    "state": state_to_dict(state, view_side="B"),
                }
                send_line(conn_a, payload_a)
                send_line(conn_b, payload_b)

            log(f"\n--- turn {state.turn} waiting actions ---")
            raw_a = read_action_with_energy(f_a, conn_a, state, "A")
            raw_b = read_action_with_energy(f_b, conn_b, state, "B")
            if raw_a is None or raw_b is None:
                log("client disconnected")
                break

            action_a = Action(
                kind=raw_a["kind"],
                skill_index=raw_a.get("skill_index"),
                pet_index=raw_a.get("pet_index"),
                magic_id=raw_a.get("magic_id"),
                magic_branch=raw_a.get("magic_branch", 0),
                choice_branch=raw_a.get("choice_branch", 0),
            )
            action_b = Action(
                kind=raw_b["kind"],
                skill_index=raw_b.get("skill_index"),
                pet_index=raw_b.get("pet_index"),
                magic_id=raw_b.get("magic_id"),
                magic_branch=raw_b.get("magic_branch", 0),
                choice_branch=raw_b.get("choice_branch", 0),
            )
            state = step(state, action_a, action_b)

            for line in state.log:
                log(line)

            # 技能脱离（移花接木/吓退等）：回合中暂停等待选人，选人后续完本回合。
            # 与特性 pending_switch 的区别：它在回合中生效、离场精灵跳过回合末结算。
            while state.paused_turn is not None and state.winner is None:
                lside = state.paused_turn["leave_side"]
                lconn, lf = (conn_a, f_a) if lside == "A" else (conn_b, f_b)
                send_line(lconn, {"type": "choose_replacement"})
                raw = read_leave_replacement(lf, lconn, state, lside)
                if raw is None:
                    log("client disconnected")
                    break
                incoming = state.teams[lside][raw["pet_index"]]
                before = len(state.log)
                state = resume_after_leave(state, lside, incoming)
                for line in state.log[before:]:
                    log(line)

            # 特性请求换人（警惕等）：不占用回合，处理完重新发 state 并继续
            trait_switched = False
            for side, conn, f in (("A", conn_a, f_a), ("B", conn_b, f_b)):
                if state.pending_switch[side]:
                    state.pending_switch[side] = False
                    send_line(conn, {"type": "choose_replacement"})
                    raw = read_trait_replacement(f, conn, state, side)
                    if raw is None:
                        log("client disconnected")
                        break
                    trait_switched = True
            else:
                payload_a = {
                    "type": "state",
                    "state": state_to_dict(state, view_side="A"),
                }
                payload_b = {
                    "type": "state",
                    "state": state_to_dict(state, view_side="B"),
                }
                send_line(conn_a, payload_a)
                send_line(conn_b, payload_b)
                if trait_switched:
                    continue

    if state.winner is not None:
        log(f"winner: {state.winner}")
        payload = {"type": "game_over", "winner": state.winner}
        send_line(conn_a, payload)
        send_line(conn_b, payload)

    conn_a.close()
    conn_b.close()
    srv.close()
    server_log.close()


if __name__ == "__main__":
    main()
