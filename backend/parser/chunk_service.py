from __future__ import annotations

from typing import Any

from config import Settings


class ChunkService:
    def __init__(self, settings: Settings) -> None:
        self.chunk_size = max(100, int(settings.chunk_size))
        self.chunk_overlap = max(0, int(settings.chunk_overlap))

    def split_text(self, text: str, **meta: Any) -> list[dict[str, Any]]:
        text = (text or "").strip()
        if not text:
            return []

        lines = text.splitlines()
        if not lines:
            return []

        chunks: list[dict[str, Any]] = []
        current_lines: list[str] = []
        current_length = 0
        chunk_index = 0
        start_line = 1

        i = 0
        while i < len(lines):
            line = lines[i]
            line_len = len(line) + 1

            if current_lines and current_length + line_len > self.chunk_size:
                chunk_text = "\n".join(current_lines).strip()
                if chunk_text:
                    chunks.append(
                        {
                            **meta,
                            "text": chunk_text,
                            "chunk_index": chunk_index,
                            "start_line": start_line,
                            "end_line": i,
                            "chunk_type": "text",
                        }
                    )
                    chunk_index += 1

                if self.chunk_overlap > 0 and current_lines:
                    overlap_lines: list[str] = []
                    overlap_len = 0
                    for old_line in reversed(current_lines):
                        candidate_len = len(old_line) + 1
                        if overlap_lines and overlap_len + candidate_len > self.chunk_overlap:
                            break
                        overlap_lines.insert(0, old_line)
                        overlap_len += candidate_len

                    current_lines = overlap_lines
                    current_length = sum(len(x) + 1 for x in current_lines)
                    start_line = max(1, i - len(current_lines) + 1)
                else:
                    current_lines = []
                    current_length = 0
                    start_line = i + 1

            current_lines.append(line)
            current_length += line_len
            i += 1

        if current_lines:
            chunk_text = "\n".join(current_lines).strip()
            if chunk_text:
                chunks.append(
                    {
                        **meta,
                        "text": chunk_text,
                        "chunk_index": chunk_index,
                        "start_line": start_line,
                        "end_line": len(lines),
                        "chunk_type": "text",
                    }
                )

        return chunks

    def chunk_parsed_file(self, parsed: dict[str, Any]) -> list[dict[str, Any]]:
        if not parsed:
            return []

        return self.split_text(
            parsed.get("raw_text", ""),
            project_id=parsed.get("project_id", ""),
            project_name=parsed.get("project_name", ""),
            file_name=parsed.get("file_name", ""),
            extension=parsed.get("extension", ""),
            relative_path=parsed.get("relative_path", ""),
            saved_path=parsed.get("saved_path", ""),
            file_path=parsed.get("file_path", parsed.get("saved_path", "")),
            file_size=parsed.get("file_size", 0),
            source_type=parsed.get("source_type", ""),
            root_container_name=parsed.get("root_container_name", ""),
            layer_type=parsed.get("layer_type", ""),
            class_name=parsed.get("class_name", ""),
            package=parsed.get("package", ""),
            content_type=parsed.get("content_type", ""),
        )