from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch

from alpha_roco.arena import TEAMS, load_model, play_match, wilson_interval
from alpha_roco.config import ModelConfig
from alpha_roco.features import ObservationEncoder, action_key, collate_inputs
from alpha_roco.losses import (
    generalized_advantage,
    normalize_advantages,
    opponent_cross_entropy,
    ppo_loss,
    terminal_rewards,
)
from alpha_roco.network import AlphaRoco
from alpha_roco.report import write_table
from alpha_roco.terminal import BattleSession


@dataclass
class TrainConfig:
    batches: int = 2
    games_per_batch: int = 28
    ppo_epochs: int = 2
    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    seed: int = 0
    output: str = "logs/az"
    learning_rate: float = 3e-4
    clip_ratio: float = 0.2
    value_coefficient: float = 0.5
    entropy_coefficient: float = 0.01
    gamma: float = 1.0
    gae_lambda: float = 0.95
    max_grad_norm: float = 0.5
    target_kl: float = 0.02
    threads: int = 1
    log_games: bool = False
    keep_checkpoints: int = 1
    eval_interval: int = 10
    eval_games: int = 200
    eval_workers: int = 4
    probe_games: int = 0
    probe_workers: int = 4
    promotion_threshold: float = 0.55
    elo_k: float = 32.0


def _masked_distribution(
    logits: torch.Tensor, mask: torch.Tensor
) -> torch.distributions.Categorical:
    mask = mask.bool()
    if not bool(mask.any(dim=-1).all()):
        raise ValueError("决策请求没有有效候选")
    return torch.distributions.Categorical(
        logits=logits.masked_fill(~mask, torch.finfo(logits.dtype).min)
    )


def _forward(
    model: AlphaRoco,
    encoder: ObservationEncoder,
    item: dict[str, Any],
    memory: Any,
    device: torch.device,
):
    batch = collate_inputs([item], device)
    return batch, model(batch, memory=memory)


def _sample_action(
    model: AlphaRoco,
    encoder: ObservationEncoder,
    request: dict[str, Any],
    memory: Any,
    device: torch.device,
):
    item = encoder.encode(request)
    with torch.no_grad():
        batch, output = _forward(model, encoder, item, memory, device)
        distribution = _masked_distribution(output["logits"], batch["action_mask"])
        action_index = distribution.sample()
        action = request["candidates"][int(action_index.item())]
        old_log_prob = float(distribution.log_prob(action_index).item())
        old_value = float(output["value"].reshape(-1)[0].item())
    return (
        action,
        tuple(value.detach() for value in output["memory"]),
        {
            "item": item,
            "phase": request["phase"],
            "action": int(action_index.item()),
            "old_log_prob": old_log_prob,
            "old_value": old_value,
            "reward": 0.0,
            "terminal": False,
            "opponent_target": None,
        },
    )


def _paired_opponent_targets(
    requests: list[dict[str, Any]],
    actions_by_side: dict[str, dict[str, Any]],
    encoder: ObservationEncoder,
    entries_by_side: dict[str, dict[str, Any]],
) -> None:
    requests_by_side = {request["side"]: request for request in requests}
    if set(requests_by_side) != {"A", "B"}:
        return
    for side, request in requests_by_side.items():
        other_side = "B" if side == "A" else "A"
        other_request = requests_by_side[other_side]
        if (
            request["phase"] != other_request["phase"]
            or request["phase"] == "replacement"
        ):
            continue
        chosen = actions_by_side[other_side]
        if chosen.get("kind") == "skill" and chosen.get("skill_id") is None:
            continue
        chosen_key = action_key(chosen)
        candidates = encoder.opponent_candidates(request)
        matches = [
            index
            for index, candidate in enumerate(candidates)
            if action_key(candidate) == chosen_key
        ]
        if matches:
            entries_by_side[side]["opponent_target"] = matches[0]


