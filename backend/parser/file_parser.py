from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

import sqlglot
from lxml import etree, html
from pygments.lexers import ClassNotFound, get_lexer_for_filename, guess_lexer, guess_lexer_for_filename

logger = logging.getLogger(__name__)

# tree-sitter는 설치 환경에 따라 없을 수 있으므로 optional dependency로 처리
# - 있으면 구조 기반(class/import/method) 추출 정확도를 높임
# - 없으면 regex 기반 fallback 로직이 동작
try:
    from tree_sitter_language_pack import get_language, get_parser
except Exception:
    get_language = None
    get_parser = None


# 단순 텍스트 설정 파일 계열 확장자
# - detect_language(), detect_layer()에서 config 계열로 분류할 때 사용
TEXT_CONFIG_EXTENSIONS = {"yml", "yaml", "json", "ini", "toml", "conf", "properties", "env"}

# SQL 문서로 간주할 확장자
# - SQL 메타데이터 파싱, content_type 판별, 테이블명 추출에 사용
SQL_LIKE_EXTENSIONS = {"sql"}

# XML 문서로 간주할 확장자
# - MyBatis mapper, XML statement id 추출 등에 사용
XML_LIKE_EXTENSIONS = {"xml", "xsd", "wsdl"}

# HTML 문서 계열 확장자
# - 템플릿/폼/script 개수 추출 시 함께 사용
HTML_LIKE_EXTENSIONS = {"html", "htm", "xhtml"}

# 서버 템플릿/프론트 템플릿 계열 확장자
# - template metadata 추출 대상
TEMPLATE_EXTENSIONS = {"jsp", "jspx", "asp", "aspx", "php", "vue", "svelte", "tsx", "jsx"}


# 코드/문서 본문에서 레이어를 추정하기 위한 패턴
# - parse_text_file() -> detect_layer() 단계에서 사용
# - 후속 code_elements 저장, 구조 요약, 레이어별 통계에 활용
LAYER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"@RestController|@Controller\b|\bController\b", re.IGNORECASE), "controller"),
    (re.compile(r"@Service\b|\bServiceImpl\b|\bService\b", re.IGNORECASE), "service"),
    (re.compile(r"@Repository\b|\bRepository\b|\bDAO\b", re.IGNORECASE), "repository"),
    (re.compile(r"@Mapper\b|\bMapper\b", re.IGNORECASE), "mapper"),
]

# 파일 내용의 성격을 추정하기 위한 패턴
# - API 엔드포인트인지, DDL/DML 성격인지 등을 구분
# - 사용자의 “이 파일이 뭐 하는 파일이야?” 같은 질문 대응에 도움
CONTENT_TYPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"@GetMapping|@PostMapping|@PutMapping|@DeleteMapping|@RequestMapping", re.IGNORECASE), "api_endpoint"),
    (re.compile(r"CREATE\s+TABLE", re.IGNORECASE), "ddl_create"),
    (re.compile(r"ALTER\s+TABLE", re.IGNORECASE), "ddl_alter"),
    (re.compile(r"INSERT\s+INTO", re.IGNORECASE), "dml_insert"),
    (re.compile(r"UPDATE\s+", re.IGNORECASE), "dml_update"),
    (re.compile(r"DELETE\s+FROM", re.IGNORECASE), "dml_delete"),
]


