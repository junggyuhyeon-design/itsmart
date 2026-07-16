from __future__ import annotations

import hashlib
import logging
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,            # 벡터 간 거리 계산 방식 (COSINE, DOT, EUCLID 등)
    FieldCondition,      # payload(메타데이터) 필터 조건 1개 정의
    FilterSelector,      # 필터를 지정하여 삭제/조회 등에서 사용할 대상 선택
    Filter,              # 여러 FieldCondition을 조합한 전체 필터
    MatchValue,          # 특정 값과 일치하는 조건 (예: file_type="py")
    PointStruct,         # Qdrant에 저장할 데이터(벡터 + payload + id)
    VectorParams,        # Dense Vector 컬렉션 생성 시 벡터 크기, 거리 방식 설정

    #######검색강화
    SparseVectorParams,  # Sparse Vector(BM25 등) 설정
    Fusion,              # Dense + Sparse 검색 결과를 결합하는 방식(RRF 등)
    FusionQuery,         # Hybrid Search(Dense + Sparse)를 수행하는 쿼리
    Prefetch,            # Fusion 검색 전 각각(Dense/Sparse) 미리 검색할 조건
    SparseVector,        # Sparse Vector(인덱스, 값) 데이터 구조
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
                    "sparse": self._sparse_vector_params(),
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
                "sparse": self._sparse_vector_params(),
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

    #sparse 변환 헬퍼
    def _sparse_vector_params(self) -> SparseVectorParams:
        """
        sparse 벡터 설정 반환.
        - sparse_embedding_model == 'Qdrant/bm25' 인 경우 Modifier.IDF 를 적용하여
          진짜 BM25 점수(Inverse Document Frequency 가중) 가 나오도록 구성.
        - 그 외 모델은 기본값 사용.
        주의: modifier 변경은 컬렉션 재생성 + 재인덱싱이 필요 (기존 컬렉션 스키마는 유지됨).
        """
        sparse_model = str(getattr(self.settings, "sparse_embedding_model", "") or "").lower()
        if sparse_model == "qdrant/bm25":
            return SparseVectorParams(modifier=Modifier.IDF)
        return SparseVectorParams()

    def _to_qdrant_sparse_vector(self, sparse_query_vector: Any) -> SparseVector | None:
        """
        fastembed sparse 결과(.indices/.values)를 Qdrant SparseVector 로 변환한다.
        indices/values 가 numpy array 인 경우 tolist() 로 list 로 정규화.
        비어있거나 None 이면 None 반환 (→ dense-only fallback).
        """
        if sparse_query_vector is None:
            return None

        indices = getattr(sparse_query_vector, "indices", None)
        values = getattr(sparse_query_vector, "values", None)

        if indices is None or values is None:
            return None

        indices = indices.tolist() if hasattr(indices, "tolist") else list(indices)
        values = values.tolist() if hasattr(values, "tolist") else list(values)

        if not indices or not values:
            return None

        return SparseVector(
            indices=[int(i) for i in indices],
            values=[float(v) for v in values],
        )

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

        limit = max(1, top_k)
        sparse_vector = self._to_qdrant_sparse_vector(sparse_query_vector)
        # config 의 hybrid_enabled(기본 True) 가 켜져있고 sparse 질의 벡터가 있으면
        # sparse(BM25) + dense(vector) 를 Qdrant RRF 로 퓨전(hybrid) 검색.
        hybrid_enabled = getattr(self.settings, "hybrid_enabled", True)

        retrieval_mode = "dense_only"

        try:
            if hybrid_enabled and sparse_vector is not None and dense_query_vector:
                prefetch_limit = max(limit * 2, 20)

                logger.info(
                    "[qdrant_service.py][search][query_points(hybrid RRF) 호출 시작 ★] collection=%s prefetch_limit=%d final_limit=%d",
                    self.settings.qdrant_collection,
                    prefetch_limit,
                    limit,
                )

                try:
                    response = self.client.query_points(
                        collection_name=self.settings.qdrant_collection,
                        prefetch=[
                            # sparse(BM25) 후보
                            Prefetch(
                                query=sparse_vector,
                                using="sparse",
                                filter=query_filter,
                                limit=prefetch_limit,
                            ),
                            # dense(vector) 후보
                            Prefetch(
                                query=dense_query_vector,
                                using="dense",
                                filter=query_filter,
                                limit=prefetch_limit,
                            ),
                        ],
                        # RRF(Reciprocal Rank Fusion) 로 두 후보군 병합
                        query=FusionQuery(fusion=Fusion.RRF),
                        query_filter=query_filter,
                        limit=limit,
                        with_payload=True,
                        with_vectors=False,
                    )
                    retrieval_mode = "hybrid_rrf"
                except Exception:
                    # hybrid 호출 실패 시 dense-only 로 fallback
                    logger.exception(
                        "[qdrant_service.py][search][hybrid RRF 실패 → dense fallback ★] collection=%s",
                        self.settings.qdrant_collection,
                    )
                    response = self.client.query_points(
                        collection_name=self.settings.qdrant_collection,
                        query=dense_query_vector,
                        using="dense",
                        query_filter=query_filter,
                        limit=limit,
                        with_payload=True,
                        with_vectors=False,
                    )
                    retrieval_mode = "dense_fallback"
            else:
                logger.info(
                    "[qdrant_service.py][search][query_points(dense) 호출 시작 ★] collection=%s using=%s limit=%d hybrid_enabled=%s has_sparse=%s",
                    self.settings.qdrant_collection,
                    "dense",
                    limit,
                    hybrid_enabled,
                    sparse_vector is not None,
                    )

                response = self.client.query_points(
                    collection_name=self.settings.qdrant_collection,
                    query=dense_query_vector,
                    using="dense",
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=True,
                    with_vectors=False,
                )
                retrieval_mode = "dense_only"

            hits: list[dict[str, Any]] = []
            for result in response.points:
                payload = dict(result.payload or {})
                payload["score"] = float(result.score)
                payload["retrieval_mode"] = retrieval_mode
                hits.append(payload)

            logger.info(
                "[qdrant_service.py][search][query_points 호출 완료 ★] retrieval_mode=%s hit_count=%d",
                retrieval_mode,
                len(hits),
            )

            return hits

        except Exception as e:
            logger.exception(
                "[qdrant_service.py][search][query_points 실패 ★] retrieval_mode=%s error_type=%s error=%s",
                retrieval_mode,
                type(e).__name__,
                str(e),
            )
            return []