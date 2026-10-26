from __future__ import annotations

import argparse
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F

from alpha_roco.arena import TEAMS
from alpha_roco.config import ModelConfig
from alpha_roco.features import ObservationEncoder, selectable_candidates
from alpha_roco.network import AlphaRoco
from alpha_roco.teacher import Teacher
from alpha_roco.terminal import BattleSession
from alpha_roco.train import TrainConfig, _checkpoint, _replay_episode, _set_trainable


def _collect_game(
    encoder: ObservationEncoder,
    teacher: Teacher,
    game_index: int,
    seed: int,
    timeout: float,
) -> dict[str, list[dict[str, Any]]]:
    rng = random.Random(seed + game_index * 0x9E3779B1)
    episodes: dict[str, list[dict[str, Any]]] = {"A": [], "B": []}
    with BattleSession(
        team_a=rng.choice(TEAMS),
        team_b=rng.choice(TEAMS),
        seed=rng.randrange(2**32),
        timeout=timeout,
    ) as session:
        while session.winner is None:
            for request in session.requests():
                side = request["side"]
                candidates = selectable_candidates(request)
                action = teacher.choose(request)
                episodes[side].append(
                    {
                        "item": encoder.encode(request),
                        "action_index": candidates.index(action),
                        "phase": request["phase"],
                        "side": side,
                    }
                )
                session.submit(side, action)
    return episodes


def collect_games(
    encoder: ObservationEncoder,
    teacher: Teacher,
    games: int,
    seed: int,
    workers: int,
    timeout: float = 20,
) -> list[dict[str, list[dict[str, Any]]]]:
    if games < 1 or workers < 1:
        raise ValueError("games与workers必须为正数")
    if min(workers, games) == 1:
        return [
            _collect_game(encoder, teacher, index, seed, timeout)
            for index in range(games)
        ]
    with ThreadPoolExecutor(max_workers=min(workers, games)) as executor:
        return list(
            executor.map(
                lambda index: _collect_game(encoder, teacher, index, seed, timeout),
                range(games),
            )
        )


def train_bc(
    model: AlphaRoco,
    encoder: ObservationEncoder,
    episodes: list[dict[str, list[dict[str, Any]]]],
    config: TrainConfig,
    device: torch.device,
    epochs: int,
    learning_rate: float = 1e-4,
) -> dict[str, float]:
    if epochs < 1:
        raise ValueError("epochs必须为正数")
    _set_trainable(model, {id(parameter) for parameter in model.policy_parameters()})
    optimizer = torch.optim.Adam(model.policy_parameters(), lr=learning_rate)
    total_loss = 0.0
    correct = 0
    samples = 0
    for _ in range(epochs):
        for game in episodes:
            for entries in game.values():
                losses = []
                for entry, _, output in _replay_episode(
                    model, encoder, entries, device
                ):
                    target = torch.tensor([entry["action_index"]], device=device)
                    loss = F.cross_entropy(output["logits"], target)
                    losses.append(loss)
                    total_loss += float(loss.detach())
                    correct += int((output["logits"].argmax(dim=-1) == target).item())
                    samples += 1
                if losses:
                    optimizer.zero_grad(set_to_none=True)
                    torch.stack(losses).mean().backward()
                    torch.nn.utils.clip_grad_norm_(
                        model.policy_parameters(), config.max_grad_norm
                    )
                    optimizer.step()
    for parameter in model.parameters():
        parameter.requires_grad_(True)
    return {
        "ce": total_loss / max(samples, 1),
        "accuracy": correct / max(samples, 1),
        "samples": samples,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="使用教师对局预训练Alpha Roco策略")
    parser.add_argument("--games", type=int, default=256)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--chunk-size", type=int, default=64)
    parser.add_argument("--device", choices=("cpu", "cuda"), default=None)
    parser.add_argument("--output", default="logs/pretrain")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.games < 1 or args.epochs < 1 or args.chunk_size < 1:
        raise ValueError("games、epochs与chunk-size必须为正数")
    device = torch.device(
        args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    )
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    encoder = ObservationEncoder()
    teacher = Teacher(encoder, seed=args.seed)
    model_config = ModelConfig()
    train_config = TrainConfig(
        device=str(device),
        seed=args.seed,
        output=args.output,
        learning_rate=args.learning_rate,
    )
    model = AlphaRoco(model_config, encoder.vocab_sizes).to(device)
    totals = {"ce": 0.0, "accuracy": 0.0, "samples": 0}
    for start in range(0, args.games, args.chunk_size):
        episodes = collect_games(
            encoder,
            teacher,
            min(args.chunk_size, args.games - start),
            args.seed + start * 0x9E3779B1,
            args.workers,
            args.timeout,
        )
        metrics = train_bc(
            model,
            encoder,
            episodes,
            train_config,
            device,
            args.epochs,
            args.learning_rate,
        )
        for key in ("ce", "accuracy"):
            totals[key] += metrics[key] * metrics["samples"]
        totals["samples"] += metrics["samples"]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output / "pretrain.pt"
    _checkpoint(
        checkpoint_path,
        model,
        {
            "policy": torch.optim.Adam(
                model.policy_parameters(), lr=args.learning_rate
            ),
            "opponent": torch.optim.Adam(
                model.opponent_parameters(), lr=args.learning_rate
            ),
        },
        encoder,
        model_config,
        train_config,
        batch_index=0,
        games_seen=args.games,
        best_batch=0,
        best_elo=0.0,
    )
    print(
        f"games={args.games} ce={totals['ce'] / totals['samples']:.4f} "
        f"accuracy={totals['accuracy'] / totals['samples']:.4f} "
        f"checkpoint={checkpoint_path}",
        flush=True,
    )


if __name__ == "__main__":
    main()
