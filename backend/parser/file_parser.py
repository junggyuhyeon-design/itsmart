import logging
import re
from pathlib import Path
from typing import Any


# ── 파일 읽기 ─────────────────────────────────────────────────────
# 확인 완료
def read_text_file(path: str) -> str:
    file_path = Path(path)
    for enc in ["utf-8", "cp949"]:
        try:
            return file_path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"인코딩 지원 불가: {path}")


# ── 레이어 감지 패턴 ──────────────────────────────────────────────

_LAYER_PATTERNS: list[tuple[str, str]] = [
    # (정규식 패턴, layer_type)  — 위에서부터 우선 매칭
    (r"@(RestController|Controller)\b", "controller"),
    (r"@Service\b", "service"),
    (r"@Repository\b", "repository"),
    (r"<mapper\s+namespace=", "mapper"),
    (r"@(Component|Configuration|Bean)\b", "component"),
    (r"CREATE\s+TABLE\b", "ddl"),
    (r"\b(INSERT|UPDATE|DELETE|SELECT)\s+", "dml"),
]

_CONTENT_TYPE_PATTERNS: list[tuple[str, str]] = [
    (
        r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|RequestMapping)\b",
        "api_endpoint",
    ),
    (r"<select\b", "sql_select"),
    (r"<insert\b", "sql_insert"),
    (r"<update\b", "sql_update"),
    (r"<delete\b", "sql_delete"),
    (r"<resultMap\b", "sql_resultmap"),
    (r"CREATE\s+TABLE\b", "ddl_create"),
]


# ── 메타데이터 추출 함수 ──────────────────────────────────────────
# 확인 완료
def _detect_layer(text: str, ext: str) -> str:
    for pattern, layer in _LAYER_PATTERNS:
        if re.search(pattern, text, re.I):
            return layer
    # 확장자 기반 fallback
    if ext == "xml":
        return "mapper"
    if ext == "sql":
        return "ddl"
    return ""


# 확인 완료
def _detect_content_type(text: str) -> str:
    for pattern, ctype in _CONTENT_TYPE_PATTERNS:
        if re.search(pattern, text, re.I):
            return ctype
    return ""


# 확인 완료
def _extract_class_name(text: str, ext: str) -> str:
    if ext == "java":
        m = re.search(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)", text)
        return m.group(1) if m else ""
    if ext == "py":
        m = re.search(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.M)
        return m.group(1) if m else ""
    if ext == "xml":
        m = re.search(r'<mapper[^>]*namespace="([^"]+)"', text, re.I)
        # namespace 마지막 세그먼트만 추출 (com.example.mapper.UserMapper → UserMapper)
        return m.group(1).split(".")[-1] if m else ""
    return ""


# 확인 완료
def _extract_package(text: str, ext: str) -> str:
    if ext == "java":
        m = re.search(r"^package\s+([\w.]+)\s*;", text, re.M)
        return m.group(1) if m else ""
    return ""


# ── file parsing API ─────────────────────────────────────────────────────
# 확인 완료
def parse_text_file(file_info: dict[str, Any]) -> dict[str, Any]:
    """
    /index 엔드포인트에서 전달되는 file_info 는 항상 dict.

    AnalysisTargetFile.__dict__ 키 목록:
        source_type, original_name, saved_path, relative_path,
        extension, size, project_id, project_name, root_container_name

    반환값에 신규 추가된 메타데이터:
        layer_type   — controller / service / mapper / ddl / dml / component / ""
        content_type — api_endpoint / sql_select / ddl_create / ...  / ""
        class_name   — 클래스명 또는 XML namespace 마지막 세그먼트
        package      — Java package 선언값 (Java 전용)
    """
    try:
        raw_text = read_text_file(file_info["saved_path"])
        if not raw_text.strip():
            return {}

        ext = file_info["extension"]
        layer_type = _detect_layer(raw_text, ext)
        content_type = _detect_content_type(raw_text)
        class_name = _extract_class_name(raw_text, ext)
        package = _extract_package(raw_text, ext)

        return {
            "raw_text": raw_text,
            "project_id": file_info["project_id"],
            "project_name": file_info["project_name"],
            "file_name": file_info["original_name"],
            "extension": ext,
            "relative_path": file_info["relative_path"],
            "file_size": file_info["size"],
            # ── 신규 메타데이터 ──────────────────────────────────
            "layer_type": layer_type,
            "content_type": content_type,
            "class_name": class_name,
            "package": package,
        }
    except KeyError as e:
        logging.error("parse_text_file: 필수 키 누락 — %s | file_info=%s", e, file_info)
        return {}
    except Exception as e:
        logging.error(
            "parse_text_file: 파싱 실패 — %s | path=%s", e, file_info.get("saved_path")
        )
        return {}
