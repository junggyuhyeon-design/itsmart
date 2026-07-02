from __future__ import annotations

from sentence_transformers import SentenceTransformer

from config import Settings


class EmbeddingService:
    def __init__(self, settings: Settings) -> None:
        self.model = SentenceTransformer(settings.embedding_model)

    @property
    def dimension(self) -> int:
        return int(self.model.get_sentence_embedding_dimension())

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self.model.encode(texts).tolist()

    def embed_query(self, query: str) -> list[float]:
        vectors = self.embed_texts([query])
        return vectors[0] if vectors else []