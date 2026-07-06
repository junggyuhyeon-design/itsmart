from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

GB = 1024 * 1024 * 1024
MB = 1024 * 1024


def _get_env_str(*keys: str, default: str) -> str:
    for key in keys:
        value = os.environ.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _get_env_int(*keys: str, default: int) -> int:
    raw = _get_env_str(*keys, default=str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


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

    upload_chunk_size: int
    max_file_size: int
    max_files_per_request: int
    upload_dir: Path
    extract_dir: Path

    chat_history_turns: int
    chat_history_max_chars: int

    retrieval_candidate_limit: int
    retrieval_max_files: int
    retrieval_max_chunks_per_file: int

    sqlite_db_path: str


@lru_cache
def get_settings() -> Settings:
    return Settings(
        ollama_base_url=_get_env_str("OLLAMA_BASE_URL", "OLLAMABASEURL", default="http://codeMind-ollama:11434"),
        ollama_model=_get_env_str("OLLAMA_MODEL", "OLLAMAMODEL", default="qwen2.5-coder:3b"),
        qdrant_url=_get_env_str("QDRANT_URL", "QDRANTURL", default="http://codeMind-qdrant:6333"),
        qdrant_collection=_get_env_str("QDRANT_COLLECTION", "QDRANTCOLLECTION", default="sourcechunks"),
        embedding_model=_get_env_str("EMBEDDING_MODEL", "EMBEDDINGMODEL", default="BAAI/bge-m3"),
        chunk_size=_get_env_int("CHUNK_SIZE", "CHUNKSIZE", default=1200),
        chunk_overlap=_get_env_int("CHUNK_OVERLAP", "CHUNKOVERLAP", default=120),
        top_k=_get_env_int("TOP_K", "TOPK", default=8),
        upload_chunk_size=_get_env_int("UPLOAD_CHUNK_SIZE", "UPLOADCHUNKSIZE", default=MB),
        max_file_size=_get_env_int("MAX_FILE_SIZE", "MAXFILESIZE", default=GB),
        max_files_per_request=_get_env_int("MAX_FILES_PER_REQUEST", "MAXFILESPERREQUEST", default=1),
        upload_dir=Path(_get_env_str("UPLOAD_DIR", "UPLOADDIR", default="data/uploads")),
        extract_dir=Path(_get_env_str("EXTRACT_DIR", "EXTRACTDIR", default="data/extracted")),
        chat_history_turns=_get_env_int("CHAT_HISTORY_TURNS", "CHATHISTORYTURNS", default=8),
        chat_history_max_chars=_get_env_int("CHAT_HISTORY_MAX_CHARS", "CHATHISTORYMAXCHARS", default=5000),
        retrieval_candidate_limit=_get_env_int("RETRIEVAL_CANDIDATE_LIMIT", "RETRIEVALCANDIDATELIMIT", default=100),
        retrieval_max_files=_get_env_int("RETRIEVAL_MAX_FILES", "RETRIEVALMAXFILES", default=12),
        retrieval_max_chunks_per_file=_get_env_int(
            "RETRIEVAL_MAX_CHUNKS_PER_FILE",
            "RETRIEVALMAXCHUNKSPERFILE",
            default=3,
        ),
        sqlite_db_path=_get_env_str("SQLITE_DB_PATH", "SQLITEDBPATH", default="data/db/app.db"),
    )