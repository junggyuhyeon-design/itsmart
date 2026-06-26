from typing import Any
from config import Settings # 경로 수정

class ChunkService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # 확인 완료.
    def split_text(self, text: str, file_metadata: dict[str, Any]) -> list[dict[str, Any]]:
        """데이터 청크화 및 청킹된 데이터에 정보 입력"""
        size = self.settings.chunk_size       # 800
        overlap = self.settings.chunk_overlap # 100

        if not text or not text.strip(): return []

        chunks = []
        start, text_len = 0, len(text)
        while start < text_len:
            end = min(start + size, text_len)
            chunk_text = text[start:end]

            if chunk_text.strip():
                chunks.append({
                    "project_id": file_metadata.get("project_id"),
                    "project_name": file_metadata.get("project_name"),
                    "text": chunk_text,
                    "file_name": file_metadata.get("file_name"),
                    "extension": file_metadata.get("extension"),
                    "relative_path": file_metadata.get("relative_path"),
                })
            if end == text_len: break
            start += (size - overlap)
        return chunks
