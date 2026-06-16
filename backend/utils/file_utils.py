from __future__ import annotations
import re
import zipfile
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

ANALYSIS_TARGET_EXTENSIONS = (
    ".py", ".sql", ".txt", ".md", ".java", ".json", ".xml", ".yml", ".yaml",
)
ALLOWED_EXTENSIONS = ANALYSIS_TARGET_EXTENSIONS + (".zip",)

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')

class UploadedFileLike(Protocol):
    name: str
    size: int
    def getvalue(self) -> bytes: ...

@dataclass(frozen=True)
class SavedFileInfo:
    original_name: str
    saved_name: str
    size: int
    extension: str
    saved_path: str
    uploaded_at: str

@dataclass(frozen=True)
class AnalysisTargetFile:
    source_type: str
    original_name: str
    saved_path: str
    relative_path: str
    extension: str
    size: int
    root_container_name: str = ""

def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path

def get_extension(filename: str) -> str:
    return Path(filename).suffix.lstrip(".").lower()

def safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    name = _INVALID_FILENAME_CHARS.sub("_", name)
    return name or "upload"

def is_allowed_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in ANALYSIS_TARGET_EXTENSIONS

def is_allowed_upload_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS

def extract_zip(zip_path: Path, extract_dir: Path) -> Path:
    if extract_dir.exists(): shutil.rmtree(extract_dir)
    ensure_dir(extract_dir)
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(extract_dir)
    return extract_dir

def collect_target_files(base_dir: Path, source_type: str = "direct_upload", root_container_name: str = "") -> list[AnalysisTargetFile]:
    targets = []
    if not base_dir.exists(): return targets

    for path in sorted(base_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in ANALYSIS_TARGET_EXTENSIONS:
            targets.append(AnalysisTargetFile(
                source_type=source_type, original_name=path.name,
                saved_path=str(path.resolve()), relative_path=path.relative_to(base_dir).as_posix(),
                extension=path.suffix.lstrip(".").lower(), size=path.stat().st_size,
                root_container_name=root_container_name
            ))
    return targets

def process_uploads_and_collect(save_dir: Path) -> list[AnalysisTargetFile]:
    all_targets = []
    extracted_root = save_dir.parent / "extracted"
    ensure_dir(extracted_root)
    for path in save_dir.glob("*"):
        if not path.is_file(): continue
        ext = path.suffix.lstrip(".").lower()
        if ext == "zip":
            extract_dir = extracted_root / path.stem
            extract_zip(path, extract_dir)
            all_targets.extend(collect_target_files(extract_dir, source_type="zip_entry", root_container_name=path.stem))
        elif f".{ext}" in ANALYSIS_TARGET_EXTENSIONS:
            all_targets.append(AnalysisTargetFile(
                source_type="direct_upload", original_name=path.name,
                saved_path=str(path.resolve()), relative_path=path.name,
                extension=ext, size=path.stat().st_size, root_container_name=path.name
            ))
    return all_targets

def save_uploaded_file(uploaded_file: UploadedFileLike, save_dir: Path) -> SavedFileInfo:
    ensure_dir(save_dir)
    saved_name = safe_filename(uploaded_file.name) # safe_filename 사용
    dest = save_dir / saved_name
    if dest.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dest = save_dir / f"{dest.stem}_{timestamp}{dest.suffix}"
    dest.write_bytes(uploaded_file.getvalue())
    return SavedFileInfo(
        original_name=uploaded_file.name, saved_name=dest.name, size=uploaded_file.size,
        extension=dest.suffix.lstrip(".").lower(), saved_path=str(dest.resolve()),
        uploaded_at=datetime.now(timezone.utc).isoformat()
    )