from __future__ import annotations

import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

ANALYSIS_TARGET_EXTENSIONS = (
    # 주요 소스코드 확장자
    ".py",    # Python
    ".java",  # Java
    ".js",    # JavaScript
    ".ts",    # TypeScript
    # 설정/데이터/스크립트 관련
    ".sql",   # SQL
    ".sh",    # Shell/Bash
    ".txt",   # Markup/Docs
    ".md",    # Markup/Docs
    ".json",  # Config
    ".xml",   # Config
    ".yml",   # Config
    ".yaml",  # Config
    ".ini",   # Config
    ".toml",  # Config
    ".html",  # HTML/CSS
    ".htm",   # HTML/CSS
    ".css",   # HTML/CSS
)
ALLOWED_EXTENSIONS = ANALYSIS_TARGET_EXTENSIONS + (".zip",)
MAX_ZIP_UNCOMPRESSED_SIZE = 1024 * 1024 * 1024 * 2  # 2GB
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


# 확인 완료.
@dataclass(frozen=True)
class AnalysisTargetFile:
    original_name: str
    saved_path: str
    relative_path: str
    extension: str
    project_id: str = ""
    project_name: str = ""


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


def is_allowed_upload_extension(filename: str) -> bool:
    """확장자 허용 여부"""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS

def extract_zip(zip_path: Path, extract_dir: Path) -> Path:
    """
    ZIP 파일을 지정된 디렉토리에 안전하게 추출한다.
    - 기존 디렉터리 삭제
    - 1. 압축 해제 후 예상 용량 계산
    - 2. Zip bomb 방어 : 지나치게 큰 압축 해제를 방어
    - 3. Zip Slip 방어 : 추출 전 각 항목의 최종 경로를 계산해 extract_dir 하위에 있는지 검증
    - 4. Zip 파일 지정경로에 압축 해제
    """
    if extract_dir.exists():
        shutil.rmtree(extract_dir) # 재귀적으로 파일을 삭제
    ensure_dir(extract_dir)        # 폴더 재 생성
    extract_dir_resolved = extract_dir.resolve()

    with zipfile.ZipFile(zip_path, "r") as archive:
        # 1. 압축 해제 후 예상 용량 계산
        total_size = sum(info.file_size for info in archive.infolist())
        if total_size > MAX_ZIP_UNCOMPRESSED_SIZE:
            raise ValueError(
                f"'{zip_path.name}' 압축 해제 예상 용량({total_size} bytes)이 "
                f"허용치({MAX_ZIP_UNCOMPRESSED_SIZE} bytes)를 초과합니다."
            )
        # 2. 각 파일(member)의 경로 검증 (보안 체크)
        # 해당 경로가 extract_dir 내부가 아니라 외부라면 잠재적 공격으로 인식.
        for member in archive.infolist():
            target_path = (extract_dir / member.filename).resolve()
            if (
                extract_dir_resolved != target_path
                and extract_dir_resolved not in target_path.parents
            ):
                raise ValueError(
                    f"잠재적으로 위험한 zip 항목입니다. (path traversal 공격 가능성) : {member.filename}"
                )
        # 3. 파일을 extract_dir에 압축 해제 (Zipfile 라이브러리)
        archive.extractall(extract_dir)


def collect_target_files(
    base_dir: Path, project_id: str, project_name: str
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
    """
    /extracted 경로에 압축해제 후 전체 파일 정보 반환
    """
    all_targets: list[AnalysisTargetFile] = []
    extracted_root = save_dir.parent / "extracted"
    ensure_dir(extracted_root)

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
            extract_dir = extracted_root / path.stem
            try:
                extract_zip(path, extract_dir) # zip 압축해제
            except (zipfile.BadZipFile, ValueError) as e:
                print(f"[WARN] ZIP 파일 처리 실패: {path.name}:{e}")
                continue
            all_targets.extend(
                collect_target_files( # 압축해제된 파일 목록 생성
                    extract_dir,                     # 압축해제 경로
                    project_id=project_id,           # 프로젝트아이디
                    project_name=project_name,       # 프로젝트명
                )
            )
    return all_targets
