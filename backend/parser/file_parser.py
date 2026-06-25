import logging
from pathlib import Path
from typing import Any


def detect_language_by_extension(ext: str) -> str:
    mapping = {"py": "python", "sql": "sql", "java": "java", "md": "markdown", "yml": "yaml", "yaml": "yaml", "json": "json", "xml": "xml", "txt": "text"}
    return mapping.get(ext.lower().lstrip("."), "text")

def read_text_file(path: str) -> str:
    file_path = Path(path)
    for enc in ["utf-8", "cp949"]:
        try: return file_path.read_text(encoding=enc)
        except UnicodeDecodeError: continue
    raise ValueError(f"인코딩 지원 불가: {path}")

def parse_text_file(file_info: Any) -> dict[str, Any]:
    # 딕셔너리 또는 객체 대응
    if isinstance(file_info, dict):
        path, name, ext, rel_path, size = file_info["saved_path"], file_info["original_name"], file_info["extension"], file_info["relative_path"], file_info["size"]
    else:
        path, name, ext, rel_path, size = file_info.saved_path, file_info.original_name, file_info.extension, file_info.relative_path, file_info.size

    # project_id / project_name: 딕셔너리와 dataclass 객체 모두 대응
    if isinstance(file_info, dict):
        project_id = file_info.get("project_id", "")
        project_name = file_info.get("project_name", "")
    else:
        project_id = getattr(file_info, "project_id", "")
        project_name = getattr(file_info, "project_name", "")

    try:
        raw_text = read_text_file(path)
        if not raw_text.strip(): return {}
        return {
            "raw_text": raw_text,
            "project_id": project_id,
            "project_name": project_name,
            "file_name": name,
            "extension": ext,
            "relative_path": rel_path,
            "language": detect_language_by_extension(ext),
            "file_size": size,
        }
    except Exception as e:
        logging.error(f"파일 파싱 실패: {path}, 에러: {e}")
        return {}
