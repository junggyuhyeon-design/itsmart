"""
QueryAnalyzer: 질문 문자열을 분석해 Retrieval 전략과 top_k를 결정한다.

질문 유형:
  listing       → SQLite file_index 직접 반환 (Qdrant 0건)
  diagram       → SQLite 전체구조 + Qdrant top_k×8
  api_doc       → Qdrant filter(layer=controller) top_k×6
  layer_search  → Qdrant filter(layer_type) top_k×4
  qa            → Qdrant top_k (기본값)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class QueryIntent:
    query_type:       str               # listing / diagram / api_doc / layer_search / qa
    top_k:            int
    layer_filter:     str | None        # "controller" | "service" | "mapper" | "ddl" | None
    extension_filter: str | None        # "xml" | "java" | "sql" | None
    entity_hint:      str               # 질문에서 추출한 PascalCase 엔티티 (힌트)
    keywords:         list[str] = field(default_factory=list)


# ── 키워드 사전 ───────────────────────────────────────────────────

_LISTING_KW  = ("목록", "전체", "모든", "몇 개", "몇개", "어떤 파일",
                 "list", "all files", "enumerate", "나열")
_DIAGRAM_KW  = ("관계도", "다이어그램", "mermaid", "diagram", "flowchart",
                 "그려", "그려줘", "시각화", "sequence", "아키텍처", "architecture")
_API_DOC_KW  = ("api 목록", "api목록", "api 정의", "api정의", "api list",
                 "엔드포인트", "endpoint", "rest api", "swagger", "명세")
_CTRL_KW     = ("controller", "컨트롤러")
_SVC_KW      = ("service", "서비스")
_MAPPER_KW   = ("mapper", "마이바티스", "mybatis", "xml", "쿼리", "query")
_REPO_KW     = ("repository", "dao", "레포지토리")
_TABLE_KW    = ("테이블", "table", "schema", "스키마", "칼럼", "column", "ddl")

_EXT_MAP: dict[str, tuple[str, ...]] = {
    "xml":  ("xml", "mapper", "마이바티스", "mybatis"),
    "java": ("java", "클래스", "class"),
    "sql":  ("sql", "쿼리", "query", "ddl", "dml"),
    "py":   ("python", ".py"),
}


class QueryAnalyzer:
    def __init__(self, default_top_k: int = 5) -> None:
        self.default_top_k = default_top_k

    def analyze(self, question: str) -> QueryIntent:
        q = question.lower()

        # 1. 파일 목록 열거 (diagram 키워드가 없을 때만)
        if any(k in q for k in _LISTING_KW) and not any(k in q for k in _DIAGRAM_KW):
            return QueryIntent(
                query_type="listing",
                top_k=0,
                layer_filter=None,
                extension_filter=self._ext_filter(q),
                entity_hint="",
            )

        # 2. 다이어그램 / 아키텍처
        if any(k in q for k in _DIAGRAM_KW):
            return QueryIntent(
                query_type="diagram",
                top_k=self.default_top_k * 8,
                layer_filter=None,
                extension_filter=None,
                entity_hint=self._extract_entity(question),
            )

        # 3. API 정의서
        if any(k in q for k in _API_DOC_KW):
            return QueryIntent(
                query_type="api_doc",
                top_k=self.default_top_k * 6,
                layer_filter="controller",
                extension_filter="java",
                entity_hint="",
            )

        # 4. 레이어 기반 검색
        layer, multiplier = self._detect_layer(q)
        if layer:
            ext = "xml" if layer == "mapper" else ("sql" if layer == "ddl" else "java")
            return QueryIntent(
                query_type="layer_search",
                top_k=self.default_top_k * multiplier,
                layer_filter=layer,
                extension_filter=ext,
                entity_hint=self._extract_entity(question),
            )

        # 5. 테이블 / 스키마
        if any(k in q for k in _TABLE_KW):
            return QueryIntent(
                query_type="layer_search",
                top_k=self.default_top_k * 4,
                layer_filter="ddl",
                extension_filter="sql",
                entity_hint=self._extract_entity(question),
            )

        # 6. 기본 QA
        return QueryIntent(
            query_type="qa",
            top_k=self.default_top_k,
            layer_filter=None,
            extension_filter=None,
            entity_hint=self._extract_entity(question),
        )

    # ── 내부 헬퍼 ────────────────────────────────────────────────

    def _detect_layer(self, q: str) -> tuple[str | None, int]:
        """레이어 감지 → (layer_type, top_k 배수)"""
        if any(k in q for k in _CTRL_KW):   return "controller", 4
        if any(k in q for k in _SVC_KW):    return "service",    4
        if any(k in q for k in _MAPPER_KW): return "mapper",     4
        if any(k in q for k in _REPO_KW):   return "repository", 4
        return None, 1

    def _ext_filter(self, q: str) -> str | None:
        for ext, keywords in _EXT_MAP.items():
            if any(k in q for k in keywords):
                return ext
        return None

    def _extract_entity(self, question: str) -> str:
        """질문에서 PascalCase 토큰 추출 (클래스명·파일명 힌트)."""
        tokens = re.findall(r"[A-Z][a-zA-Z0-9]+", question)
        return tokens[0] if tokens else ""
