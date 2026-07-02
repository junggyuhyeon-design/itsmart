"""
<<<<<<< HEAD
QueryAnalyzer: 질문을 분석해 Retrieval 전략을 결정한다.
=======
QueryAnalyzer: 질문 문자열을 분석해 Retrieval 전략과 top_k를 결정한다.
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class QueryIntent:
<<<<<<< HEAD
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
_CTRL_KW   = ("controller", "컨트롤러", "@RestController", "@controller")
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
=======
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
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
}


class QueryAnalyzer:
    def __init__(self, default_top_k: int = 5) -> None:
        self.default_top_k = default_top_k

    def analyze(self, question: str) -> QueryIntent:
<<<<<<< HEAD
        q           = question.lower().strip()
        entity_hint = self._extract_entity_hint(question)                   # key word 추출
        search_query = self._build_search_query(question, entity_hint)      # 질문 정제
        layer_filter = self._detect_layer(q, entity_hint)                   # 질문 내 포함된 단어로 계층 추출
        ext_filter   = self._detect_extension(q, layer_filter, entity_hint) # 확장자 추출
        query_type   = self._detect_type(q, layer_filter)                   # 질의 유형 추출(api_doc, layer_search, qa)
        top_k        = self._decide_top_k(query_type, entity_hint)          # top_k 결정

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

=======
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
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9

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


    def _detect_layer(self, q: str, entity_hint: str) -> str | None:
        eh = entity_hint.lower()
        if self._has(q, _CTRL_KW)   or eh.endswith("controller"):  return "controller"
        if self._has(q, _SVC_KW)    or eh.endswith("service"):     return "service"
        if self._has(q, _MAPPER_KW) or eh.endswith("mapper"):      return "mapper"
        if self._has(q, _REPO_KW)   or eh.endswith("repository"):  return "repository"
        if self._has(q, _DDL_KW)    or self._has(q, _SQL_KW):      return "ddl"
        return None

<<<<<<< HEAD
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


    def _detect_type(self, q: str, layer_filter: str | None) -> str:
        if self._has(q, _API_KW):
            return "api_doc"
        if layer_filter:
            return "layer_search"
        return "qa"

    
    def _decide_top_k(self, query_type: str, entity_hint: str) -> int:
        k = self.default_top_k
        # entity_hint가 있으면(특정 클래스/파일 지목) 범위 확대
        hint_boost = 2 if entity_hint else 1
        if query_type == "api_doc":      return k * 6
        if query_type == "layer_search": return k * 4
        # qa: entity_hint 있으면 top_k 확대, 없으면 기본값
        return k * hint_boost
=======
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
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
