"""
QueryAnalyzer: 질문을 분석해 Retrieval 전략을 결정한다.

흐름:
  1. 질문에서 핵심 검색어(search_query) 추출  — 노이즈("설명해줘", "알려줘") 제거
  2. entity_hint 추출                         — CamelCase 클래스명, URL 경로 등
  3. query_type 결정                          — diagram / api_doc / layer_search / qa
  4. layer_filter / extension_filter 결정     — Qdrant payload 필터
  5. top_k 결정                               — query_type별 적정 청크 수

query_type:
  qa            — 일반 코드 질의응답 (기본)
  diagram       — Mermaid 관계도/흐름도/아키텍처
  api_doc       — API 엔드포인트/컨트롤러 중심
  layer_search  — 특정 레이어(controller/service/mapper/repository/ddl) 검색
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class QueryIntent:
    query_type:       str
    top_k:            int
    layer_filter:     str | None
    extension_filter: str | None
    entity_hint:      str = ""
    search_query:     str = ""   # Qdrant 검색에 사용할 정제된 쿼리


# ── 노이즈 제거 패턴 ──────────────────────────────────────────────
_NOISE_KW = (
    "설명해줘", "설명해", "설명 해줘", "알려줘", "알려", "보여줘", "보여",
    "찾아줘", "찾아", "뭐야", "뭔가요", "뭔지", "어떻게", "어떤", "무엇",
    "전체적인", "전체", "대해서", "대해", "관련해서", "관련된", "관련",
    "해줘", "해주세요", "주세요", "주시겠어요", "해", "줘",
    "에 대해", "에 대한", "이란", "이란게", "이란걸", "란", "란게",
)

# ── 키워드 테이블 ──────────────────────────────────────────────────
_DIAGRAM_KW = (
    "관계도", "다이어그램", "mermaid", "머메이드", "diagram",
    "flowchart", "플로우차트", "sequence", "시퀀스",
    "아키텍처", "architecture", "구조도", "흐름도", "의존 관계", "의존관계",
    "그려", "그려줘", "시각화",
)
_API_KW    = ("api", "엔드포인트", "endpoint", "rest", "swagger", "uri", "명세", "요청값", "응답값")
_CTRL_KW   = ("controller", "컨트롤러", "@restcontroller", "@controller")
_SVC_KW    = ("service", "서비스", "@service")
_MAPPER_KW = ("mapper", "마이바티스", "mybatis")
_REPO_KW   = ("repository", "repo", "dao", "레포지토리")
_DDL_KW    = ("테이블", "table", "schema", "스키마", "column", "칼럼", "컬럼", "ddl")
_SQL_KW    = ("sql", "쿼리", "query", "select", "insert", "update", "delete")

_EXT_MAP: dict[str, tuple[str, ...]] = {
    "java": (".java", "controller", "service", "repository", "dto", "vo", "entity"),
    "xml":  (".xml",  "mapper", "마이바티스", "mybatis"),
    "sql":  (".sql",  "ddl", "dml", "schema"),
    "py":   (".py",   "python"),
    "js":   (".js",   "javascript"),
    "ts":   (".ts",   "typescript"),
}


class QueryAnalyzer:
    def __init__(self, default_top_k: int = 5) -> None:
        self.default_top_k = default_top_k

    def analyze(self, question: str) -> QueryIntent:
        q           = question.lower().strip()
        entity_hint = self._extract_entity_hint(question)
        search_query = self._build_search_query(question, entity_hint)
        # is_diagram  = self._has(q, _DIAGRAM_KW)

        # diagram: layer/ext 필터 없이 전체 청크를 넓게 검색
        # if is_diagram:
        #     return QueryIntent(
        #         query_type="diagram",
        #         top_k=self.default_top_k * 8,
        #         layer_filter=None,
        #         extension_filter=None,
        #         entity_hint=entity_hint,
        #         search_query=search_query,
        #     )

        layer_filter = self._detect_layer(q, entity_hint)
        ext_filter   = self._detect_extension(q, layer_filter, entity_hint)
        query_type   = self._detect_type(q, layer_filter)
        top_k        = self._decide_top_k(query_type, entity_hint)

        # api_doc 는 controller + java 고정
        if query_type == "api_doc":
            layer_filter = "controller"
            ext_filter   = "java"

        return QueryIntent(
            query_type=query_type,
            top_k=top_k,
            layer_filter=layer_filter,
            extension_filter=ext_filter,
            entity_hint=entity_hint,
            search_query=search_query,
        )

    # ── 내부 헬퍼 ─────────────────────────────────────────────────

    def _has(self, q: str, kws: tuple[str, ...]) -> bool:
        return any(k in q for k in kws)

    # 확인 완료
    def _build_search_query(self, question: str, entity_hint: str) -> str:
        """
        임베딩 검색에 사용할 정제 쿼리 생성.
        - 노이즈 표현("설명해줘", "알려줘" 등) 제거
        - entity_hint가 있으면 앞에 배치해 검색 정확도 향상
        """
        cleaned = question.strip()
        # 노이즈 제거 (긴 패턴 먼저)
        for noise in sorted(_NOISE_KW, key=len, reverse=True):
            cleaned = re.sub(re.escape(noise), " ", cleaned, flags=re.I)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

        # entity_hint가 있고 cleaned에 이미 포함되어 있지 않으면 앞에 붙임
        if entity_hint and entity_hint.lower() not in cleaned.lower():
            cleaned = f"{entity_hint} {cleaned}"

        return cleaned if cleaned else question

    # 확인 완료
    def _extract_entity_hint(self, question: str) -> str:
        """CamelCase 클래스명, snake_case 식별자, URL 경로를 순서대로 탐색."""
        patterns = [
            r"\b([A-Z][A-Za-z0-9]+(?:Controller|Service|Repository|Mapper|DTO|DAO|VO|Entity))\b",
            r"\b([A-Z][A-Za-z0-9]{3,})\b",
            r"\b([a-z][a-z0-9]+(?:_[a-z0-9]+){1,})\b",
            r"(/[a-zA-Z0-9_\-/{}/]+)",
        ]
        for pat in patterns:
            m = re.search(pat, question)
            if m:
                return m.group(1)
        return ""

    # 확인 완료
    def _detect_layer(self, q: str, entity_hint: str) -> str | None:
        eh = entity_hint.lower()
        if self._has(q, _CTRL_KW)   or eh.endswith("controller"):  return "controller"
        if self._has(q, _SVC_KW)    or eh.endswith("service"):     return "service"
        if self._has(q, _MAPPER_KW) or eh.endswith("mapper"):      return "mapper"
        if self._has(q, _REPO_KW)   or eh.endswith("repository"):  return "repository"
        if self._has(q, _DDL_KW)    or self._has(q, _SQL_KW):      return "ddl"
        return None

    # 확인 완료
    def _detect_extension(self, q: str, layer_filter: str | None, entity_hint: str) -> str | None:
        for ext, kws in _EXT_MAP.items():
            if any(k in q for k in kws):
                return ext
        eh = entity_hint.lower()
        if eh.endswith(("controller", "service", "repository", "dto", "dao", "vo", "entity")): return "java"
        if eh.endswith("mapper"):                                    return "xml"
        if layer_filter == "mapper":                                 return "xml"
        if layer_filter in ("controller", "service", "repository"):  return "java"
        if layer_filter == "ddl":                                    return "sql"
        return None

    # 확인 완료
    def _detect_type(self, q: str, layer_filter: str | None) -> str:
        if self._has(q, _API_KW):
            return "api_doc"
        if layer_filter:
            return "layer_search"
        return "qa"

    # 확인 완료
    def _decide_top_k(self, query_type: str, entity_hint: str) -> int:
        k = self.default_top_k
        # entity_hint가 있으면(특정 클래스/파일 지목) 범위 확대
        hint_boost = 2 if entity_hint else 1
        if query_type == "api_doc":      return k * 6
        if query_type == "layer_search": return k * 4
        # qa: entity_hint 있으면 top_k 확대, 없으면 기본값
        return k * hint_boost
