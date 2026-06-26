"""
PromptBuilder: 질문 유형에 따라 LLM 메시지 배열을 조립한다.

조립 순서 (LLM이 최신·마지막 정보를 우선하므로 중요도 역순 배치):
  1. System Prompt  — 역할, 언어, 출력 형식 규칙
  2. History        — 이전 대화 (오래된 → 최신)
  3. [User 메시지]
     A. 현재 프로젝트명
     B. 파일 구조 요약 (listing/diagram 시 주입)
     C. 검색된 파일의 Metadata 요약 (layer/class/package)
     D. 소스코드 청크 (실제 코드)
     E. 질문
"""
from __future__ import annotations


# ── System Prompt 사전 ─────────────────────────────────────────────

_SYSTEM_BASE = """\
당신은 소스코드 분석 전문 AI입니다.
- 항상 한국어로 답변합니다.
- 코드 블록은 언어를 명시합니다 (```java, ```xml, ```sql 등).
- 근거가 없으면 추측하지 않고 "정보가 부족합니다"라고 답합니다.
- 이전 대화가 있으면 후속 질문(그 파일, 그 서비스, 아까 등)은 대화 맥락을 우선합니다.
- 소스코드 관련 질문은 제공된 [소스코드 컨텍스트]를 근거로 사용합니다."""

_SYSTEM_DIAGRAM = """\
당신은 소스코드를 분석하여 Mermaid 다이어그램을 생성하는 전문 AI입니다.

[출력 규칙]
1. 반드시 단 하나의 ```mermaid 코드블록만 출력합니다. 설명·제목·해설 텍스트 금지.
2. 첫 줄은 반드시 flowchart LR 로 시작합니다.
3. classDef, style, subgraph, click, %% 주석은 사용하지 않습니다.
4. 노드명에 공백 대신 언더스코어(_)를 사용합니다.
5. 엣지 라벨: READS / WRITES / JOINS / CALLS / EXTENDS / IMPLEMENTS 만 허용합니다.
6. DDL/SQL 파일 자체는 노드로 만들지 않습니다. 테이블명만 노드로 사용합니다.
7. 제공된 파일 목록과 소스코드 청크를 기반으로 실제 존재하는 관계만 그립니다."""

_SYSTEM_API_DOC = """\
당신은 REST API 명세서를 작성하는 전문 AI입니다.

[출력 형식 — Markdown 표]
| Method | URL | 설명 | Request Body | Response |
|--------|-----|------|-------------|----------|

- @GetMapping, @PostMapping, @PutMapping, @DeleteMapping, @RequestMapping 을 파싱합니다.
- URL은 클래스 레벨 @RequestMapping + 메서드 레벨 Mapping을 합산합니다.
- 정보가 없는 항목은 "-"로 표기합니다.
- 메서드별로 한 행씩 작성합니다."""

_SYSTEM_LAYER = """\
당신은 소스코드 레이어 분석 전문 AI입니다.
- 제공된 소스코드 컨텍스트에서 클래스명, 메서드명, 의존관계를 추출합니다.
- 한국어로 구조적으로 답변합니다.
- 근거 코드를 인용할 때는 파일 경로와 함께 표시합니다."""

_SYSTEM_PROMPTS: dict[str, str] = {
    "diagram":      _SYSTEM_DIAGRAM,
    "api_doc":      _SYSTEM_API_DOC,
    "layer_search": _SYSTEM_LAYER,
    "listing":      _SYSTEM_BASE,
    "file_search":  _SYSTEM_BASE,
    "qa":           _SYSTEM_BASE,
}


