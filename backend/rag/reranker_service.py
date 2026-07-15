from __future__ import annotations

import logging
from typing import Any

from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)


class RerankerService:
    def __init__(self, settings) -> None:
        self.settings = settings
        self._model: CrossEncoder | None = None

    @property
    def model(self) -> CrossEncoder:
        if self._model is None:
            logger.info(
                "reranker_service.py model loading model_name=%s device=%s",
                self.settings.reranker_model_name,
                self.settings.reranker_device,
            )
            self._model = CrossEncoder(
                self.settings.reranker_model_name,
                device=self.settings.reranker_device,
                trust_remote_code=False,  #Hugging Face 저장소 쪽의 커스텀 코드 로딩을 허용
            )
            logger.info(
                "reranker_service.py model loaded model_name=%s",
                self.settings.reranker_model_name,
            )
        return self._model

    def rerank(
            self,
            query: str,
            hits: list[dict[str, Any]],
            final_top_n: int | None = None,
    ) -> list[dict[str, Any]]:
        if not query or not query.strip():
            logger.info("reranker_service.py rerank skipped empty query")
            return hits

        if not hits:
            logger.info("reranker_service.py rerank skipped empty hits")
            return hits

        limit = final_top_n or self.settings.reranker_final_top_n
        pairs: list[tuple[str, str]] = []

        valid_hits: list[dict[str, Any]] = []
        for hit in hits:
            text = (hit.get("text") or "").strip()
            if not text:
                continue
            valid_hits.append(hit)
            pairs.append((query, text))

        if not pairs:
            logger.info("reranker_service.py rerank skipped no valid texts")
            return hits[:limit]

        scores = self.model.predict(pairs)

        rescored: list[dict[str, Any]] = []
        for hit, score in zip(valid_hits, scores):
            item = dict(hit)
            item["rerank_score"] = float(score)
            rescored.append(item)

        rescored.sort(key=lambda x: x.get("rerank_score", 0.0), reverse=True)

        logger.info(
            "reranker_service.py rerank completed input_hits=%d valid_hits=%d output_hits=%d top_relative_path=%s top_score=%s",
            len(hits),
            len(valid_hits),
            min(len(rescored), limit),
            rescored[0].get("relative_path") if rescored else None,
            rescored[0].get("rerank_score") if rescored else None,
        )

        return rescored[:limit]