def read_text_file(path: str) -> str:
    """
    파일을 텍스트로 읽어 반환한다.

    언제 사용되나:
    - parse_text_file()에서 파일 원문을 읽을 때 가장 먼저 호출됨

    특징:
    - 한글 프로젝트를 고려해 utf-8, cp949, euc-kr, latin-1 순으로 시도
    - 어떤 인코딩으로도 읽지 못하면 ValueError 발생
    """
    file_path = Path(path)
    for encoding in ("utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return file_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"cannot decode file: {path}")


def normalize_extension(value: str | None) -> str:
    """
    확장자 문자열을 소문자 + 점(.) 제거 형태로 정규화한다.

    예:
    - ".PY" -> "py"
    - "SQL" -> "sql"
    """
    return str(value or "").lower().lstrip(".")


def detect_mime_type(path: str) -> str:
    """
    파일 경로 기준으로 MIME 타입을 추정한다.

    사용처:
    - parse_text_file() 결과에 mime_type을 채워 후속 분류/표시용 메타데이터로 사용
    """
    mime_type, _ = mimetypes.guess_type(path)
    return mime_type or ""


def detect_language(file_name: str, text: str) -> str:
    """
    파일명과 본문을 바탕으로 언어/문서 유형을 추정한다.

    우선순위:
    1) 확장자 기반 빠른 판별
    2) pygments guess_lexer_for_filename()
    3) pygments get_lexer_for_filename()
    4) pygments guess_lexer()
    5) 그래도 실패하면 extension 또는 text 반환

    사용처:
    - parse_text_file()에서 language 필드를 채움
    - 이후 tree-sitter 파서 선택, 레이어 판별, import/method 추출에 사용
    """
    extension = normalize_extension(Path(file_name).suffix)

    if extension in SQL_LIKE_EXTENSIONS:
        return "sql"
    if extension in XML_LIKE_EXTENSIONS:
        return "xml"
    if extension in TEXT_CONFIG_EXTENSIONS:
        return extension
    if extension == "py":
        return "python"
    if extension == "js":
        return "javascript"
    if extension == "ts":
        return "typescript"
    if extension in {"c", "h"}:
        return "c"
    if extension in {"cc", "cpp", "cxx", "hpp"}:
        return "cpp"
    if extension == "cs":
        return "c_sharp"
    if extension == "java":
        return "java"
    if extension == "go":
        return "go"
    if extension == "rs":
        return "rust"
    if extension == "kt":
        return "kotlin"
    if extension == "swift":
        return "swift"
    if extension in HTML_LIKE_EXTENSIONS:
        return "html"
    if extension in TEMPLATE_EXTENSIONS:
        return extension

    try:
        lexer = guess_lexer_for_filename(file_name, text)
        alias = (lexer.aliases[0] if getattr(lexer, "aliases", None) else "").lower()
        if alias:
            return alias
    except ClassNotFound:
        pass
    except Exception:
        pass

    try:
        lexer = get_lexer_for_filename(file_name)
        alias = (lexer.aliases[0] if getattr(lexer, "aliases", None) else "").lower()
        if alias:
            return alias
    except ClassNotFound:
        pass
    except Exception:
        pass

    try:
        lexer = guess_lexer(text)
        alias = (lexer.aliases[0] if getattr(lexer, "aliases", None) else "").lower()
        if alias:
            return alias
    except ClassNotFound:
        pass
    except Exception:
        pass

    return extension or "text"


def get_tree_sitter_parser(language_name: str):
    """
    언어 이름에 맞는 tree-sitter parser를 반환한다.

    언제 사용되나:
    - tree_sitter_query_captures() 내부에서 호출
    - class/import/method/package 추출 시 구조 기반 파싱을 위해 사용

    특징:
    - language alias(py/js/ts/cs 등)를 실제 tree-sitter 이름으로 보정
    - 설치되지 않았거나 지원되지 않는 언어면 None 반환
    """
    if get_parser is None:
        return None

    alias_candidates = [
        language_name,
        language_name.replace("-", "_"),
        language_name.replace(" ", "_"),
    ]

    alias_map = {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "c#": "c_sharp",
        "cs": "c_sharp",
        "shell": "bash",
        "sh": "bash",
        "html+django": "html",
        "html+jinja": "html",
        "html+php": "php",
        "jsp": "html",
        "aspx": "html",
        "asp": "html",
        "jsx": "javascript",
        "tsx": "tsx",
    }

    for candidate in list(alias_candidates):
        mapped = alias_map.get(candidate.lower())
        if mapped:
            alias_candidates.append(mapped)

    seen = set()
    for candidate in alias_candidates:
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return get_parser(candidate)
        except Exception:
            continue

    return None


def get_tree_sitter_language(language_name: str):
    """
    언어 이름에 맞는 tree-sitter language 객체를 반환한다.

    사용처:
    - tree_sitter_query_captures()에서 query 실행 전 language 객체 확보용

    특징:
    - parser와 동일한 alias 보정 규칙을 사용
    - 지원 불가 시 None 반환
    """
    if get_language is None:
        return None

    alias_candidates = [
        language_name,
        language_name.replace("-", "_"),
        language_name.replace(" ", "_"),
    ]

    alias_map = {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "c#": "c_sharp",
        "cs": "c_sharp",
        "shell": "bash",
        "sh": "bash",
        "html+django": "html",
        "html+jinja": "html",
        "html+php": "php",
        "jsp": "html",
        "aspx": "html",
        "asp": "html",
        "jsx": "javascript",
        "tsx": "tsx",
    }

    for candidate in list(alias_candidates):
        mapped = alias_map.get(candidate.lower())
        if mapped:
            alias_candidates.append(mapped)

    seen = set()
    for candidate in alias_candidates:
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return get_language(candidate)
        except Exception:
            continue

    return None


def tree_sitter_query_captures(language_name: str, text: str, query_source: str) -> list[tuple[Any, str]]:
    """
    tree-sitter query를 실행해 capture 결과를 반환한다.

    언제 사용되나:
    - extract_class_name_tree_sitter()
    - extract_imports_tree_sitter()
    - extract_methods_tree_sitter()
    - extract_package_tree_sitter()

    반환:
    - [(node, capture_name), ...]
    - parser/language 준비 실패 또는 query 실패 시 []
    """
    parser = get_tree_sitter_parser(language_name)
    language = get_tree_sitter_language(language_name)

    if parser is None or language is None:
        return []

    try:
        tree = parser.parse(text.encode("utf-8"))
        query = language.query(query_source)
        return query.captures(tree.root_node)
    except Exception:
        return []


def node_text(node: Any, text: str) -> str:
    """
    tree-sitter node가 가리키는 원문 부분 문자열을 반환한다.
    """
    return text[node.start_byte:node.end_byte]


def detect_layer(text: str, extension: str, language: str, relative_path: str = "") -> str:
    """
    파일의 아키텍처 레이어를 추정한다.

    판별 기준:
    1) 소스 본문 내 annotation/키워드 패턴
    2) 상대경로 내 디렉터리명(controller/service/repository/mapper)
    3) 확장자/언어 기반 보정(xml -> mapper, sql -> ddl, config 계열 -> config)

    사용처:
    - parse_text_file()에서 layer_type 생성
    - 이후 DB 저장, 구조 요약, 레이어별 필터링에 활용
    """
    for pattern, layer in LAYER_PATTERNS:
        if pattern.search(text):
            return layer

    lowered_path = (relative_path or "").lower()

    if any(part in lowered_path for part in ("/controller/", "\\controller\\")):
        return "controller"
    if any(part in lowered_path for part in ("/service/", "\\service\\")):
        return "service"
    if any(part in lowered_path for part in ("/repository/", "\\repository\\", "/dao/", "\\dao\\")):
        return "repository"
    if any(part in lowered_path for part in ("/mapper/", "\\mapper\\")):
        return "mapper"

    if extension in XML_LIKE_EXTENSIONS:
        return "mapper"
    if extension in SQL_LIKE_EXTENSIONS:
        return "ddl"
    if extension in TEXT_CONFIG_EXTENSIONS:
        return "config"
    if language in {"yaml", "toml", "json", "ini"}:
        return "config"

    return ""


def detect_content_type(text: str, extension: str) -> str:
    """
    파일의 콘텐츠 성격을 추정한다.

    예:
    - api_endpoint
    - sql_select / sql_insert / sql_update / sql_delete
    - ddl_create / ddl_alter
    - dml_insert / dml_update / dml_delete

    사용처:
    - parse_text_file()에서 content_type 생성
    - 사용자가 파일 역할을 물었을 때 설명 근거로 사용 가능
    """
    if extension == "xml":
        xml_meta = extract_xml_metadata(text)
        if xml_meta["statement_ids"]:
            tags = {item["tag"] for item in xml_meta["statement_ids"]}
            if "select" in tags:
                return "sql_select"
            if "insert" in tags:
                return "sql_insert"
            if "update" in tags:
                return "sql_update"
            if "delete" in tags:
                return "sql_delete"

    if extension == "sql":
        sql_meta = extract_sql_metadata(text)
        if sql_meta["statement_type"]:
            return sql_meta["statement_type"]

    for pattern, content_type in CONTENT_TYPE_PATTERNS:
        if pattern.search(text):
            return content_type

    return ""


def extract_class_name_regex(text: str, extension: str) -> str:
    """
    regex 기반으로 대표 클래스/인터페이스명을 추출한다.

    사용처:
    - tree-sitter 추출 실패 시 fallback
    - 현재는 java, python만 지원
    """
    if extension == "java":
        match = re.search(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)", text)
        if match:
            return match.group(1)
        match = re.search(r"\binterface\s+([A-Za-z_][A-Za-z0-9_]*)", text)
        return match.group(1) if match else ""

    if extension == "py":
        match = re.search(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.MULTILINE)
        return match.group(1) if match else ""

    return ""


def extract_package_regex(text: str, extension: str) -> str:
    """
    regex 기반으로 package 선언을 추출한다.

    사용처:
    - tree-sitter package 추출 실패 시 fallback
    - 현재는 java만 지원
    """
    if extension == "java":
        match = re.search(r"^\s*package\s+([A-Za-z0-9_.]+)\s*;", text, re.MULTILINE)
        return match.group(1) if match else ""
    return ""


def extract_imports_regex(text: str, extension: str) -> list[str]:
    """
    regex 기반으로 import 목록을 추출한다.

    사용처:
    - extract_static_analysis()에서 tree-sitter 실패 시 fallback
    - 현재는 java, python만 지원
    """
    if extension == "java":
        return re.findall(r"^\s*import\s+([A-Za-z0-9_.*]+)\s*;", text, re.MULTILINE)

    if extension == "py":
        imports = re.findall(r"^\s*import\s+([A-Za-z0-9_.,\s]+)", text, re.MULTILINE)
        from_imports = re.findall(r"^\s*from\s+([A-Za-z0-9_.,\s]+)\s+import\s+([A-Za-z0-9_.*,\s]+)", text, re.MULTILINE)
        result = [item.strip() for item in imports]
        result.extend([f"{module.strip()} import {names.strip()}" for module, names in from_imports])
        return result

    return []


def extract_methods_regex(text: str, extension: str) -> list[dict[str, Any]]:
    """
    regex 기반으로 함수/메서드 목록을 추출한다.

    반환 형식:
    - [{"name": ..., "signature": ..., "params": ...}, ...]

    사용처:
    - extract_static_analysis()에서 tree-sitter 실패 시 fallback
    """
    methods: list[dict[str, Any]] = []

    if extension == "java":
        pattern = re.compile(
            r"^\s*(?:public|private|protected)?\s*(?:static\s+)?[A-Za-z0-9_<>\[\], ?]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)",
            re.MULTILINE,
        )
        for match in pattern.finditer(text):
            methods.append(
                {
                    "name": match.group(1),
                    "signature": match.group(0).strip(),
                    "params": match.group(2).strip(),
                }
            )

    elif extension == "py":
        pattern = re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)", re.MULTILINE)
        for match in pattern.finditer(text):
            methods.append(
                {
                    "name": match.group(1),
                    "signature": match.group(0).strip(),
                    "params": match.group(2).strip(),
                }
            )

    return methods


