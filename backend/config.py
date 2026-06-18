import os

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    ollama_base_url: str
    ollama_model: str
    qdrant_url: str
    qdrant_collection: str
    embedding_model: str
    chunk_size: int
    chunk_overlap: int
    top_k: int
    upload_dir: Path


@lru_cache
def get_settings() -> Settings:
    return Settings( # os.environ.get() 로 Docker 환경변수의 설정값을 가져옴.
        ollama_base_url=os.environ.get(
            "OLLAMA_BASE_URL",
            "http://codeMind-ollama:11434"
        ),
        ollama_model=os.environ.get(
            "OLLAMA_MODEL",
            "qwen2.5-coder:3b"
        ),
        qdrant_url=os.environ.get(
            "QDRANT_URL",
            "http://codeMind-qdrant:6333"
        ),
        qdrant_collection=os.environ.get(
            "QDRANT_COLLECTION",
            "source_chunks"
        ),
        embedding_model=os.environ.get(
            "EMBEDDING_MODEL",
            "BAAI/bge-m3"
        ),
        chunk_size=int(
            os.environ.get("CHUNK_SIZE", 800)
        ),
        chunk_overlap=int(
            os.environ.get("CHUNK_OVERLAP", 100)
        ),
        top_k=int(
            os.environ.get("TOP_K", 5)
        ),
        upload_dir=Path(
            os.environ.get("UPLOAD_DIR", "/data/uploads")
        )
    )