from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)
@dataclass
class QueryIntent:
    """
    사용자의 자연어 질문을 분석한 결과를 담는 구조체.

    - query_type        : 질의 유형(qa, diagram, api_doc, xml_analysis, table_analysis, architecture, layer_search 등)
    - top_k             : 벡터 검색에서 가져올 청크 수
    - layer_filter      : controller/service/repository/mapper 등 계층 필터
    - extension_filter  : 확장자 필터 (예: 'java', 'xml', 'sql')
    - entity_hint       : 질문에서 추출한 대표 엔티티/파일/클래스/URI 힌트
    - keywords          : 검색에 사용할 핵심 키워드 리스트
    - search_query      : 불용어 제거 후 만든 검색용 질의 문자열
    """
    query_type: str = "qa"
    top_k: int = 5
    layer_filter: str | None = None
    extension_filter: str | None = None
    entity_hint: str | None = None
    keywords: list[str] = field(default_factory=list)
    search_query: str = ""


# 검색 질의에서 제거하고 싶은 “말끝/완곡 표현/설명 요구” 같은 노이즈 키워드들
_NOISE_KW = (
    "설명해줘", "설명해", "설명 해줘", "알려줘", "알려", "보여줘", "보여",
    "찾아줘", "찾아", "뭐야", "뭔가요", "뭔지", "어떻게", "어떤", "무엇",
    "전체적인", "전체", "대해서", "대해", "관련해서", "관련된", "관련",
    "해줘", "해주세요", "주세요", "주시겠어요", "해", "줘",
    "에 대해", "에 대한", "이란", "이란게", "이란걸", "란", "란게",
    "please", "show", "tell", "explain", "about", "what", "is", "the", "a", "an",
)

# 질문 내용에서 특정 레이어(Controller/Service/Repository/Mapper)를 암시하는 패턴들
LAYER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 컨트롤러 계층
    (re.compile(r"\bcontroller\b|컨트롤러|@restcontroller|@controller", re.IGNORECASE), "controller"),
    # 서비스 계층
    (re.compile(r"\bservice\b|서비스|@service", re.IGNORECASE), "service"),
    # 레포지토리/DAO 계층
    (re.compile(r"\brepository\b|\brepo\b|\bdao\b|레포지토리|@repository", re.IGNORECASE), "repository"),
    # 마이바티스 Mapper 계층
    (re.compile(r"\bmapper\b|마이바티스|mybatis|@mapper", re.IGNORECASE), "mapper"),
]

# 질문 내용에서 질의 유형을 판단하기 위한 패턴들
TYPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # API 명세/엔드포인트/요청/응답 관련
    (re.compile(r"\b(api|endpoint|엔드포인트|rest|swagger|uri|명세|요청값|응답값)\b", re.IGNORECASE), "api_doc"),
    # Mermaid / 다이어그램 / ERD / 플로우차트 등 시각화 관련
    (re.compile(r"\b(diagram|mermaid|flowchart|erd|sequence|시퀀스|다이어그램|관계도|구조도|흐름도|시각화)\b", re.IGNORECASE), "diagram"),
    # XML / MyBatis Mapper 관련
    (re.compile(r"\b(xml|mybatis|mapper)\b", re.IGNORECASE), "xml_analysis"),
    # DB 테이블/스키마/SQL 관련
    (re.compile(r"\b(table|schema|db|sql|ddl|dml|테이블|스키마|쿼리)\b", re.IGNORECASE), "table_analysis"),
    # 전체 아키텍처/구조/흐름 관련
    (re.compile(r"\b(architecture|아키텍처|구조|flow|흐름)\b", re.IGNORECASE), "architecture"),
]


