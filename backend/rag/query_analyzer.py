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


class QueryAnalyzer:
    def __init__(self, default_top_k: int = 5) -> None:
        self.default_top_k = default_top_k

    def analyze(self, question: str) -> QueryIntent:
        q = (question or "").strip().lower()
        entity_hint = self.extract_entity(question)
        keywords = self.extract_keywords(question)

        if any(token in q for token in ["mermaid", "diagram", "flowchart", "erd", "다이어그램"]):
            return QueryIntent(query_type="diagram", top_k=max(self.default_top_k, 8), entity_hint=entity_hint, keywords=keywords)

        if any(token in q for token in ["api", "endpoint", "controller", "엔드포인트"]):
            return QueryIntent(query_type="api_doc", top_k=max(self.default_top_k, 8), layer_filter="controller", entity_hint=entity_hint, keywords=keywords)

        if any(token in q for token in ["xml", "mapper", "mybatis"]):
            return QueryIntent(query_type="xml_analysis", top_k=max(self.default_top_k, 8), layer_filter="mapper", extension_filter="xml", entity_hint=entity_hint, keywords=keywords)

        if any(token in q for token in ["table", "schema", "db", "sql", "테이블", "스키마"]):
            return QueryIntent(query_type="table_analysis", top_k=max(self.default_top_k, 8), extension_filter="sql", entity_hint=entity_hint, keywords=keywords)

        if any(token in q for token in ["architecture", "구조", "아키텍처", "flow", "흐름"]):
            return QueryIntent(query_type="architecture", top_k=max(self.default_top_k, 8), entity_hint=entity_hint, keywords=keywords)

        if "controller" in q:
            return QueryIntent(query_type="layer_search", top_k=max(self.default_top_k, 6), layer_filter="controller", entity_hint=entity_hint, keywords=keywords)

        if "service" in q:
            return QueryIntent(query_type="layer_search", top_k=max(self.default_top_k, 6), layer_filter="service", entity_hint=entity_hint, keywords=keywords)

        if any(token in q for token in ["repository", "dao"]):
            return QueryIntent(query_type="layer_search", top_k=max(self.default_top_k, 6), layer_filter="repository", entity_hint=entity_hint, keywords=keywords)

        if "mapper" in q:
            return QueryIntent(query_type="layer_search", top_k=max(self.default_top_k, 6), layer_filter="mapper", entity_hint=entity_hint, keywords=keywords)

        extension_filter = self.detect_extension_filter(q)
        return QueryIntent(query_type="qa", top_k=self.default_top_k, extension_filter=extension_filter, entity_hint=entity_hint, keywords=keywords)

    def detect_extension_filter(self, q: str) -> str | None:
        extension_map = {
            "java": ["java", ".java"],
            "xml": ["xml", ".xml", "mapper"],
            "sql": ["sql", ".sql", "query", "table"],
            "py": ["python", ".py"],
            "md": ["markdown", "readme", ".md"],
        }
        for extension, keywords in extension_map.items():
            if any(keyword in q for keyword in keywords):
                return extension
        return None

    def extract_entity(self, question: str) -> str | None:
        if not question:
            return None

        file_match = re.search(r"([A-Za-z0-9_\-./]+\.(java|xml|sql|py|md|js|ts|json|yml|yaml))", question, re.IGNORECASE)
        if file_match:
            return file_match.group(1)

        class_match = re.search(r"\b([A-Z][A-Za-z0-9_]+)\b", question)
        if class_match:
            return class_match.group(1)

        return None

    def extract_keywords(self, question: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z0-9_.#\-가-힣]+", (question or "").lower())
        stop_words = {
            "설명", "해줘", "해주세요", "알려줘", "한글로",
            "what", "is", "the", "a", "an", "please", "about",
            "code", "파일", "코드",
        }

        result = []
        seen = set()

        for token in tokens:
            if len(token) <= 1 or token in stop_words:
                continue
            if token not in seen:
                seen.add(token)
                result.append(token)

        return result[:12]