def _collect_game(
    encoder: ObservationEncoder,
    model: AlphaRoco,
    team_a: str,
    team_b: str,
    seed: int,
    device: torch.device,
    log_dir: Path | None = None,
) -> dict[str, Any]:
    episodes: dict[str, list[dict[str, Any]]] = {"A": [], "B": []}
    memories: dict[str, Any] = {"A": None, "B": None}
    with BattleSession(
        team_a=team_a,
        team_b=team_b,
        seed=seed,
        timeout=20,
        log_dir=log_dir,
        log_jsonl=log_dir / "battle.jsonl" if log_dir else None,
    ) as session:
        while session.winner is None:
            requests = session.requests()
            actions_by_side: dict[str, dict[str, Any]] = {}
            entries_by_side: dict[str, dict[str, Any]] = {}
            for request in requests:
                side = request["side"]
                action, memories[side], entry = _sample_action(
                    model, encoder, request, memories[side], device
                )
                actions_by_side[side] = action
                entries_by_side[side] = entry
                episodes[side].append(entry)
            _paired_opponent_targets(
                requests, actions_by_side, encoder, entries_by_side
            )
            for side, action in actions_by_side.items():
                session.submit(side, action)
        winner = session.winner
    if winner not in {"A", "B", "both_lose"}:
        raise RuntimeError(f"对局未得到有效终局结果：{winner!r}")
    rewards = terminal_rewards(winner)
    for index, side in enumerate(("A", "B")):
        if not episodes[side]:
            raise RuntimeError(f"{side}方本局没有决策记录")
        episodes[side][0]["reward"] = rewards[index]
        episodes[side][0]["terminal"] = True
        battle_entries = [
            entry for entry in episodes[side] if entry["phase"] != "lead"
        ]
        if battle_entries:
            battle_entries[-1]["reward"] = rewards[index]
            battle_entries[-1]["terminal"] = True
    return {
        "episodes": episodes,
        "winner": winner,
        "rewards": dict(zip(("A", "B"), rewards, strict=True)),
    }


def _prepare_advantages(
    episodes: list[dict[str, Any]], config: TrainConfig, device: torch.device
) -> dict[str, int]:
    counts = {"lead": 0, "battle": 0}
    phase_advantages: dict[str, list[torch.Tensor]] = {"lead": [], "battle": []}
    for episode in episodes:
        lead_entries = [entry for entry in episode if entry["phase"] == "lead"]
        for entry in lead_entries:
            advantage = torch.tensor(
                entry["reward"] - entry["old_value"], device=device
            )
            entry["return"] = torch.tensor(entry["reward"], device=device)
            entry["raw_advantage"] = advantage
            phase_advantages["lead"].append(advantage)
            counts["lead"] += 1
        battle_entries = [entry for entry in episode if entry["phase"] != "lead"]
        if battle_entries:
            rewards = torch.tensor(
                [entry["reward"] for entry in battle_entries], device=device
            )
            values = torch.tensor(
                [entry["old_value"] for entry in battle_entries], device=device
            )
            terminal = torch.tensor(
                [entry["terminal"] for entry in battle_entries], device=device
            )
            advantages, returns = generalized_advantage(
                rewards, values, terminal, config.gamma, config.gae_lambda
            )
            for entry, advantage, target in zip(
                battle_entries, advantages, returns, strict=True
            ):
                entry["return"] = target
                entry["raw_advantage"] = advantage
                phase_advantages["battle"].append(advantage)
                counts["battle"] += 1
    for phase, advantages in phase_advantages.items():
        if not advantages:
            continue
        normalized = normalize_advantages(torch.stack(advantages))
        index = 0
        for episode in episodes:
            for entry in episode:
                if (entry["phase"] == "lead") == (phase == "lead"):
                    entry["advantage"] = normalized[index]
                    index += 1
    return counts


def _set_trainable(model: AlphaRoco, trainable: set[int]) -> None:
    for parameter in model.parameters():
        parameter.requires_grad_(id(parameter) in trainable)


def _replay_episode(
    model: AlphaRoco,
    encoder: ObservationEncoder,
    episode: list[dict[str, Any]],
    device: torch.device,
):
    memory = None
    for entry in episode:
        batch, output = _forward(model, encoder, entry["item"], memory, device)
        memory = output["memory"]
        yield entry, batch, output


