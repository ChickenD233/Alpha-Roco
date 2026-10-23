from __future__ import annotations

import hashlib
import json
import math
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import torch

from .config import ModelConfig
from .features import ObservationEncoder, collate_inputs
from .network import AlphaRoco
from .terminal import BattleSession

TEAMS = tuple(f"team{index}" for index in range(14))


def wilson_interval(wins: int, games: int) -> tuple[float, float]:
    if games == 0:
        return 0.0, 1.0
    z = 1.959963984540054
    rate = wins / games
    denominator = 1 + z * z / games
    center = (rate + z * z / (2 * games)) / denominator
    margin = (
        z
        * math.sqrt(rate * (1 - rate) / games + z * z / (4 * games * games))
        / denominator
    )
    return center - margin, center + margin


def load_model(path: Path, encoder: ObservationEncoder, device: torch.device) -> AlphaRoco:
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    if checkpoint.get("format") != "single" or "model" not in checkpoint:
        raise ValueError(f"checkpoint不是当前单网络格式：{path}")
    signature = hashlib.sha256(
        json.dumps(encoder.vocabularies, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    if checkpoint.get("vocab_sizes") != encoder.vocab_sizes:
        raise ValueError(f"checkpoint词表大小不匹配：{path}")
    if checkpoint.get("vocab_signature") not in (None, signature):
        raise ValueError(f"checkpoint词表签名不匹配：{path}")
    model = AlphaRoco(
        config=ModelConfig(**checkpoint["config"]["model_config"]),
        vocab_sizes=encoder.vocab_sizes,
    ).to(device)
    model.load_state_dict(checkpoint["model"])
    model.eval()
    return model


def choose(model, encoder, request, memory, device, sample):
    item = encoder.encode(request)
    batch = collate_inputs([item], device)
    with torch.no_grad():
        output = model(batch, memory=memory)
        logits = output["logits"][0].masked_fill(
            ~batch["action_mask"][0], torch.finfo(output["logits"].dtype).min
        )
        if sample:
            index = int(torch.distributions.Categorical(logits=logits).sample().item())
        else:
            index = int(logits.argmax().item())
    return request["candidates"][index], tuple(
        value.detach() for value in output["memory"]
    )


def _play_game(
    candidate, reference, encoder, game_index, seed, device, sample, timeout
):
    candidate_side = "A" if game_index % 2 == 0 else "B"
    reference_side = "B" if candidate_side == "A" else "A"
    models = {candidate_side: candidate, reference_side: reference}
    memories: dict[str, Any] = {"A": None, "B": None}
    rng = random.Random(seed + game_index * 0x9E3779B1)
    with BattleSession(
        team_a=rng.choice(TEAMS),
        team_b=rng.choice(TEAMS),
        seed=rng.randrange(2**32),
        timeout=timeout,
    ) as session:
        while session.winner is None:
            for request in session.requests():
                side = request["side"]
                action, memories[side] = choose(
                    models[side], encoder, request, memories[side], device, sample
                )
                session.submit(side, action)
    if session.winner == "both_lose":
        return candidate_side, "both_lose"
    if session.winner == candidate_side:
        return candidate_side, "win"
    return candidate_side, "loss"


def _play_games(
    candidate, reference, encoder, game_indices, seed, device, sample, timeout
):
    tallies = {
        side: {"win": 0, "loss": 0, "both_lose": 0} for side in ("A", "B")
    }
    for game_index in game_indices:
        candidate_side, outcome = _play_game(
            candidate, reference, encoder, game_index, seed, device, sample, timeout
        )
        tallies[candidate_side][outcome] += 1
    return tallies


def play_match(
    candidate: AlphaRoco,
    reference: AlphaRoco,
    encoder: ObservationEncoder,
    games: int,
    seed: int,
    device: torch.device,
    sample: bool,
    timeout: float = 20,
    workers: int = 1,
) -> dict[str, dict[str, int]]:
    if games < 1:
        raise ValueError("games必须为正数")
    if workers < 1:
        raise ValueError("workers必须为正数")
    worker_count = min(workers, games)
    chunks = [range(index, games, worker_count) for index in range(worker_count)]
    if worker_count == 1:
        return _play_games(
            candidate,
            reference,
            encoder,
            chunks[0],
            seed,
            device,
            sample,
            timeout,
        )
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(
            executor.map(
                lambda chunk: _play_games(
                    candidate,
                    reference,
                    encoder,
                    chunk,
                    seed,
                    device,
                    sample,
                    timeout,
                ),
                chunks,
            )
        )
    tallies = {
        side: {"win": 0, "loss": 0, "both_lose": 0} for side in ("A", "B")
    }
    for result in results:
        for side in ("A", "B"):
            for outcome in tallies[side]:
                tallies[side][outcome] += result[side][outcome]
    return tallies
