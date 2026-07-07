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

try:
    from tree_sitter_language_pack import get_language, get_parser
except Exception:
    get_language = None
    get_parser = None


TEXT_CONFIG_EXTENSIONS = {"yml", "yaml", "json", "ini", "toml", "conf", "properties", "env"}
SQL_LIKE_EXTENSIONS = {"sql"}
XML_LIKE_EXTENSIONS = {"xml", "xsd", "wsdl"}
HTML_LIKE_EXTENSIONS = {"html", "htm", "xhtml"}
TEMPLATE_EXTENSIONS = {"jsp", "jspx", "asp", "aspx", "php", "vue", "svelte", "tsx", "jsx"}

LAYER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"@RestController|@Controller\b|\bController\b", re.IGNORECASE), "controller"),
    (re.compile(r"@Service\b|\bServiceImpl\b|\bService\b", re.IGNORECASE), "service"),
    (re.compile(r"@Repository\b|\bRepository\b|\bDAO\b", re.IGNORECASE), "repository"),
    (re.compile(r"@Mapper\b|\bMapper\b", re.IGNORECASE), "mapper"),
]

CONTENT_TYPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"@GetMapping|@PostMapping|@PutMapping|@DeleteMapping|@RequestMapping", re.IGNORECASE), "api_endpoint"),
    (re.compile(r"CREATE\s+TABLE", re.IGNORECASE), "ddl_create"),
    (re.compile(r"ALTER\s+TABLE", re.IGNORECASE), "ddl_alter"),
    (re.compile(r"INSERT\s+INTO", re.IGNORECASE), "dml_insert"),
    (re.compile(r"UPDATE\s+", re.IGNORECASE), "dml_update"),
    (re.compile(r"DELETE\s+FROM", re.IGNORECASE), "dml_delete"),
]


def read_text_file(path: str) -> str:
    file_path = Path(path)
    for encoding in ("utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return file_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"cannot decode file: {path}")


def normalize_extension(value: str | None) -> str:
    return str(value or "").lower().lstrip(".")


def detect_mime_type(path: str) -> str:
    mime_type, _ = mimetypes.guess_type(path)
    return mime_type or ""


def detect_language(file_name: str, text: str) -> str:
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
    return text[node.start_byte:node.end_byte]


def detect_layer(text: str, extension: str, language: str, relative_path: str = "") -> str:
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
    if extension == "java":
        match = re.search(r"^\s*package\s+([A-Za-z0-9_.]+)\s*;", text, re.MULTILINE)
        return match.group(1) if match else ""
    return ""


def extract_imports_regex(text: str, extension: str) -> list[str]:
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
    try:
        saved_path = (
                file_info.get("saved_path")
                or file_info.get("savedpath")
                or file_info.get("file_path")
                or file_info.get("filepath")
        )
        if not saved_path:
            raise KeyError("saved_path")

        raw_text = read_text_file(saved_path)
        if not raw_text.strip():
            return {}

        file_name = file_info.get(
            "file_name",
            file_info.get("filename", file_info.get("original_name", file_info.get("originalname", Path(saved_path).name))),
        )
        relative_path = file_info.get("relative_path", file_info.get("relativepath", ""))
        extension = normalize_extension(file_info.get("extension") or Path(file_name).suffix)
        mime_type = detect_mime_type(saved_path)
        language = detect_language(file_name, raw_text)
        layer_type = detect_layer(raw_text, extension, language, relative_path)
        content_type = detect_content_type(raw_text, extension)

        class_name = extract_class_name_tree_sitter(raw_text, language) or extract_class_name_regex(raw_text, extension)
        package = extract_package_tree_sitter(raw_text, language) or extract_package_regex(raw_text, extension)

        xml_meta = extract_xml_metadata(raw_text) if extension == "xml" else {}
        sql_meta = extract_sql_metadata(raw_text) if extension == "sql" else {}
        template_meta = extract_template_metadata(raw_text, extension) if extension in TEMPLATE_EXTENSIONS | HTML_LIKE_EXTENSIONS else {}

        if extension == "xml" and xml_meta.get("namespace") and not class_name:
            class_name = str(xml_meta["namespace"]).split(".")[-1]

        if extension == "sql" and sql_meta.get("statement_type") and not content_type:
            content_type = sql_meta["statement_type"]

        return {
            "raw_text": raw_text,
            "project_id": file_info.get("project_id", file_info.get("projectid", "")),
            "project_name": file_info.get("project_name", file_info.get("projectname", "")),
            "file_name": file_name,
            "extension": extension,
            "language": language,
            "mime_type": mime_type,
            "relative_path": relative_path,
            "saved_path": saved_path,
            "file_path": saved_path,
            "file_size": file_info.get("file_size", file_info.get("size", 0)),
            "source_type": file_info.get("source_type", file_info.get("sourcetype", "")),
            "root_container_name": file_info.get("root_container_name", file_info.get("rootcontainername", "")),
            "layer_type": layer_type,
            "content_type": content_type,
            "class_name": class_name,
            "package": package,
            "xml_namespace": xml_meta.get("namespace", ""),
            "xml_sql_fragments": xml_meta.get("sql_fragments", []),
            "template_meta": template_meta,
            "sql_meta": sql_meta,
        }

    except KeyError as error:
        logger.error("parse_text_file required key missing - %s file_info=%s", error, file_info)
        return {}
    except Exception as error:
        logger.error("parse_text_file failed - %s path=%s", error, file_info.get("saved_path", file_info.get("savedpath", "")))
        return {}


def extract_static_analysis(file_info: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_text_file(file_info)
    if not parsed:
        return {}

    raw_text = parsed["raw_text"]
    extension = parsed["extension"]
    language = parsed.get("language", "")

    imports = extract_imports_tree_sitter(raw_text, language)
    if not imports:
        imports = extract_imports_regex(raw_text, extension)

    methods = extract_methods_tree_sitter(raw_text, language)
    if not methods:
        methods = extract_methods_regex(raw_text, extension)

    xml_meta = extract_xml_metadata(raw_text) if extension == "xml" else {}
    sql_meta = extract_sql_metadata(raw_text) if extension == "sql" else {}
    template_meta = extract_template_metadata(raw_text, extension) if extension in TEMPLATE_EXTENSIONS | HTML_LIKE_EXTENSIONS else {}

    table_names = sql_meta.get("table_names", [])
    if not table_names:
        table_names = extract_table_names_regex(raw_text)

    return {
        "project_id": parsed.get("project_id", ""),
        "project_name": parsed.get("project_name", ""),
        "file_name": parsed.get("file_name", ""),
        "relative_path": parsed.get("relative_path", ""),
        "saved_path": parsed.get("saved_path", ""),
        "extension": extension,
        "language": language,
        "mime_type": parsed.get("mime_type", ""),
        "layer_type": parsed.get("layer_type", ""),
        "content_type": parsed.get("content_type", ""),
        "class_name": parsed.get("class_name", ""),
        "package": parsed.get("package", ""),
        "imports": imports,
        "methods": methods,
        "xml_statements": xml_meta.get("statement_ids", []),
        "xml_namespace": xml_meta.get("namespace", ""),
        "xml_sql_fragments": xml_meta.get("sql_fragments", []),
        "table_names": table_names,
        "template_meta": template_meta,
        "raw_text": raw_text,
    }