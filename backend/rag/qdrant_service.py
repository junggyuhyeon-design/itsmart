from __future__ import annotations

import hashlib
import logging
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

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

    def collection_exists(self) -> bool:
        try:
            collections = self.client.get_collections().collections
            return any(collection.name == self.settings.qdrant_collection for collection in collections)
        except Exception:
            return False

    def ensure_collection(self, vector_size: int) -> None:
        if not self.collection_exists():
            self.client.create_collection(
                collection_name=self.settings.qdrant_collection,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
            logger.info("Qdrant collection created: %s", self.settings.qdrant_collection)

    def recreate_collection(self, vector_size: int) -> None:
        if self.collection_exists():
            self.client.delete_collection(self.settings.qdrant_collection)
        self.client.create_collection(
            collection_name=self.settings.qdrant_collection,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )

    def upsert_chunks(self, chunks: list[dict[str, Any]], vectors: list[list[float]]) -> int:
        if not chunks or not vectors:
            return 0

        points: list[PointStruct] = []
        for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
            point_id = hashlib.md5(
                f"{chunk.get('project_id','')}|{chunk.get('relative_path','')}|{chunk.get('chunk_index', index)}".encode()
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

            points.append(PointStruct(id=point_id, vector=vector, payload=payload))

        self.client.upsert(collection_name=self.settings.qdrant_collection, points=points)
        return len(points)

    def search(
            self,
            query_vector: list[float],
            *,
            project_id: str | None = None,
            top_k: int = 5,
            layer_filter: str | None = None,
            extension_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        must = []
        if project_id:
            must.append(FieldCondition(key="project_id", match=MatchValue(value=project_id)))
        if layer_filter:
            must.append(FieldCondition(key="layer_type", match=MatchValue(value=layer_filter)))
        if extension_filter:
            must.append(FieldCondition(key="extension", match=MatchValue(value=extension_filter)))

        query_filter = Filter(must=must) if must else None

        try:
            results = self.client.search(
                collection_name=self.settings.qdrant_collection,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=max(1, top_k),
                with_payload=True,
            )
            hits = []
            for result in results:
                payload = dict(result.payload or {})
                payload["score"] = float(result.score)
                hits.append(payload)
            return hits
        except Exception:
            logger.exception("qdrant search failed")
            return []