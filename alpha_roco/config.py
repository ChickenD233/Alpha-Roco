from dataclasses import dataclass
from typing import Any


@dataclass
class ModelConfig:
    hidden_dim: int = 384
    num_heads: int = 8
    num_layers: int = 3
    ffn_multiplier: int = 4
    gru_layers: int = 2
    identity_embedding_dim: int = 32
    category_embedding_dim: int = 16
    dropout: float = 0.0

    def to_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "ModelConfig":
        return cls(**values)