def extract_xml_metadata(text: str) -> dict[str, Any]:
    """
    XML 문서에서 구조 메타데이터를 추출한다.

    추출 대상:
    - namespace
    - statement_ids(select/insert/update/delete의 id)
    - sql_fragments(<sql id="...">)
    - root_tag

    사용처:
    - XML mapper 파일 분석
    - content_type 보정
    - code_elements 저장 시 xml_statements/xml_namespace 근거 데이터
    """
    result = {
        "namespace": "",
        "statement_ids": [],
        "sql_fragments": [],
        "root_tag": "",
    }

    try:
        parser = etree.XMLParser(recover=True, remove_comments=False)
        root = etree.fromstring(text.encode("utf-8"), parser=parser)
        result["root_tag"] = etree.QName(root.tag).localname if isinstance(root.tag, str) else ""

        if result["root_tag"] == "mapper":
            namespace = root.xpath("string(@namespace)")
            result["namespace"] = namespace or ""

        statement_nodes = root.xpath(
            "//*[local-name()='select' or local-name()='insert' or local-name()='update' or local-name()='delete']"
        )
        for node in statement_nodes:
            tag = etree.QName(node.tag).localname if isinstance(node.tag, str) else ""
            node_id = node.get("id", "")
            if tag and node_id:
                result["statement_ids"].append({"tag": tag, "id": node_id})

        sql_nodes = root.xpath("//*[local-name()='sql']")
        for node in sql_nodes:
            node_id = node.get("id", "")
            if node_id:
                result["sql_fragments"].append(node_id)

    except Exception:
        pass

    return result


