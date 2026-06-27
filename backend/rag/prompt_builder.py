"""
PromptBuilder: 질문 유형·컨텍스트·히스토리를 받아 Ollama messages 배열을 반환한다.

메시지 조립 순서:
  1. System Prompt  — 역할 및 출력 규칙
  2. History        — 이전 대화 (오래된 → 최신)
  3. User 메시지
     - [프로젝트] 프로젝트명
     - [파일구조] extra_context (diagram 시 구조 파악용, 200줄 제한)
     - [소스코드] Qdrant 검색 청크 (파일경로 헤더 + 코드)
     - [질문]

hits 구조 (qdrant_service.search() 반환값):
  {"score": float, ...chunk_payload}
  chunk_payload 필드 (chunk_service._make_chunk 기준):
    project_id, project_name, text, file_name, extension,
    relative_path, chunk_index,
    layer_type, class_name, package, content_type
"""
from __future__ import annotations

_EXT_LANG: dict[str, str] = {
    "java": "java", "py": "python", "xml": "xml", "sql": "sql",
    "js": "javascript", "ts": "typescript",
    "md": "markdown", "json": "json", "yml": "yaml", "yaml": "yaml",
}

# ── System Prompts ─────────────────────────────────────────────────

_SYS_BASE = """\
당신은 소스코드 분석 전문 AI입니다.
- 항상 한국어로 답변합니다.
- 코드 블록에는 언어를 명시합니다 (```java, ```xml 등).
- 제공된 [소스코드]를 근거로만 답변하고, 근거가 없으면 "정보가 부족합니다"라고 말합니다.
- 이전 대화의 "그 파일", "아까" 같은 지시어는 대화 맥락을 우선합니다.
- 답변 후 예상 질문, 추가 질문, 관련 질문을 절대 제안하지 않습니다."""

_SYS_DIAGRAM = """\
당신은 소스코드를 분석하여 Mermaid 다이어그램을 생성하는 전문 AI입니다.

[출력 규칙 — 반드시 준수]
1. 반드시 단 하나의 ```mermaid 코드블록만 출력합니다. 설명·제목·해설 텍스트 금지.
2. 첫 줄은 반드시 flowchart LR 로 시작합니다.
3. classDef, style, subgraph, click, %% 주석은 사용하지 않습니다.
4. 노드명에 공백 대신 언더스코어(_)를 사용합니다.
5. 엣지 라벨은 CALLS / READS / WRITES / JOINS / EXTENDS / IMPLEMENTS 만 허용합니다.
6. DDL/SQL 파일 자체는 노드로 만들지 않습니다. 테이블명만 노드로 사용합니다.
7. 제공된 [파일구조]와 [소스코드]를 기반으로 실제 존재하는 관계만 그립니다.
8. 답변 후 예상 질문, 추가 질문, 관련 질문을 절대 제안하지 않습니다."""

_SYS_API = """\
당신은 REST API 명세서를 작성하는 전문 AI입니다.
- 항상 한국어로 답변합니다.
- 제공된 [소스코드]의 @GetMapping/@PostMapping/@PutMapping/@DeleteMapping을 파싱합니다.
- 출력 형식: Markdown 표 (Method | URL | 설명 | Request | Response).
- URL은 클래스 레벨 @RequestMapping + 메서드 레벨을 합산합니다.
- 정보가 없는 항목은 "-"로 표기합니다.
- 답변 후 예상 질문, 추가 질문, 관련 질문을 절대 제안하지 않습니다."""

_SYS_LAYER = """\
당신은 소스코드 레이어 분석 전문 AI입니다.
- 항상 한국어로 답변합니다.
- 제공된 [소스코드]에서 클래스명, 메서드명, 의존관계를 추출해 구조적으로 설명합니다.
- 근거 코드를 인용할 때는 파일 경로를 함께 표시합니다.
- 답변 후 예상 질문, 추가 질문, 관련 질문을 절대 제안하지 않습니다."""

_SYS_MAP: dict[str, str] = {
    "diagram":      _SYS_DIAGRAM,
    "api_doc":      _SYS_API,
    "layer_search": _SYS_LAYER,
}


