"""
PromptBuilder: 질문 유형·컨텍스트·히스토리를 받아 Ollama messages 배열을 반환한다.

프롬프트 조립 순서 (소형 모델 최적화):
  1. System Prompt
  2. History (오래된 → 최신)
  3. User 메시지
     a. [프로젝트]   — 프로젝트명
     b. [소스코드]   — Qdrant 검색 결과 (score 필터 후 조립)
     c. [질문]       — 원문 질문 (항상 마지막 — 소형 모델은 끝부분에 집중)
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

# score 임계값: 이 값 미만의 청크는 노이즈로 판단해 제외
_SCORE_THRESHOLD = 0.35

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
- [소스코드]가 제공된 경우 반드시 그것을 근거로 답변합니다.
- [소스코드]가 없거나 질문과 관련 없는 경우, 보유한 일반 지식으로 성실히 답변합니다.
- 이전 대화의 "그 파일", "아까" 같은 지시어는 대화 맥락을 우선합니다.
- 답변 후 예상 질문, 추가 질문, 관련 질문을 절대 제안하지 않습니다."""


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
    "api_doc":      _SYS_API,
    "layer_search": _SYS_LAYER,
}


class PromptBuilder:

    def build_messages(
        self,
        question:          str,
        hits:              list[dict],
        query_type:        str,
        project_name:      str | None        = None,
        chat_history:      list[dict] | None = None,
        max_history_chars: int               = 4000,
    ) -> list[dict]:
        """질문 유형·컨텍스트·히스토리를 받아 Ollama messages 배열을 반환한다."""
        messages: list[dict] = [
            {"role": "system", "content": _SYS_MAP.get(query_type, _SYS_BASE)}
        ]

        # History
        for row in self._trim_history(chat_history or [], max_history_chars):
            messages.append({"role": "user",      "content": row["question"]})
            messages.append({"role": "assistant",  "content": row["answer"]})

        # ── User 메시지 조립 ───────────────────────────────────────
        parts: list[str] = []

        # a. 프로젝트명
        if project_name:
            parts.append(f"[프로젝트: {project_name}]")

        # b. 소스코드 청크(+ 헤더) (score 필터 적용)
        code_ctx = self._build_code_context(hits, query_type)
        if code_ctx:
            parts.append(f"[소스코드]\n{code_ctx}")
        else:
            parts.append("[소스코드]\n관련 소스코드를 찾지 못했습니다. 일반 지식으로 답변하세요.")

        # c. 질문 — 항상 마지막 (소형 모델은 끝부분에 집중)
        parts.append(f"[질문]\n{question}")

        messages.append({"role": "user", "content": "\n\n".join(parts)})

        # 작성된 프롬프트를 확인한다.
        for m in messages:
            content = m.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            logger.info("content: %s", content)

        return messages

    # ── 내부 헬퍼 ─────────────────────────────────────────────────
    def _build_code_context(self, hits: list[dict], query_type: str) -> str:
        """
        Qdrant 청크를 헤더 + 코드블록으로 조립.

        hits[i] 구조:
          - score        : Qdrant 유사도 점수 (float)
                           → _SCORE_THRESHOLD 미만은 노이즈로 제외
          - relative_path: 파일 상대경로            ← 헤더 필수
          - file_name    : 원본 파일명              ← relative_path 없을 때 fallback
          - extension    : 확장자                  ← 코드블록 언어 결정
          - chunk_index  : 청크 순번               ← 중복 제거 키
          - text         : 실제 소스코드            ← 코드블록 본문
          - class_name   : 클래스명 or XML namespace ← 헤더 보조
          - layer_type   : controller/service/...  ← 헤더 보조
          - project_id   : 프로젝트 식별자          ← 미사용
          - project_name : 프로젝트명              ← 상위에서 별도 표기, 미사용
        """
        if not hits:
            return ""

        # api_doc은 구조 파악용으로 넓게 가져오므로 임계값 완화
        threshold = (
            _SCORE_THRESHOLD * 0.7
            if query_type in ("api_doc")
            else _SCORE_THRESHOLD
        )

        seen:  set[str]  = set()
        parts: list[str] = []

        for h in hits:
            # score 필터: 임계값 미만은 노이즈 청크로 제외
            score = h.get("score", 1.0)
            if score < threshold:
                continue

            # relative_path + chunk_index 기준 중복 제거
            chunk_key = f"{h.get('relative_path', '')}:{h.get('chunk_index', '')}"
            if chunk_key in seen:
                continue
            seen.add(chunk_key)

            # 헤더: path | class | layer
            header_tokens: list[str] = [h.get("relative_path") or h.get("file_name", "unknown")]
            if h.get("class_name"):   header_tokens.append(f"class={h['class_name']}")
            if h.get("layer_type"):   header_tokens.append(f"layer={h['layer_type']}")

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
            # 첫 항목은 무조건 포함, 이후는 한도 초과 시 중단
            if trimmed and total + pair_len > max_chars:
                break
            trimmed.append({"question": q, "answer": a})
            total += pair_len
        trimmed.reverse()
        return trimmed
