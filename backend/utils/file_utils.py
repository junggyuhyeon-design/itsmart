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
    original_name: str
    saved_path: str
    relative_path: str
    extension: str
    project_id: str = ""
    project_name: str = ""

<<<<<<< HEAD
=======
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

>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9

def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    name = _INVALID_FILENAME_CHARS.sub("_", name)
    return name or "upload"


<<<<<<< HEAD
=======
def is_allowed_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in ANALYSIS_TARGET_EXTENSIONS


>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
def is_allowed_upload_extension(filename: str) -> bool:
    """확장자 허용 여부"""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS

<<<<<<< HEAD
def extract_zip(zip_path: Path, extract_dir: Path) -> Path:
    """
    ZIP 파일을 지정된 디렉토리에 안전하게 추출한다.
    - 기존 디렉터리 삭제
    - 1. 압축 해제 후 예상 용량 계산
    - 2. Zip bomb 방어 : 지나치게 큰 압축 해제를 방어
    - 3. Zip Slip 방어 : 추출 전 각 항목의 최종 경로를 계산해 extract_dir 하위에 있는지 검증
    - 4. Zip 파일 지정경로에 압축 해제
    """
=======

def extract_zip(zip_path: Path, extract_dir: Path) -> Path:
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
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
<<<<<<< HEAD
        # 3. 파일을 extract_dir에 압축 해제 (Zipfile 라이브러리)
        archive.extractall(extract_dir)


def collect_target_files(
    base_dir: Path, project_id: str, project_name: str
=======

        archive.extractall(extract_dir)

    return extract_dir


def collect_target_files(
        base_dir: Path,
        project_id: str,
        project_name: str,
        source_type: str,
        root_container_name: str,
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
) -> list[AnalysisTargetFile]:
    targets: list[AnalysisTargetFile] = []

    if not base_dir.exists():
        return targets

    for path in sorted(base_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in ANALYSIS_TARGET_EXTENSIONS:
            targets.append(
                AnalysisTargetFile(
                    original_name=path.name,                               # 파일명
                    saved_path=str(path.resolve()),                        # 저장 경로(절대경로)
                    relative_path=path.relative_to(base_dir).as_posix(),   # 저장 경로(상대경로)
                    extension=path.suffix.lstrip(".").lower(),             # 확장자
                    project_id=project_id,                                 # 프로젝트아이디
                    project_name=project_name,                             # 프로젝트명
                )
            )

    return targets


def process_uploads_and_collect(
        save_dir: Path,
        only_filenames: list[str] | None = None,
) -> list[AnalysisTargetFile]:
<<<<<<< HEAD
    """
    /extracted 경로에 압축해제 후 전체 파일 정보 반환
    """
=======
    settings = get_settings()
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
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
            project_id = str(uuid.uuid4()) # 프로젝트 고유 id 생성
            project_name = path.stem
            extract_dir = extracted_root / project_name

            try:
                extract_zip(path, extract_dir) # zip 압축해제
            except (zipfile.BadZipFile, ValueError) as e:
                print(f"[WARN] ZIP 파일 처리 실패: {path.name}: {e}")
                continue

            all_targets.extend(
<<<<<<< HEAD
                collect_target_files( # 압축해제된 파일 목록 생성
                    extract_dir,                     # 압축해제 경로
                    project_id=project_id,           # 프로젝트아이디
                    project_name=project_name,       # 프로젝트명
=======
                collect_target_files(
                    extract_dir,
                    project_id=project_id,
                    project_name=project_name,
                    source_type="zip_entry",
                    root_container_name=project_name,
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
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