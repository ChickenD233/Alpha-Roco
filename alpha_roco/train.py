from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import random
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch

from alpha_roco.arena import TEAMS, choose, load_model, play_match, wilson_interval
from alpha_roco.config import ModelConfig
from alpha_roco.features import (
    ObservationEncoder,
    action_key,
    collate_inputs,
    selectable_candidates,
)
from alpha_roco.losses import (
    auxiliary_loss,
    generalized_advantage,
    normalize_advantages,
    opponent_cross_entropy,
    ppo_loss,
)
from alpha_roco.network import AlphaRoco
from alpha_roco.report import write_table
from alpha_roco.rewards import (
    apply_event_bonus,
    assign_rewards,
    counter_bonus,
    effective_bonus,
    potential,
    state_summary,
)
from alpha_roco.terminal import BattleSession
from sim.data_loader import load_typechart
from sim.skills.registry import static_of
from sim.typechart import type_multiplier

TYPE_CHART = load_typechart()


@dataclass
class TrainConfig:
    batches: int = 2
    games_per_batch: int = 56
    ppo_epochs: int = 2
    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    seed: int = 0
    output: str = "logs/az"
    learning_rate: float = 1e-4
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
    pool_size: int = 8
    eval_interval: int = 8
    eval_games: int = 256
    eval_workers: int = 32
    sample_workers: int = 16
    update_chunk: int = 16
    promotion_threshold: float = 0.55
    elo_k: float = 32.0
    aux_coefficient: float = 0.1


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
    summary = state_summary(request["state"], request["side"])
    with torch.no_grad():
        batch, output = _forward(model, encoder, item, memory, device)
        distribution = _masked_distribution(output["logits"], batch["action_mask"])
        action_index = distribution.sample()
        action = selectable_candidates(request)[int(action_index.item())]
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
            "summary": summary,
            "potential": potential(summary),
            "event_bonus": 0.0,
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
        if side not in entries_by_side:
            continue
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


def _pet_elements(encoder: ObservationEncoder, pet: dict[str, Any]) -> list[int]:
    if pet.get("attributes"):
        return pet["attributes"]
    species = encoder.spirit_by_name.get(
        pet.get("name", "")
    ) or encoder.spirit_by_id.get(str(pet.get("spirit_id", "")))
    if species is None:
        return []
    return [species["mainElement"]] + (
        [species["subElement"]] if species.get("subElement") is not None else []
    )


def _action_category(encoder: ObservationEncoder, action: dict[str, Any]) -> str:
    if action.get("kind") == "charge":
        return "status"
    if action.get("kind") != "skill":
        return ""
    skill_id = action.get("skill_id")
    if skill_id is None:
        return ""
    category = encoder.skill_by_id.get(str(skill_id), {}).get("category")
    return {0: "attack", 1: "attack", 2: "defense", 3: "status"}.get(category, "")


def _event_bonus(
    encoder: ObservationEncoder,
    request: dict[str, Any],
    action: dict[str, Any],
    opponent_action: dict[str, Any],
) -> float:
    if action.get("kind") != "skill" or action.get("skill_id") is None:
        return 0.0
    skill_id = int(action["skill_id"])
    static = static_of(skill_id)
    skill = encoder.skill_by_id.get(str(skill_id), {})
    element = (
        static["element"] if static["element"] is not None else skill.get("element")
    )
    bonus = 0.0
    if (
        skill.get("category") in (0, 1)
        and skill.get("power") is not None
        and element is not None
    ):
        state = request["state"]
        enemy_side = "B" if request["side"] == "A" else "A"
        active = state.get("active", {}).get(enemy_side, -1)
        enemies = state.get("teams", {}).get(enemy_side, [])
        if 0 <= active < len(enemies):
            bonus += effective_bonus(
                type_multiplier(
                    element, _pet_elements(encoder, enemies[active]), TYPE_CHART
                )
            )
    if static["counter_target"] and static["counter_target"] == _action_category(
        encoder, opponent_action
    ):
        bonus += counter_bonus(True)
    return bonus


