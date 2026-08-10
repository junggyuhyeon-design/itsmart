from __future__ import annotations

import logging

from sentence_transformers import SentenceTransformer

from config import Settings
from fastembed import SparseTextEmbedding

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self, settings: Settings) -> None:
        logger.info(
            "[embedder.py][__init__][초기화 시작 ★] embedding_model=%s sparse_embedding_model=%s",
            getattr(settings, "embedding_model", None),        # BAAI/bge-m3
            getattr(settings, "sparse_embedding_model", None), # Qdrant/bm25 [Sparse Embedding(BM25 기반 가중치 생성기)]
        )

        self.dense_model = SentenceTransformer(settings.embedding_model)
        self.sparse_model = SparseTextEmbedding(model_name=settings.sparse_embedding_model)

        logger.info(
            "[embedder.py][__init__][초기화 완료 ★] dense_model=%s sparse_model=%s",
            getattr(settings, "embedding_model", None),
            getattr(settings, "sparse_embedding_model", None),
        )

    @property
    def dimension(self) -> int:
        value = self.dense_model.get_embedding_dimension()

        logger.info(
            "[embedder.py][dimension][차원 조회 ★] dimension=%s",
            value,
        )

        return value

    def embed_texts_dense(self, texts: list[str]) -> list[list[float]]:
        logger.info(
            "[embedder.py][embed_texts_dense][Dense 임베딩 시작 ★] text_count=%d",
            len(texts or []),
        )

        if not texts:
            logger.warning(
                "[embedder.py][embed_texts_dense][Dense 임베딩 스킵 ★] empty texts"
            )
            return []

        result = self.dense_model.encode(texts).tolist()

        logger.info(
            "[embedder.py][embed_texts_dense][Dense 임베딩 완료 ★] vector_count=%d first_vector_dim=%s",
            len(result or []),
            len(result[0]) if result else 0,
        )

        return result

    def embed_query_dense(self, query: str) -> list[float]:
        logger.info(
            "[embedder.py][embed_query_dense][Dense 질의 임베딩 시작 ★] query_len=%d preview=%s",
            len(query or ""),
            (query or "")[:200],
        )

        vectors = self.embed_texts_dense([query])
        result = vectors[0] if vectors else []

        logger.info(
            "[embedder.py][embed_query_dense][Dense 질의 임베딩 완료 ★] vector_dim=%s",
            len(result) if result else 0,
        )

        return result

    def embed_texts_sparse(self, texts: list[str]):
        logger.info(
            "[embedder.py][embed_texts_sparse][Sparse 임베딩 시작 ★] text_count=%d",
            len(texts or []),
        )

        if not texts:
            logger.warning(
                "[embedder.py][embed_texts_sparse][Sparse 임베딩 스킵 ★] empty texts"
            )
            return []

        result = list(self.sparse_model.embed(texts))

        logger.info(
            "[embedder.py][embed_texts_sparse][Sparse 임베딩 완료 ★] embedding_count=%d first_indices_len=%s first_values_len=%s",
            len(result or []),
            len(result[0].indices) if result else 0,
            len(result[0].values) if result else 0,
        )

        return result

    def embed_query_sparse(self, query: str):
        logger.info(
            "[embedder.py][embed_query_sparse][Sparse 질의 임베딩 시작 ★] query_len=%d preview=%s",
            len(query or ""),
            (query or "")[:200],
        )

        vectors = self.embed_texts_sparse([query])
        result = vectors[0] if vectors else None

        logger.info(
            "[embedder.py][embed_query_sparse][Sparse 질의 임베딩 완료 ★] indices_len=%s values_len=%s",
            len(result.indices) if result else 0,
            len(result.values) if result else 0,
        )

        return result