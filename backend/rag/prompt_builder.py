from __future__ import annotations

import logging
import re
from typing import Any
import logging

logger = logging.getLogger(__name__)
# score 임계값: 이 값 미만의 청크는 노이즈로 판단해 제외
_SCORE_THRESHOLD = 0.6

logger = logging.getLogger(__name__)

SYSTEM_BASE = """너는 업로드된 소스 코드를 분석하는 AI다.
- 반드시 제공된 evidence, metadata, structure, sqlite context를 우선 참고해 답변한다.
- 사용자가 프로젝트나 소스 설명을 요청하면, 업로드된 파일과 코드 조각을 기준으로 파일별 역할을 설명한다.
- GitHub URL, 레포지토리 주소, 외부 저장소 정보가 없어도 현재 전달된 파일 내용만으로 분석을 시도한다.
- evidence가 일부만 있어도, 확인 가능한 파일부터 설명하고, 부족한 부분은 '추정'이나 '추측'이라는 말을 명시하며 제한적으로만 언급한다.
- "레포지토리 주소가 필요하다", "URL을 달라", "폴더 경로를 달라" 같은 답변은 하지 않는다.
- 전체 소스에 대한 완전한 설명이 어려우면, 먼저 현재 evidence로 확실히 설명 가능한 파일/레이어 구조를 정리한 다음, 어떤 부분이 부족한지만 짧게 알려준다.
- 가능하면 한국어로 자세히 설명한다.
- Java, XML, SQL, Markdown, 설정 파일도 문맥에 맞게 설명한다.
"""

SYSTEM_EDIT = """너는 업로드된 소스 코드에서 문자열/문구/타이틀 변경 위치를 찾아 안내하는 AI다.
- 반드시 제공된 evidence, metadata, structure, sqlite context를 우선 참고해 답변한다.
- 사용자가 "A를 B로 바꿔줘" 같은 요청을 하면, 설명형 답변보다 수정 위치 안내를 우선한다.
- exact match가 보이면 그 파일과 근거 코드를 가장 먼저 제시한다.
- exact match가 없으면 유사 후보 파일을 제시하고, "정확한 문자열 일치는 미발견"이라고 명확히 말한다.
- "최신 소스를 달라", "URL을 달라", "레포지토리 주소가 필요하다" 같은 답변은 하지 않는다.
- [edit_candidates]가 제공되면 이것을 최우선 근거로 사용한다. evidence보다 우선한다.
- 변경 전/변경 후 값은 새로 추측하지 말고 [edit_candidates]의 값을 그대로 사용한다.
- 변경 후는 변경 전 라인에서 edit_source(변경 전)를 edit_target(변경 후)으로 치환한 결과여야 한다.
- [edit_candidates]가 "정확한 문자열 일치 미발견"이면 파일/줄/전후값을 확정하지 말고 그대로 안내한다.
- 답변은 가능하면 아래 순서/형식을 따른다 (블록형, 표는 긴 코드 라인이 깨지므로 사용하지 않는다):

  ## 변경 대상
  1) 파일경로
     - 줄: N
     - 변경 전: ...
     - 변경 후: ...
  2) ...

  ## 적용 방법
  - 위 각 라인의 변경 전 문자열을 변경 후 문자열로 치환
- evidence가 부족해도, 현재 evidence 안에서 확인 가능한 범위까지는 반드시 안내한다.
- 가능하면 한국어로 자세히 설명한다.
"""

SYSTEM_DIAGRAM = """너는 Mermaid 다이어그램 생성 AI다.
1. 답변은 mermaid 코드 블록 중심으로 작성한다.
2. Mermaid 문법 오류가 없도록 한다.
3. 필요 시 짧은 설명을 덧붙인다.
4. DB는 erDiagram, 흐름은 flowchart LR 또는 TD를 사용한다.
"""

SYSTEM_API_DOC = """너는 REST API 분석 AI다.
- 코드와 SQLite 문맥을 근거로 API를 설명한다.
- 엔드포인트, 역할, 입력/출력, 연관 컴포넌트를 정리한다.
"""

SYSTEM_LAYER = """너는 Controller / Service / Repository / Mapper 구조 분석 AI다.
- 레이어 역할과 호출 흐름을 설명한다.
- 파일명, 클래스명, 메서드명을 근거로 제시한다.
"""

SYSTEM_XML = """너는 MyBatis XML / SQL 분석 AI다.
- XML statement id, namespace, 테이블 사용처를 근거로 설명한다.
"""

