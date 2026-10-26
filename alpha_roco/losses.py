from __future__ import annotations

import torch
import torch.nn.functional as F


def terminal_rewards(winner: str) -> tuple[float, float]:
    return {
        "A": (1.0, -1.0),
        "B": (-1.0, 1.0),
        "both_lose": (-1.0, -1.0),
    }[winner]


def generalized_advantage(
    rewards: torch.Tensor,
    values: torch.Tensor,
    terminal: torch.Tensor | None = None,
    gamma: float = 1.0,
    lam: float = 0.95,
) -> tuple[torch.Tensor, torch.Tensor]:
    """按一局有序决策计算GAE，终局不进行价值自举。"""
    terminal = (
        torch.zeros_like(rewards, dtype=torch.bool)
        if terminal is None
        else terminal.bool()
    )
    advantages = torch.zeros_like(values)
    next_value = torch.zeros_like(values[-1])
    next_advantage = torch.zeros_like(values[-1])
    for index in range(rewards.numel() - 1, -1, -1):
        continuation = (~terminal[index]).to(values.dtype)
        delta = rewards[index] + gamma * next_value * continuation - values[index]
        next_advantage = delta + gamma * lam * continuation * next_advantage
        advantages[index] = next_advantage
        next_value = values[index]
    return advantages, advantages + values


def normalize_advantages(advantages: torch.Tensor) -> torch.Tensor:
    if advantages.numel() <= 1:
        return advantages
    scale = advantages.std(unbiased=False)
    if scale < 1e-8:
        return advantages
    return (advantages - advantages.mean()) / scale


def ppo_loss(
    logits: torch.Tensor,
    actions: torch.Tensor,
    old_log_probs: torch.Tensor,
    advantages: torch.Tensor,
    values: torch.Tensor,
    returns: torch.Tensor,
    action_mask: torch.Tensor,
    clip_ratio: float = 0.2,
    value_coefficient: float = 0.5,
    entropy_coefficient: float = 0.01,
) -> dict[str, torch.Tensor]:
    masked_logits = logits.masked_fill(
        ~action_mask.bool(), torch.finfo(logits.dtype).min
    )
    distribution = torch.distributions.Categorical(logits=masked_logits)
    new_log_probs = distribution.log_prob(actions)
    entropy = distribution.entropy()
    ratio = (new_log_probs - old_log_probs).exp()
    surrogate = torch.minimum(
        ratio * advantages,
        ratio.clamp(1.0 - clip_ratio, 1.0 + clip_ratio) * advantages,
    )
    policy = -surrogate.mean()
    value = F.mse_loss(values, returns)
    loss = policy + value_coefficient * value - entropy_coefficient * entropy.mean()
    approximate_kl = ((ratio - 1.0) - (new_log_probs - old_log_probs)).mean()
    clip_fraction = ((ratio - 1.0).abs() > clip_ratio).float().mean()
    return {
        "loss": loss,
        "policy": policy,
        "value": value,
        "entropy": entropy.mean(),
        "kl": approximate_kl,
        "clip_fraction": clip_fraction,
    }


def auxiliary_loss(
    predictions: dict[str, torch.Tensor],
    targets: dict[str, torch.Tensor],
) -> torch.Tensor:
    damage = F.mse_loss(
        predictions["damage_dealt"], targets["damage_dealt"]
    ) + F.mse_loss(predictions["damage_taken"], targets["damage_taken"])
    ko = F.binary_cross_entropy_with_logits(predictions["ko"], targets["ko"])
    return damage + ko


def opponent_cross_entropy(
    logits: torch.Tensor,
    actions: torch.Tensor,
    opponent_mask: torch.Tensor,
) -> torch.Tensor:
    masked_logits = logits.masked_fill(
        ~opponent_mask.bool(), torch.finfo(logits.dtype).min
    )
    return F.cross_entropy(masked_logits, actions)
