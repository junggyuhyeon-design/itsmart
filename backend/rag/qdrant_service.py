from typing import Any
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
from backend.config import Settings # 경로 수정

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
        results = self.client.search(collection_name=self.settings.qdrant_collection, query_vector=query_vector, limit=top_k, with_payload=True)
        return [{"score": r.score, **r.payload} for r in results]

    def count_points(self) -> int:
        try: return self.client.count(collection_name=self.settings.qdrant_collection).count
        except: return 0

    def delete_all(self):
        try: self.client.delete_collection(collection_name=self.settings.qdrant_collection)
        except: pass