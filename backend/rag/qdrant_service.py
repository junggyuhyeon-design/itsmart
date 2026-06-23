import logging
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, Distance, PointStruct, VectorParams

from config import Settings

logger = logging.getLogger(__name__)


class QdrantService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: QdrantClient | None = None

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            self._client = QdrantClient(url=self.settings.qdrant_url)
        return self._client

    # ── 컬렉션 관리 ─────────────────────────────────────────────

    def _collection_exists(self) -> bool:
        """컬렉션 존재 여부 확인 (404/예외 없이 bool 반환)."""
        try:
            collections = self.client.get_collections().collections
            return any(c.name == self.settings.qdrant_collection for c in collections)
        except Exception:
            return False

    def ensure_collection(self, vector_size: int) -> None:
        """컬렉션이 없으면 생성."""
        try:
            if not self._collection_exists():
                self.client.create_collection(
                    collection_name=self.settings.qdrant_collection,
                    vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
                )
                logger.info("Qdrant 컬렉션 생성: %s", self.settings.qdrant_collection)
        except Exception:
            logger.exception("ensure_collection 실패")
            raise

    def upsert_chunks(self, chunks: list[dict[str, Any]], vectors: list[list[float]]) -> int:
        """청크와 벡터를 Qdrant에 저장. 저장된 포인트 수 반환."""
        if not chunks or not vectors:
            return 0
        try:
            points = [
                PointStruct(id=c["chunk_id"], vector=v, payload=c)
                for c, v in zip(chunks, vectors)
            ]
            self.client.upsert(
                collection_name=self.settings.qdrant_collection,
                points=points,
            )
            return len(points)
        except Exception:
            logger.exception("upsert_chunks 실패 (chunk 수: %d)", len(chunks))
            raise

    def search(self,
                query_vector: list[float],
                project_id: str | None = None,
                top_k: int = 5
        ) -> list[dict[str, Any]]:
        """유사 벡터 검색. project_id 지정 시 해당 프로젝트만 검색, None이면 전체."""
        if not self._collection_exists():
            logger.warning("search 호출 시 컬렉션 없음 — 인덱싱 전 상태")
            return []

        query_filter = None
        if project_id:
            query_filter = Filter(
                must=[FieldCondition(
                    key="project_id",
                    match=MatchValue(value=project_id)
                )]
            )

        try:
            results = self.client.query_points(
                collection_name=self.settings.qdrant_collection,
                query=query_vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
            ).points
            return [{"score": r.score, **r.payload} for r in results]
        except Exception:
            logger.exception("search 실패")
            raise

    def count_points(self) -> int:
        """저장된 포인트 수 반환. 컬렉션 없거나 오류 시 0 반환."""
        if not self._collection_exists():
            return 0
        try:
            result = self.client.count(collection_name=self.settings.qdrant_collection)
            return int(result.count or 0)
        except Exception:
            logger.warning("count_points 실패 — 0 반환")
            return 0

    def delete_collection(self) -> None:
        """컬렉션 삭제."""
        if not self._collection_exists():
            logger.info("삭제 대상 컬렉션 없음: %s", self.settings.qdrant_collection)
            return
        try:
            self.client.delete_collection(collection_name=self.settings.qdrant_collection)
            logger.info("Qdrant 컬렉션 삭제: %s", self.settings.qdrant_collection)
        except Exception:
            logger.warning("컬렉션 삭제 실패")

    def reset_collection(self, vector_size: int) -> None:
        """컬렉션 삭제 후 재생성 (완전 초기화)."""
        self.delete_collection()
        self.ensure_collection(vector_size)
        logger.info("Qdrant 컬렉션 초기화 완료: %s", self.settings.qdrant_collection)
