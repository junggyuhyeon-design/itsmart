import logging
from pathlib import Path
from typing import Any

# 확인 완료
def read_text_file(path: str) -> str:
    file_path = Path(path)
    for enc in ["utf-8", "cp949"]:
        try:
            return file_path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"인코딩 지원 불가: {path}")

# 확인 완료
def parse_text_file(file_info: dict[str, Any]) -> dict[str, Any]:
    try:
        raw_text = read_text_file(file_info["saved_path"])
        if not raw_text.strip():
            return {}
        return {
            "raw_text":      raw_text,
            "project_id":    file_info["project_id"],
            "project_name":  file_info["project_name"],
            "file_name":     file_info["original_name"],
            "extension":     file_info["extension"],
            "relative_path": file_info["relative_path"],
            "file_size":     file_info["size"],
        }
    except KeyError as e:
        logging.error("parse_text_file: 필수 키 누락 — %s | file_info=%s", e, file_info)
        return {}
    except Exception as e:
        logging.error("parse_text_file: 파싱 실패 — %s | path=%s", e, file_info.get("saved_path"))
        return {}