def extract_template_metadata(text: str, extension: str) -> dict[str, Any]:
    """
    HTML/JSP/ASP/Vue/Svelte 등 템플릿 문서의 메타데이터를 추출한다.

    추출 대상:
    - template_directives: <%@ ... %> 같은 지시자
    - template_tags: 커스텀 태그/asp 태그
    - form_actions: form action 경로 목록
    - script_blocks: script 블록 개수

    사용처:
    - 프론트/템플릿 파일 설명
    - 화면 파일의 폼 제출 경로/스크립트 존재 여부 파악
    """
    result = {
        "template_directives": [],
        "template_tags": [],
        "form_actions": [],
        "script_blocks": 0,
    }

    try:
        if extension in {"jsp", "jspx"}:
            result["template_directives"] = re.findall(r"<%@\s*([a-zA-Z0-9_:-]+)", text)
            result["template_tags"] = re.findall(r"<([a-zA-Z]+:[a-zA-Z0-9_:-]+)\b", text)
        elif extension in {"asp", "aspx"}:
            result["template_directives"] = re.findall(r"<%@\s*([a-zA-Z0-9_:-]+)", text)
            result["template_tags"] = re.findall(r"<asp:([A-Za-z0-9_:-]+)\b", text)

        doc = html.fromstring(text)
        result["form_actions"] = [action.strip() for action in doc.xpath("//form/@action") if action and action.strip()]
        result["script_blocks"] = len(doc.xpath("//script"))
    except Exception:
        pass

    return result


