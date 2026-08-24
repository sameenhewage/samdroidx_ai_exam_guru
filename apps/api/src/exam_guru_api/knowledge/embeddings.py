import hashlib
from dataclasses import dataclass


class EmbeddingContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    provider: str
    model: str
    dimension: int
    version: str
    config_fingerprint: str


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vector: tuple[float, ...]
    config: EmbeddingConfig


class DeterministicEmbeddingProvider:
    def embed(self, text: str, config: EmbeddingConfig) -> EmbeddingResult:
        if not text.strip():
            raise EmbeddingContractError("embedding text must be non-blank")
        if (
            not config.provider.strip()
            or not config.model.strip()
            or not config.version.strip()
            or not config.config_fingerprint.strip()
            or not 1 <= config.dimension <= 4096
        ):
            raise EmbeddingContractError("embedding configuration is invalid")
        digest = hashlib.shake_256(f"{config.config_fingerprint}\0{text}".encode()).digest(
            config.dimension
        )
        vector = tuple((value / 127.5) - 1.0 for value in digest)
        return EmbeddingResult(vector=vector, config=config)
