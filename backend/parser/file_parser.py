from dataclasses import dataclass
from pathlib import Path
from typing import Any
from backend.config import Settings # 경로 수정
from backend.utils.file_utils import ( # 경로 수정
    AnalysisTargetFile, SavedFileInfo, collect_target_files,
    extract_zip, is_allowed_extension,
)

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

    try:
        raw_text = read_text_file(path)
        if not raw_text.strip(): return {}
        return {
            "raw_text": raw_text, "file_name": name, "extension": ext,
            "relative_path": rel_path, "language": detect_language_by_extension(ext), "file_size": size,
        }
    except Exception as e:
        logging.error(f"파일 파싱 실패: {path}, 에러: {e}")
        return {}

class FileParser:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # self.extracted_root = get_extracted_root(settings.upload_dir) # 사용하지 않으므로 제거

    def collect_analysis_targets(self, saved_files: list[SavedFileInfo]) -> list[AnalysisTargetFile]:
        # 이 함수는 현재 사용되지 않음 (process_uploads_and_collect가 대체)
        return []