def extract_sql_metadata(text: str) -> dict[str, Any]:
    """
    SQL 본문을 파싱해 statement_type과 table_names를 추출한다.

    사용처:
    - detect_content_type()에서 SQL 타입 판단
    - parse_text_file()에서 SQL 상세 메타데이터 생성
    - SQL 설명/DB 구조 질문에 대한 컨텍스트 생성

    반환 예:
    {
        "statement_type": "sql_select",
        "table_names": ["USERS", "ORDERS"]
    }
    """
    result = {
        "statement_type": "",
        "table_names": [],
    }

    seen = set()

    try:
        expressions = sqlglot.parse(text)
        for expression in expressions:
            key = expression.key.upper() if getattr(expression, "key", None) else ""
            if not result["statement_type"]:
                type_map = {
                    "SELECT": "sql_select",
                    "INSERT": "sql_insert",
                    "UPDATE": "sql_update",
                    "DELETE": "sql_delete",
                    "CREATE": "ddl_create",
                    "ALTER": "ddl_alter",
                    "DROP": "ddl_drop",
                }
                result["statement_type"] = type_map.get(key, "")

            for table in expression.find_all(sqlglot.exp.Table):
                name = table.name
                if name:
                    upper_name = name.upper()
                    if upper_name not in seen:
                        seen.add(upper_name)
                        result["table_names"].append(upper_name)
    except Exception:
        pass

    return result


def extract_class_name_tree_sitter(text: str, language: str) -> str:
    """
    tree-sitter 기반으로 대표 클래스/타입명을 추출한다.

    사용처:
    - parse_text_file()에서 class_name 생성
    - regex보다 구조적으로 정확한 추출이 가능할 때 우선 사용
    """
    query_map = {
        "python": """
            (class_definition
              name: (identifier) @class_name)
        """,
        "java": """
            (class_declaration
              name: (identifier) @class_name)
            (interface_declaration
              name: (identifier) @class_name)
            (enum_declaration
              name: (identifier) @class_name)
        """,
        "javascript": """
            (class_declaration
              name: (identifier) @class_name)
        """,
        "typescript": """
            (class_declaration
              name: (type_identifier) @class_name)
        """,
        "tsx": """
            (class_declaration
              name: (type_identifier) @class_name)
        """,
        "c_sharp": """
            (class_declaration
              name: (identifier) @class_name)
            (interface_declaration
              name: (identifier) @class_name)
        """,
        "go": """
            (type_declaration
              (type_spec
                name: (type_identifier) @class_name))
        """,
        "rust": """
            (struct_item
              name: (type_identifier) @class_name)
            (enum_item
              name: (type_identifier) @class_name)
            (trait_item
              name: (type_identifier) @class_name)
        """,
    }

    query_source = query_map.get(language)
    if not query_source:
        return ""

    for node, capture_name in tree_sitter_query_captures(language, text, query_source):
        if capture_name == "class_name":
            return node_text(node, text).strip()

    return ""


def extract_imports_tree_sitter(text: str, language: str) -> list[str]:
    """
    tree-sitter 기반으로 import/use 목록을 추출한다.

    사용처:
    - extract_static_analysis()에서 imports 생성
    - 의존성 파악, 구조 설명, 파일 간 관계 파악에 활용
    """
    query_map = {
        "python": """
            (import_statement) @import_stmt
            (import_from_statement) @import_stmt
        """,
        "java": """
            (import_declaration) @import_stmt
        """,
        "javascript": """
            (import_statement) @import_stmt
        """,
        "typescript": """
            (import_statement) @import_stmt
        """,
        "tsx": """
            (import_statement) @import_stmt
        """,
        "go": """
            (import_declaration) @import_stmt
        """,
        "rust": """
            (use_declaration) @import_stmt
        """,
    }

    query_source = query_map.get(language)
    if not query_source:
        return []

    results: list[str] = []
    seen: set[str] = set()

    for node, capture_name in tree_sitter_query_captures(language, text, query_source):
        if capture_name != "import_stmt":
            continue
        value = node_text(node, text).strip()
        if value and value not in seen:
            seen.add(value)
            results.append(value)

    return results


