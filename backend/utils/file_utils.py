from __future__ import annotations

import logging
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from config import get_settings

logger = logging.getLogger(__name__)

ANALYSIS_TARGET_EXTENSIONS = {
    ".py", ".java", ".js", ".ts", ".sql", ".sh", ".txt", ".md",
    ".json", ".xml", ".yml", ".yaml", ".ini", ".toml", ".html", ".htm", ".css",
}
ALLOWED_EXTENSIONS = ANALYSIS_TARGET_EXTENSIONS | {".zip"}
MAX_ZIP_UNCOMPRESSED_SIZE = 2 * 1024 * 1024 * 1024
INVALID_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._\- ]+")


@dataclass(frozen=True)
class AnalysisTargetFile:
    source_type: str
    original_name: str
    saved_path: str
    relative_path: str
    extension: str
    size: int
    project_id: str
    project_name: str
    root_container_name: str


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    name = INVALID_FILENAME_CHARS.sub("_", name)
    return name or "upload"


def is_allowed_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in ANALYSIS_TARGET_EXTENSIONS


def is_allowed_upload_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def _make_project_name(filename: str) -> str:
    return Path(filename).stem.strip() or "project"


def _make_project_id() -> str:
    return str(uuid.uuid4())


def _collect_regular_file(saved_path: Path) -> list[AnalysisTargetFile]:
    if not is_allowed_extension(saved_path.name):
        return []

    project_id = _make_project_id()
    project_name = _make_project_name(saved_path.name)

    return [
        AnalysisTargetFile(
            source_type="file",
            original_name=saved_path.name,
            saved_path=str(saved_path),
            relative_path=saved_path.name,
            extension=saved_path.suffix.lower().lstrip("."),
            size=saved_path.stat().st_size if saved_path.exists() else 0,
            project_id=project_id,
            project_name=project_name,
            root_container_name=saved_path.name,
        )
    ]


def _extract_zip(saved_zip: Path, extract_root: Path, user_id: str) -> list[AnalysisTargetFile]:
    project_id = _make_project_id()
    project_name = _make_project_name(saved_zip.name)
    target_root = extract_root / f"{user_id}" / f"{project_name}" # user_id 별로 프로젝트 경로 설정

    # logger.info("_extract_zip 경로 ::: %s", target_root)   # /data/extracted/admin/board_final-master
    # logger.info("saved_path ::: %s", str(saved_zip))       # /data/uploads/admin/board_final-master.zip

    if target_root.exists():
        logger.info("[INFO] 기존 디렉토리를 제거: %s", target_root)
        shutil.rmtree(target_root, ignore_errors=True)
    ensure_dir(target_root) # 경로 생성

    results: list[AnalysisTargetFile] = []

    with zipfile.ZipFile(saved_zip, "r") as archive:
        total_uncompressed = sum(info.file_size for info in archive.infolist())
        if total_uncompressed > MAX_ZIP_UNCOMPRESSED_SIZE:
            raise ValueError(f"zip too large: {saved_zip.name}")

        archive.extractall(target_root)

    for path in target_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in ANALYSIS_TARGET_EXTENSIONS:
            continue

        relative_path = str(path.relative_to(target_root)).replace("\\", "/")
        results.append(
            AnalysisTargetFile(
                source_type="zip_entry",
                original_name=path.name,
                saved_path=str(path),
                relative_path=relative_path,
                extension=path.suffix.lower().lstrip("."),
                size=path.stat().st_size,
                project_id=project_id,
                project_name=project_name,
                root_container_name=saved_zip.name,
            )
        )

    return results


def process_uploads_and_collect(saved_path: Path, saved_filenames: list[str], user_id: str) -> list[AnalysisTargetFile]:
    settings = get_settings()
    extract_dir = Path(settings.extract_dir)

    collected: list[AnalysisTargetFile] = []

    for filename in saved_filenames:
        # logger.info("saved_path=%s", saved_path) # /data/uploads/admin/board_final-master.zip
        # logger.info("filename=%s", filename)     # board_final-master.zip

        if not saved_path.exists() or not saved_path.is_file():
            continue

        suffix = saved_path.suffix.lower()
        if suffix == ".zip":
            collected.extend(_extract_zip(saved_path, extract_dir, user_id))
        elif suffix in ANALYSIS_TARGET_EXTENSIONS:
            collected.extend(_collect_regular_file(saved_path))

    return collected