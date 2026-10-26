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
from .features import ObservationEncoder, collate_inputs, selectable_candidates
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


def load_model(
    path: Path, encoder: ObservationEncoder, device: torch.device
) -> AlphaRoco:
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


def _search_logits(output, batch, top_k, risk):
    q = torch.softmax(output["opponent_logits"], dim=-1)
    interactions = output["interactions"]
    expected = (interactions * q[:, None, :]).sum(dim=-1)
    future = interactions.masked_fill(~batch["opponent_mask"][:, None, :], torch.inf)
    indices = q.topk(min(top_k, q.shape[-1]), dim=-1).indices
    worst = (
        future.gather(2, indices.unsqueeze(1).expand(-1, future.shape[1], -1))
        .min(dim=-1)
        .values
    )
    return output["base_logits"] + (1 - risk) * expected + risk * worst


def choose(
    model,
    encoder,
    request,
    memory,
    device,
    sample,
    search=False,
    search_top_k=5,
    search_risk=0.5,
):
    candidates = selectable_candidates(request)
    item = encoder.encode(request)
    batch = collate_inputs([item], device)
    with torch.no_grad():
        output = model(batch, memory=memory)
        logits = output["logits"]
        if search and request["phase"] == "action":
            logits = _search_logits(output, batch, search_top_k, search_risk)
        logits = logits[0].masked_fill(
            ~batch["action_mask"][0], torch.finfo(output["logits"].dtype).min
        )
        if sample:
            index = int(torch.distributions.Categorical(logits=logits).sample().item())
        else:
            index = int(logits.argmax().item())
    return candidates[index], tuple(value.detach() for value in output["memory"])


def _play_game(
    candidate,
    reference,
    encoder,
    game_index,
    seed,
    device,
    sample,
    timeout,
    search,
    search_top_k,
    search_risk,
):
    models = {"A": candidate, "B": reference}
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
                    models[side],
                    encoder,
                    request,
                    memories[side],
                    device,
                    sample,
                    search=search and side == "A",
                    search_top_k=search_top_k,
                    search_risk=search_risk,
                )
                session.submit(side, action)
    if session.winner == "both_lose":
        return "both_lose"
    return "win" if session.winner == "A" else "loss"


def _play_games(
    candidate,
    reference,
    encoder,
    game_indices,
    seed,
    device,
    sample,
    timeout,
    search,
    search_top_k,
    search_risk,
):
    tally = {"win": 0, "loss": 0, "both_lose": 0}
    for game_index in game_indices:
        tally[
            _play_game(
                candidate,
                reference,
                encoder,
                game_index,
                seed,
                device,
                sample,
                timeout,
                search,
                search_top_k,
                search_risk,
            )
        ] += 1
    return tally


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
    search: bool = False,
    search_top_k: int = 5,
    search_risk: float = 0.5,
) -> dict[str, int]:
    if games < 1:
        raise ValueError("games必须为正数")
    if workers < 1:
        raise ValueError("workers必须为正数")
    if search_top_k < 1:
        raise ValueError("search-top-k必须为正数")
    if not 0 <= search_risk <= 1:
        raise ValueError("search-risk必须在[0,1]内")
    worker_count = min(workers, games)
    chunks = [range(index, games, worker_count) for index in range(worker_count)]
    arguments = (
        candidate,
        reference,
        encoder,
        seed,
        device,
        sample,
        timeout,
        search,
        search_top_k,
        search_risk,
    )
    if worker_count == 1:
        return _play_games(*arguments[:3], chunks[0], *arguments[3:])
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(
            executor.map(
                lambda chunk: _play_games(*arguments[:3], chunk, *arguments[3:]),
                chunks,
            )
        )
    tally = {"win": 0, "loss": 0, "both_lose": 0}
    for result in results:
        for outcome in tally:
            tally[outcome] += result[outcome]
    return tally
