import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from dotenv import load_dotenv

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
    load_dotenv()
    base_dir = Path(__file__).resolve().parent.parent
    return Settings(
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://chatbot-ollama:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b"),
        qdrant_url=os.getenv("QDRANT_URL", "http://chatbot-qdrant:6333"),
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "source_chunks"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
        chunk_size=int(os.getenv("CHUNK_SIZE", 800)),
        chunk_overlap=int(os.getenv("CHUNK_OVERLAP", 100)),
        top_k=int(os.getenv("TOP_K", 5)),
        upload_dir=Path(os.getenv("UPLOAD_DIR", str(base_dir / "data" / "uploads")))
    )