def extract_methods_tree_sitter(text: str, language: str) -> list[dict[str, Any]]:
    """
    tree-sitter 기반으로 함수/메서드 목록을 추출한다.

    반환 형식:
    - [{"name": ..., "signature": ..., "params": ...}, ...]

    사용처:
    - extract_static_analysis()에서 methods 생성
    - 코드 구조 설명, 메서드 목록 조회, 흐름 분석의 기초 데이터
    """
    query_map = {
        "python": """
            (function_definition
              name: (identifier) @method_name
              parameters: (parameters) @method_params) @method_def
        """,
        "java": """
            (method_declaration
              name: (identifier) @method_name
              parameters: (formal_parameters) @method_params) @method_def
            (constructor_declaration
              name: (identifier) @method_name
              parameters: (formal_parameters) @method_params) @method_def
        """,
        "javascript": """
            (function_declaration
              name: (identifier) @method_name
              parameters: (formal_parameters) @method_params) @method_def
            (method_definition
              name: (property_identifier) @method_name
              parameters: (formal_parameters) @method_params) @method_def
        """,
        "typescript": """
            (function_declaration
              name: (identifier) @method_name
              parameters: (formal_parameters) @method_params) @method_def
            (method_signature
              name: (property_identifier) @method_name
              parameters: (formal_parameters) @method_params) @method_def
            (method_definition
              name: (property_identifier) @method_name
              parameters: (formal_parameters) @method_params) @method_def
        """,
        "tsx": """
            (function_declaration
              name: (identifier) @method_name
              parameters: (formal_parameters) @method_params) @method_def
            (method_definition
              name: (property_identifier) @method_name
              parameters: (formal_parameters) @method_params) @method_def
        """,
        "go": """
            (function_declaration
              name: (identifier) @method_name
              parameters: (parameter_list) @method_params) @method_def
            (method_declaration
              name: (field_identifier) @method_name
              parameters: (parameter_list) @method_params) @method_def
        """,
        "rust": """
            (function_item
              name: (identifier) @method_name
              parameters: (parameters) @method_params) @method_def
        """,
        "c": """
            (function_definition
              declarator: (function_declarator
                declarator: (identifier) @method_name
                parameters: (parameter_list) @method_params)) @method_def
        """,
        "cpp": """
            (function_definition
              declarator: (function_declarator
                declarator: (_) @method_name
                parameters: (parameter_list) @method_params)) @method_def
        """,
        "c_sharp": """
            (method_declaration
              name: (identifier) @method_name
              parameters: (parameter_list) @method_params) @method_def
            (constructor_declaration
              name: (identifier) @method_name
              parameters: (parameter_list) @method_params) @method_def
        """,
    }

    query_source = query_map.get(language)
    if not query_source:
        return []

    methods: list[dict[str, Any]] = []
    current: dict[str, str] = {}

    for node, capture_name in tree_sitter_query_captures(language, text, query_source):
        value = node_text(node, text).strip()

        if capture_name == "method_def":
            if current.get("name"):
                methods.append(
                    {
                        "name": current.get("name", ""),
                        "signature": current.get("signature", ""),
                        "params": current.get("params", ""),
                    }
                )
            current = {"signature": value}
        elif capture_name == "method_name":
            current["name"] = value
        elif capture_name == "method_params":
            current["params"] = value

    if current.get("name"):
        methods.append(
            {
                "name": current.get("name", ""),
                "signature": current.get("signature", ""),
                "params": current.get("params", ""),
            }
        )

    return methods


def extract_package_tree_sitter(text: str, language: str) -> str:
    """
    tree-sitter 기반으로 package 선언을 추출한다.

    사용처:
    - parse_text_file()에서 package 필드 생성
    - 현재는 java만 지원
    """
    if language != "java":
        return ""

    query_source = """
        (package_declaration
          (scoped_identifier) @package_name)
        (package_declaration
          (identifier) @package_name)
    """

    for node, capture_name in tree_sitter_query_captures(language, text, query_source):
        if capture_name == "package_name":
            return node_text(node, text).strip()

    return ""


def extract_table_names_regex(text: str) -> list[str]:
    """
    regex 기반으로 SQL/본문에서 테이블명을 추출한다.

    사용처:
    - extract_static_analysis()에서 sqlglot 파싱 실패 시 fallback
    - 빠른 테이블명 힌트 수집 용도

    주의:
    - 정규식 기반이라 완벽하지 않으므로 sqlglot 결과가 있으면 그 값을 우선 사용
    """
    found: list[str] = []
    upper_text = text.upper()

    patterns = [
        r"\bFROM\s+([A-Z_][A-Z0-9_]*)",
        r"\bJOIN\s+([A-Z_][A-Z0-9_]*)",
        r"\bUPDATE\s+([A-Z_][A-Z0-9_]*)",
        r"\bINTO\s+([A-Z_][A-Z0-9_]*)",
        r"\bTABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Z_][A-Z0-9_]*)",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, upper_text, re.IGNORECASE):
            found.append(match.group(1))

    deduped: list[str] = []
    seen = set()
    for name in found:
        if name not in seen:
            seen.add(name)
            deduped.append(name)

    return deduped


