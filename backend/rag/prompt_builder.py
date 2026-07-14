from __future__ import annotations

from typing import Any
import logging

logger = logging.getLogger(__name__)
# score 임계값: 이 값 미만의 청크는 노이즈로 판단해 제외
_SCORE_THRESHOLD = 0.6

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
    "qa": SYSTEM_BASE,
    "listing": SYSTEM_BASE,
}


class PromptBuilder:
    def trim_history(
            self,
            chat_history: list[dict[str, Any]],
            max_history_chars: int = 4000,
    ) -> list[dict[str, Any]]:
        if not chat_history:
            return []

        selected: list[dict[str, Any]] = []
        total = 0

        for row in reversed(chat_history):
            question = (row.get("question") or "").strip()
            answer = (row.get("answer") or "").strip()
            size = len(question) + len(answer)

            if selected and total + size > max_history_chars:
                break

            if question or answer:
                selected.append({"question": question, "answer": answer})
                total += size

        selected.reverse()
        return selected

    def build_metadata_summary(self, hits: list[dict[str, Any]]) -> str:
        if not hits:
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

            suffix = f" ({', '.join(meta_parts)})" if meta_parts else ""
            lines.append(f"- {key}{suffix}")

        return "\n".join(lines)

    def build_chunk_context(self, hits: list[dict[str, Any]]) -> str:
        if not hits:
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

            lines.append(f"[evidence {index}] {relative_path}")
            lines.append(f"```{language}" if language else "```")
            lines.append(text)
            lines.append("```")
            lines.append("")

        return "\n".join(lines).strip()

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
    ) -> list[dict[str, str]]:
        system_prompt = SYSTEM_PROMPTS.get(query_type, SYSTEM_BASE)
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

        trimmed = self.trim_history(chat_history or [], max_history_chars)
        for row in trimmed:
            if row["question"]:
                messages.append({"role": "user", "content": row["question"]})
            if row["answer"]:
                messages.append({"role": "assistant", "content": row["answer"]})

        parts: list[str] = []

        lowered_question = (question or "").lower()
        wants_structure = any(
            token in lowered_question
            for token in ["소스", "파일", "구조", "설명", "프로젝트", "source", "file", "structure"]
        )

        if wants_structure:
            parts.append(
                "[instruction]\n"
                "소스/파일/구조/프로젝트 설명 요청일 때는, "
                "현재 evidence와 metadata, structure에 포함된 정보만으로도 "
                "파일명, 경로, 레이어(Controller/Service/Repository 등), 확장자 기준의 "
                "구조 요약을 먼저 제시한 후, 부족한 부분을 언급한다."
            )

        if project_name:
            parts.append(f"[project]\n{project_name}")

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
                logger.info("===============================================")

            if entity_lines:
                parts.append("[recent_entities]\n" + "\n".join(entity_lines))

        if struct_context:
            parts.append("[structure]\n" + struct_context)

        if sqlite_context:
            parts.append("[sqlite_context]\n" + sqlite_context)

        metadata_summary = self.build_metadata_summary(hits)
        if metadata_summary:
            parts.append("[metadata]\n" + metadata_summary)

        chunk_context = self.build_chunk_context(hits)
        if chunk_context:
            parts.append("[evidence]\n" + chunk_context)

        parts.append("[question]\n" + question.strip())

        messages.append({"role": "user", "content": "\n\n".join(parts)})
        return messages