def _policy_update(
    model: AlphaRoco,
    optimizer: torch.optim.Optimizer,
    encoder: ObservationEncoder,
    episodes: list[list[dict[str, Any]]],
    config: TrainConfig,
    ppo_epochs: int,
    device: torch.device,
) -> dict[str, float]:
    counts = _prepare_advantages(episodes, config, device)
    policy_ids = {id(parameter) for parameter in model.policy_parameters()}
    opponent_params = list(model.opponent_parameters())
    _set_trainable(model, policy_ids)
    metrics = {
        "policy": 0.0,
        "value": 0.0,
        "entropy": 0.0,
        "kl": 0.0,
        "clip_fraction": 0.0,
        "grad_norm": 0.0,
    }
    completed_epochs = 0
    for _ in range(ppo_epochs):
        optimizer.zero_grad(set_to_none=True)
        sums = {
            key: 0.0 for key in ("policy", "value", "entropy", "kl", "clip_fraction")
        }
        for episode in episodes:
            episode_outputs = _replay_episode(model, encoder, episode, device)
            episode_losses: list[torch.Tensor] = []
            for entry, batch, output in episode_outputs:
                phase = "lead" if entry["phase"] == "lead" else "battle"
                result = ppo_loss(
                    output["logits"],
                    torch.tensor([entry["action"]], device=device),
                    torch.tensor([entry["old_log_prob"]], device=device),
                    entry["advantage"].reshape(1),
                    output["value"].reshape(-1),
                    entry["return"].reshape(1),
                    batch["action_mask"],
                    config.clip_ratio,
                    config.value_coefficient,
                    config.entropy_coefficient,
                )
                episode_losses.append(result["loss"] / max(counts[phase], 1))
                for key in sums:
                    sums[key] += float(result[key].detach().item()) / max(
                        counts[phase], 1
                    )
            if episode_losses:
                torch.stack(episode_losses).sum().backward()
        norm = torch.nn.utils.clip_grad_norm_(
            model.policy_parameters(), config.max_grad_norm
        )
        epoch_kl = sums["kl"]
        if epoch_kl < config.target_kl:
            optimizer.step()
        else:
            optimizer.zero_grad(set_to_none=True)
        completed_epochs += 1
        for key in sums:
            metrics[key] = sums[key]
        metrics["grad_norm"] = float(norm.detach().item())
        if epoch_kl >= config.target_kl:
            break
    _set_trainable(model, {id(parameter) for parameter in opponent_params})
    metrics["epochs"] = float(completed_epochs)
    return metrics


def _opponent_update(
    model: AlphaRoco,
    optimizer: torch.optim.Optimizer,
    encoder: ObservationEncoder,
    episodes: list[list[dict[str, Any]]],
    config: TrainConfig,
    device: torch.device,
) -> dict[str, float]:
    opponent_ids = {id(parameter) for parameter in model.opponent_parameters()}
    _set_trainable(model, opponent_ids)
    target_count = sum(
        entry["opponent_target"] is not None
        for episode in episodes
        for entry in episode
    )
    optimizer.zero_grad(set_to_none=True)
    loss_sum = 0.0
    for episode in episodes:
        losses = []
        for entry, batch, output in _replay_episode(model, encoder, episode, device):
            target = entry["opponent_target"]
            if target is None:
                continue
            loss = opponent_cross_entropy(
                output["opponent_logits"],
                torch.tensor([target], device=device),
                batch["opponent_mask"],
            )
            if not bool(torch.isfinite(loss).all()):
                raise FloatingPointError("对手交叉熵出现非有限值")
            loss_sum += float(loss.detach().item())
            losses.append(loss / max(target_count, 1))
        if losses:
            torch.stack(losses).sum().backward()
    norm = torch.nn.utils.clip_grad_norm_(
        model.opponent_parameters(), config.max_grad_norm
    )
    if target_count:
        optimizer.step()
    _set_trainable(model, {id(parameter) for parameter in model.policy_parameters()})
    return {
        "opponent_ce": loss_sum / max(target_count, 1),
        "opponent_grad_norm": float(norm.detach().item()),
        "opponent_samples": float(target_count),
    }


