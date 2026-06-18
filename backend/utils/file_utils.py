from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

ANALYSIS_TARGET_EXTENSIONS = (
    ".py",
    ".sql",
    ".txt",
    ".md",
    ".java",
    ".json",
    ".xml",
    ".yml",
    ".yaml",
)
ALLOWED_EXTENSIONS = ANALYSIS_TARGET_EXTENSIONS + (".zip",)
# zip bomb 방어용 상한. 압축 해제 후 총 용량이 이 값을 넘으면 거부.
MAX_ZIP_UNCOMPRESSED_SIZE = 1024 * 1024 * 500  # 500MB
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
    """디렉토리가 존재하지 않으면 생성한다."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(filename: str) -> str:
    """
    경로 조작에 쓰일 수 있는 문자/구조를 제거하고 안전한 파일명만 남긴다.
    - PATH(filename).name: 디렉터리 구분자, 상위 경로(..)를 제거하고 마지막 구성요소만 취함
    - 정규식 : OS에서 문제될 수 있는 특수문자를 '_'로 치환
    """
    name = Path(filename).name.strip()
    name = _INVALID_FILENAME_CHARS.sub("_", name)
    return name or "upload"


def is_allowed_extension(filename: str) -> bool:
    """업로드 시점에 허용할 확장자인지 검사 (분석 대상 확장자 + zip)."""
    return Path(filename).suffix.lower() in ANALYSIS_TARGET_EXTENSIONS


def is_allowed_upload_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def extract_zip(zip_path: Path, extract_dir: Path) -> Path:
    """
    ZIP 파일을 지정된 디렉토리에 안전하게 추출한다.
    - Zip Slip 방어 : 추출 전 각 항목의 최종 경로를 계산해 extract_dir 하위에 있는지 검증
    - Zip bomb 방어 : 지나치게 큰 압축 해제를 방어
    - 기존 디렉터리 삭제
    """
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
                    f"잠재적으로 위험한 zip 항목입니다. (path traversal 공격 가능성) : {member.filename}"
                )
        archive.extractall(extract_dir)
    return extract_dir


def collect_target_files(
    base_dir: Path, source_type: str = "direct_upload", root_container_name: str = ""
) -> list[AnalysisTargetFile]:
    """
    업로드(혹은 압축 해제)된 디렉터리를 재귀 탐색하여 분석 대상 파일 목록을 만든다.
    """
    targets = []
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
                    root_container_name=root_container_name,
                )
            )
    return targets


def process_uploads_and_collect(save_dir: Path) -> list[AnalysisTargetFile]:
    """
    save_dir(업로드 원본이 저장된 디렉터리)를 순회하며 분석 대상을 수집.
    """
    all_targets: list[AnalysisTargetFile] = []
    extracted_root = save_dir.parent / "extracted"
    ensure_dir(extracted_root)

    for path in save_dir.glob("*"):
        if not path.is_file():
            continue
        ext = path.suffix.lstrip(".").lower()

        if ext == "zip":
            extract_dir = extracted_root / path.stem
            try:
                extract_zip(path, extract_dir)
            except (zipfile.BadZipFile, ValueError) as e:
                print(f"[WARN] ZIP 파일 처리 실패: {path.name}:{e}")
                continue
            all_targets.extend(
                collect_target_files(
                    extract_dir, source_type="zip_entry", root_container_name=path.stem
                )
            )
        elif f".{ext}" in ANALYSIS_TARGET_EXTENSIONS:
            all_targets.append(
                AnalysisTargetFile(
                    source_type="direct_upload",
                    original_name=path.name,
                    saved_path=str(path.resolve()),
                    relative_path=path.name,
                    extension=ext,
                    size=path.stat().st_size,
                    root_container_name=path.name,
                )
            )
    return all_targets