def _collect_versus_game(
    encoder: ObservationEncoder,
    candidate: AlphaRoco,
    opponent: AlphaRoco,
    team_a: str,
    team_b: str,
    seed: int,
    device: torch.device,
    log_dir: Path | None = None,
    gamma: float = 1.0,
) -> dict[str, Any]:
    episode: list[dict[str, Any]] = []
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
                if side == "A":
                    action, memories[side], entry = _sample_action(
                        candidate, encoder, request, memories[side], device
                    )
                    episode.append(entry)
                    entries_by_side[side] = entry
                else:
                    action, memories[side] = choose(
                        opponent, encoder, request, memories[side], device, sample=False
                    )
                actions_by_side[side] = action
            _paired_opponent_targets(
                requests, actions_by_side, encoder, entries_by_side
            )
            if "A" in entries_by_side and "B" in actions_by_side:
                request_a = next(
                    request for request in requests if request["side"] == "A"
                )
                entries_by_side["A"]["event_bonus"] = _event_bonus(
                    encoder,
                    request_a,
                    actions_by_side["A"],
                    actions_by_side["B"],
                )
            for side, action in actions_by_side.items():
                session.submit(side, action)
        winner = session.winner
    if winner not in {"A", "B", "both_lose"}:
        raise RuntimeError(f"对局未得到有效终局结果：{winner!r}")
    if not episode:
        raise RuntimeError("candidate方本局没有决策记录")
    assign_rewards(episode, winner, gamma)
    apply_event_bonus(episode)
    _assign_aux_targets(episode)
    return {"episode": episode, "winner": winner}


def _assign_aux_targets(episode: list[dict[str, Any]]) -> None:
    battle_entries = [entry for entry in episode if entry["phase"] != "lead"]
    for entry, following in zip(battle_entries, battle_entries[1:]):
        before, after = entry["summary"], following["summary"]
        entry["aux_target"] = {
            "damage_dealt": max(0.0, before["enemy_hp"] - after["enemy_hp"]),
            "damage_taken": max(0.0, before["own_hp"] - after["own_hp"]),
            "ko": [
                float(after["enemy_alive"] < before["enemy_alive"]),
                float(after["own_alive"] < before["own_alive"]),
            ],
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


def _batched_episode_steps(
    model: AlphaRoco,
    episodes: list[list[dict[str, Any]]],
    device: torch.device,
):
    memories = [None] * len(episodes)
    max_steps = max((len(episode) for episode in episodes), default=0)
    for step in range(max_steps):
        active = [
            index for index, episode in enumerate(episodes) if step < len(episode)
        ]
        if not active:
            break
        batch = collate_inputs(
            [episodes[index][step]["item"] for index in active], device
        )
        memory = (
            None
            if step == 0
            else (
                torch.cat([memories[index][0] for index in active], dim=1),
                torch.cat([memories[index][1] for index in active], dim=1),
            )
        )
        output = model(batch, memory=memory)
        next_memory = output["memory"]
        for position, index in enumerate(active):
            memories[index] = (
                next_memory[0][:, position : position + 1],
                next_memory[1][:, position : position + 1],
            )
        yield step, active, batch, output


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
        "aux": 0.0,
        "grad_norm": 0.0,
    }
    aux_count = sum(
        1
        for episode in episodes
        for entry in episode
        if entry.get("aux_target") is not None
    )
    completed_epochs = 0
    for _ in range(ppo_epochs):
        optimizer.zero_grad(set_to_none=True)
        sums = {
            key: 0.0
            for key in ("policy", "value", "entropy", "kl", "clip_fraction", "aux")
        }
        for start in range(0, len(episodes), config.update_chunk):
            chunk = episodes[start : start + config.update_chunk]
            losses: list[torch.Tensor] = []
            for step, active, batch, output in _batched_episode_steps(
                model, chunk, device
            ):
                entries = [chunk[index][step] for index in active]
                phases = [
                    "lead" if entry["phase"] == "lead" else "battle"
                    for entry in entries
                ]
                for phase in ("lead", "battle"):
                    positions = [
                        position
                        for position, value in enumerate(phases)
                        if value == phase
                    ]
                    if not positions:
                        continue
                    selected = [entries[position] for position in positions]
                    result = ppo_loss(
                        output["logits"][positions],
                        torch.tensor(
                            [entry["action"] for entry in selected], device=device
                        ),
                        torch.tensor(
                            [entry["old_log_prob"] for entry in selected],
                            device=device,
                        ),
                        torch.stack([entry["advantage"] for entry in selected]),
                        output["value"][positions].reshape(-1),
                        torch.stack([entry["return"] for entry in selected]),
                        batch["action_mask"][positions],
                        config.clip_ratio,
                        config.value_coefficient,
                        config.entropy_coefficient,
                    )
                    scale = len(positions) / max(counts[phase], 1)
                    losses.append(result["loss"] * scale)
                    for key in (
                        "policy",
                        "value",
                        "entropy",
                        "kl",
                        "clip_fraction",
                    ):
                        sums[key] += float(result[key].detach().item()) * scale
                aux_positions = [
                    position
                    for position, entry in enumerate(entries)
                    if entry.get("aux_target") is not None
                ]
                if aux_positions:
                    targets = [
                        entries[position]["aux_target"] for position in aux_positions
                    ]
                    aux = auxiliary_loss(
                        {
                            key: value[aux_positions]
                            for key, value in output["aux"].items()
                        },
                        {
                            "damage_dealt": torch.tensor(
                                [target["damage_dealt"] for target in targets],
                                device=device,
                            ),
                            "damage_taken": torch.tensor(
                                [target["damage_taken"] for target in targets],
                                device=device,
                            ),
                            "ko": torch.tensor(
                                [target["ko"] for target in targets],
                                device=device,
                            ),
                        },
                    )
                    scale = len(aux_positions) / max(aux_count, 1)
                    losses.append(aux * config.aux_coefficient * scale)
                    sums["aux"] += float(aux.detach().item()) * scale
            if losses:
                torch.stack(losses).sum().backward()
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
    for start in range(0, len(episodes), config.update_chunk):
        chunk = episodes[start : start + config.update_chunk]
        losses = []
        for step, active, batch, output in _batched_episode_steps(model, chunk, device):
            targets = [chunk[index][step]["opponent_target"] for index in active]
            positions = [
                position
                for position, target in enumerate(targets)
                if target is not None
            ]
            if not positions:
                continue
            loss = opponent_cross_entropy(
                output["opponent_logits"][positions],
                torch.tensor(
                    [targets[position] for position in positions], device=device
                ),
                batch["opponent_mask"][positions],
            )
            if not bool(torch.isfinite(loss).all()):
                raise FloatingPointError("对手交叉熵出现非有限值")
            loss_sum += float(loss.detach().item()) * len(positions)
            losses.append(loss * len(positions) / max(target_count, 1))
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
    best_elo: float,
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
        "best_elo": best_elo,
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
    model: AlphaRoco,
    model_config: ModelConfig,
    encoder: ObservationEncoder,
    device: torch.device,
) -> AlphaRoco:
    clone = AlphaRoco(config=model_config, vocab_sizes=encoder.vocab_sizes).to(device)
    clone.load_state_dict(model.state_dict())
    clone.eval()
    return clone


