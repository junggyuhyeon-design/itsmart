from __future__ import annotations

from typing import Any

from config import Settings

import logging
logger = logging.getLogger(__name__)


# ── 청크 분할 서비스 ────────────────────────────────────────────────
class ChunkService:
    # 용도:
    # - 문서 원문을 일정 크기의 청크(chunk)로 나누기 위한 서비스 클래스
    # - 설정값(chunk_size, chunk_overlap)을 받아 분할 기준을 초기화함
    def __init__(self, settings: Settings) -> None:
        # 최소 청크 크기는 100자로 강제
        self.chunk_size = max(100, int(settings.chunk_size))
        # 청크 간 겹침(overlap)은 0 이상만 허용
        self.chunk_overlap = max(0, int(settings.chunk_overlap))

    # ── 텍스트 청크 분할 ────────────────────────────────────────────────
    def split_text(self, text: str, meta: dict[str, Any]) -> list[dict[str, Any]]:
        # 용도:
        # - 긴 텍스트를 줄(line) 기준으로 나눠 여러 개의 청크로 분할
        # - 각 청크에 메타정보(meta), 청크 순번, 시작/끝 라인 번호를 함께 담아 반환
        #
        # 동작 방식:
        # 1. 입력 텍스트를 줄 단위로 분리
        # 2. chunk_size를 넘기기 전까지 current_lines에 누적
        # 3. 초과 시 현재까지를 하나의 청크로 저장
        # 4. chunk_overlap 설정이 있으면 마지막 일부 줄을 다음 청크에 재사용
        # 5. 최종적으로 청크 목록 반환

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
            # 개행 문자 1자를 포함한 길이로 계산
            line_len = len(line) + 1

            # 현재 청크에 새 줄을 추가하면 chunk_size를 초과하는 경우
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

                # overlap이 설정되어 있으면 마지막 일부 줄을 다음 청크 시작점으로 재사용
                if self.chunk_overlap > 0 and current_lines:
                    overlap_lines: list[str] = []
                    overlap_len = 0

                    for old_line in reversed(current_lines):
                        candidate_len = len(old_line) + 1
                        # overlap 허용 길이를 넘으면 중단
                        if overlap_lines and overlap_len + candidate_len > self.chunk_overlap:
                            break
                        overlap_lines.insert(0, old_line)
                        overlap_len += candidate_len

                    current_lines = overlap_lines
                    current_length = sum(len(x) + 1 for x in current_lines)
                    start_line = max(1, i - len(current_lines) + 1)
                else:
                    # overlap이 없으면 다음 줄부터 새 청크 시작
                    current_lines = []
                    current_length = 0
                    start_line = i + 1

            current_lines.append(line)
            current_length += line_len
            i += 1

        # 반복이 끝난 뒤 남아 있는 줄들을 마지막 청크로 저장
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

    # ── 파싱 결과 청크 변환 ────────────────────────────────────────────────
    def chunk_parsed_file(self, parsed: dict[str, Any]) -> list[dict[str, Any]]:
        # 용도:
        # - 파서(parse_text_file 등)에서 만들어진 파일 분석 결과(parsed)를
        #   청크 분할에 맞는 메타데이터 구조로 정리한 뒤 split_text()에 전달
        #
        # 반환값:
        # - raw_text를 분할한 청크 리스트
        # - 각 청크에는 파일/프로젝트/분석 메타정보가 함께 포함됨

        if not parsed:
            return []

        # parsed 딕셔너리에서 다양한 키 표기(project_id / projectid 등)를 흡수해서
        # 일관된 메타데이터 구조로 정규화
        meta = {
            "project_id": parsed.get("project_id", ""),
            "project_name": parsed.get("project_name", ""),
            "file_name": parsed.get("file_name", ""),
            "extension": parsed.get("extension", ""),
            "language": parsed.get("language", ""),
            "mime_type": parsed.get("mime_type", ""),
            "relative_path": parsed.get("relative_path", ""),
            "saved_path": parsed.get("saved_path", ""),
            "file_path": parsed.get("saved_path", ""),
            "file_size": parsed.get("file_size", 0),
            "layer_type": parsed.get("layer_type", ""),
            "class_name": parsed.get("class_name", ""),
            "package": parsed.get("package", ""),
            "content_type": parsed.get("content_type", ""),
            "xml_namespace": parsed.get("xml_namespace", ""),
            "xml_sql_fragments": parsed.get("xml_sql_fragments", []),
            "table_names": parsed.get("table_names", []),
            "template_meta": parsed.get("template_meta", {}),
            "sql_meta": parsed.get("sql_meta", {}),
        }

        # 파싱된 원문(raw_text)을 메타정보와 함께 청크로 분할
        return self.split_text(parsed.get("raw_text", ""), meta)