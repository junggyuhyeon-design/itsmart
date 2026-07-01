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

    # ── 컬렉션 관리 ─────────────────────────────────────────────
    def _collection_exists(self) -> bool:
        """Qdrant 컬렉션 생성 여부 조회"""
        try:
            collections = self.client.get_collections().collections
            return any(c.name == self.settings.qdrant_collection for c in collections)
        except Exception:
            return False

    def ensure_collection(self, vector_size: int) -> None:
        """Qdrant 컬렉션을 생성한다."""
        try:
            if not self._collection_exists():
                # ? create_collection 속성 설명
                # collection_name  : 컬렉션명
                # vectors_config   : 컬렉션에 저장될 벡터의 설정
                #   size           : 벡터차원의 수(BAAI/bge-m3)
                #   distance       : 벡터 간 유사도를 계산할 때 사용할 거리
                self.client.create_collection(
                    collection_name=self.settings.qdrant_collection,
                    vectors_config=VectorParams(
                        size=vector_size, distance=Distance.COSINE
                    ),
                )
                logger.info("Qdrant 컬렉션 생성: %s", self.settings.qdrant_collection)
        except Exception:
            logger.exception("ensure_collection 실패")
            raise

    # ── 저장 ────────────────────────────────────────────────────
    def upsert_chunks(
        self, chunks: list[dict[str, Any]], vectors: list[list[float]]
    ) -> int:
        """청크와 벡터를 Qdrant에 저장. 저장된 포인트 수 반환."""
        if not chunks or not vectors:
            return 0
        try:
            points = [
                PointStruct(
                    id=hashlib.md5(
                        f"{chunk['project_name']}:{chunk['relative_path']}:{idx}".encode()
                    ).hexdigest(),
                    vector=vector,
                    payload=chunk,
                )
                for idx, (chunk, vector) in enumerate(zip(chunks, vectors))
            ]
            self.client.upsert(
                collection_name=self.settings.qdrant_collection,
                points=points,
            )
            return len(points)
        except Exception:
            logger.exception("upsert_chunks 실패 (chunk 수: %d)", len(chunks))
            raise

    # ── 검색 ────────────────────────────────────────────────────
    def search(
        self,
        query_vector: list[float],
        project_id: str | None = None,
        top_k: int | None = None,
        layer_filter: str | None = None,      # "controller" | "service" | "mapper" | "ddl" …
        extension_filter: str | None = None,  # "java" | "xml" | "sql" …
    ) -> list[dict[str, Any]]:
        """
        유사 벡터 검색.
        - layer_filter / extension_filter 로 Qdrant payload 필터링
        """
        if not self._collection_exists():
            logger.warning("search 호출 시 컬렉션 없음 — 인덱싱 전 상태")
            return []

        conditions = []
        if project_id:
            conditions.append(
                FieldCondition(key="project_id", match=MatchValue(value=project_id))
            )
        if layer_filter:
            conditions.append(
                FieldCondition(key="layer_type", match=MatchValue(value=layer_filter))
            )
        if extension_filter:
            conditions.append(
                FieldCondition(
                    key="extension", match=MatchValue(value=extension_filter)
                )
            )

        query_filter = Filter(must=conditions) if conditions else None

        try:
            results = self.client.query_points(
                collection_name=self.settings.qdrant_collection,
                query=query_vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,  # 메타데이터 반환여부
            ).points
            return [{"score": r.score, **r.payload} for r in results]
        except Exception:
            logger.exception("search 실패")
            raise

    # ── 전체 검색 (Mermaid 분석용) ────────────────────────────
    def scroll_all(
        self,
        project_id: str | None = None,
        keyword_hint: str | None = None,
        batch_size: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Qdrant 전체 청크를 페이지 단위로 순회해 반환.
        벡터 검색 없이 payload 전체를 가져온다 — analyze_db_relations() 전용.

        relative_path_keyword:
        Qdrant payload filter는 exact/prefix match만 지원하므로
        부분 문자열 매칭은 Python 레벨에서 후처리한다.
        entity_filter("USER") → relative_path나 file_name에 "USER" 포함 청크만 반환.
        """
        if not self._collection_exists():
            return []

        conditions = []
        # Qdrant 에서 검색할 필터 설정
        if project_id:
            conditions.append(
                FieldCondition(key="project_id", match=MatchValue(value=project_id))
            )
        scroll_filter = Filter(must=conditions) if conditions else None

        all_payloads: list[dict[str, Any]] = []
        kw = keyword_hint.upper() if keyword_hint else None
        offset = None

        try:
            while True:
                results, next_offset = self.client.scroll(
                    collection_name=self.settings.qdrant_collection,
                    scroll_filter=scroll_filter,  # payload 기반 필터 : project_id 설정.
                    limit=batch_size,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for point in results:
                    if not point.payload:
                        continue
                    if kw:  # key word 필터가 존재한다면 경로/파일명/클래스명에서 탐색
                        rp = (point.payload.get("relative_path") or "").upper()
                        fn = (point.payload.get("file_name") or "").upper()
                        cn = (point.payload.get("class_name") or "").upper()
                        if kw not in rp and kw not in fn and kw not in cn:
                            continue
                    all_payloads.append(point.payload)
                if next_offset is None:
                    break
                offset = next_offset
        except Exception:
            logger.exception("scroll_all 실패")

        return all_payloads

    # ── 관리 ────────────────────────────────────────────────────

    def count_points(self) -> int:
        if not self._collection_exists():
            return 0
        try:
            return int(
                self.client.count(collection_name=self.settings.qdrant_collection).count
                or 0
            )
        except Exception:
            logger.warning("count_points 실패 — 0 반환")
            return 0

    def delete_collection(self) -> None:
        if not self._collection_exists():
            return
        try:
            self.client.delete_collection(
                collection_name=self.settings.qdrant_collection
            )
            logger.info("Qdrant 컬렉션 삭제: %s", self.settings.qdrant_collection)
        except Exception:
            logger.warning("컬렉션 삭제 실패")

    def reset_collection(self, vector_size: int) -> None:
        self.delete_collection()
        self.ensure_collection(vector_size)
        logger.info("Qdrant 컬렉션 초기화 완료: %s", self.settings.qdrant_collection)
