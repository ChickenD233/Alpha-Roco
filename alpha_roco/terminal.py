"""Python-managed terminal battles using the existing simulator processes."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def candidate_commands(request: dict, action: dict) -> list[str]:
    if action not in request["candidates"]:
        raise ValueError("action must be one of the request's original candidates")
    phase = request["phase"]
    if phase == "lead":
        return [str(action.get("lead", action.get("pet_index", 0)) + 1)]
    if phase == "replacement":
        return [f"E{action['pet_index'] + 1}"]
    commands = []
    if action.get("magic_id") is not None:
        commands.append("W1")
        if action["magic_id"] == 1:
            commands.append(str(action.get("magic_branch", 0) + 1))
    if action["kind"] == "skill":
        commands.append(str(action["skill_index"] + 1))
        choices = [
            candidate
            for candidate in request["candidates"]
            if candidate["kind"] == "skill"
            and candidate.get("skill_index") == action["skill_index"]
            and candidate.get("magic_id") == action.get("magic_id")
        ]
        if any(candidate.get("choice_branch") == 1 for candidate in choices):
            commands.append(str(action.get("choice_branch", 0)))
    elif action["kind"] == "charge":
        commands.append("X")
    elif action["kind"] == "switch":
        commands.append(f"E{action['pet_index'] + 1}")
    elif action["kind"] == "flee":
        commands.append("esc")
    else:
        raise ValueError(f"unsupported action kind: {action['kind']!r}")
    return commands


class BattleSession:
    def __init__(
        self,
        team_a="team4",
        team_b="team0",
        seed=0,
        timeout=20,
        *,
        log_dir=None,
        log_jsonl=None,
        host="127.0.0.1",
    ):
        self.team_a = team_a
        self.team_b = team_b
        self.seed = seed
        self.timeout = timeout
        self.requested_log_dir = Path(log_dir) if log_dir is not None else None
        self.log_jsonl = Path(log_jsonl) if log_jsonl is not None else None
        self.host = host
        self.winner = None
        self.processes = []
        self.clients = {}
        self.requests_by_side = {}
        self._buffers = {"A": b"", "B": b""}
        self._messages = {"A": [], "B": []}
        self._selector = selectors.DefaultSelector()
        self._temporary = None
        self._log_file = None
        self._stderr_files = []
        self._closed = False
        self._started_at = None

    def __enter__(self):
        self._started_at = time.monotonic()
        self._start_deadline = None
        self._temporary = tempfile.TemporaryDirectory(prefix="alpha-roco-")
        temp_root = Path(self._temporary.name)
        battle_logs = self.requested_log_dir or temp_root / "logs"
        battle_logs.mkdir(parents=True, exist_ok=True)
        jsonl_path = self.log_jsonl or temp_root / "battle.jsonl"
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = jsonl_path.open("w", encoding="utf-8")
        self._server_log_reader = None
        server_stderr = (battle_logs / "server.stderr.log").open("w", encoding="utf-8")
        self._stderr_files.append(server_stderr)
        server = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-B",
                str(ROOT / "server.py"),
                "--host",
                self.host,
                "--port",
                "0",
                "--machine",
                "--seed",
                str(self.seed),
                "--log-dir",
                str(battle_logs),
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=server_stderr,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.processes.append(server)
        ready = server.stdout.readline()
        if not ready:
            raise RuntimeError("battle server exited before reporting its port")
        ready_message = json.loads(ready)
        port = ready_message["port"]
        for side, team in (("A", self.team_a), ("B", self.team_b)):
            self._stderr_files.append(
                (battle_logs / f"client-{side}.stderr.log").open("w", encoding="utf-8")
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    "-B",
                    str(ROOT / "client.py"),
                    "--host",
                    self.host,
                    "--port",
                    str(port),
                    "--team",
                    str(team),
                    "--machine",
                    "--log-dir",
                    str(battle_logs),
                ],
                cwd=ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr_files[-1],
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
            self.processes.append(process)
            self.clients[side] = process
            self._selector.register(process.stdout, selectors.EVENT_READ, side)
            self._wait_for_welcome(side, process)
        self._write_log({"type": "session", "seed": self.seed, "port": port})
        return self

    def _wait_for_welcome(self, side, process):
        deadline = time.monotonic() + self.timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while b"\n" not in self._buffers[side]:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError(f"client {side} 未及时确认服务端席位")
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError(
                        f"client {side} 在确认席位前退出：{self._stderr_detail(side)}"
                    )
                self._buffers[side] += chunk
        raw, self._buffers[side] = self._buffers[side].split(b"\n", 1)
        welcome = json.loads(raw)
        if welcome != {"type": "welcome", "side": side}:
            raise RuntimeError(f"client {side} 的服务端席位不一致：{welcome}")
        while b"\n" in self._buffers[side]:
            raw, self._buffers[side] = self._buffers[side].split(b"\n", 1)
            if raw.strip():
                self._messages[side].append(json.loads(raw))

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        if self._temporary is not None:
            self._temporary.cleanup()
        return False

    def _write_log(self, value):
        self._log_file.write(json.dumps(value, ensure_ascii=False) + "\n")
        self._log_file.flush()

    def _remaining(self):
        if self._start_deadline is None:
            self._start_deadline = time.monotonic() + self.timeout
        remaining = self._start_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"battle session exceeded {self.timeout} seconds")
        return remaining

    def _read_decision(self):
        for side in ("A", "B"):
            if self._messages[side]:
                message = self._messages[side].pop(0)
                if message.get("type") == "game_over":
                    self.winner = message["winner"]
                    self._write_log(message)
                    return None, None
                if message.get("type") == "protocol_error":
                    self._write_log(message)
                    raise RuntimeError(
                        f"client {side} rejected submitted action: {message}"
                    )
                if message.get("type") == "decision":
                    request = message["request"]
                    self.requests_by_side[request["side"]] = request
                    self._write_log({"type": "request", "request": request})
                    return request["side"], request
        ready = self._selector.select(self._remaining())
        if not ready:
            raise TimeoutError(f"battle session exceeded {self.timeout} seconds")
        for key, _ in ready:
            side = key.data
            chunk = os.read(key.fileobj.fileno(), 65536)
            if not chunk:
                process = self.clients[side]
                if process.poll() is not None:
                    detail = self._stderr_detail(side)
                    raise RuntimeError(
                        f"client {side} exited with {process.returncode}: {detail}"
                    )
                continue
            self._buffers[side] += chunk
            while b"\n" in self._buffers[side]:
                raw, self._buffers[side] = self._buffers[side].split(b"\n", 1)
                if raw.strip():
                    self._messages[side].append(json.loads(raw))
        return self._read_decision()

    def _stderr_detail(self, side):
        path = self.requested_log_dir
        if path is None:
            path = Path(self._temporary.name) / "logs"
        files = [
            path / f"client-{side}.stderr.log",
            path / "server.stderr.log",
            path / "server.log",
        ]
        return " | ".join(
            f"{file.name}: {file.read_text(encoding='utf-8')[-1200:]}"
            for file in files
            if file.is_file()
        )

    def requests(self) -> list[dict]:
        """Collect the current decision group, blocking until it is available."""
        if self.winner is not None:
            return []
        pending = getattr(self, "_pending_request", None)
        if pending is not None:
            self._pending_request = None
            return [pending]
        self._start_deadline = time.monotonic() + self.timeout
        collected = {}
        target_phase = None
        while self.winner is None:
            side, request = self._read_decision()
            if request is None:
                if self.winner is not None:
                    return []
                continue
            phase = request["phase"]
            if target_phase is None:
                target_phase = phase
                collected[side] = request
                if phase == "replacement":
                    return [request]
            elif phase == target_phase and phase != "replacement":
                collected[side] = request
            elif phase == "replacement":
                if collected:
                    self._pending_request = request
                    return list(collected.values())
                return [request]
            if phase == "replacement" or len(collected) == 2:
                return [collected[key] for key in ("A", "B") if key in collected]
        return []

    def submit(self, side: str, action: dict) -> None:
        """Submit a selected candidate using the existing terminal command syntax."""
        if side not in self.clients:
            raise ValueError("side must be 'A' or 'B'")
        request = self.requests_by_side.get(side)
        if request is None:
            raise RuntimeError(f"side {side} has no outstanding decision")
        commands = candidate_commands(request, action)
        phase = request["phase"]
        client = self.clients[side]
        self._start_deadline = time.monotonic() + self.timeout
        for command in commands:
            client.stdin.write(command + "\n")
        client.stdin.flush()
        self.requests_by_side.pop(side)
        self._write_log(
            {
                "type": "submit",
                "side": side,
                "phase": request["phase"],
                "action": action,
            }
        )

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._selector.close()
        for process in self.clients.values():
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
            process.wait()
            process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        for stream in self._stderr_files:
            stream.close()
        if self._log_file is not None:
            self._log_file.close()
