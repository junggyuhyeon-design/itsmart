import uuid
from typing import Any
from config import Settings # 경로 수정

class ChunkService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def split_text(self, text: str, file_metadata: dict[str, Any], chunk_size: int | None = None, chunk_overlap: int | None = None) -> list[dict[str, Any]]:
        size = chunk_size or self.settings.chunk_size
        overlap = chunk_overlap or self.settings.chunk_overlap
        if not text or not text.strip(): return []

        chunks = []
        start, chunk_index, text_len = 0, 0, len(text)
        while start < text_len:
            end = min(start + size, text_len)
            chunk_text = text[start:end]

            if len(chunk_text.strip()) >= 10:
                chunks.append({
                    "chunk_id": str(uuid.uuid4()), "chunk_index": chunk_index,
                    "text": chunk_text, "start_pos": start, "end_pos": end,
                    "file_name": file_metadata.get("file_name"),
                    "extension": file_metadata.get("extension"),
                    "relative_path": file_metadata.get("relative_path"),
                    "language": file_metadata.get("language"),
                })
                chunk_index += 1
            if end == text_len: break
            start += (size - overlap)
        return chunks
