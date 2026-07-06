from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LAYER_PATTERNS: list[tuple[str, str]] = [
    (r"@RestController|Controller", "controller"),
    (r"@Service\b|ServiceImpl\b|Service\b", "service"),
    (r"@Repository\b|Repository\b|DAO\b", "repository"),
    (r"@Mapper\b|Mapper\b", "mapper"),
]

CONTENT_TYPE_PATTERNS: list[tuple[str, str]] = [
    (r"@GetMapping|@PostMapping|@PutMapping|@DeleteMapping|@RequestMapping", "api_endpoint"),
    (r"<select\b", "sql_select"),
    (r"<insert\b", "sql_insert"),
    (r"<update\b", "sql_update"),
    (r"<delete\b", "sql_delete"),
    (r"CREATE\s+TABLE", "ddl_create"),
    (r"ALTER\s+TABLE", "ddl_alter"),
    (r"INSERT\s+INTO", "dml_insert"),
    (r"UPDATE\s+", "dml_update"),
    (r"DELETE\s+FROM", "dml_delete"),
]


def read_text_file(path: str) -> str:
    file_path = Path(path)
    for encoding in ("utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return file_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"cannot decode file: {path}")


def detect_layer(text: str, extension: str) -> str:
    for pattern, layer in LAYER_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return layer

    if extension == "xml":
        return "mapper"
    if extension == "sql":
        return "ddl"
    if extension in {"yml", "yaml", "json", "ini", "toml"}:
        return "config"
    return ""


def detect_content_type(text: str) -> str:
    for pattern, content_type in CONTENT_TYPE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return content_type
    return ""


def extract_class_name(text: str, extension: str) -> str:
    if extension == "java":
        match = re.search(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)", text)
        if match:
            return match.group(1)
        match = re.search(r"\binterface\s+([A-Za-z_][A-Za-z0-9_]*)", text)
        return match.group(1) if match else ""

    if extension == "py":
        match = re.search(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.MULTILINE)
        return match.group(1) if match else ""

    if extension == "xml":
        match = re.search(r'namespace\s*=\s*"([^"]+)"', text, re.IGNORECASE)
        if match:
            return match.group(1).split(".")[-1]
    return ""


def extract_package(text: str, extension: str) -> str:
    if extension == "java":
        match = re.search(r"^\s*package\s+([A-Za-z0-9_.]+)\s*;", text, re.MULTILINE)
        return match.group(1) if match else ""
    return ""


def extract_imports(text: str, extension: str) -> list[str]:
    if extension == "java":
        return re.findall(r"^\s*import\s+([A-Za-z0-9_.*]+)\s*;", text, re.MULTILINE)

    if extension == "py":
        imports = re.findall(r"^\s*import\s+([A-Za-z0-9_.,\s]+)", text, re.MULTILINE)
        from_imports = re.findall(r"^\s*from\s+([A-Za-z0-9_.,\s]+)\s+import\s+([A-Za-z0-9_.*,\s]+)", text, re.MULTILINE)
        result = list(imports)
        result.extend([f"{module} import {names}" for module, names in from_imports])
        return result

    return []


def extract_methods(text: str, extension: str) -> list[dict[str, Any]]:
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


def extract_xml_statement_ids(text: str) -> list[dict[str, str]]:
    statements: list[dict[str, str]] = []
    for tag in ("select", "insert", "update", "delete"):
        pattern = re.compile(rf"<{tag}\b[^>]*\bid\s*=\s*\"([^\"]+)\"", re.IGNORECASE)
        for match in pattern.finditer(text):
            statements.append({"tag": tag, "id": match.group(1)})
    return statements


def extract_table_names(text: str, extension: str) -> list[str]:
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

        extension = str(file_info.get("extension", "")).lower().lstrip(".")
        layer_type = detect_layer(raw_text, extension)
        content_type = detect_content_type(raw_text)
        class_name = extract_class_name(raw_text, extension)
        package = extract_package(raw_text, extension)

        return {
            "raw_text": raw_text,
            "project_id": file_info.get("project_id", file_info.get("projectid", "")),
            "project_name": file_info.get("project_name", file_info.get("projectname", "")),
            "file_name": file_info.get(
                "file_name",
                file_info.get("filename", file_info.get("original_name", file_info.get("originalname", ""))),
            ),
            "extension": extension,
            "relative_path": file_info.get("relative_path", file_info.get("relativepath", "")),
            "saved_path": saved_path,
            "file_path": saved_path,
            "file_size": file_info.get("file_size", file_info.get("size", 0)),
            "source_type": file_info.get("source_type", file_info.get("sourcetype", "")),
            "root_container_name": file_info.get(
                "root_container_name",
                file_info.get("rootcontainername", ""),
            ),
            "layer_type": layer_type,
            "content_type": content_type,
            "class_name": class_name,
            "package": package,
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

    return {
        "project_id": parsed.get("project_id", ""),
        "project_name": parsed.get("project_name", ""),
        "file_name": parsed.get("file_name", ""),
        "relative_path": parsed.get("relative_path", ""),
        "saved_path": parsed.get("saved_path", ""),
        "extension": extension,
        "layer_type": parsed.get("layer_type", ""),
        "content_type": parsed.get("content_type", ""),
        "class_name": parsed.get("class_name", ""),
        "package": parsed.get("package", ""),
        "imports": extract_imports(raw_text, extension),
        "methods": extract_methods(raw_text, extension),
        "xml_statements": extract_xml_statement_ids(raw_text) if extension == "xml" else [],
        "table_names": extract_table_names(raw_text, extension),
        "raw_text": raw_text,
    }