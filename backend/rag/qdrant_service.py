from typing import Any
from qdrant_client import QdrantClient, models
from qdrant_client.models import Distance, VectorParams, PointStruct
from config import Settings

class QdrantService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = None

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            self._client = QdrantClient(url=self.settings.qdrant_url)
        return self._client

    def ensure_collection(self, vector_size: int) -> None:
        collections = self.client.get_collections().collections
        if not any(c.name == self.settings.qdrant_collection for c in collections):
            self.client.create_collection(
                collection_name=self.settings.qdrant_collection,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )

    def upsert_chunks(self, chunks: list[dict[str, Any]], vectors: list[list[float]]) -> int:
        points = [PointStruct(id=c["chunk_id"], vector=v, payload=c) for c, v in zip(chunks, vectors)]
        self.client.upsert(collection_name=self.settings.qdrant_collection, points=points)
        return len(points)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        try:
            results = self.client.query_points(
                collection_name=self.settings.qdrant_collection,
                query=query_vector,
                limit=top_k,
                with_payload=True,
            ).points
            return [{"score": r.score, **r.payload} for r in results]
        except Exception as e:
            if "doesn't exist" in str(e) or "Not found" in str(e):
                return []  # 컬렉션 없음 → 빈 결과 반환 (인덱싱 전 상태)
            raise

    def count_points(self) -> int:
        try:
            result = self.client.count(collection_name=self.settings.qdrant_collection)
            return int(result.count or 0)
        except Exception:
            return 0

    def delete_all(self):
        try:
            self.client.delete_collection(collection_name=self.settings.qdrant_collection)
        except Exception:
            pass