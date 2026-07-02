import re
from typing import Any

from config import Settings


class ChunkService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def split_text(self, text: str, file_metadata: dict[str, Any]) -> list[dict[str, Any]]:
<<<<<<< HEAD
        """데이터 청크화 및 청킹된 데이터에 정보 입력"""
        ext      = file_metadata.get("extension", "")
        segments = self._split_by_semantic_unit(text, ext)  # 확장자별로 의미있는 seg로 분리
        size     = self.settings.chunk_size        # 800
        overlap  = self.settings.chunk_overlap     # 100
=======
        ext = file_metadata.get("extension", "")
        segments = self._split_by_semantic_unit(text, ext)

        size = self.settings.chunk_size
        overlap = self.settings.chunk_overlap
        step = max(1, size - overlap)

>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
        chunks: list[dict[str, Any]] = []
        idx = 0

        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue

            if len(seg) <= size:
                chunk = self._make_chunk(seg, idx, file_metadata) # 청크 생성
                if chunk:
                    chunks.append(chunk)
                    idx += 1
                continue

            start = 0
            while start < len(seg):
                end = min(start + size, len(seg))
                piece = seg[start:end]
                chunk = self._make_chunk(piece, idx, file_metadata)
                if chunk:
                    chunks.append(chunk)
                    idx += 1
                if end >= len(seg):
                    break
                start += step

        return chunks

<<<<<<< HEAD
    # ── 의미 단위 분할 ───────────────────────────────────────────
=======
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
    def _split_by_semantic_unit(self, text: str, ext: str) -> list[str]:
        if ext == "xml":
            return self._split_xml(text)
        if ext == "sql":
            return self._split_sql(text)
        if ext in ("java", "py", "js", "ts"):
            return self._split_code_blocks(text)
        return [text]

    def _split_xml(self, text: str) -> list[str]:
        tags = ["select", "insert", "update", "delete", "resultMap", "sql"]
        pattern = r"(<(?:" + "|".join(tags) + r")\b[^>]*>.*?</(?:" + "|".join(tags) + r")>)"

        parts = re.split(pattern, text, flags=re.DOTALL | re.IGNORECASE)
        header = parts[0].strip() if parts else ""

        segments: list[str] = []
        if header:
            segments.append(header)

        for part in parts[1:]:
            s = part.strip()
            if s:
                segments.append(f"{header[:300]}\n{s}" if header else s)

        return segments if segments else [text]

    def _split_sql(self, text: str) -> list[str]:
        parts = re.split(r";\s*(?:\n|$)", text)
        return [p.strip() + ";" for p in parts if p.strip()]

    def _split_code_blocks(self, text: str) -> list[str]:
        parts = re.split(r"\n{2,}", text)
        return [p.strip() for p in parts if p.strip()]

<<<<<<< HEAD
    def _split_sql(self, text: str) -> list[str]:
        """SQL: 세미콜론 단위로 분리."""
        parts = re.split(r";[ \t]*(?:\n|$)", text)
        result = []
        for p in parts:
            s = p.strip()
            if s:
                result.append(s + ";")
        return result if result else [text]

    # ── 청크 생성 ────────────────────────────────────────────────
    def _make_chunk(
        self, text: str, idx: int, meta: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not text.strip():
=======
    def _make_chunk(self, text: str, idx: int, meta: dict[str, Any]) -> dict[str, Any] | None:
        if not text or not text.strip():
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
            return None

        return {
<<<<<<< HEAD
            # ── 기본 식별 정보 ─────────────────────────────────
            "project_id":    meta.get("project_id"),
            "project_name":  meta.get("project_name"),
            "text":          text,                      # seg 텍스트
            "file_name":     meta.get("file_name"),
            "extension":     meta.get("extension"),
            "relative_path": meta.get("relative_path"),
            "chunk_index":   idx,
            "layer_type":    meta.get("layer_type", ""),    # controller/service/mapper/ddl 등
            "class_name":    meta.get("class_name", ""),
        }
=======
            "project_id": meta.get("project_id", ""),
            "project_name": meta.get("project_name", ""),
            "text": text.strip(),
            "file_name": meta.get("file_name", ""),
            "extension": meta.get("extension", ""),
            "relative_path": meta.get("relative_path", ""),
            "saved_path": meta.get("saved_path", ""),
            "file_path": meta.get("file_path", meta.get("saved_path", "")),
            "chunk_index": idx,
            "file_size": meta.get("file_size", 0),
            "source_type": meta.get("source_type", ""),
            "root_container_name": meta.get("root_container_name", ""),
            "layer_type": meta.get("layer_type", ""),
            "class_name": meta.get("class_name", ""),
            "package": meta.get("package", ""),
            "content_type": meta.get("content_type", ""),
        }
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