class PromptBuilder:
    """질문 유형·컨텍스트·히스토리를 받아 Ollama messages 배열을 반환한다."""

    def build_messages(
        self,
        question:          str,
        hits:              list[dict],
        query_type:        str,
        project_name:      str | None  = None,
        struct_context:    str         = "",   # file_index_summary 텍스트 (listing/diagram)
        chat_history:      list[dict] | None = None,
        max_history_chars: int         = 4000,
    ) -> list[dict]:

        system_prompt = _SYSTEM_PROMPTS.get(query_type, _SYSTEM_BASE)
        messages: list[dict] = [{"role": "system", "content": system_prompt}]

        # ── History (오래된 → 최신) ───────────────────────────────
        for row in self._trim_history(chat_history or [], max_history_chars):
            messages.append({"role": "user",      "content": row["question"]})
            messages.append({"role": "assistant",  "content": row["answer"]})

        # ── User 메시지 조립 ──────────────────────────────────────
        parts: list[str] = []

        # (A) 현재 프로젝트명
        if project_name:
            parts.append(f"[현재 프로젝트: {project_name}]")

        # (B) 파일 구조 요약 (diagram, listing 질문 시 주입)
        if struct_context:
            parts.append(f"[프로젝트 파일 구조]\n{struct_context}")

        # (C) 검색된 파일의 Metadata 요약
        meta_summary = self._build_metadata_summary(hits)
        if meta_summary:
            parts.append(f"[검색된 소스 메타데이터]\n{meta_summary}")

        # (D) 소스코드 청크
        chunk_context = self._build_chunk_context(hits)
        if chunk_context:
            parts.append(f"[소스코드 컨텍스트]\n{chunk_context}")

        # (E) 질문
        parts.append(f"[질문]\n{question}")

        messages.append({"role": "user", "content": "\n\n".join(parts)})
        return messages

    # ── 내부 헬퍼 ─────────────────────────────────────────────────

    def _build_metadata_summary(self, hits: list[dict]) -> str:
        """검색된 파일별 메타데이터를 한 줄 요약으로 반환 (중복 파일 제거)."""
        if not hits:
            return ""
        seen: set[str] = set()
        lines: list[str] = []
        for h in hits:
            key = h.get("relative_path", h.get("file_name", ""))
            if key in seen:
                continue
            seen.add(key)
            meta_parts = []
            if h.get("layer_type"):   meta_parts.append(f"layer={h['layer_type']}")
            if h.get("class_name"):   meta_parts.append(f"class={h['class_name']}")
            if h.get("package"):      meta_parts.append(f"package={h['package']}")
            if h.get("content_type"): meta_parts.append(f"type={h['content_type']}")
            meta_str = f"  [{', '.join(meta_parts)}]" if meta_parts else ""
            lines.append(f"  - {key}{meta_str}")
        return "\n".join(lines)

    def _build_chunk_context(self, hits: list[dict]) -> str:
        """청크 목록을 파일 경로·클래스·레이어 헤더와 함께 코드블록으로 조립."""
        if not hits:
            return ""
        ext_lang_map = {
            "java": "java", "py": "python", "xml": "xml",
            "sql": "sql",   "js": "javascript", "ts": "typescript",
            "md": "markdown", "json": "json", "yml": "yaml", "yaml": "yaml",
        }
        parts: list[str] = []
        for h in hits:
            header_parts = [h.get("relative_path") or h.get("file_name", "")]
            if h.get("class_name"):   header_parts.append(f"class={h['class_name']}")
            if h.get("layer_type"):   header_parts.append(f"layer={h['layer_type']}")
            if h.get("chunk_index") is not None:
                header_parts.append(f"chunk#{h['chunk_index']}")
            lang = ext_lang_map.get(h.get("extension", ""), "")
            parts.append(
                f"--- {' | '.join(header_parts)} ---\n"
                f"```{lang}\n{h.get('text', '')}\n```"
            )
        return "\n\n".join(parts)

    def _trim_history(self, history: list[dict], max_chars: int) -> list[dict]:
        """오래된 턴부터 제거하여 max_chars 이내로 유지 (최신 턴 우선 보존)."""
        if max_chars <= 0 or not history:
            return []
        trimmed: list[dict] = []
        total = 0
        for row in reversed(history):
            q = (row.get("question") or "").strip()
            a = (row.get("answer")   or "").strip()
            if not q or not a:
                continue
            pair_len = len(q) + len(a)
            if trimmed and total + pair_len > max_chars:
                break
            trimmed.append({"question": q, "answer": a})
            total += pair_len
        trimmed.reverse()
        return trimmed
