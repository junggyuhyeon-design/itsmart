"""
QueryAnalyzer: 질문 문자열을 분석해 Retrieval 전략과 top_k를 결정한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class QueryIntent:
    query_type: str
    top_k: int
    layer_filter: str | None
    extension_filter: str | None
    entity_hint: str
    keywords: list[str] = field(default_factory=list)


_LISTING_KW = (
    "목록", "전체", "모든", "몇 개", "몇개", "어떤 파일", "파일 목록",
    "list", "all files", "enumerate", "나열"
)

_DIAGRAM_KW = (
    "관계도", "다이어그램", "mermaid", "diagram", "flowchart",
    "그려", "그려줘", "시각화", "sequence", "architecture diagram"
)

_ARCHITECTURE_KW = (
    "아키텍처", "architecture", "구조도", "시스템 구조", "프로젝트 구조"
)

_API_DOC_KW = (
    "api 목록", "api목록", "api 정의", "api정의", "api list",
    "엔드포인트", "endpoint", "rest api", "swagger", "명세", "정의서"
)

_XML_KW = (
    "xml", "mapper xml", "mybatis", "마이바티스", "쿼리 xml", "sql xml"
)

_TABLE_KW = (
    "테이블", "table", "schema", "스키마", "칼럼", "column", "ddl", "erd"
)

_FLOW_KW = (
    "호출 관계", "호출", "흐름", "flow", "service 호출", "controller service mapper",
    "service mapper", "login 흐름", "호출흐름"
)

_CTRL_KW = ("controller", "컨트롤러")
_SVC_KW = ("service", "서비스")
_MAPPER_KW = ("mapper", "매퍼", "마이바티스")
_REPO_KW = ("repository", "dao", "레포지토리")
_XML_LAYER_KW = ("xml", "mapper xml", "mybatis xml")
_SQL_KW = ("sql", "query", "쿼리", "ddl", "dml")

_EXT_MAP: dict[str, tuple[str, ...]] = {
    "xml": ("xml", "mapper", "마이바티스", "mybatis"),
    "java": ("java", "클래스", "class", "controller", "service", "repository"),
    "sql": ("sql", "쿼리", "query", "ddl", "dml", "table"),
    "py": ("python", ".py"),
}


class QueryAnalyzer:
    def __init__(self, default_top_k: int = 5) -> None:
        self.default_top_k = default_top_k

    def analyze(self, question: str) -> QueryIntent:
        raw = question.strip()
        q = raw.lower()
        entity_hint = self._extract_entity(question)
        keywords = self._extract_keywords(question)

        if any(k in q for k in _LISTING_KW) and not any(k in q for k in _DIAGRAM_KW):
            return QueryIntent(
                query_type="listing",
                top_k=0,
                layer_filter=None,
                extension_filter=self._ext_filter(q),
                entity_hint=entity_hint,
                keywords=keywords,
            )

        if any(k in q for k in _ARCHITECTURE_KW):
            return QueryIntent(
                query_type="architecture",
                top_k=max(self.default_top_k * 8, 30),
                layer_filter=None,
                extension_filter=None,
                entity_hint=entity_hint,
                keywords=keywords,
            )

        if any(k in q for k in _DIAGRAM_KW):
            if any(k in q for k in _TABLE_KW):
                return QueryIntent(
                    query_type="diagram",
                    top_k=max(self.default_top_k * 6, 25),
                    layer_filter="mapper",
                    extension_filter="xml",
                    entity_hint=entity_hint,
                    keywords=keywords,
                )
            return QueryIntent(
                query_type="diagram",
                top_k=max(self.default_top_k * 8, 30),
                layer_filter=None,
                extension_filter=None,
                entity_hint=entity_hint,
                keywords=keywords,
            )

        if any(k in q for k in _API_DOC_KW):
            return QueryIntent(
                query_type="api_doc",
                top_k=max(self.default_top_k * 5, 20),
                layer_filter="controller",
                extension_filter="java",
                entity_hint=entity_hint,
                keywords=keywords,
            )

        if any(k in q for k in _FLOW_KW):
            return QueryIntent(
                query_type="layer_search",
                top_k=max(self.default_top_k * 6, 25),
                layer_filter=None,
                extension_filter=None,
                entity_hint=entity_hint,
                keywords=keywords,
            )

        if any(k in q for k in _XML_KW):
            return QueryIntent(
                query_type="xml_analysis",
                top_k=max(self.default_top_k * 4, 15),
                layer_filter="mapper",
                extension_filter="xml",
                entity_hint=entity_hint,
                keywords=keywords,
            )

        if any(k in q for k in _TABLE_KW):
            ext = "xml" if "xml" in q else "sql"
            layer = "mapper" if ext == "xml" else "ddl"
            return QueryIntent(
                query_type="table_analysis",
                top_k=max(self.default_top_k * 5, 20),
                layer_filter=layer,
                extension_filter=ext,
                entity_hint=entity_hint,
                keywords=keywords,
            )

        layer, multiplier = self._detect_layer(q)
        if layer:
            ext = "xml" if layer == "mapper" else ("sql" if layer == "ddl" else "java")
            return QueryIntent(
                query_type="layer_search",
                top_k=max(self.default_top_k * multiplier, 15),
                layer_filter=layer,
                extension_filter=ext,
                entity_hint=entity_hint,
                keywords=keywords,
            )

        return QueryIntent(
            query_type="qa",
            top_k=self.default_top_k,
            layer_filter=None,
            extension_filter=None,
            entity_hint=entity_hint,
            keywords=keywords,
        )

    def _detect_layer(self, q: str) -> tuple[str | None, int]:
        if any(k in q for k in _CTRL_KW):
            return "controller", 4
        if any(k in q for k in _SVC_KW):
            return "service", 4
        if any(k in q for k in _MAPPER_KW):
            return "mapper", 4
        if any(k in q for k in _REPO_KW):
            return "repository", 4
        if any(k in q for k in _XML_LAYER_KW):
            return "mapper", 4
        if any(k in q for k in _SQL_KW):
            return "ddl", 4
        return None, 1

    def _ext_filter(self, q: str) -> str | None:
        for ext, keywords in _EXT_MAP.items():
            if any(k in q for k in keywords):
                return ext
        return None

    def _extract_entity(self, question: str) -> str:
        pascal_tokens = re.findall(r"[A-Z][a-zA-Z0-9_]+", question)
        if pascal_tokens:
            return pascal_tokens[0]

        file_tokens = re.findall(r"([A-Za-z0-9_\-]+\.(?:xml|java|sql|py|js|ts))", question, re.I)
        if file_tokens:
            return file_tokens[0]

        korean_quoted = re.findall(r"'([^']+)'|\"([^\"]+)\"", question)
        flat = [x for tup in korean_quoted for x in tup if x]
        return flat[0] if flat else ""

    def _extract_keywords(self, question: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z가-힣_][A-Za-z0-9가-힣_:-]{1,30}", question)
        stopwords = {
            "보여줘", "찾아줘", "정리해줘", "설명해줘", "분석해줘", "작성해줘",
            "만들어줘", "그려줘", "프로젝트", "전체", "관련", "소스", "파일",
            "무엇", "어디", "있는지", "사용", "호출", "흐름", "관계", "목록"
        }
        cleaned = []
        for t in tokens:
            if len(t) < 2:
                continue
            if t in stopwords:
                continue
            cleaned.append(t)
        seen = set()
        result = []
        for t in cleaned:
            if t not in seen:
                seen.add(t)
                result.append(t)
        return result[:8]