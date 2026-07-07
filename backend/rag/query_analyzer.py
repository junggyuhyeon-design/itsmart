from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class QueryIntent:
    query_type: str = "qa"
    top_k: int = 5
    layer_filter: str | None = None
    extension_filter: str | None = None
    entity_hint: str | None = None
    keywords: list[str] = field(default_factory=list)
    search_query: str = ""


_NOISE_KW = (
    "설명해줘", "설명해", "설명 해줘", "알려줘", "알려", "보여줘", "보여",
    "찾아줘", "찾아", "뭐야", "뭔가요", "뭔지", "어떻게", "어떤", "무엇",
    "전체적인", "전체", "대해서", "대해", "관련해서", "관련된", "관련",
    "해줘", "해주세요", "주세요", "주시겠어요", "해", "줘",
    "에 대해", "에 대한", "이란", "이란게", "이란걸", "란", "란게",
    "please", "show", "tell", "explain", "about", "what", "is", "the", "a", "an",
)

LAYER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bcontroller\b|컨트롤러|@restcontroller|@controller", re.IGNORECASE), "controller"),
    (re.compile(r"\bservice\b|서비스|@service", re.IGNORECASE), "service"),
    (re.compile(r"\brepository\b|\brepo\b|\bdao\b|레포지토리|@repository", re.IGNORECASE), "repository"),
    (re.compile(r"\bmapper\b|마이바티스|mybatis|@mapper", re.IGNORECASE), "mapper"),
]

TYPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(api|endpoint|엔드포인트|rest|swagger|uri|명세|요청값|응답값)\b", re.IGNORECASE), "api_doc"),
    (re.compile(r"\b(diagram|mermaid|flowchart|erd|sequence|시퀀스|다이어그램|관계도|구조도|흐름도|시각화)\b", re.IGNORECASE), "diagram"),
    (re.compile(r"\b(xml|mybatis|mapper)\b", re.IGNORECASE), "xml_analysis"),
    (re.compile(r"\b(table|schema|db|sql|ddl|dml|테이블|스키마|쿼리)\b", re.IGNORECASE), "table_analysis"),
    (re.compile(r"\b(architecture|아키텍처|구조|flow|흐름)\b", re.IGNORECASE), "architecture"),
]


class QueryAnalyzer:
    def __init__(self, default_top_k: int = 5) -> None:
        self.default_top_k = default_top_k

    def analyze(self, question: str) -> QueryIntent:
        question = (question or "").strip()
        entity_hint = self.extract_entity(question)
        keywords = self.extract_keywords(question)
        search_query = self.build_search_query(question, entity_hint)
        layer_filter = self.detect_layer(question, entity_hint)
        extension_filter = self.detect_extension_filter(question, entity_hint, layer_filter)
        query_type = self.detect_type(question, layer_filter, extension_filter)
        top_k = self.decide_top_k(query_type, entity_hint)

        if query_type == "api_doc":
            layer_filter = "controller"

        if query_type == "xml_analysis":
            layer_filter = layer_filter or "mapper"
            extension_filter = extension_filter or "xml"

        if query_type == "table_analysis":
            extension_filter = extension_filter or "sql"

        return QueryIntent(
            query_type=query_type,
            top_k=top_k,
            layer_filter=layer_filter,
            extension_filter=extension_filter,
            entity_hint=entity_hint,
            keywords=keywords,
            search_query=search_query,
        )

    def build_search_query(self, question: str, entity_hint: str | None) -> str:
        cleaned = question.strip()
        for noise in sorted(_NOISE_KW, key=len, reverse=True):
            cleaned = re.sub(re.escape(noise), " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

        if entity_hint and entity_hint.lower() not in cleaned.lower():
            cleaned = f"{entity_hint} {cleaned}".strip()

        return cleaned or question

    def detect_type(self, question: str, layer_filter: str | None, extension_filter: str | None) -> str:
        for pattern, query_type in TYPE_PATTERNS:
            if pattern.search(question):
                return query_type

        if layer_filter:
            return "layer_search"

        if extension_filter == "xml":
            return "xml_analysis"

        if extension_filter == "sql":
            return "table_analysis"

        return "qa"

    def detect_layer(self, question: str, entity_hint: str | None) -> str | None:
        for pattern, layer in LAYER_PATTERNS:
            if pattern.search(question):
                return layer

        if entity_hint:
            lowered = entity_hint.lower()
            if lowered.endswith("controller"):
                return "controller"
            if lowered.endswith("service") or lowered.endswith("serviceimpl"):
                return "service"
            if lowered.endswith("repository") or lowered.endswith("dao"):
                return "repository"
            if lowered.endswith("mapper"):
                return "mapper"

        return None

    def detect_extension_filter(self, question: str, entity_hint: str | None, layer_filter: str | None) -> str | None:
        explicit = self.extract_extension_from_text(question)
        if explicit:
            return explicit

        if entity_hint:
            file_match = re.search(r"\.([A-Za-z0-9]+)$", entity_hint)
            if file_match:
                return file_match.group(1).lower()

            lowered = entity_hint.lower()
            if lowered.endswith("mapper"):
                return "xml"
            if lowered.endswith(("controller", "service", "serviceimpl", "repository", "dao", "entity", "dto", "vo")):
                return "java"

        if layer_filter == "mapper":
            return "xml"

        return None

    def extract_extension_from_text(self, text: str) -> str | None:
        match = re.search(r"(?<!\w)\.([A-Za-z0-9]{1,12})\b", text)
        if match:
            return match.group(1).lower()

        file_match = re.search(r"\b[A-Za-z0-9_\-./]+\.(\w{1,12})\b", text)
        if file_match:
            return file_match.group(1).lower()

        return None

    def extract_entity(self, question: str) -> str | None:
        if not question:
            return None

        patterns = [
            r"\b([A-Za-z0-9_\-./]+\.[A-Za-z0-9]{1,12})\b",
            r"\b([A-Z][A-Za-z0-9_]+(?:Controller|Service|ServiceImpl|Repository|Mapper|DTO|DAO|VO|Entity))\b",
            r"\b([A-Z][A-Za-z0-9_]{3,})\b",
            r"\b([a-z][a-z0-9]+(?:_[a-z0-9]+){1,})\b",
            r"(/[a-zA-Z0-9_\-/{}/]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, question)
            if match:
                return match.group(1)

        return None

    def extract_keywords(self, question: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z0-9_.#/\-가-힣]+", (question or "").lower())
        stop_words = {
            "설명", "해줘", "해주세요", "알려줘", "한글로", "보여줘", "찾아줘",
            "what", "is", "the", "a", "an", "please", "about", "show", "tell", "explain",
            "code", "파일", "코드", "소스", "관련", "대해", "전체", "분석",
        }

        result: list[str] = []
        seen: set[str] = set()

        for token in tokens:
            if len(token) <= 1 or token in stop_words:
                continue
            if token not in seen:
                seen.add(token)
                result.append(token)

        return result[:12]

    def decide_top_k(self, query_type: str, entity_hint: str | None) -> int:
        k = self.default_top_k
        hint_boost = 2 if entity_hint else 1

        if query_type in {"diagram", "api_doc", "architecture", "xml_analysis", "table_analysis"}:
            return max(k * hint_boost, 8)

        if query_type == "layer_search":
            return max(k * hint_boost, 6)

        return k * hint_boost