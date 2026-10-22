import json
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path

from server import make_team
from sim.battle import MAX_TURN, create_team_battle, state_to_dict, step
from sim.data_loader import load_spirits
from sim.models import Action


ROOT = Path(__file__).resolve().parents[1]


def battle(team_a=4, team_b=0, active_a=0, active_b=0):
    return create_team_battle(
        make_team("A", json.loads((ROOT / f"data/teams/team{team_a}.json").read_text())["team"], load_spirits()),
        make_team("B", json.loads((ROOT / f"data/teams/team{team_b}.json").read_text())["team"], load_spirits()),
        active_a=active_a,
        active_b=active_b,
    )


def hp_ratio(state, side):
    return sum(Fraction(pet.hp, pet.max_hp) for pet in state.teams[side] if pet.hp > 0)


def finish(state):
    for _ in range(MAX_TURN):
        if state.winner is not None:
            return
        step(state, Action("charge"), Action("charge"))
    assert state.winner is not None


class TurnLimitTests(unittest.TestCase):
    def test_equal_percentages_make_both_sides_lose(self):
        state = battle()
        self.assertNotEqual(
            sum(pet.hp for pet in state.teams["A"]),
            sum(pet.hp for pet in state.teams["B"]),
        )
        finish(state)
        self.assertEqual(state.magic["A"], state.magic["B"])
        self.assertEqual(hp_ratio(state, "A"), hp_ratio(state, "B"))
        self.assertEqual("both_lose", state.winner)
        self.assertIn("双方均失败", state.log[-1])
        for view in ("A", "B"):
            self.assertEqual("both_lose", state_to_dict(state, view)["winner"])

    def test_percentage_total_overrides_absolute_hp(self):
        for side in ("A", "B"):
            with self.subTest(side=side):
                state = battle(active_a=3) if side == "A" else battle(0, 4, active_b=3)
                skill = Action("skill", skill_index=2)
                charge = Action("charge")
                step(state, skill if side == "A" else charge, skill if side == "B" else charge)
                enemy = "B" if side == "A" else "A"
                self.assertLess(
                    sum(pet.hp for pet in state.teams[side]),
                    sum(pet.hp for pet in state.teams[enemy]),
                )
                self.assertGreater(hp_ratio(state, side), hp_ratio(state, enemy))
                finish(state)
                self.assertEqual(state.magic["A"], state.magic["B"])
                self.assertEqual(side, state.winner)
                self.assertIn("血量百分比总和", state.log[-1])

    def test_magic_has_priority(self):
        state = battle()
        for action in ("skill", "skill", "charge", "skill"):
            step(state, Action(action, skill_index=2 if action == "skill" else None), Action("charge"))
        self.assertEqual(0, state.teams["B"][0].hp)
        self.assertGreater(state.magic["A"], state.magic["B"])
        finish(state)
        self.assertEqual("A", state.winner)
        self.assertIn("魔力值", state.log[-1])

    def test_server_and_clients_report_both_lose(self):
        with tempfile.TemporaryDirectory(prefix="roco-turn-limit-") as directory:
            root = Path(directory)
            for name in ("sim", "data"):
                shutil.copytree(ROOT / name, root / name)
            for name in ("server.py", "client.py"):
                shutil.copy2(ROOT / name, root / name)
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            process = subprocess.Popen(
                [sys.executable, "-u", "-B", str(root / "server.py"), "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
            )
            self.addCleanup(process.stdout.close)
            self.addCleanup(process.wait, 5)
            self.addCleanup(process.kill)
            self.assertIn("server listening", process.stdout.readline())
            self.assertIn("waiting for client A", process.stdout.readline())
            with ThreadPoolExecutor(max_workers=2) as clients:
                pending = [
                    clients.submit(
                        subprocess.run,
                        [sys.executable, "-u", "-B", str(root / "client.py"), "--port", str(port), "--team", f"team{number}"],
                        input="1\n" + "X\n" * MAX_TURN,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=30,
                    )
                    for number in (4, 0)
                ]
                for result in pending:
                    completed = result.result()
                    self.assertEqual(0, completed.returncode, completed.stderr)
                    self.assertIn("游戏结束，双方均失败", completed.stdout)
                    self.assertNotIn("胜者：both_lose", completed.stdout)
            self.assertEqual(0, process.wait(timeout=10))
            self.assertIn("winner: both_lose", process.stdout.read())


if __name__ == "__main__":
    unittest.main()