def _load_pool(
    pool_dir: Path, encoder: ObservationEncoder, device: torch.device, size: int
) -> list[tuple[str, AlphaRoco]]:
    paths = sorted(pool_dir.glob("best-batch-*.pt"))
    for stale in paths[:-size]:
        stale.unlink()
    return [(path.name, load_model(path, encoder, device)) for path in paths[-size:]]


def _sample_opponent(
    best_model: AlphaRoco,
    pool: list[tuple[str, AlphaRoco]],
    stats: dict[str, dict[str, int]],
) -> tuple[str, AlphaRoco]:
    if not pool or random.random() < 0.5:
        return "current", best_model
    weights = []
    for name, _ in pool:
        record = stats.get(name, {})
        rate = record["wins"] / record["games"] if record.get("games") else 0.5
        weights.append(1.0 if 0.2 <= rate <= 0.8 else 0.2)
    return random.choices(pool, weights=weights, k=1)[0]


def _collect_batch(
    encoder: ObservationEncoder,
    candidate: AlphaRoco,
    best_model: AlphaRoco,
    pool: list[tuple[str, AlphaRoco]],
    pool_stats: dict[str, dict[str, int]],
    first_game: int,
    device: torch.device,
    config: TrainConfig,
    output: Path,
) -> list[tuple[int, str, dict[str, Any]]]:
    tasks = []
    for offset in range(config.games_per_batch):
        game_index = first_game + offset
        game_seed = random.randrange(2**32)
        opponent_name, opponent_model = _sample_opponent(best_model, pool, pool_stats)
        game_log_dir = (
            output / "games" / f"game-{game_index:06d}-seed-{game_seed}"
            if config.log_games
            else None
        )
        if game_log_dir is not None:
            game_log_dir.mkdir(parents=True, exist_ok=True)
        tasks.append(
            (
                game_index,
                opponent_name,
                opponent_model,
                random.choice(TEAMS),
                random.choice(TEAMS),
                game_seed,
                game_log_dir,
            )
        )

    def collect(task):
        (
            game_index,
            opponent_name,
            opponent_model,
            team_a,
            team_b,
            game_seed,
            log_dir,
        ) = task
        return (
            game_index,
            opponent_name,
            _collect_versus_game(
                encoder,
                candidate,
                opponent_model,
                team_a,
                team_b,
                game_seed,
                device,
                log_dir=log_dir,
                gamma=config.gamma,
            ),
        )

    if config.sample_workers == 1:
        return [collect(task) for task in tasks]
    with ThreadPoolExecutor(
        max_workers=min(config.sample_workers, config.games_per_batch)
    ) as executor:
        return list(executor.map(collect, tasks))