class PromptBuilder:
    """질문 유형·컨텍스트·히스토리를 받아 Ollama messages 배열을 반환한다."""

    def build_messages(
        self,
        question:          str,
        hits:              list[dict],
        query_type:        str,
        project_name:      str | None        = None,
        struct_context:    str               = "",
        chat_history:      list[dict] | None = None,
        max_history_chars: int               = 4000,
    ) -> list[dict]:

        messages: list[dict] = [
            {"role": "system", "content": _SYS_MAP.get(query_type, _SYS_BASE)}
        ]

        # History (오래된 → 최신)
        for row in self._trim_history(chat_history or [], max_history_chars):
            messages.append({"role": "user",      "content": row["question"]})
            messages.append({"role": "assistant",  "content": row["answer"]})

        # User 메시지 조립
        parts: list[str] = []

        if project_name:
            parts.append(f"[프로젝트: {project_name}]")

        # 파일구조: diagram 시 관계 파악에 핵심, 200줄 제한으로 토큰 절약
        if struct_context:
            lines   = struct_context.splitlines()
            trimmed = "\n".join(lines[:200])
            if len(lines) > 200:
                trimmed += f"\n... (총 {len(lines)}줄 중 200줄 표시)"
            parts.append(f"[파일구조]\n{trimmed}")

        # 소스코드 청크
        code_ctx = self._build_code_context(hits)
        if code_ctx:
            parts.append(f"[소스코드]\n{code_ctx}")

        parts.append(f"[질문]\n{question}")

        messages.append({"role": "user", "content": "\n\n".join(parts)})
        return messages

    # ── 내부 헬퍼 ─────────────────────────────────────────────────

    def _build_code_context(self, hits: list[dict]) -> str:
        """
        Qdrant 청크를 헤더 + 코드블록으로 조립.

        hits[i] 구조:
          - score        : Qdrant 유사도 점수 (float) — 프롬프트에 불필요, 미사용
          - relative_path: 파일 상대경로            ← 헤더 필수
          - file_name    : 원본 파일명              ← relative_path 없을 때 fallback
          - extension    : 확장자                  ← 코드블록 언어 결정
          - chunk_index  : 청크 순번               ← 중복 제거 키
          - text         : 실제 소스코드            ← 코드블록 본문
          - class_name   : 클래스명 or XML namespace ← 헤더 보조
          - layer_type   : controller/service/...  ← 헤더 보조
          - content_type : api_endpoint/sql_select/... ← 헤더 보조 (LLM 역할 파악)
          - package      : Java package            ← 헤더 노이즈, 미사용
          - project_id   : 프로젝트 식별자          ← 불필요, 미사용
          - project_name : 프로젝트명              ← 상위에서 별도 표기, 미사용
        """
        if not hits:
            return ""

        seen:  set[str]  = set()
        parts: list[str] = []

        for h in hits:
            # relative_path + chunk_index 기준 중복 제거
            chunk_key = f"{h.get('relative_path', '')}:{h.get('chunk_index', '')}"
            if chunk_key in seen:
                continue
            seen.add(chunk_key)

            # 헤더: 경로 | class | layer | content_type (값 있는 것만)
            header_tokens: list[str] = [h.get("relative_path") or h.get("file_name", "unknown")]
            if h.get("class_name"):   header_tokens.append(f"class={h['class_name']}")
            if h.get("layer_type"):   header_tokens.append(f"layer={h['layer_type']}")
            if h.get("content_type"): header_tokens.append(f"type={h['content_type']}")

            lang = _EXT_LANG.get(h.get("extension", ""), "")
            parts.append(
                f"--- {' | '.join(header_tokens)} ---\n"
                f"```{lang}\n{h.get('text', '').strip()}\n```"
            )

        return "\n\n".join(parts)

    def _trim_history(self, history: list[dict], max_chars: int) -> list[dict]:
        """최신 턴을 우선 보존하면서 max_chars 이내로 자른다."""
        if not history or max_chars <= 0:
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