def parse_text_file(file_info: dict[str, Any]) -> dict[str, Any]:
    """
    파일 1건에 대한 기본 파싱을 수행한다.

    언제 사용되나:
    - 업로드/압축해제 후 인덱싱 파이프라인에서 각 파일을 순회할 때 1차로 호출

    주요 역할:
    1) 파일 원문을 읽음
    2) 파일명/확장자/MIME/언어를 판별함
    3) layer_type / content_type / class_name / package를 추출함
    4) XML/SQL/템플릿 메타데이터를 기본 수준으로 수집함

    반환:
    - 후속 청킹, 정적 분석, DB 저장에 쓸 기본 메타데이터 dict
    - 필수 키 누락, 읽기 실패, 빈 파일이면 {}
    """
    try:
        # 여러 업로드 경로 키명을 허용해 saved_path를 최대한 유연하게 찾음
        saved_path = (
                file_info.get("saved_path")
                or file_info.get("savedpath")
                or file_info.get("file_path")
                or file_info.get("filepath")
        )
        if not saved_path:
            raise KeyError("saved_path")

        # 파일 원문 전체 텍스트
        raw_text = read_text_file(saved_path)
        if not raw_text.strip():
            return {}

        # 원본 파일명 우선, 없으면 저장된 경로의 파일명 사용
        file_name = file_info.get(
            "file_name",
            file_info.get("filename", file_info.get("original_name", file_info.get("originalname", Path(saved_path).name))),
        )

        # 프로젝트 루트 기준 상대경로
        relative_path = file_info.get("relative_path", file_info.get("relativepath", ""))

        # 확장자 정규화
        extension = normalize_extension(file_info.get("extension") or Path(file_name).suffix)

        # MIME 타입 추정
        mime_type = detect_mime_type(saved_path)

        # 프로그래밍 언어 또는 문서 유형 추정   (USE pygments)
        language = detect_language(file_name, raw_text)

        # controller/service/repository/mapper/config/ddl 등 레이어 분류
        layer_type = detect_layer(raw_text, extension, language, relative_path)

        # api_endpoint / sql_select / ddl_create 등 콘텐츠 성격 분류 (USE XML파일일경우:lxml, SQL파일일경우: sqlglot)
        # SQL,XML 일경우 우선수위 SELECT -> INSERT -> UPDATE -> DELETE
        content_type = detect_content_type(raw_text, extension)

        # 대표 클래스/타입명 추출
        # - tree-sitter 우선
        # - 실패 시 regex fallback
        # (USE tree-sitter)
        class_name = extract_class_name_tree_sitter(raw_text, language) or extract_class_name_regex(raw_text, extension)

        # 대표 package/namespace 추출 (USE tree-sitter)
        package = extract_package_tree_sitter(raw_text, language) or extract_package_regex(raw_text, extension)

        # 파일 종류별 상세 메타데이터
        # xml_meta = {
        #     "namespace": "com.example.user.UserMapper",
        #     "statement_ids": [
        #         { "tag": "select", "id": "selectUser" },
        #         { "tag": "insert", "id": "insertUser" },
        #         { "tag": "update", "id": "updateUser" },
        #         { "tag": "delete", "id": "deleteUser" }
        #     ],
        #     "sql_fragments": [ "Base_Column_List", "User_Where"],
        #     "root_tag": "mapper"
        # }
        xml_meta = extract_xml_metadata(raw_text) if extension == "xml" else {} #(USE lxml)
        sql_meta = extract_sql_metadata(raw_text) if extension == "sql" else {} #(USE sqlglot)
        template_meta = (
            extract_template_metadata(raw_text, extension)
            if extension in TEMPLATE_EXTENSIONS | HTML_LIKE_EXTENSIONS
            else {}
        )

        # mapper XML의 경우 namespace만 있고 class_name이 없으면 namespace 마지막 토큰을 대표명으로 사용
        if extension == "xml" and xml_meta.get("namespace") and not class_name:
            class_name = str(xml_meta["namespace"]).split(".")[-1]

        # SQL 파일은 sqlglot에서 얻은 statement_type을 content_type으로 보정
        if extension == "sql" and sql_meta.get("statement_type") and not content_type:
            content_type = sql_meta["statement_type"]

        return {
            "raw_text": raw_text,  # 파일 원문 전체 텍스트, 추가 분석의 기준 데이터
            "project_id": file_info.get("project_id", file_info.get("projectid", "")),  # 프로젝트 식별자
            "project_name": file_info.get("project_name", file_info.get("projectname", "")),  # 프로젝트명
            "file_name": file_name,  # 파일명, UI/로그/언어 감지에 사용
            "extension": extension,  # 정규화된 확장자
            "language": language,  # 감지된 프로그래밍 언어/문서 종류
            "mime_type": mime_type,  # MIME 타입
            "relative_path": relative_path,  # 프로젝트 내 상대경로, 중복 식별/정렬/검색용
            "saved_path": saved_path,  # 서버 내 실제 저장 경로
            "file_path": saved_path,  # 타 모듈 호환용 파일 경로 별칭
            "file_size": file_info.get("file_size", file_info.get("size", 0)),  # 파일 크기(byte)
            "source_type": file_info.get("source_type", file_info.get("sourcetype", "")),  # 업로드 출처 유형(stream/file/local/url/file-legacy)
            "root_container_name": file_info.get("root_container_name", file_info.get("rootcontainername", "")),  # zip 루트 폴더명 등 상위 컨테이너 정보
            "layer_type": layer_type,  # 추정 레이어(controller/service/repository/mapper/config/ddl)
            "content_type": content_type,  # 추정 콘텐츠 역할(api/sql ddl/dml 등)
            "class_name": class_name,  # 대표 클래스/타입명
            "package": package,  # 패키지명/네임스페이스
            "xml_namespace": xml_meta.get("namespace", ""),  # XML mapper namespace
            "xml_sql_fragments": xml_meta.get("sql_fragments", []),  # XML <sql id="..."> fragment 목록
            "xml_statements": xml_meta.get("statement_ids", []), # XML select/insert/update/delete id 목록
            "template_meta": template_meta,  # 템플릿 파일 관련 메타데이터
            "sql_meta": sql_meta,  # SQL statement type / table names 등 상세 SQL 메타데이터
        }

    except KeyError as error:
        logger.error("parse_text_file required key missing - %s file_info=%s", error, file_info)
        return {}
    except Exception as error:
        logger.error("parse_text_file failed - %s path=%s", error, file_info.get("saved_path", file_info.get("savedpath", "")))
        return {}