def run(config: TrainConfig, resume: Path | None = None) -> list[Path]:
    if (
        config.batches < 1
        or config.games_per_batch < 1
        or config.ppo_epochs < 1
        or config.keep_checkpoints < 1
        or config.sample_workers < 1
        or config.update_chunk < 1
    ):
        raise ValueError(
            "batches、games-per-batch、ppo-epochs、sample-workers、update-chunk与keep-checkpoints必须为正数"
        )
    if config.eval_interval < 1:
        raise ValueError("eval-interval必须为正数")
    if config.eval_games < 2:
        raise ValueError("eval-games至少为2")
    if config.eval_workers < 1:
        raise ValueError("eval-workers必须为正数")
    if config.pool_size < 1:
        raise ValueError("pool-size必须为正数")
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
            games_per_batch=config.games_per_batch,
            ppo_epochs=config.ppo_epochs,
            aux_coefficient=config.aux_coefficient,
            device=config.device,
            output=config.output,
            threads=config.threads,
            log_games=config.log_games,
            keep_checkpoints=config.keep_checkpoints,
            pool_size=config.pool_size,
            eval_interval=config.eval_interval,
            eval_games=config.eval_games,
            eval_workers=config.eval_workers,
            sample_workers=config.sample_workers,
            update_chunk=config.update_chunk,
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
    best_elo = 0.0
    output = Path(config.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    best_path = output / "best.pt"
    pool_dir = output / "pool"
    pool_dir.mkdir(parents=True, exist_ok=True)
    pool_stats_path = output / "pool_stats.json"
    if resume is not None:
        checkpoint, first_batch, games_seen = _restore(
            checkpoint, model, optimizers, encoder, device
        )
    if resume is not None and best_path.exists():
        best_model = load_model(best_path, encoder, device)
        best_checkpoint = torch.load(best_path, map_location="cpu", weights_only=False)
        best_batch = int(best_checkpoint["best_batch"])
        best_elo = float(best_checkpoint["best_elo"])
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
            best_elo,
            overwrite=True,
        )
    pool = _load_pool(pool_dir, encoder, device, config.pool_size)
    pool_stats = (
        json.loads(pool_stats_path.read_text(encoding="utf-8"))
        if pool_stats_path.exists()
        else {}
    )
    pool_stats = {
        name: record
        for name, record in pool_stats.items()
        if name == "current" or name in {pool_name for pool_name, _ in pool}
    }
    saved_paths = []
    for batch_index in range(first_batch, first_batch + config.batches):
        batch_number = batch_index + 1
        print(f"batch={batch_number} sampling={config.games_per_batch}", flush=True)
        episodes: list[list[dict[str, Any]]] = []
        both_lose = 0
        results = _collect_batch(
            encoder,
            model,
            best_model,
            pool,
            pool_stats,
            games_seen,
            device,
            config,
            output,
        )
        for _, opponent_name, result in results:
            episodes.append(result["episode"])
            both_lose += result["winner"] == "both_lose"
            record = pool_stats.setdefault(opponent_name, {"wins": 0, "games": 0})
            record["games"] += 1
            record["wins"] += result["winner"] == "A"
        games_seen += config.games_per_batch
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
            torch.tensor([*policy_metrics.values(), *opponent_metrics.values()])
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
            best_elo,
        )
        saved_paths.append(checkpoint_path)
        for stale_path in sorted(output.glob("checkpoint-batch-*.pt"))[
            : -config.keep_checkpoints
        ]:
            stale_path.unlink()
        evaluated = (
            batch_number % config.eval_interval == 0
            or batch_index == first_batch + config.batches - 1
        )
        rate = None
        reference_batch = best_batch
        elo = best_elo
        promoted = False
        if evaluated:
            model.eval()
            tally = play_match(
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
            rate = tally["win"] / sum(tally.values())
            interval = wilson_interval(tally["win"], sum(tally.values()))
            elo = best_elo + config.elo_k * (rate - 0.5)
            promoted = rate > config.promotion_threshold
            if promoted:
                snapshot = pool_dir / f"best-batch-{reference_batch:06d}.pt"
                if not snapshot.exists():
                    shutil.copy2(best_path, snapshot)
                if snapshot.name not in {name for name, _ in pool}:
                    pool.append((snapshot.name, load_model(snapshot, encoder, device)))
                if "current" in pool_stats:
                    pool_stats[snapshot.name] = pool_stats.pop("current")
                while len(pool) > config.pool_size:
                    name, _ = pool.pop(0)
                    (pool_dir / name).unlink(missing_ok=True)
                    pool_stats.pop(name, None)
                best_batch = batch_number
                best_elo = elo
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
                    best_elo,
                    overwrite=True,
                )
                best_model.load_state_dict(model.state_dict())
                best_model.eval()
            eval_record = {
                "batch": batch_number,
                "games": config.eval_games,
                "reference_batch": reference_batch,
                "win": tally["win"],
                "loss": tally["loss"],
                "both_lose": tally["both_lose"],
                "win_rate": rate,
                "ci95": list(interval),
                "elo": elo,
                "promoted": promoted,
                "best_batch": best_batch,
                "best_elo": best_elo,
            }
            with (output / "eval.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(eval_record, ensure_ascii=False) + "\n")
            write_table(output / "eval.jsonl", output / "eval_table.md")
        record = {
            "batch": batch_number,
            "eval_win_rate": rate,
            "promoted": promoted,
            "best_batch": best_batch,
            "entropy": policy_metrics["entropy"],
            "kl": policy_metrics["kl"],
            "value_loss": policy_metrics["value"],
            "aux_loss": policy_metrics["aux"],
            "opponent_ce": opponent_metrics["opponent_ce"],
            "both_lose_rate": both_lose / config.games_per_batch,
        }
        with (output / "metrics.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        pool_stats_path.write_text(
            json.dumps(pool_stats, ensure_ascii=False), encoding="utf-8"
        )
        eval_text = "-" if rate is None else f"{rate:.3f}"
        print(
            f"batch={batch_number} eval_win_rate={eval_text} promoted={promoted} "
            f"best_batch={best_batch} entropy={record['entropy']:.4f} "
            f"kl={record['kl']:.4f} value={record['value_loss']:.4f} "
            f"aux={record['aux_loss']:.4f} "
            f"opp_ce={record['opponent_ce']:.4f} "
            f"both_lose_rate={record['both_lose_rate']:.3f}",
            flush=True,
        )
    return saved_paths


def _parse_args() -> tuple[TrainConfig, Path | None]:
    parser = argparse.ArgumentParser(
        description="使用真实对局训练Alpha Roco recurrent PPO"
    )
    parser.add_argument("--batches", type=int, default=2)
    parser.add_argument("--games-per-batch", type=int, default=56)
    parser.add_argument("--ppo-epochs", type=int, default=2)
    parser.add_argument("--device", choices=("cpu", "cuda"), default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", default="logs/az")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--log-games", action="store_true")
    parser.add_argument("--keep-checkpoints", type=int, default=1)
    parser.add_argument("--pool-size", type=int, default=8)
    parser.add_argument("--eval-interval", type=int, default=8)
    parser.add_argument("--eval-games", type=int, default=256)
    parser.add_argument("--eval-workers", type=int, default=32)
    parser.add_argument("--sample-workers", type=int, default=16)
    parser.add_argument("--update-chunk", type=int, default=16)
    parser.add_argument("--promotion-threshold", type=float, default=0.55)
    parser.add_argument("--aux-coefficient", type=float, default=0.1)
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
            pool_size=args.pool_size,
            eval_interval=args.eval_interval,
            eval_games=args.eval_games,
            eval_workers=args.eval_workers,
            sample_workers=args.sample_workers,
            update_chunk=args.update_chunk,
            promotion_threshold=args.promotion_threshold,
            aux_coefficient=args.aux_coefficient,
        ),
        args.resume,
    )


def main() -> None:
    config, resume = _parse_args()
    run(config, resume)


if __name__ == "__main__":
    main()
