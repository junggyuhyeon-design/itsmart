from __future__ import annotations

import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from config import get_settings

ANALYSIS_TARGET_EXTENSIONS = (
    ".py",
    ".java",
    ".js",
    ".ts",
    ".sql",
    ".sh",
    ".txt",
    ".md",
    ".json",
    ".xml",
    ".yml",
    ".yaml",
    ".ini",
    ".toml",
    ".html",
    ".htm",
    ".css",
)

ALLOWED_EXTENSIONS = ANALYSIS_TARGET_EXTENSIONS + (".zip",)
MAX_ZIP_UNCOMPRESSED_SIZE = 1024 * 1024 * 1024 * 2
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


@dataclass(frozen=True)
class AnalysisTargetFile:
    source_type: str
    original_name: str
    saved_path: str
    relative_path: str
    extension: str
    size: int
    project_id: str = ""
    project_name: str = ""
    root_container_name: str = ""

    @property
    def sourcetype(self) -> str:
        return self.source_type

    @property
    def originalname(self) -> str:
        return self.original_name

    @property
    def savedpath(self) -> str:
        return self.saved_path

    @property
    def relativepath(self) -> str:
        return self.relative_path

    @property
    def projectid(self) -> str:
        return self.project_id

    @property
    def projectname(self) -> str:
        return self.project_name

    @property
    def rootcontainername(self) -> str:
        return self.root_container_name


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    name = _INVALID_FILENAME_CHARS.sub("_", name)
    return name or "upload"


def is_allowed_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in ANALYSIS_TARGET_EXTENSIONS


def is_allowed_upload_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def extract_zip(zip_path: Path, extract_dir: Path) -> Path:
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    ensure_dir(extract_dir)
    extract_dir_resolved = extract_dir.resolve()

    with zipfile.ZipFile(zip_path, "r") as archive:
        total_size = sum(info.file_size for info in archive.infolist())
        if total_size > MAX_ZIP_UNCOMPRESSED_SIZE:
            raise ValueError(
                f"'{zip_path.name}' 압축 해제 예상 용량({total_size} bytes)이 "
                f"허용치({MAX_ZIP_UNCOMPRESSED_SIZE} bytes)를 초과합니다."
            )

        for member in archive.infolist():
            target_path = (extract_dir / member.filename).resolve()
            if (
                    extract_dir_resolved != target_path
                    and extract_dir_resolved not in target_path.parents
            ):
                raise ValueError(
                    f"잠재적으로 위험한 zip 항목입니다. "
                    f"(path traversal 공격 가능성): {member.filename}"
                )

        archive.extractall(extract_dir)

    return extract_dir


def collect_target_files(
        base_dir: Path,
        project_id: str,
        project_name: str,
        source_type: str,
        root_container_name: str,
) -> list[AnalysisTargetFile]:
    targets: list[AnalysisTargetFile] = []

    if not base_dir.exists():
        return targets

    for path in sorted(base_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in ANALYSIS_TARGET_EXTENSIONS:
            targets.append(
                AnalysisTargetFile(
                    source_type=source_type,
                    original_name=path.name,
                    saved_path=str(path.resolve()),
                    relative_path=path.relative_to(base_dir).as_posix(),
                    extension=path.suffix.lstrip(".").lower(),
                    size=path.stat().st_size,
                    project_id=project_id,
                    project_name=project_name,
                    root_container_name=root_container_name,
                )
            )

    return targets


def process_uploads_and_collect(
        save_dir: Path,
        only_filenames: list[str] | None = None,
) -> list[AnalysisTargetFile]:
    settings = get_settings()
    all_targets: list[AnalysisTargetFile] = []

    extracted_root = ensure_dir(Path("/data/extracted"))

    candidates = (
        [save_dir / fn for fn in only_filenames]
        if only_filenames
        else list(save_dir.glob("*"))
    )

    for path in candidates:
        if not path.is_file():
            continue

        ext = path.suffix.lstrip(".").lower()

        if ext == "zip":
            project_id = str(uuid.uuid4())
            project_name = path.stem
            extract_dir = extracted_root / project_name

            try:
                extract_zip(path, extract_dir)
            except (zipfile.BadZipFile, ValueError) as e:
                print(f"[WARN] ZIP 파일 처리 실패: {path.name}: {e}")
                continue

            all_targets.extend(
                collect_target_files(
                    extract_dir,
                    project_id=project_id,
                    project_name=project_name,
                    source_type="zip_entry",
                    root_container_name=project_name,
                )
            )

        elif path.suffix.lower() in ANALYSIS_TARGET_EXTENSIONS:
            project_id = str(uuid.uuid4())
            project_name = path.stem

            all_targets.append(
                AnalysisTargetFile(
                    source_type="upload_file",
                    original_name=path.name,
                    saved_path=str(path.resolve()),
                    relative_path=path.name,
                    extension=path.suffix.lstrip(".").lower(),
                    size=path.stat().st_size,
                    project_id=project_id,
                    project_name=project_name,
                    root_container_name=project_name,
                )
            )

    return all_targets


# ------------------------------------------------------------------
# backward-compatible aliases for old imports
# ------------------------------------------------------------------

def ensuredir(path: Path) -> Path:
    return ensure_dir(path)


def safefilename(filename: str) -> str:
    return safe_filename(filename)


def isallowedextension(filename: str) -> bool:
    return is_allowed_extension(filename)


def isalloweduploadextension(filename: str) -> bool:
    return is_allowed_upload_extension(filename)


def collecttargetfiles(
        base_dir: Path,
        projectid: str,
        projectname: str,
        sourcetype: str,
        rootcontainername: str,
) -> list[AnalysisTargetFile]:
    return collect_target_files(
        base_dir=base_dir,
        project_id=projectid,
        project_name=projectname,
        source_type=sourcetype,
        root_container_name=rootcontainername,
    )


def processuploadsandcollect(
        save_dir: Path,
        only_filenames: list[str] | None = None,
) -> list[AnalysisTargetFile]:
    return process_uploads_and_collect(save_dir, only_filenames)