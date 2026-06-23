import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_GB = 1024 * 1024 * 1024   # 1 GiB in bytes
_MB = 1024 * 1024          # 1 MiB in bytes


@dataclass(frozen=True)
class Settings:
    ollama_base_url:       str
    ollama_model:          str
    qdrant_url:            str
    qdrant_collection:     str
    embedding_model:       str
    chunk_size:            int
    chunk_overlap:         int
    top_k:                 int
    upload_chunk_size:     int   # 스트리밍 청크 단위 (bytes)
    max_file_size:         int   # 단일 파일 최대 크기 (bytes)
    max_files_per_request: int
    upload_dir:            Path


@lru_cache
def get_settings() -> Settings:
    return Settings(
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
        upload_chunk_size=int(
            os.environ.get("UPLOAD_CHUNK_SIZE", _MB)          # 기본 1 MiB
        ),
        max_file_size=int(
            os.environ.get("MAX_FILE_SIZE", _GB)              # 기본 1 GiB
        ),
        max_files_per_request=int(
            os.environ.get("MAX_FILES_PER_REQUEST", 5)
        ),
        upload_dir=Path(
            os.environ.get("UPLOAD_DIR", "/data/uploads")
        )
    )