from __future__ import annotations

import hashlib
import logging
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    FilterSelector,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
    SparseVectorParams,
)

from config import Settings

logger = logging.getLogger(__name__)


class QdrantService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: QdrantClient | None = None

        logger.info(
            "[qdrant_service.py][__init__][초기화 완료 ★] qdrant_url=%s collection=%s",
            getattr(settings, "qdrant_url", None),
            getattr(settings, "qdrant_collection", None),
        )

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            logger.info(
                "[qdrant_service.py][client][클라이언트 생성 ★] qdrant_url=%s",
                self.settings.qdrant_url,
            )
            self._client = QdrantClient(url=self.settings.qdrant_url)

        logger.info(
            "[qdrant_service.py][client][클라이언트 반환] initialized=%s",
            self._client is not None,
            )
        return self._client

    def collection_exists(self) -> bool:
        logger.info(
            "[qdrant_service.py][collection_exists][컬렉션 존재 확인 시작 ★] collection=%s",
            self.settings.qdrant_collection,
        )
        try:
            collections = self.client.get_collections().collections
            exists = any(
                collection.name == self.settings.qdrant_collection
                for collection in collections
            )
            logger.info(
                "[qdrant_service.py][collection_exists][컬렉션 존재 확인 완료 ★] collection=%s exists=%s total_collections=%d",
                self.settings.qdrant_collection,
                exists,
                len(collections or []),
            )
            return exists
        except Exception:
            logger.exception(
                "[qdrant_service.py][collection_exists][컬렉션 존재 확인 실패 ★] collection=%s",
                self.settings.qdrant_collection,
            )
            return False

    def ensure_collection(self, vector_size: int) -> None:
        logger.info(
            "[qdrant_service.py][ensure_collection][컬렉션 보장 시작 ★] collection=%s vector_size=%s",
            self.settings.qdrant_collection,
            vector_size,
        )

        if not self.collection_exists():
            self.client.create_collection(
                collection_name=self.settings.qdrant_collection,
                vectors_config={
                    "dense": VectorParams(size=vector_size, distance=Distance.COSINE),
                },
                sparse_vectors_config={
                    "sparse": SparseVectorParams(),
                },
            )
            logger.info(
                "[qdrant_service.py][ensure_collection][컬렉션 생성 완료 ★] collection=%s dense_name=%s sparse_name=%s vector_size=%s",
                self.settings.qdrant_collection,
                "dense",
                "sparse",
                vector_size,
            )
        else:
            logger.info(
                "[qdrant_service.py][ensure_collection][컬렉션 이미 존재 ★] collection=%s",
                self.settings.qdrant_collection,
            )

    def recreate_collection(self, vector_size: int) -> None:
        logger.info(
            "[qdrant_service.py][recreate_collection][컬렉션 재생성 시작 ★] collection=%s vector_size=%s",
            self.settings.qdrant_collection,
            vector_size,
        )

        if self.collection_exists():
            self.client.delete_collection(self.settings.qdrant_collection)
            logger.info(
                "[qdrant_service.py][recreate_collection][기존 컬렉션 삭제 완료 ★] collection=%s",
                self.settings.qdrant_collection,
            )

        self.client.create_collection(
            collection_name=self.settings.qdrant_collection,
            vectors_config={
                "dense": VectorParams(size=vector_size, distance=Distance.COSINE),
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams(),
            },
        )

        logger.info(
            "[qdrant_service.py][recreate_collection][컬렉션 재생성 완료 ★] collection=%s dense_name=%s sparse_name=%s vector_size=%s",
            self.settings.qdrant_collection,
            "dense",
            "sparse",
            vector_size,
        )

    def upsert_chunks(
            self,
            chunks: list[dict[str, Any]],
            dense_vectors: list[list[float]],
            sparse_vectors: list[Any],
    ) -> int:
        logger.info(
            "[qdrant_service.py][upsert_chunks][업서트 시작 ★] chunk_count=%d dense_count=%d sparse_count=%d",
            len(chunks or []),
            len(dense_vectors or []),
            len(sparse_vectors or []),
        )

        if not chunks or not dense_vectors:
            logger.warning(
                "[qdrant_service.py][upsert_chunks][업서트 스킵 ★] invalid inputs chunk_count=%d dense_count=%d sparse_count=%d",
                len(chunks or []),
                len(dense_vectors or []),
                len(sparse_vectors or []),
            )
            return 0

        points: list[PointStruct] = []

        for index, (chunk, dense_vector, sparse_vector) in enumerate(
                zip(chunks, dense_vectors, sparse_vectors)
        ):
            point_id = hashlib.md5(
                f"{chunk.get('project_id', '')}|{chunk.get('relative_path', '')}|{chunk.get('chunk_index', index)}".encode()
            ).hexdigest()

            payload = {
                "project_id": chunk.get("project_id", ""),
                "project_name": chunk.get("project_name", ""),
                "file_name": chunk.get("file_name", ""),
                "extension": chunk.get("extension", ""),
                "relative_path": chunk.get("relative_path", ""),
                "saved_path": chunk.get("saved_path", ""),
                "file_path": chunk.get("file_path", chunk.get("saved_path", "")),
                "chunk_index": chunk.get("chunk_index", index),
                "text": chunk.get("text", ""),
                "start_line": chunk.get("start_line"),
                "end_line": chunk.get("end_line"),
                "file_size": chunk.get("file_size", 0),
                "source_type": chunk.get("source_type", ""),
                "root_container_name": chunk.get("root_container_name", ""),
                "layer_type": chunk.get("layer_type", ""),
                "class_name": chunk.get("class_name", ""),
                "package": chunk.get("package", ""),
                "content_type": chunk.get("content_type", ""),
                "chunk_type": chunk.get("chunk_type", "text"),
            }

            logger.info(
                "[qdrant_service.py][upsert_chunks][포인트 준비 ★] step=%d relative_path=%s chunk_index=%s text_len=%d sparse_indices_len=%s",
                index + 1,
                chunk.get("relative_path", ""),
                chunk.get("chunk_index", index),
                len(chunk.get("text", "") or ""),
                len(sparse_vector.indices) if sparse_vector is not None else 0,
                )

            points.append(
                PointStruct(
                    id=point_id,
                    vector={
                        "dense": dense_vector,
                        "sparse": {
                            "indices": sparse_vector.indices.tolist()
                            if sparse_vector is not None
                            else [],
                            "values": sparse_vector.values.tolist()
                            if sparse_vector is not None
                            else [],
                        },
                    },
                    payload=payload,
                )
            )


        self.client.upsert( # id 를 기준으로 upsert 수행, 동일한 id가 존재하면 덮어쓰기
            collection_name=self.settings.qdrant_collection,
            points=points,
        )

        logger.info(
            "[qdrant_service.py][upsert_chunks][업서트 완료 ★] upserted_count=%d collection=%s",
            len(points),
            self.settings.qdrant_collection,
        )

        return len(points)

    def delete_by_project_id(self, project_id: str) -> int:
        logger.info(
            "[qdrant_service.py][delete_by_project_id][프로젝트 삭제 시작 ★] project_id=%s",
            project_id,
        )

        if not project_id:
            logger.warning(
                "[qdrant_service.py][delete_by_project_id][프로젝트 삭제 스킵 ★] empty project_id"
            )
            return 0

        try:
            result = self.client.delete(
                collection_name=self.settings.qdrant_collection,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key="project_id",
                                match=MatchValue(value=project_id),
                            )
                        ]
                    )
                ),
            )
            logger.info(
                "[qdrant_service.py][delete_by_project_id][프로젝트 삭제 완료 ★] project_id=%s result=%s",
                project_id,
                result,
            )
            return 1
        except Exception:
            logger.exception(
                "[qdrant_service.py][delete_by_project_id][프로젝트 삭제 실패 ★] project_id=%s",
                project_id,
            )
            return 0

    def search(
            self,
            dense_query_vector: list[float],
            sparse_query_vector: Any,
            *,
            project_id: str | None = None,
            top_k: int = 5,
            layer_filter: str | None = None,
            extension_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        logger.info(
            "[qdrant_service.py][search][검색 시작 ★] top_k=%s project_id=%s layer_filter=%s extension_filter=%s dense_dim=%d sparse_indices_len=%s",
            top_k,
            project_id,
            layer_filter,
            extension_filter,
            len(dense_query_vector or []),
            len(sparse_query_vector.indices) if sparse_query_vector is not None else 0,
        )

        must = []

        if project_id:
            must.append(
                FieldCondition(
                    key="project_id",
                    match=MatchValue(value=project_id),
                )
            )

        if layer_filter:
            must.append(
                FieldCondition(
                    key="layer_type",
                    match=MatchValue(value=layer_filter),
                )
            )

        if extension_filter:
            must.append(
                FieldCondition(
                    key="extension",
                    match=MatchValue(value=extension_filter),
                )
            )

        query_filter = Filter(must=must) if must else None

        logger.info(
            "[qdrant_service.py][search][필터 구성 완료] must_count=%d has_filter=%s",
            len(must),
            query_filter is not None,
            )

        try:
            logger.info(
                "[qdrant_service.py][search][query_points(dense) 호출 시작 ★] collection=%s using=%s limit=%d",
                self.settings.qdrant_collection,
                "dense",
                max(1, top_k),
            )

            response = self.client.query_points(
                collection_name=self.settings.qdrant_collection,
                query=dense_query_vector,
                using="dense",
                query_filter=query_filter,
                limit=max(1, top_k),
                with_payload=True,
                with_vectors=False,
            )

            hits: list[dict[str, Any]] = []
            for result in response.points:
                payload = dict(result.payload or {})
                payload["score"] = float(result.score)
                hits.append(payload)

            logger.info(
                "[qdrant_service.py][search][query_points(dense) 호출 완료 ★] hit_count=%d",
                len(hits),
            )

            return hits

        except Exception as e:
            logger.exception(
                "[qdrant_service.py][search][query_points(dense) 실패 ★] error_type=%s error=%s",
                type(e).__name__,
                str(e),
            )
            return []
