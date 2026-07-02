import hashlib
import logging
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

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

    def _collection_exists(self) -> bool:
        try:
            collections = self.client.get_collections().collections
            return any(c.name == self.settings.qdrant_collection for c in collections)
        except Exception:
            return False

    def ensure_collection(self, vector_size: int) -> None:
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
        if not chunks or not vectors:
            return 0

        try:
            points: list[PointStruct] = []

            for idx, (chunk, vector) in enumerate(zip(chunks, vectors)):
                point_id = hashlib.md5(
                    f"{chunk.get('project_name','')}:{chunk.get('relative_path','')}:{chunk.get('chunk_index', idx)}".encode()
                ).hexdigest()

                payload = {
                    "project_id": chunk.get("project_id", ""),
                    "project_name": chunk.get("project_name", ""),
                    "file_name": chunk.get("file_name", ""),
                    "extension": chunk.get("extension", ""),
                    "relative_path": chunk.get("relative_path", ""),
                    "saved_path": chunk.get("saved_path", ""),
                    "file_path": chunk.get("file_path", chunk.get("saved_path", "")),
                    "chunk_index": chunk.get("chunk_index", idx),
                    "file_size": chunk.get("file_size", 0),
                    "source_type": chunk.get("source_type", ""),
                    "root_container_name": chunk.get("root_container_name", ""),
                    "layer_type": chunk.get("layer_type", ""),
                    "class_name": chunk.get("class_name", ""),
                    "package": chunk.get("package", ""),
                    "content_type": chunk.get("content_type", ""),
                    "text": chunk.get("text", ""),
                }

                points.append(
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload=payload,
                    )
                )

            self.client.upsert(
                collection_name=self.settings.qdrant_collection,
                points=points,
            )
            return len(points)

        except Exception:
            logger.exception("upsert_chunks 실패 (chunk 수: %d)", len(chunks))
            raise

    def search(
            self,
            query_vector: list[float],
            project_id: str | None = None,
            top_k: int = 5,
            layer_filter: str | None = None,
            extension_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        if top_k <= 0:
            return []

        if not self._collection_exists():
            logger.warning("search 호출 시 컬렉션 없음 — 인덱싱 전 상태")
            return []

        conditions = []
        if project_id:
            conditions.append(FieldCondition(key="project_id", match=MatchValue(value=project_id)))
        if layer_filter:
            conditions.append(FieldCondition(key="layer_type", match=MatchValue(value=layer_filter)))
        if extension_filter:
            conditions.append(FieldCondition(key="extension", match=MatchValue(value=extension_filter)))

        query_filter = Filter(must=conditions) if conditions else None

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
        if not self._collection_exists():
            return 0
        try:
            return int(self.client.count(collection_name=self.settings.qdrant_collection).count or 0)
        except Exception:
            logger.warning("count_points 실패 — 0 반환")
            return 0

    def delete_collection(self) -> None:
        if not self._collection_exists():
            return
        try:
            self.client.delete_collection(collection_name=self.settings.qdrant_collection)
            logger.info("Qdrant 컬렉션 삭제: %s", self.settings.qdrant_collection)
        except Exception:
            logger.warning("컬렉션 삭제 실패")

    def reset_collection(self, vector_size: int) -> None:
        self.delete_collection()
        self.ensure_collection(vector_size)
        logger.info("Qdrant 컬렉션 초기화 완료: %s", self.settings.qdrant_collection)