class QueryAnalyzer:
    """
    자연어 질문을 분석해서 RAG 파이프라인에 사용할 QueryIntent를 만들어 주는 분석기.

    주요 역할:
    - 질의 유형 판단 (qa, diagram, api_doc, xml_analysis, table_analysis, architecture 등)
    - 레이어 필터(controller/service/...) 추출
    - 확장자 필터(xml/sql/java 등) 추출
    - 대표 엔티티(파일명, 클래스명, URI 등) 추출
    - 검색용 키워드 및 search_query 생성
    - top_k 결정 (질의 유형/엔티티 여부에 따라 가변)
    """

    def __init__(self, default_top_k: int = 5) -> None:
        # 기본 top_k 값 (설정에서 넘겨줄 수 있도록 파라미터화)
        self.default_top_k = default_top_k

    def analyze(self, question: str) -> QueryIntent:
        """
        메인 진입점.
        사용자의 질문 문자열을 받아서 QueryIntent를 생성한다.
        """
        question = (question or "").strip()

        # 질문에서 대표 엔티티(파일/클래스/URI 등) 추출
        entity_hint = self.extract_entity(question)
        logger.info("entity_hint : %s", entity_hint)

        # 질문에서 검색용 주요 키워드 추출
        keywords = self.extract_keywords(question)
        for kw in keywords:
            logger.info("keyword item: %s", kw)

        # 불용어 제거 후 검색용 질의 문자열 생성
        search_query = self.build_search_query(question, entity_hint)
        logger.info("search_query : %s", search_query)
        
        # 컨트롤러/서비스/레포지토리/매퍼 계층 추론
        layer_filter = self.detect_layer(question, entity_hint)
        logger.info("layer_filter : %s", layer_filter)

        # 확장자 필터(xml/sql/java 등) 추론
        extension_filter = self.detect_extension_filter(question, entity_hint, layer_filter)
        logger.info("extension_filter : %s", extension_filter)

        # 질의 유형(qa, diagram, api_doc, xml_analysis, table_analysis, architecture 등) 결정
        query_type = self.detect_type(question, layer_filter, extension_filter)
        logger.info("query_type : %s", query_type)

        # 질의 유형과 엔티티 힌트에 따라 top_k 결정
        top_k = self.decide_top_k(query_type, entity_hint)
        logger.info("top_k : %s", top_k)

        # API 문서 분석은 기본적으로 controller 레이어에 포커스
        if query_type == "api_doc":
            layer_filter = "controller"

        # XML 분석은 mapper 레이어 + xml 확장자에 맞춰 필터링
        if query_type == "xml_analysis":
            layer_filter = layer_filter or "mapper"
            extension_filter = extension_filter or "xml"

        # 테이블/스키마 분석은 sql 확장자를 기본 필터로 사용
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
        """
        검색용 질의 문자열 생성.

        - _NOISE_KW에 정의된 불필요한 표현(“설명해줘”, “알려줘” 등)을 제거
        - 엔티티 힌트가 있으면 앞에 붙여서 검색 정확도 향상
        """
        cleaned = question.strip()

        # 길이가 긴 노이즈부터 제거 (중복 패턴 방지)
        for noise in sorted(_NOISE_KW, key=len, reverse=True):
            cleaned = re.sub(re.escape(noise), " ", cleaned, flags=re.IGNORECASE)

        # 다중 공백 정리
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

        # 엔티티 힌트가 검색 질의에 포함되어 있지 않으면 앞에 붙여줌
        if entity_hint and entity_hint.lower() not in cleaned.lower():
            cleaned = f"{entity_hint} {cleaned}".strip()

        # 모든 처리를 했는데도 빈 문자열이면 원 질문을 그대로 반환
        return cleaned or question

    def detect_type(self, question: str, layer_filter: str | None, extension_filter: str | None) -> str:
        """
        질의 유형 결정.

        우선 TYPE_PATTERNS에서 키워드 기반으로 타입을 찾고,
        없으면 layer_filter/extension_filter를 활용해 보조적으로 결정,
        최종 기본값은 'qa'.
        """
        # 1) 키워드 기반 우선 판단
        for pattern, query_type in TYPE_PATTERNS:
            if pattern.search(question):
                return query_type

        # 2) 레이어 필터가 있으면 레이어 중심 검색(layer_search)로 분류
        if layer_filter:
            return "layer_search"

        # 3) 확장자 기반 보조 판단
        if extension_filter == "xml":
            return "xml_analysis"

        if extension_filter == "sql":
            return "table_analysis"

        # 4) 기본값: 일반 Q&A
        return "qa"

    def detect_layer(self, question: str, entity_hint: str | None) -> str | None:
        """
        컨트롤러/서비스/레포지토리/매퍼 등의 레이어 필터를 추론.

        - 질문 내용에 레이어 관련 키워드가 있으면 우선 사용
        - 없으면 엔티티 힌트(클래스명/파일명)의 suffix로 레이어 추론
        """
        # 1) 질문 텍스트에서 레이어 키워드 직접 검색
        for pattern, layer in LAYER_PATTERNS:
            if pattern.search(question):
                return layer

        # 2) 엔티티 힌트 기반 레이어 추론
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
        """
        확장자 필터를 결정.

        우선 질문 텍스트에 명시된 확장자를 찾고,
        없으면 엔티티 힌트(파일명, 클래스명 등)에서 추론,
        그래도 없으면 레이어 필터를 기반으로 기본값을 정한다.
        """
        # 1) 질문 텍스트에서 명시적 확장자 추출
        explicit = self.extract_extension_from_text(question)
        if explicit:
            return explicit

        # 2) 엔티티 힌트 기반 추론 (파일명/클래스명 suffix 등)
        if entity_hint:
            # foo/bar/Example.java 같은 형식에서 확장자 추출
            file_match = re.search(r"\.([A-Za-z0-9]+)$", entity_hint)
            if file_match:
                return file_match.group(1).lower()

            lowered = entity_hint.lower()
            # Mapper 같은 이름이면 xml로 가정
            if lowered.endswith("mapper"):
                return "xml"
            # Controller/Service/Repository/DAO/Entity/DTO/VO 등은 주로 Java로 가정
            if lowered.endswith(("controller", "service", "serviceimpl", "repository", "dao", "entity", "dto", "vo")):
                return "java"

        # 3) 레이어 필터 기반 보조 추론
        if layer_filter == "mapper":
            return "xml"

        return None

    def extract_extension_from_text(self, text: str) -> str | None:
        """
        텍스트에서 명시된 파일 확장자를 추출.

        예:
        - ".java", ".xml" 같은 단독 확장자 언급
        - "ExampleController.java", "mapper.xml" 같은 파일명 언급
        """
        # .java, .xml 등 확장자만 단독으로 언급된 경우
        match = re.search(r"(?<!\w)\.([A-Za-z0-9]{1,12})\b", text)
        if match:
            return match.group(1).lower()

        # 경로/파일명 전체가 언급된 경우에서 확장자만 추출
        file_match = re.search(r"\b[A-Za-z0-9_\-./]+\.(\w{1,12})\b", text)
        if file_match:
            return file_match.group(1).lower()

        return None

    def extract_entity(self, question: str) -> str | None:
        """
        질문에서 대표 엔티티(파일명, 클래스명, URI 등)를 추출.

        우선순위:
        1) "Foo.java", "bar.xml" 같은 파일명/경로
        2) "UserController", "OrderServiceImpl" 등 특정 suffix를 가진 클래스명
        3) 일반적인 대문자 시작 클래스명
        4) snake_case 식의 식별자
        5) "/api/users/{id}" 같은 URI
        """
        if not question:
            return None

        patterns = [
            # 파일/경로 + 확장자 (예: com/example/UserController.java)
            r"\b([A-Za-z0-9_\-./]+\.[A-Za-z0-9]{1,12})\b",
            # 특정 suffix를 가진 클래스명 (Controller, Service, Repository, Mapper, DTO 등)
            r"\b([A-Z][A-Za-z0-9_]+(?:Controller|Service|ServiceImpl|Repository|Mapper|DTO|DAO|VO|Entity))\b",
            # 대문자 시작 일반 클래스명 (길이 3 이상)
            r"\b([A-Z][A-Za-z0-9_]{3,})\b",
            # snake_case / 소문자 식별자 (예: user_service_impl)
            r"\b([a-z][a-z0-9]+(?:_[a-z0-9]+){1,})\b",
            # REST URI 등 (예: /api/users/{id})
            r"(/[a-zA-Z0-9_\-/{}/]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, question)
            if match:
                return match.group(1)

        return None

    def extract_keywords(self, question: str) -> list[str]:
        """
        검색에 사용할 핵심 키워드 추출.

        - 한글/영문/숫자/특수문자 일부(.,#,/,-,_)를 포함하는 토큰을 모두 뽑고
        - stop_words에 정의된 불필요한 토큰을 제거
        - 중복 제거 후 최대 12개까지만 반환
        """
        tokens = re.findall(r"[A-Za-z0-9_.#/\-가-힣]+", (question or "").lower())

        # 검색에 크게 도움이 되지 않는 일반적인 단어들
        stop_words = {
            "설명", "해줘", "해주세요", "알려줘", "한글로", "보여줘", "찾아줘",
            "what", "is", "the", "a", "an", "please", "about", "show", "tell", "explain",
            "code", "파일", "코드", "소스", "관련", "대해", "전체", "분석",
        }

        result: list[str] = []
        seen: set[str] = set()

        for token in tokens:
            # 한 글자짜리 토큰이나 stop word는 제외
            if len(token) <= 1 or token in stop_words:
                continue
            # 중복 토큰 제거
            if token not in seen:
                seen.add(token)
                result.append(token)

        # 최대 12개까지만 사용
        return result[:12]

    def decide_top_k(self, query_type: str, entity_hint: str | None) -> int:
        """
        top_k 결정 로직.

        - 기본값은 self.default_top_k
        - 엔티티 힌트가 있으면 boost(2배) 적용
        - diagram, api_doc, architecture, xml_analysis, table_analysis는
          좀 더 넓게 문맥을 보고 싶어 해서 최소 8 이상으로 상향
        - 레이어 검색(layer_search)는 적당히 6 이상으로 상향
        """
        k = self.default_top_k
        hint_boost = 2 if entity_hint else 1

        # 다이어그램/아키텍처/테이블/XML/API 분석은 문맥을 더 넓게 보는 편이 좋아서 top_k 상향
        if query_type in {"diagram", "api_doc", "architecture", "xml_analysis", "table_analysis"}:
            return max(k * hint_boost, 8)

        # 레이어 검색은 충분한 샘플을 확보하기 위해 중간 정도 상향
        if query_type == "layer_search":
            return max(k * hint_boost, 6)

        # 일반 Q&A는 기본값 * boost
        return k * hint_boost