def _checkpoint(
    path: Path,
    model: AlphaRoco,
    optimizers: dict[str, torch.optim.Optimizer],
    encoder: ObservationEncoder,
    model_config: ModelConfig,
    train_config: TrainConfig,
    batch_index: int,
    games_seen: int,
    best_batch: int,
    best_elo_a: float,
    best_elo_b: float,
    overwrite: bool = False,
) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"拒绝覆盖已有checkpoint：{path}")
    payload = {
        "format": "single",
        "config": {
            "model_config": dataclasses.asdict(model_config),
            "train_config": dataclasses.asdict(train_config),
        },
        "vocab_sizes": encoder.vocab_sizes,
        "vocabularies": encoder.vocabularies,
        "vocab_signature": hashlib.sha256(
            json.dumps(
                encoder.vocabularies, ensure_ascii=False, sort_keys=True
            ).encode()
        ).hexdigest(),
        "model": model.state_dict(),
        "optimizers": {
            name: optimizer.state_dict() for name, optimizer in optimizers.items()
        },
        "batch_index": batch_index,
        "next_batch": batch_index + 1,
        "games_seen": games_seen,
        "best_batch": best_batch,
        "best_elo_a": best_elo_a,
        "best_elo_b": best_elo_b,
        "rng": {
            "python": random.getstate(),
            "torch_cpu": torch.get_rng_state(),
            "torch_cuda": (
                torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None
            ),
        },
    }
    if overwrite:
        temporary = path.with_suffix(path.suffix + ".tmp")
        torch.save(payload, temporary)
        temporary.replace(path)
    else:
        torch.save(payload, path)


