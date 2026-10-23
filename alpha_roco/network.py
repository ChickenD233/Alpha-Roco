from __future__ import annotations

import torch
from torch import Tensor, nn

from .config import ModelConfig
from .features import EVENT_FEATURES


def _safe_ids(values, size):
    values = values.long()
    return torch.where((values >= 0) & (values < size), values, torch.ones_like(values))


class AttentionBlock(nn.Module):
    def __init__(self, config):
        super().__init__()
        width = config.hidden_dim
        self.norm1 = nn.LayerNorm(width)
        self.attention = nn.MultiheadAttention(
            width, config.num_heads, batch_first=True
        )
        self.norm2 = nn.LayerNorm(width)
        self.ffn = nn.Sequential(
            nn.Linear(width, 256), nn.GELU(), nn.Linear(256, width)
        )

    def forward(self, x, mask):
        if not mask.any(dim=1).all():
            mask = mask.clone()
            mask[~mask.any(dim=1), 0] = True
        normalized = self.norm1(x)
        attended = self.attention(
            normalized,
            normalized,
            normalized,
            key_padding_mask=~mask,
            need_weights=False,
        )[0]
        residual = x + attended
        return residual + self.ffn(self.norm2(residual))


class AttentionStack(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.blocks = nn.ModuleList(
            AttentionBlock(config) for _ in range(config.num_layers)
        )
        self.norm = nn.LayerNorm(config.hidden_dim)

    def forward(self, x, mask):
        for block in self.blocks:
            x = block(x, mask)
        return self.norm(x)


class CandidateEncoder(nn.Module):
    def __init__(self, config, vocab_sizes):
        super().__init__()
        self.kind = nn.Embedding(
            vocab_sizes["action"], config.category_embedding_dim, padding_idx=0
        )
        self.skill = nn.Embedding(
            vocab_sizes["skill"], config.identity_embedding_dim, padding_idx=0
        )
        self.numeric = nn.Sequential(nn.Linear(2, 32), nn.GELU())
        size = config.category_embedding_dim + config.identity_embedding_dim + 32 + 4
        self.project = nn.Sequential(
            nn.Linear(size, config.hidden_dim),
            nn.GELU(),
            nn.Linear(config.hidden_dim, config.hidden_dim),
            nn.LayerNorm(config.hidden_dim),
        )

    def forward(self, actions):
        kind = actions[..., 0].long().clamp(0, self.kind.num_embeddings - 1)
        skill = actions[..., 2].long().clamp(0, self.skill.num_embeddings - 1)
        numeric = self.numeric(actions[..., 6:8])
        misc = torch.stack(
            (
                actions[..., 1] / 6,
                actions[..., 3] / 3,
                actions[..., 4] * 0.5 + actions[..., 5] * 0.5,
                actions[..., 8] / 4,
            ),
            dim=-1,
        )
        return self.project(
            torch.cat((self.kind(kind), self.skill(skill), numeric, misc), dim=-1)
        )


class OpponentModel(nn.Module):
    def __init__(self, config, vocab_sizes):
        super().__init__()
        width = config.hidden_dim
        self.spirit = nn.Embedding(
            vocab_sizes["spirit"], config.identity_embedding_dim, padding_idx=0
        )
        self.element = nn.Embedding(
            vocab_sizes["element"], config.category_embedding_dim, padding_idx=0
        )
        self.public_pet = nn.Sequential(
            nn.Linear(
                config.identity_embedding_dim + config.category_embedding_dim + 26,
                width,
            ),
            nn.GELU(),
            nn.Linear(width, width),
            nn.LayerNorm(width),
        )
        self.public_attention = AttentionStack(config)
        self.decision = nn.Sequential(
            nn.Linear(width * 3, 256),
            nn.GELU(),
            nn.Linear(256, width),
            nn.LayerNorm(width),
        )
        self.field_encoder = nn.Sequential(
            nn.Linear(5, width), nn.GELU(), nn.Linear(width, width), nn.LayerNorm(width)
        )
        self.event_kind = nn.Embedding(vocab_sizes["event"], 16, padding_idx=0)
        self.event_phase = nn.Embedding(vocab_sizes["phase"], 16, padding_idx=0)
        self.event_action_kind = nn.Embedding(vocab_sizes["action"], 16, padding_idx=0)
        self.event_skill = nn.Embedding(vocab_sizes["skill"], 32, padding_idx=0)
        self.event_encoder = nn.Sequential(
            nn.Linear(EVENT_FEATURES + 32 + 16 + 32, width), nn.GELU(), nn.LayerNorm(width)
        )
        self.public_gru = nn.GRUCell(width, width)
        self.candidate_encoder = CandidateEncoder(config, vocab_sizes)
        self.candidate_score = nn.Sequential(
            nn.Linear(width * 3, 128), nn.GELU(), nn.Linear(128, 1)
        )

    def _events(self, batch, hidden):
        events = batch["events"]
        encoded = self.event_encoder(
            torch.cat(
                (
                    events,
                    self.event_kind(batch["event_kinds"]),
                    self.event_phase(batch["event_phases"]),
                    self.event_action_kind(batch["event_action_kinds"]),
                    self.event_skill(_safe_ids(batch["event_skill_ids"], self.event_skill.num_embeddings)),
                ),
                dim=-1,
            )
        )
        valid = batch["event_public_mask"]
        for index in range(events.shape[1]):
            next_hidden = self.public_gru(encoded[:, index], hidden)
            hidden = torch.where(valid[:, index, None], next_hidden, hidden)
        return hidden

    def forward(self, batch, memory):
        pets = self.public_pet(
            torch.cat(
                (
                    self.spirit(batch["public_pet_ids"]),
                    self.element(batch["public_pet_elements"]),
                    batch["public_pets"],
                ),
                dim=-1,
            )
        )
        fields = self.field_encoder(batch["fields"])
        tokens = torch.cat((pets, fields), dim=1)
        mask = torch.cat((batch["public_pet_mask"], batch["field_mask"]), dim=1)
        state = self.public_attention(tokens, mask)
        state = (state * mask.unsqueeze(-1)).sum(dim=1) / mask.sum(
            dim=1, keepdim=True
        ).clamp_min(1)
        public_hidden = self._events(batch, memory)
        candidates = self.candidate_encoder(batch["opponent_candidates"])
        context = self.decision(
            torch.cat((state, public_hidden, state * public_hidden), dim=-1)
        )
        logits = self.candidate_score(
            torch.cat(
                (
                    context.unsqueeze(1).expand_as(candidates),
                    candidates,
                    context.unsqueeze(1) * candidates,
                ),
                dim=-1,
            )
        ).squeeze(-1)
        if not batch["opponent_mask"].any(dim=1).all():
            raise ValueError("each sample requires at least one opponent candidate")
        return logits.masked_fill(~batch["opponent_mask"], -torch.inf), public_hidden


class AlphaRoco(nn.Module):
    def __init__(
        self, config: ModelConfig | None = None, vocab_sizes: dict | None = None
    ):
        super().__init__()
        self.config = config or ModelConfig()
        vocab_sizes = vocab_sizes or {
            "spirit": 640,
            "skill": 600,
            "effect": 128,
            "element": 24,
            "category": 8,
            "weather": 8,
            "mark": 64,
            "action": 16,
            "phase": 8,
            "event": 16,
            "side": 8,
        }
        self.spirit_embedding = nn.Embedding(
            vocab_sizes["spirit"], self.config.identity_embedding_dim, padding_idx=0
        )
        self.element_embedding = nn.Embedding(
            vocab_sizes["element"], self.config.category_embedding_dim, padding_idx=0
        )
        self.skill_embedding = nn.Embedding(
            vocab_sizes["skill"], self.config.identity_embedding_dim, padding_idx=0
        )
        self.skill_category = nn.Embedding(
            vocab_sizes["category"], self.config.category_embedding_dim, padding_idx=0
        )
        self.effect_embedding = nn.Embedding(
            vocab_sizes["effect"], self.config.category_embedding_dim, padding_idx=0
        )
        width = self.config.hidden_dim
        self.skill_encoder = nn.Sequential(
            nn.Linear(
                self.config.identity_embedding_dim
                + self.config.category_embedding_dim * 2
                + 5,
                width,
            ),
            nn.GELU(),
            nn.Linear(width, width),
            nn.LayerNorm(width),
        )
        self.effect_encoder = nn.Sequential(
            nn.Linear(self.config.category_embedding_dim + 3, width),
            nn.GELU(),
            nn.Linear(width, width),
            nn.LayerNorm(width),
        )
        self.pet_encoder = nn.Sequential(
            nn.Linear(
                self.config.identity_embedding_dim
                + self.config.category_embedding_dim
                + 26
                + width * 2,
                256,
            ),
            nn.GELU(),
            nn.Linear(256, width),
            nn.LayerNorm(width),
        )
        self.field_encoder = nn.Sequential(
            nn.Linear(5, width), nn.GELU(), nn.Linear(width, width), nn.LayerNorm(width)
        )
        self.decision = nn.Sequential(
            nn.Embedding(vocab_sizes["phase"], width),
            nn.Linear(width, width),
            nn.GELU(),
            nn.LayerNorm(width),
        )
        self.lead_attention = AttentionStack(self.config)
        self.battle_attention = AttentionStack(self.config)
        self.self_event_kind = nn.Embedding(vocab_sizes["event"], 16, padding_idx=0)
        self.self_event_phase = nn.Embedding(vocab_sizes["phase"], 16, padding_idx=0)
        self.self_event_action_kind = nn.Embedding(vocab_sizes["action"], 16, padding_idx=0)
        self.self_event_skill = nn.Embedding(vocab_sizes["skill"], 32, padding_idx=0)
        self.self_event_encoder = nn.Sequential(
            nn.Linear(EVENT_FEATURES + 32 + 16 + 32, width), nn.GELU(), nn.LayerNorm(width)
        )
        self.self_gru = nn.GRUCell(width, width)
        self.opponent = OpponentModel(self.config, vocab_sizes)
        self.context = nn.Sequential(
            nn.Linear(width * 3, 256),
            nn.GELU(),
            nn.Linear(256, width),
            nn.LayerNorm(width),
        )
        self.candidate_encoder = CandidateEncoder(self.config, vocab_sizes)
        self.action_skill = nn.Sequential(
            nn.Linear(width * 2, width), nn.GELU(), nn.LayerNorm(width)
        )
        self.base_score = nn.Sequential(
            nn.Linear(width * 2, 128), nn.GELU(), nn.Linear(128, 1)
        )
        self.interaction_score = nn.Sequential(
            nn.Linear(width * 3, 128), nn.GELU(), nn.Linear(128, 1)
        )
        self.lead_score = nn.Sequential(
            nn.Linear(width * 2, 128), nn.GELU(), nn.Linear(128, 1)
        )
        self.lead_value = nn.Sequential(
            nn.Linear(width, 128), nn.GELU(), nn.Linear(128, 1)
        )
        self.battle_value = nn.Sequential(
            nn.Linear(width, 128), nn.GELU(), nn.Linear(128, 1)
        )

    def _event_memory(self, batch, hidden):
        events = batch["events"]
        encoded = self.self_event_encoder(
            torch.cat(
                (
                    events,
                    self.self_event_kind(batch["event_kinds"]),
                    self.self_event_phase(batch["event_phases"]),
                    self.self_event_action_kind(batch["event_action_kinds"]),
                    self.self_event_skill(_safe_ids(batch["event_skill_ids"], self.self_event_skill.num_embeddings)),
                ),
                dim=-1,
            )
        )
        for index in range(events.shape[1]):
            next_hidden = self.self_gru(encoded[:, index], hidden)
            hidden = torch.where(
                batch["event_mask"][:, index, None], next_hidden, hidden
            )
        return hidden

    def _pets(self, batch, prefix):
        skills = self.skill_encoder(
            torch.cat(
                (
                    self.skill_embedding(batch[f"{prefix}_skill_ids"]),
                    self.element_embedding(batch[f"{prefix}_skill_elements"]),
                    self.skill_category(batch[f"{prefix}_skill_categories"]),
                    batch[f"{prefix}_skills"],
                ),
                dim=-1,
            )
        )
        skill_mask = batch[f"{prefix}_skill_mask"]
        skill_mean = (skills * skill_mask.unsqueeze(-1)).sum(dim=2) / skill_mask.sum(
            dim=2, keepdim=True
        ).clamp_min(1)
        effects = self.effect_encoder(
            torch.cat(
                (
                    self.effect_embedding(batch[f"{prefix}_effect_ids"]),
                    batch[f"{prefix}_effects"],
                ),
                dim=-1,
            )
        )
        effect_mask = batch[f"{prefix}_effect_mask"]
        effect_mean = (effects * effect_mask.unsqueeze(-1)).sum(
            dim=2
        ) / effect_mask.sum(dim=2, keepdim=True).clamp_min(1)
        pet = self.pet_encoder(
            torch.cat(
                (
                    self.spirit_embedding(batch[f"{prefix}_pet_ids"]),
                    self.element_embedding(batch[f"{prefix}_pet_elements"]),
                    batch[f"{prefix}_pets"],
                    skill_mean,
                    effect_mean,
                ),
                dim=-1,
            )
        )
        return pet, skills

    def _action_embedding(self, actions, base, skills):
        pet_index = actions[..., 1].long().clamp(0, base.shape[1] - 1)
        own_pet = base.gather(1, pet_index.unsqueeze(-1).expand(-1, -1, base.shape[-1]))
        skill_index = actions[..., 8].long()
        valid_skill = (skill_index >= 0) & (skill_index < skills.shape[2])
        skill_index = skill_index.clamp(0, skills.shape[2] - 1)
        pet_skills = skills.gather(
            1,
            pet_index.unsqueeze(-1)
            .unsqueeze(-1)
            .expand(-1, -1, skills.shape[2], skills.shape[-1]),
        )
        selected_skill = pet_skills.gather(
            2,
            skill_index.unsqueeze(-1).unsqueeze(-1).expand(-1, -1, 1, skills.shape[-1]),
        ).squeeze(2)
        is_skill = (actions[..., 0].long() == 6) & valid_skill
        selected_skill = selected_skill * is_skill.unsqueeze(-1)
        return own_pet, selected_skill

    def forward(self, batch: dict, memory: tuple[Tensor, Tensor] | None = None) -> dict:
        batch_size = batch["phase"].shape[0]
        device = batch["phase"].device
        hidden_self, hidden_public = (
            memory
            if memory is not None
            else (
                torch.zeros(batch_size, self.config.hidden_dim, device=device),
                torch.zeros(batch_size, self.config.hidden_dim, device=device),
            )
        )
        hidden_self = self._event_memory(batch, hidden_self)
        opponent_logits, hidden_public = self.opponent(batch, hidden_public)
        own, own_skills = self._pets(batch, "own")
        enemy, _ = self._pets(batch, "enemy")
        own_mask = batch["own_pet_mask"]
        enemy_mask = batch["enemy_pet_mask"]
        fields = self.field_encoder(batch["fields"])
        field_mask = batch["field_mask"]
        decision = self.decision[0](batch["phase"]).unsqueeze(1)
        tokens = torch.cat((decision, own, enemy, fields), dim=1)
        token_mask = torch.cat(
            (
                torch.ones(batch_size, 1, dtype=torch.bool, device=device),
                own_mask,
                enemy_mask,
                field_mask,
            ),
            dim=1,
        )
        lead_state = self.lead_attention(tokens, token_mask)[:, 0]
        battle_state = self.battle_attention(tokens, token_mask)[:, 0]
        battle_context = self.context(
            torch.cat((battle_state, hidden_self, hidden_public.detach()), dim=-1)
        )
        candidates = batch["own_candidates"]
        candidate_base = self.candidate_encoder(candidates)
        own_action_pet, selected_skill = self._action_embedding(
            candidates, own, own_skills
        )
        action_embeddings = self.action_skill(
            torch.cat((candidate_base, selected_skill), dim=-1)
        )
        lead_logits = self.lead_score(
            torch.cat(
                (own_action_pet, lead_state.unsqueeze(1).expand_as(own_action_pet)),
                dim=-1,
            )
        ).squeeze(-1)
        base_logits = self.base_score(
            torch.cat(
                (
                    battle_context.unsqueeze(1).expand_as(action_embeddings),
                    action_embeddings,
                ),
                dim=-1,
            )
        ).squeeze(-1)
        opponent_candidates = batch["opponent_candidates"]
        opponent_base = self.candidate_encoder(opponent_candidates)
        opponent_actions = self.action_skill(
            torch.cat((opponent_base, torch.zeros_like(opponent_base)), dim=-1)
        )
        opponent_logits = opponent_logits.masked_fill(
            ~batch["opponent_mask"], -torch.inf
        )
        q = torch.softmax(opponent_logits, dim=-1).detach()
        pair_context = battle_context[:, None, None, :].expand(
            -1, action_embeddings.shape[1], opponent_actions.shape[1], -1
        )
        own_pair = action_embeddings[:, :, None, :].expand_as(pair_context)
        opp_pair = opponent_actions[:, None, :, :].expand(
            -1, action_embeddings.shape[1], -1, -1
        )
        interactions = self.interaction_score(
            torch.cat((pair_context, own_pair, opp_pair), dim=-1)
        ).squeeze(-1)
        interactions = interactions.masked_fill(
            ~batch["opponent_mask"][:, None, :], 0.0
        )
        battle_logits = base_logits + (interactions * q[:, None, :]).sum(dim=-1)
        is_lead = batch["phase_is_lead"]
        logits = torch.where(is_lead[:, None], lead_logits, battle_logits)
        if not batch["action_mask"].any(dim=1).all():
            raise ValueError("each sample requires at least one own action candidate")
        logits = logits.masked_fill(~batch["action_mask"], -torch.inf)
        value = torch.where(
            is_lead,
            self.lead_value(lead_state).squeeze(-1),
            self.battle_value(battle_context).squeeze(-1),
        ).tanh()
        return {
            "logits": logits,
            "value": value,
            "opponent_logits": opponent_logits,
            "memory": (hidden_self, hidden_public),
        }

    def policy_parameters(self):
        opponent_ids = {id(parameter) for parameter in self.opponent.parameters()}
        return (
            parameter
            for parameter in self.parameters()
            if id(parameter) not in opponent_ids
        )

    def opponent_parameters(self):
        return self.opponent.parameters()
