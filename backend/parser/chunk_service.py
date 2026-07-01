import re
from typing import Any

from config import Settings


class ChunkService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def split_text(self, text: str, file_metadata: dict[str, Any]) -> list[dict[str, Any]]:
        """데이터 청크화 및 청킹된 데이터에 정보 입력"""
        ext      = file_metadata.get("extension", "")
        segments = self._split_by_semantic_unit(text, ext)  # 확장자별로 의미있는 seg로 분리
        size     = self.settings.chunk_size        # 800
        overlap  = self.settings.chunk_overlap     # 100
        chunks: list[dict[str, Any]] = []
        idx = 0

        for seg in segments:
            if len(seg) <= size:
                chunk = self._make_chunk(seg, idx, file_metadata) # 청크 생성
                if chunk:
                    chunks.append(chunk)
                    idx += 1
            else:
                start = 0
                while start < len(seg):
                    end   = min(start + size, len(seg))
                    chunk = self._make_chunk(seg[start:end], idx, file_metadata)
                    if chunk:
                        chunks.append(chunk)
                        idx += 1
                    if end == len(seg):
                        break
                    start += (size - overlap)
        return chunks

    # ── 의미 단위 분할 ───────────────────────────────────────────
    def _split_by_semantic_unit(self, text: str, ext: str) -> list[str]:
        """확장자별로 의미 단위(클래스·메서드·SQL 구문·태그)로 분리."""
        if ext == "xml":
            return self._split_xml(text)
        if ext in ("java", "py", "js", "ts"):
            return self._split_by_blank_lines(text)
        if ext == "sql":
            return self._split_sql(text)
        return [text]

    def _split_xml(self, text: str) -> list[str]:
        """XML: MyBatis SQL 태그 단위로 분리. 헤더(namespace)를 각 청크에 접두어로 붙인다."""
        tags    = ["select", "insert", "update", "delete", "resultMap", "sql"]
        pattern = (
            r"(<(?:" + "|".join(tags) + r")\b[^>]*>.*?</(?:" + "|".join(tags) + r")>)"
        )
        parts    = re.split(pattern, text, flags=re.DOTALL | re.IGNORECASE)
        header   = parts[0].strip()          # mapper 헤더 (namespace 선언 등)
        segments: list[str] = []
        if header:
            segments.append(header)
        for part in parts[1:]:
            s = part.strip()
            if s:
                # 각 SQL 구문에 namespace 컨텍스트를 접두어로 보존
                segments.append(f"{header[:300]}\n{s}" if header else s)
        return segments if segments else [text]

    def _split_by_blank_lines(self, text: str) -> list[str]:
        """Java/Python/JS/TS: 빈 줄 2개 이상 기준으로 분리 (클래스·메서드 경계)."""
        parts = re.split(r"\n{2,}", text)
        return [p.strip() for p in parts if p.strip()]

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
            return None
        return {
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