def extract_static_analysis(parsed: dict[str, Any]) -> dict[str, Any]:
    """
    parse_text_file() 결과 1건에 대해 정적 분석 메타데이터를 확장 추출한다.

    언제 사용되나:
    - RAGService.index_files()에서 parse_text_file() 이후 호출됨
    - SQLite code_elements 저장용 구조화 데이터를 만들 때 사용됨
    - 사용자의 구조 질의, 레이어 질의, SQL/XML 관련 질의에 쓸 근거 데이터 생성 단계

    주요 역할:
    1) import 구문 추출
    2) 함수/메서드 시그니처 추출
    3) parse_text_file()에서 계산한 XML/SQL/템플릿 메타데이터 재사용
    4) SQL 기준 테이블명 추출
    5) 후속 DB 저장용 최종 분석 dict 생성

    특징:
    - 파일을 다시 읽거나 parse_text_file()를 재호출하지 않음
    - 1차 파싱 결과(parsed)를 재사용해 중복 계산을 줄임

    반환:
    - code_elements 저장 및 구조 분석용 정적 분석 결과 dict
    - 입력 parsed가 비어 있으면 {}
    """
    if not parsed:
        return {}

    # 1차 파싱 결과 재사용
    raw_text = parsed["raw_text"]
    extension = parsed["extension"]
    language = parsed.get("language", "")

    # import는 tree-sitter 우선 추출, 실패하면 regex fallback
    imports = extract_imports_tree_sitter(raw_text, language) #(USE tree-sitter)
    if not imports:
        imports = extract_imports_regex(raw_text, extension)

    # 메서드/함수는 tree-sitter 우선 추출, 실패하면 regex fallback
    methods = extract_methods_tree_sitter(raw_text, language) #(USE tree-sitter)
    if not methods:
        methods = extract_methods_regex(raw_text, extension)

    # parse_text_file()에서 이미 계산한 메타데이터 재사용
    sql_meta = parsed.get("sql_meta", {})
    template_meta = parsed.get("template_meta", {})

    # SQL 파서 기반 테이블명 우선, 없으면 regex fallback
    table_names = sql_meta.get("table_names", [])
    if not table_names:
        table_names = extract_table_names_regex(raw_text)

    return {
        "raw_text": raw_text,                                       # 원문 전체 텍스트, preview/hash/추가 분석용 원본
        "project_id": parsed.get("project_id", ""),                 # 프로젝트 식별자
        "project_name": parsed.get("project_name", ""),             # 프로젝트명
        "file_name": parsed.get("file_name", ""),                   # 파일명
        "extension": extension,                                     # 정규화된 확장자
        "language": language,                                       # 감지 언어/문서 유형
        "mime_type": parsed.get("mime_type", ""),                   # MIME 타입
        "relative_path": parsed.get("relative_path", ""),           # 프로젝트 내 상대경로
        "saved_path": parsed.get("saved_path", ""),                 # 실제 저장 경로
        "layer_type": parsed.get("layer_type", ""),                 # 추정 레이어 타입
        "content_type": parsed.get("content_type", ""),             # 추정 콘텐츠 타입
        "class_name": parsed.get("class_name", ""),                 # 대표 클래스/타입명
        "package": parsed.get("package", ""),                       # 패키지/네임스페이스
        "xml_namespace": parsed.get("xml_namespace", ""),           # XML namespace
        "xml_sql_fragments": parsed.get("xml_sql_fragments", []),   # XML sql fragment id 목록
        "xml_statements": parsed.get("xml_statements", []),         # XML select/insert/update/delete statement id 목록
        "template_meta": template_meta,                             # JSP/HTML/Vue 등 템플릿 메타데이터
        "table_names": table_names,                                 # (ADD) SQL/본문 기준 추출한 테이블명 목록
        "imports": imports,                                         # (ADD) import/use/include 목록
        "methods": methods,                                         # (ADD) 함수/메서드 목록(name, signature, params)
    }