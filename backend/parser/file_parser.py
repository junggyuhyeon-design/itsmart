from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LAYER_PATTERNS: list[tuple[str, str]] = [
    (r"@RestController|@Controller", "controller"),
    (r"@Service", "service"),
    (r"@Repository", "repository"),
    (r"@Mapper", "mapper"),
    (r"DAO", "repository"),
    (r"Controller", "controller"),
    (r"ServiceImpl?", "service"),
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

<<<<<<< HEAD
# ── 파일 읽기 ─────────────────────────────────────────────────────
=======

>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
def read_text_file(path: str) -> str:
    file_path = Path(path)
    for enc in ("utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return file_path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"텍스트 파일을 읽을 수 없습니다: {path}")


<<<<<<< HEAD
# ── 레이어 감지 패턴 ──────────────────────────────────────────────
# controller, service, repository, mapper, component
_LAYER_PATTERNS: list[tuple[str, str]] = [
    (r"@(RestController|Controller)\b", "controller"),
    (r"@Service\b", "service"),
    (r"@Repository\b", "repository"),
    (r"<mapper\s+namespace=", "mapper"),
    (r"@(Component|Configuration|Bean)\b", "component"),
]


# ── 메타데이터 추출 함수 ──────────────────────────────────────────
=======
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
def _detect_layer(text: str, ext: str) -> str:
    for pattern, layer in LAYER_PATTERNS:
        if re.search(pattern, text, re.I):
            return layer
    if ext == "xml":
        return "mapper"
    if ext == "sql":
        return "ddl"
    if ext in {"yml", "yaml", "json", "ini", "toml"}:
        return "config"
    return ""


<<<<<<< HEAD
=======
def _detect_content_type(text: str) -> str:
    for pattern, content_type in CONTENT_TYPE_PATTERNS:
        if re.search(pattern, text, re.I):
            return content_type
    return ""


>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
def _extract_class_name(text: str, ext: str) -> str:
    if ext == "java":
        m = re.search(r"class\s+([A-Za-z_][A-Za-z0-9_]*)", text)
        if m:
            return m.group(1)
        m = re.search(r"interface\s+([A-Za-z_][A-Za-z0-9_]*)", text)
        return m.group(1) if m else ""
    if ext == "py":
        m = re.search(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.M)
        return m.group(1) if m else ""
    if ext == "xml":
<<<<<<< HEAD
        m = re.search(r'<mapper[^>]*namespace="([^"]+)"', text, re.I)
=======
        m = re.search(r'namespace="([^"]+)"', text, re.I)
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
        return m.group(1).split(".")[-1] if m else ""
    return ""


<<<<<<< HEAD
# ── file parsing API ─────────────────────────────────────────────────────
def parse_text_file(file_info: dict[str, Any]) -> dict[str, Any]:
    """
    target 정보를 기반으로 파일을 파싱한다.
    """
    try:
        raw_text = read_text_file(file_info["saved_path"]) # 파일 읽기
        if not raw_text.strip():
            return {}

        ext = file_info["extension"]                      # 확장자
        layer_type = _detect_layer(raw_text, ext)         # 계층타입추출 [controller, service, repository, component, mapper]
        class_name = _extract_class_name(raw_text, ext)   # 클래스이름 추출

        return {
            "raw_text": raw_text,                       # 파일 원문데이터
            "project_id": file_info["project_id"],      # 프로젝트아이디
            "project_name": file_info["project_name"],  # 프로젝트명
            "file_name": file_info["original_name"],    # 파일명
            "extension": ext,                           # 확장자
            "relative_path": file_info["relative_path"],# 저장경로
            "layer_type": layer_type,                   # 계층타입
            "class_name": class_name,                   # 클래스명
=======
def _extract_package(text: str, ext: str) -> str:
    if ext == "java":
        m = re.search(r"^package\s+([\w.]+)\s*;", text, re.M)
        return m.group(1) if m else ""
    return ""


def _extract_imports(text: str, ext: str) -> list[str]:
    if ext == "java":
        return re.findall(r"^import\s+([\w.]+)\s*;", text, re.M)
    if ext == "py":
        imports = re.findall(r"^import\s+([\w.]+)", text, re.M)
        from_imports = re.findall(r"^from\s+([\w.]+)\s+import\s+", text, re.M)
        return imports + from_imports
    return []


def _extract_methods(text: str, ext: str) -> list[dict[str, Any]]:
    methods: list[dict[str, Any]] = []

    if ext == "java":
        pattern = re.compile(
            r"(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\], ?]+\s+"
            r"([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)",
            re.M,
        )
        for m in pattern.finditer(text):
            methods.append(
                {
                    "name": m.group(1),
                    "signature": m.group(0).strip(),
                    "params": m.group(2).strip(),
                }
            )

    elif ext == "py":
        pattern = re.compile(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)", re.M)
        for m in pattern.finditer(text):
            methods.append(
                {
                    "name": m.group(1),
                    "signature": m.group(0).strip(),
                    "params": m.group(2).strip(),
                }
            )

    return methods


def _extract_xml_statement_ids(text: str) -> list[dict[str, str]]:
    statements: list[dict[str, str]] = []
    for tag in ("select", "insert", "update", "delete"):
        pattern = re.compile(rf"<{tag}\b[^>]*id=\"([^\"]+)\"", re.I)
        for m in pattern.finditer(text):
            statements.append(
                {
                    "tag": tag,
                    "id": m.group(1),
                }
            )
    return statements


def _extract_table_names(text: str, ext: str) -> list[str]:
    found: list[str] = []
    patterns = [
        r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"\bJOIN\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"\bUPDATE\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"\bINSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"\bDELETE\s+FROM\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    ]

    upper_text = text.upper()
    for pattern in patterns:
        for m in re.finditer(pattern, upper_text, re.I):
            found.append(m.group(1))

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

        ext = str(file_info.get("extension", "")).lower()
        layer_type = _detect_layer(raw_text, ext)
        content_type = _detect_content_type(raw_text)
        class_name = _extract_class_name(raw_text, ext)
        package = _extract_package(raw_text, ext)

        return {
            "raw_text": raw_text,
            "project_id": file_info.get("project_id", file_info.get("projectid", "")),
            "project_name": file_info.get("project_name", file_info.get("projectname", "")),
            "file_name": file_info.get(
                "file_name",
                file_info.get("filename", file_info.get("original_name", file_info.get("originalname", ""))),
            ),
            "extension": ext,
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
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
        }

    except KeyError as e:
        logger.error("parse_text_file: required key missing - %s | file_info=%s", e, file_info)
        return {}
    except Exception as e:
<<<<<<< HEAD
        logging.error(
            "parse_text_file: 파싱 실패 — %s | path=%s", e, file_info.get("saved_path")
=======
        logger.error(
            "parse_text_file: failed - %s | path=%s",
            e,
            file_info.get("saved_path", file_info.get("savedpath")),
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
        )
        return {}


def extract_static_analysis(file_info: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_text_file(file_info)
    if not parsed:
        return {}

    raw_text = parsed["raw_text"]
    ext = parsed["extension"]

    return {
        "project_id": parsed.get("project_id", ""),
        "project_name": parsed.get("project_name", ""),
        "file_name": parsed.get("file_name", ""),
        "relative_path": parsed.get("relative_path", ""),
        "saved_path": parsed.get("saved_path", ""),
        "extension": ext,
        "layer_type": parsed.get("layer_type", ""),
        "content_type": parsed.get("content_type", ""),
        "class_name": parsed.get("class_name", ""),
        "package": parsed.get("package", ""),
        "imports": _extract_imports(raw_text, ext),
        "methods": _extract_methods(raw_text, ext),
        "xml_statements": _extract_xml_statement_ids(raw_text) if ext == "xml" else [],
        "table_names": _extract_table_names(raw_text, ext),
        "raw_text": raw_text,
    }


# ------------------------------------------------------------------
# backward-compatible aliases
# ------------------------------------------------------------------

def readtextfile(path: str) -> str:
    return read_text_file(path)


def parsetextfile(file_info: dict[str, Any]) -> dict[str, Any]:
    return parse_text_file(file_info)


def extractstaticanalysis(file_info: dict[str, Any]) -> dict[str, Any]:
    return extract_static_analysis(file_info)