import logging
import re
from pathlib import Path
from typing import Any


# ── 파일 읽기 ─────────────────────────────────────────────────────
def read_text_file(path: str) -> str:
    file_path = Path(path)
    for enc in ["utf-8", "cp949"]:
        try:
            return file_path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"인코딩 지원 불가: {path}")


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
def _detect_layer(text: str, ext: str) -> str:
    for pattern, layer in _LAYER_PATTERNS:
        if re.search(pattern, text, re.I):
            return layer
    if ext == "xml":
        return "mapper"
    if ext == "sql":
        return "ddl"
    return ""


def _extract_class_name(text: str, ext: str) -> str:
    if ext == "java":
        m = re.search(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)", text)
        return m.group(1) if m else ""
    if ext == "py":
        m = re.search(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.M)
        return m.group(1) if m else ""
    if ext == "xml":
        m = re.search(r'<mapper[^>]*namespace="([^"]+)"', text, re.I)
        return m.group(1).split(".")[-1] if m else ""
    return ""


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
        }
    except KeyError as e:
        logging.error("parse_text_file: 필수 키 누락 — %s | file_info=%s", e, file_info)
        return {}
    except Exception as e:
        logging.error(
            "parse_text_file: 파싱 실패 — %s | path=%s", e, file_info.get("saved_path")
        )
        return {}