SYSTEM_TABLE = """너는 DB 분석 AI다.
- SQLite table usage, references, code evidence를 근거로 설명한다.
"""

SYSTEM_PROMPTS = {
    "diagram": SYSTEM_DIAGRAM,
    "table_analysis": SYSTEM_TABLE,
    "api_doc": SYSTEM_API_DOC,
    "layer_search": SYSTEM_LAYER,
    "xml_analysis": SYSTEM_XML,
    "architecture": SYSTEM_LAYER,
    "edit_text_one": SYSTEM_EDIT,
    "edit_text_all": SYSTEM_EDIT,
    "qa": SYSTEM_BASE,
    "listing": SYSTEM_BASE,
}


def _preview_text(value: str, limit: int = 300) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"...(truncated {len(text) - limit} chars)"


class PromptBuilder:
    def trim_history(
            self,
            chat_history: list[dict[str, Any]],
            max_history_chars: int = 4000,
    ) -> list[dict[str, Any]]:
        logger.info(
            "[prompt_builder.py][trim_history][1.시작] chat_history_count=%d max_history_chars=%d",
            len(chat_history or []),
            max_history_chars,
        )

        if not chat_history:
            logger.info("[prompt_builder.py][trim_history][2.히스토리 없음] return empty")
            return []

        selected: list[dict[str, Any]] = []
        total = 0

        for row in reversed(chat_history):
            question = (row.get("question") or "").strip()
            answer = (row.get("answer") or "").strip()
            size = len(question) + len(answer)

            if selected and total + size > max_history_chars:
                logger.info(
                    "[prompt_builder.py][trim_history][3.길이 제한 도달] current_total=%d next_size=%d",
                    total,
                    size,
                )
                break

            if question or answer:
                selected.append({"question": question, "answer": answer})
                total += size

        selected.reverse()

        logger.info(
            "[prompt_builder.py][trim_history][4.완료] selected_count=%d total_chars=%d",
            len(selected),
            total,
        )
        return selected

    def build_metadata_summary(self, hits: list[dict[str, Any]]) -> str:
        logger.info(
            "[prompt_builder.py][build_metadata_summary][1.시작] hits_count=%d",
            len(hits or []),
        )

        if not hits:
            logger.info("[prompt_builder.py][build_metadata_summary][2.hit 없음] return empty")
            return ""

        seen = set()
        lines: list[str] = []

        for hit in hits:
            key = hit.get("relative_path") or hit.get("file_name") or hit.get("filename") or ""
            if not key or key in seen:
                continue
            seen.add(key)

            meta_parts: list[str] = []
            if hit.get("layer_type"):
                meta_parts.append(f"layer={hit['layer_type']}")
            if hit.get("class_name"):
                meta_parts.append(f"class={hit['class_name']}")
            if hit.get("package"):
                meta_parts.append(f"package={hit['package']}")
            if hit.get("content_type"):
                meta_parts.append(f"type={hit['content_type']}")
            if hit.get("chunk_type"):
                meta_parts.append(f"chunk_type={hit['chunk_type']}")
            if hit.get("match_type"):
                meta_parts.append(f"match_type={hit['match_type']}")

            suffix = f" ({', '.join(meta_parts)})" if meta_parts else ""
            lines.append(f"- {key}{suffix}")

        result = "\n".join(lines)

        logger.info(
            "[prompt_builder.py][build_metadata_summary][3.완료] unique_file_count=%d result_len=%d preview=%s",
            len(lines),
            len(result),
            _preview_text(result, 300),
        )
        return result

    def build_chunk_context(self, hits: list[dict[str, Any]]) -> str:
        logger.info(
            "[prompt_builder.py][build_chunk_context][1.시작] hits_count=%d",
            len(hits or []),
        )

        if not hits:
            logger.info("[prompt_builder.py][build_chunk_context][2.hit 없음] return empty")
            return ""

        ext_lang_map = {
            "java": "java",
            "py": "python",
            "xml": "xml",
            "sql": "sql",
            "js": "javascript",
            "ts": "typescript",
            "md": "markdown",
            "json": "json",
            "yml": "yaml",
            "yaml": "yaml",
            "jsp": "html",
            "jspx": "html",
            "html": "html",
            "htm": "html",
        }

        lines: list[str] = []

        for index, hit in enumerate(hits, start=1):
            score = hit.get("score", 1.0)
            logger.info("적중 score ::: %.2f", score)

            text = (hit.get("text") or "").strip()
            if not text:
                continue

            logger.info("가져온 청크 ::: %s", text)

            relative_path = hit.get("relative_path") or hit.get("file_name") or f"chunk-{index}"
            extension = (hit.get("extension") or "").lower().strip(".")
            language = ext_lang_map.get(extension, "")

            # edit grep hit 는 줄 번호(matched_line/start_line~end_line)를 헤더에 명시
            line_meta = ""
            matched_line = hit.get("matched_line")
            start_line = hit.get("start_line")
            end_line = hit.get("end_line")
            if matched_line:
                line_meta = f" (line {matched_line})"
            elif start_line and end_line:
                line_meta = f" (lines {start_line}-{end_line})"
            elif start_line:
                line_meta = f" (line {start_line})"

            match_type = hit.get("match_type")
            if match_type:
                line_meta += f" [{match_type}]" if not line_meta else f" [{match_type}]"

            lines.append(f"[evidence {index}] {relative_path}{line_meta}")
            lines.append(f"```{language}" if language else "```")
            lines.append(text)
            lines.append("```")
            lines.append("")

        result = "\n".join(lines).strip()

        logger.info(
            "[prompt_builder.py][build_chunk_context][3.완료] context_len=%d preview=%s",
            len(result),
            _preview_text(result, 300),
        )
        return result

    def _replace_literal_case_insensitive(
            self,
            line: str,
            edit_source: str,
            edit_target: str,
            *,
            replace_all: bool = False,
    ) -> tuple[str, int]:
        """
        line 내에서 edit_source 를 edit_target 으로 치환 (대소문자 무시 리터럴).
        grep 이 대소문자 무시로 매칭하므로 원문 casing 이 다를 수 있어 case-insensitive 로 처리.
        반환: (치환된 라인, 치환 횟수)
        - replace_all=False: 한 라인에서 첫 1회만 치환 (edit_text_one)
        - replace_all=True: 한 라인의 모든 발생 치환 (edit_text_all)
        """
        if not line or not edit_source or edit_target is None:
            return line, 0

        pattern = re.compile(re.escape(edit_source), re.IGNORECASE)
        count = 0 if replace_all else 1
        return pattern.subn(edit_target, line, count=count)

    def _extract_marked_line(self, text: str) -> str:
        """
        exact_grep hit 의 text(snippet) 에서 >> 표시된 매칭 라인을 추출.
        snippet 형식: ">> L12: <line content>"
        """
        for line in (text or "").splitlines():
            match = re.match(r"\s*>>\s*L\d+:\s(.*)$", line)
            if match:
                return match.group(1).rstrip()
        return ""

    def build_edit_candidates(
            self,
            hits: list[dict[str, Any]],
            *,
            edit_source: str | None,
            edit_target: str | None,
            query_type: str,
    ) -> str:
        """
        edit 계열 요청에서 각 exact_grep hit 의
        파일/줄/변경전/변경후 를 deterministic 하게 계산하여 구조화된 블록으로 반환.
        - 변경후는 변경전 라인에서 edit_source→edit_target 치환한 결과 (LLM 이 추측하지 않음).
        - exact_grep hit(match_type=="exact_grep")만 확정 변경 대상으로 사용.
          vector fallback hit(match_type 이 exact_line/substring_line 등)은 "유사 후보" 표시.
        """
        logger.info(
            "[prompt_builder.py][build_edit_candidates][1.시작] hits_count=%d query_type=%s has_source=%s has_target=%s",
            len(hits or []),
            query_type,
            bool(edit_source),
            edit_target is not None,
            )

        if query_type not in {"edit_text_one", "edit_text_all"}:
            return ""

        if not edit_source or edit_target is None:
            return (
                "변경 전/후 문자열 파싱 실패.\n"
                "변경 파일/줄/전후값을 확정하지 말고, 사용자에게 변경 전/후 문자열을 다시 확인해야 한다."
            )

        exact_hits = [
            hit for hit in (hits or [])
            if (hit.get("match_type") or "") == "exact_grep"
        ]

        if not exact_hits:
            # exact_grep hit 이 없으면 확정 불가. match_type 이 있는 비-grep 후보는 유사 후보로만 안내.
            has_similar = any(
                (hit.get("match_type") or "") not in {"exact_grep", "", None}
                for hit in (hits or [])
            )
            if has_similar:
                msg = "정확한 문자열 일치 미발견. 아래 evidence는 유사 후보일 뿐이며, 변경 파일/줄/전후값으로 확정하지 말 것."
            else:
                msg = "정확한 문자열 일치 미발견. 변경 파일/줄/전후값을 확정하지 말 것."
            logger.info(
                "[prompt_builder.py][build_edit_candidates][2.exact_grep 미발견] has_similar=%s",
                has_similar,
            )
            return msg

        replace_all = query_type == "edit_text_all"
        lines: list[str] = [f"총 exact match: {len(exact_hits)}건"]

        for index, hit in enumerate(exact_hits, start=1):
            relative_path = (
                    hit.get("relative_path")
                    or hit.get("file_name")
                    or hit.get("filename")
                    or "unknown"
            )
            matched_line = hit.get("matched_line") or hit.get("start_line") or ""
            before_line = (
                    hit.get("line_text")
                    or self._extract_marked_line(hit.get("text") or "")
                    or ""
            ).rstrip()

            after_line, replace_count = self._replace_literal_case_insensitive(
                before_line,
                edit_source,
                edit_target,
                replace_all=replace_all,
            )

            lines.append(f"\n[candidate {index}]")
            lines.append(f"- 파일: {relative_path}")
            lines.append(f"- 줄: {matched_line}")
            lines.append(f"- match_type: {hit.get('match_type') or ''}")
            lines.append(f"- 변경 전: {before_line}")

            if replace_count > 0:
                lines.append(f"- 변경 후: {after_line}")
                lines.append(f"- 치환 횟수: {replace_count}")
            else:
                lines.append("- 변경 후: (치환 실패: 해당 라인에서 변경 전 문자열을 찾지 못함)")
                lines.append("- 상태: replace_failed")

        result = "\n".join(lines).strip()

        logger.info(
            "[prompt_builder.py][build_edit_candidates][3.완료] exact_count=%d result_len=%d preview=%s",
            len(exact_hits),
            len(result),
            _preview_text(result, 300),
        )
        return result

    def build_messages(
            self,
            *,
            question: str,
            hits: list[dict[str, Any]],
            query_type: str,
            project_name: str | None = None,
            struct_context: str = "",
            chat_history: list[dict[str, Any]] | None = None,
            recent_entities: list[dict[str, Any]] | None = None,
            sqlite_context: str = "",
            max_history_chars: int = 4000,
            edit_source: str | None = None, #변경 파일/줄/전후값 중심 답변 패치
            edit_target: str | None = None, #변경 파일/줄/전후값 중심 답변 패치
    ) -> list[dict[str, str]]:
        logger.info(
            "[prompt_builder.py][build_messages][1.시작] query_type=%s project_name=%s question_len=%d hits_count=%d struct_context_len=%d chat_history_count=%d recent_entities_count=%d sqlite_context_len=%d question_preview=%s",
            query_type,
            project_name,
            len(question or ""),
            len(hits or []),
            len(struct_context or ""),
            len(chat_history or []),
            len(recent_entities or []),
            len(sqlite_context or ""),
            _preview_text(question, 300),
        )

        system_prompt = SYSTEM_PROMPTS.get(query_type, SYSTEM_BASE)
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

        logger.info(
            "[prompt_builder.py][build_messages][2.system prompt 선택] system_prompt_len=%d",
            len(system_prompt),
        )

        trimmed = self.trim_history(chat_history or [], max_history_chars)
        for row in trimmed:
            if row["question"]:
                messages.append({"role": "user", "content": row["question"]})
            if row["answer"]:
                messages.append({"role": "assistant", "content": row["answer"]})

        logger.info(
            "[prompt_builder.py][build_messages][3.history 반영완료] trimmed_count=%d message_count=%d",
            len(trimmed),
            len(messages),
        )

        parts: list[str] = []

        lowered_question = (question or "").lower()
        wants_structure = any(
            token in lowered_question
            for token in ["소스", "파일", "구조", "설명", "프로젝트", "source", "file", "structure"]
        )

        if query_type in {"edit_text_one", "edit_text_all"}:
            parts.append(
                "[instruction]\n"
                "이번 요청은 문자열/타이틀/문구 수정 요청이다.\n"
                "설명형 답변보다 변경 위치 안내를 우선한다.\n"
                "[edit_candidates]가 제공되면 그것이 최우선 근거이며 evidence보다 우선한다.\n"
                "변경 전/후 값은 추측하지 말고 [edit_candidates]의 값을 그대로 사용한다.\n"
                "변경 후는 변경 전 라인에서 edit_source를 edit_target으로 치환한 결과다.\n"
                "[edit_candidates]가 '정확한 문자열 일치 미발견'이면 파일/줄/전후값을 확정하지 않는다.\n"
                "답변은 '## 변경 대상(파일/줄/변경 전/변경 후) → ## 적용 방법' 블록형으로 정리한다."
            )
            logger.info("[prompt_builder.py][build_messages][4.edit instruction 추가] query_type=%s", query_type)

            # [edit_pair]: 변경 전(검색어)/변경 후(치환값) 명시
            if edit_source or edit_target is not None:
                parts.append(
                    "[edit_pair]\n"
                    f"변경 전(검색어): {edit_source or ''}\n"
                    f"변경 후(치환값): {edit_target if edit_target is not None else ''}"
                )
                logger.info(
                    "[prompt_builder.py][build_messages][4-1.edit_pair 추가] edit_source_len=%d has_target=%s",
                    len(edit_source or ""),
                    edit_target is not None,
                    )

            # [edit_candidates]: 각 exact_grep hit 의 파일/줄/전후값 (deterministic)
            edit_candidates = self.build_edit_candidates(
                hits,
                edit_source=edit_source,
                edit_target=edit_target,
                query_type=query_type,
            )
            if edit_candidates:
                parts.append("[edit_candidates]\n" + edit_candidates)
                logger.info(
                    "[prompt_builder.py][build_messages][4-2.edit_candidates 추가] edit_candidates_len=%d",
                    len(edit_candidates),
                )

        elif wants_structure:
            parts.append(
                "[instruction]\n"
                "소스/파일/구조/프로젝트 설명 요청일 때는, "
                "현재 evidence와 metadata, structure에 포함된 정보만으로도 "
                "파일명, 경로, 레이어(Controller/Service/Repository 등), 확장자 기준의 "
                "구조 요약을 먼저 제시한 후, 부족한 부분을 언급한다."
            )
            logger.info("[prompt_builder.py][build_messages][4.structure instruction 추가] wants_structure=True")

        if project_name:
            parts.append(f"[project]\n{project_name}")
            logger.info("[prompt_builder.py][build_messages][5.project 추가] project_name=%s", project_name)

        if recent_entities:
            entity_lines: list[str] = []
            seen = set()

            for entity in recent_entities[:12]:
                key = (
                    entity.get("entity_type", ""),
                    entity.get("entity_name", ""),
                    entity.get("relative_path", ""),
                )
                if key in seen:
                    continue
                seen.add(key)

                label = f"- {entity.get('entity_type', '')}: {entity.get('entity_name', '')}".strip()
                if entity.get("relative_path"):
                    label += f" ({entity['relative_path']})"
                entity_lines.append(label)

            if entity_lines:
                parts.append("[recent_entities]\n" + "\n".join(entity_lines))
                logger.info(
                    "[prompt_builder.py][build_messages][6.recent_entities 추가] entity_count=%d",
                    len(entity_lines),
                )

        if struct_context:
            parts.append("[structure]\n" + struct_context)
            logger.info(
                "[prompt_builder.py][build_messages][7.structure 추가] struct_context_len=%d preview=%s",
                len(struct_context),
                _preview_text(struct_context, 300),
            )

        if sqlite_context:
            parts.append("[sqlite_context]\n" + sqlite_context)
            logger.info(
                "[prompt_builder.py][build_messages][8.sqlite_context 추가] sqlite_context_len=%d preview=%s",
                len(sqlite_context),
                _preview_text(sqlite_context, 300),
            )

        metadata_summary = self.build_metadata_summary(hits)
        if metadata_summary:
            parts.append("[metadata]\n" + metadata_summary)
            logger.info(
                "[prompt_builder.py][build_messages][9.metadata 추가] metadata_len=%d",
                len(metadata_summary),
            )

        chunk_context = self.build_chunk_context(hits)
        if chunk_context:
            parts.append("[evidence]\n" + chunk_context)
            logger.info(
                "[prompt_builder.py][build_messages][10.evidence 추가] evidence_len=%d",
                len(chunk_context),
            )

        parts.append("[question]\n" + question.strip())
        logger.info(
            "[prompt_builder.py][build_messages][11.question 추가] question_len=%d preview=%s",
            len(question or ""),
            _preview_text(question, 300),
        )

        final_user_content = "\n\n".join(parts)
        messages.append({"role": "user", "content": final_user_content})

        logger.info(
            "[prompt_builder.py][build_messages][12.완료] parts_count=%d final_user_content_len=%d final_message_count=%d final_user_preview=%s",
            len(parts),
            len(final_user_content),
            len(messages),
            _preview_text(final_user_content, 500),
        )
        return messages