def _restore(
    checkpoint: dict[str, Any],
    model: AlphaRoco,
    optimizers: dict[str, torch.optim.Optimizer],
    encoder: ObservationEncoder,
    device: torch.device,
) -> tuple[dict[str, Any], int, int]:
    if checkpoint.get("format") != "single" or "model" not in checkpoint:
        raise ValueError("checkpoint不是当前单网络格式")
    signature = hashlib.sha256(
        json.dumps(encoder.vocabularies, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    if checkpoint["vocab_signature"] != signature:
        raise ValueError("checkpoint词表签名与当前真实数据不一致")
    if checkpoint["vocab_sizes"] != encoder.vocab_sizes:
        raise ValueError("checkpoint词表大小与当前真实数据不一致")
    model.load_state_dict(checkpoint["model"])
    for name, optimizer in optimizers.items():
        optimizer.load_state_dict(checkpoint["optimizers"][name])
    model.to(device)
    for optimizer in optimizers.values():
        for state in optimizer.state.values():
            for key, value in state.items():
                if isinstance(value, torch.Tensor):
                    state[key] = value.to(device)
    random.setstate(checkpoint["rng"]["python"])
    torch.set_rng_state(checkpoint["rng"]["torch_cpu"].cpu())
    if torch.cuda.is_available() and checkpoint["rng"]["torch_cuda"] is not None:
        torch.cuda.set_rng_state_all(checkpoint["rng"]["torch_cuda"])
    return checkpoint, checkpoint["next_batch"], checkpoint["games_seen"]


def _clone_model(
    model: AlphaRoco, model_config: ModelConfig, encoder: ObservationEncoder, device: torch.device
) -> AlphaRoco:
    clone = AlphaRoco(config=model_config, vocab_sizes=encoder.vocab_sizes).to(device)
    clone.load_state_dict(model.state_dict())
    clone.eval()
    return clone


def run(config: TrainConfig, resume: Path | None = None) -> list[Path]:
    if (
        config.batches < 1
        or config.games_per_batch < 1
        or config.ppo_epochs < 1
        or config.keep_checkpoints < 1
    ):
        raise ValueError("batches、games-per-batch、ppo-epochs与keep-checkpoints必须为正数")
    if config.eval_interval < 0:
        raise ValueError("eval-interval不能为负数")
    if config.eval_interval > 0 and config.eval_games < 2:
        raise ValueError("eval-games至少为2")
    if config.eval_workers < 1:
        raise ValueError("eval-workers必须为正数")
    if config.probe_games > 0 and config.probe_games < 2:
        raise ValueError("probe-games至少为2")
    if config.probe_workers < 1:
        raise ValueError("probe-workers必须为正数")
    if not 0 <= config.promotion_threshold <= 1:
        raise ValueError("promotion-threshold必须在[0,1]内")
    random.seed(config.seed)
    torch.manual_seed(config.seed)
    torch.set_num_threads(config.threads)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(config.seed)
    device = torch.device(config.device)
    encoder = ObservationEncoder()
    checkpoint = (
        torch.load(resume, map_location="cpu", weights_only=False)
        if resume is not None
        else None
    )
    model_config = (
        ModelConfig(**checkpoint["config"]["model_config"])
        if checkpoint is not None
        else ModelConfig()
    )
    if checkpoint is not None:
        saved_config = TrainConfig(**checkpoint["config"]["train_config"])
        config = dataclasses.replace(
            saved_config,
            batches=config.batches,
            ppo_epochs=config.ppo_epochs,
            device=config.device,
            output=config.output,
            threads=config.threads,
            log_games=config.log_games,
            keep_checkpoints=config.keep_checkpoints,
            eval_interval=config.eval_interval,
            eval_games=config.eval_games,
            eval_workers=config.eval_workers,
            probe_games=config.probe_games,
            probe_workers=config.probe_workers,
            promotion_threshold=config.promotion_threshold,
        )
    model = AlphaRoco(config=model_config, vocab_sizes=encoder.vocab_sizes).to(device)
    optimizers = {
        "policy": torch.optim.Adam(model.policy_parameters(), lr=config.learning_rate),
        "opponent": torch.optim.Adam(
            model.opponent_parameters(), lr=config.learning_rate
        ),
    }
    first_batch = 0
    games_seen = 0
    best_batch = 0
    best_elo_a = 0.0
    best_elo_b = 0.0
    output = Path(config.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    best_path = output / "best.pt"
    if resume is not None:
        checkpoint, first_batch, games_seen = _restore(
            checkpoint, model, optimizers, encoder, device
        )
    if resume is not None and best_path.exists():
        best_model = load_model(best_path, encoder, device)
        best_checkpoint = torch.load(best_path, map_location="cpu", weights_only=False)
        best_batch = int(best_checkpoint["best_batch"])
        best_elo_a = float(best_checkpoint["best_elo_a"])
        best_elo_b = float(best_checkpoint["best_elo_b"])
    else:
        best_model = _clone_model(model, model_config, encoder, device)
        if resume is not None:
            best_batch = first_batch
        _checkpoint(
            best_path,
            model,
            optimizers,
            encoder,
            model_config,
            config,
            first_batch - 1,
            games_seen,
            best_batch,
            best_elo_a,
            best_elo_b,
            overwrite=True,
        )
    saved_paths = []
    for batch_index in range(first_batch, first_batch + config.batches):
        batch_number = batch_index + 1
        print(f"batch={batch_number} sampling={config.games_per_batch}", flush=True)
        episodes: list[list[dict[str, Any]]] = []
        winners = {"A": 0, "B": 0, "both_lose": 0}
        for _ in range(config.games_per_batch):
            game_index = games_seen
            game_seed = random.randrange(2**32)
            game_log_dir = (
                output / "games" / f"game-{game_index:06d}-seed-{game_seed}"
                if config.log_games
                else None
            )
            if game_log_dir is not None:
                game_log_dir.mkdir(parents=True, exist_ok=True)
            result = _collect_game(
                encoder,
                model,
                random.choice(TEAMS),
                random.choice(TEAMS),
                game_seed,
                device,
                log_dir=game_log_dir,
            )
            episodes.append(result["episodes"]["A"])
            episodes.append(result["episodes"]["B"])
            winners[result["winner"]] += 1
            games_seen += 1
        policy_metrics = _policy_update(
            model,
            optimizers["policy"],
            encoder,
            episodes,
            config,
            config.ppo_epochs,
            device,
        )
        opponent_metrics = _opponent_update(
            model,
            optimizers["opponent"],
            encoder,
            episodes,
            config,
            device,
        )
        if not torch.isfinite(
            torch.tensor(
                [*policy_metrics.values(), *opponent_metrics.values()]
            )
        ).all():
            raise FloatingPointError("训练批次指标出现非有限值，拒绝保存")
        if policy_metrics["grad_norm"] <= 0:
            raise RuntimeError("训练批次没有产生有效策略梯度，拒绝保存")
        if opponent_metrics["opponent_samples"] <= 0:
            raise RuntimeError("训练批次没有可确认的对手行动标签，拒绝保存")
        if opponent_metrics["opponent_grad_norm"] <= 0:
            raise RuntimeError("训练批次没有产生有效对手梯度，拒绝保存")
        checkpoint_path = output / f"checkpoint-batch-{batch_number:06d}.pt"
        _checkpoint(
            checkpoint_path,
            model,
            optimizers,
            encoder,
            model_config,
            config,
            batch_index,
            games_seen,
            best_batch,
            best_elo_a,
            best_elo_b,
        )
        saved_paths.append(checkpoint_path)
        for stale_path in sorted(output.glob("checkpoint-batch-*.pt"))[
            : -config.keep_checkpoints
        ]:
            stale_path.unlink()
        probe_record = None
        probe_best_batch = best_batch
        if config.probe_games > 0:
            model.eval()
            probe_tallies = play_match(
                model,
                best_model,
                encoder,
                config.probe_games,
                config.seed + 1_000_000 + batch_number,
                device,
                sample=False,
                workers=config.probe_workers,
            )
            model.train()
            probe_record = {
                side: probe_tallies[side]["win"] / sum(probe_tallies[side].values())
                for side in ("A", "B")
            }
        eval_record = None
        promoted = False
        if config.eval_interval > 0 and batch_number % config.eval_interval == 0:
            model.eval()
            tallies = play_match(
                model,
                best_model,
                encoder,
                config.eval_games,
                config.seed + batch_number,
                device,
                sample=False,
                workers=config.eval_workers,
            )
            model.train()
            rates = {
                side: tallies[side]["win"] / sum(tallies[side].values())
                for side in ("A", "B")
            }
            intervals = {
                side: wilson_interval(
                    tallies[side]["win"], sum(tallies[side].values())
                )
                for side in ("A", "B")
            }
            elo_a = best_elo_a + config.elo_k * (rates["A"] - 0.5)
            elo_b = best_elo_b + config.elo_k * (rates["B"] - 0.5)
            promoted = min(rates.values()) > config.promotion_threshold
            if promoted:
                best_batch = batch_number
                best_elo_a = elo_a
                best_elo_b = elo_b
                _checkpoint(
                    best_path,
                    model,
                    optimizers,
                    encoder,
                    model_config,
                    config,
                    batch_index,
                    games_seen,
                    best_batch,
                    best_elo_a,
                    best_elo_b,
                    overwrite=True,
                )
                best_model.load_state_dict(model.state_dict())
                best_model.eval()
            eval_record = {
                "batch": batch_number,
                "games": config.eval_games,
                "candidate_side_A": {
                    **tallies["A"],
                    "win_rate": rates["A"],
                    "ci95": list(intervals["A"]),
                    "elo": elo_a,
                },
                "candidate_side_B": {
                    **tallies["B"],
                    "win_rate": rates["B"],
                    "ci95": list(intervals["B"]),
                    "elo": elo_b,
                },
                "promoted": promoted,
                "best_batch": best_batch,
                "best_elo_a": best_elo_a,
                "best_elo_b": best_elo_b,
            }
            with (output / "eval.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(eval_record, ensure_ascii=False) + "\n")
            print(
                f"eval batch={batch_number} games={config.eval_games} "
                f"A={rates['A']:.3f} [{intervals['A'][0]:.3f},{intervals['A'][1]:.3f}] "
                f"B={rates['B']:.3f} [{intervals['B'][0]:.3f},{intervals['B'][1]:.3f}] "
                f"elo_A={elo_a:.1f} elo_B={elo_b:.1f} "
                f"promoted={promoted} best_batch={best_batch}",
                flush=True,
            )
        record = {
            "batch": batch_number,
            "games": config.games_per_batch,
            "games_seen": games_seen,
            "probe_games": config.probe_games if probe_record else 0,
            "probe_A": probe_record["A"] if probe_record else None,
            "probe_B": probe_record["B"] if probe_record else None,
            "probe_best_batch": probe_best_batch if probe_record else None,
            "wins_A": winners["A"],
            "wins_B": winners["B"],
            "both_lose": winners["both_lose"],
            "policy_loss": policy_metrics["policy"],
            "value_loss": policy_metrics["value"],
            "entropy": policy_metrics["entropy"],
            "kl": policy_metrics["kl"],
            "clip_fraction": policy_metrics["clip_fraction"],
            "grad_norm": policy_metrics["grad_norm"],
            "epochs": policy_metrics["epochs"],
            "opponent_ce": opponent_metrics["opponent_ce"],
            "opponent_grad_norm": opponent_metrics["opponent_grad_norm"],
            "opponent_samples": opponent_metrics["opponent_samples"],
            "eval_A": eval_record["candidate_side_A"]["win_rate"]
            if eval_record
            else None,
            "eval_B": eval_record["candidate_side_B"]["win_rate"]
            if eval_record
            else None,
            "promoted": promoted,
            "best_batch": best_batch,
        }
        with (output / "metrics.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if config.probe_games > 0:
            write_table(output / "metrics.jsonl", output / "probe_table.md")
        print(
            f"batch={batch_number} games={config.games_per_batch} "
            f"wins_A={winners['A']} wins_B={winners['B']} "
            f"both_lose={winners['both_lose']} "
            f"policy={policy_metrics['policy']:.4f} "
            f"value={policy_metrics['value']:.4f} "
            f"entropy={policy_metrics['entropy']:.4f} "
            f"kl={policy_metrics['kl']:.4f} "
            f"epochs={int(policy_metrics['epochs'])} "
            f"opp_ce={opponent_metrics['opponent_ce']:.4f} "
            f"best_batch={best_batch} saved={checkpoint_path}",
            flush=True,
        )
    return saved_paths


def _parse_args() -> tuple[TrainConfig, Path | None]:
    parser = argparse.ArgumentParser(
        description="使用真实对局训练Alpha Roco recurrent PPO"
    )
    parser.add_argument("--batches", type=int, default=2)
    parser.add_argument("--games-per-batch", type=int, default=28)
    parser.add_argument("--ppo-epochs", type=int, default=2)
    parser.add_argument("--device", choices=("cpu", "cuda"), default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", default="logs/az")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--log-games", action="store_true")
    parser.add_argument("--keep-checkpoints", type=int, default=1)
    parser.add_argument("--eval-interval", type=int, default=10)
    parser.add_argument("--eval-games", type=int, default=200)
    parser.add_argument("--eval-workers", type=int, default=4)
    parser.add_argument("--probe-games", type=int, default=0)
    parser.add_argument("--probe-workers", type=int, default=4)
    parser.add_argument("--promotion-threshold", type=float, default=0.55)
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()
    return (
        TrainConfig(
            batches=args.batches,
            games_per_batch=args.games_per_batch,
            ppo_epochs=args.ppo_epochs,
            device=args.device or ("cuda" if torch.cuda.is_available() else "cpu"),
            seed=args.seed,
            output=args.output,
            threads=args.threads,
            log_games=args.log_games,
            keep_checkpoints=args.keep_checkpoints,
            eval_interval=args.eval_interval,
            eval_games=args.eval_games,
            eval_workers=args.eval_workers,
            probe_games=args.probe_games,
            probe_workers=args.probe_workers,
            promotion_threshold=args.promotion_threshold,
        ),
        args.resume,
    )


def main() -> None:
    config, resume = _parse_args()
    run(config, resume)


if __name__ == "__main__":
    main()
