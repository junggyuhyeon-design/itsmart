from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from config import Settings
from database.history_repository import get_project_upload_info

logger = logging.getLogger(__name__)


# grep 스캔 대상 확장자 (file_utils.ANALYSIS_TARGET_EXTENSIONS 기반 + 템플릿/설정 보강)
GREP_EXTENSIONS = {
    ".py", ".java", ".js", ".ts", ".sql", ".sh", ".txt", ".md",
    ".json", ".xml", ".yml", ".yaml", ".ini", ".toml", ".html", ".htm", ".css",
    ".jsp", ".jspx", ".vue", ".properties",
}

# 동일 파일에서 수집할 최대 매칭 수 (무한 루프/과다 수집 방지)
_MAX_MATCHES_PER_FILE = 20
# 전체 프로젝트에서 수집할 최대 매칭 수
_MAX_TOTAL_MATCHES = 60


class ExactGrepService:
    """
    vector 검색 이전에 동작하는 리터럴(lexical) grep 서비스.

    edit 요청(edit_text_one / edit_text_all)에서 "변경 전 문자열(edit_source)"을
    프로젝트 실제 소스 파일에서 리터럴 검색한다.
    - 정규식이 아닌 단순 부분문자열(대소문자 무시) 매칭.
    - 결과는 vector hit 와 동일한 hit dict 형태로 변환되어 가장 우선순위가 높은 evidence 로 병합된다.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.extract_dir = Path(settings.extract_dir)

        logger.info(
            "[exact_grep_service.py][__init__][초기화 완료] extract_dir=%s",
            str(self.extract_dir),
        )

    # ─────────────────────────────────────────────────────────────
    # 소스 루트 해석
    # ─────────────────────────────────────────────────────────────
    def _resolve_scan_targets(self, project_id: str, project_name: str | None) -> list[tuple[Path, Path]]:
        """
        project_id 기준으로 grep 대상 (파일/디렉토리, base_root) 튜플 목록을 반환.

        - zip 업로드: /data/extracted/<user_id>/<project_name> 디렉토리
        - 단일 파일 업로드: uploaded_files.saved_path 파일 자체
        base_root 는 relative_path 계산의 기준이 되는 루트다.
        """
        info = get_project_upload_info(project_id)
        if not info:
            logger.warning(
                "[exact_grep_service.py][_resolve_scan_targets][업로드 정보 미발견] project_id=%s",
                project_id,
            )
            return []

        user_id = info.get("user_id")
        pname = info.get("project_name") or project_name
        saved_path_raw = info.get("saved_path")

        targets: list[tuple[Path, Path]] = []

        # 1) zip 추출 디렉토리
        if user_id and pname:
            extract_root = self.extract_dir / user_id / pname
            if extract_root.exists() and extract_root.is_dir():
                targets.append((extract_root, extract_root))
                logger.info(
                    "[exact_grep_service.py][_resolve_scan_targets][zip 추출 디렉토리 추가] root=%s",
                    str(extract_root),
                )

        # 2) 단일 파일 업로드 (saved_path 가 허용 확장자 파일인 경우)
        if saved_path_raw:
            saved_path = Path(saved_path_raw)
            if saved_path.exists() and saved_path.is_file() and saved_path.suffix.lower() in GREP_EXTENSIONS:
                # base_root = 파일이 있는 디렉토리, relative_path = 파일명
                targets.append((saved_path, saved_path.parent))
                logger.info(
                    "[exact_grep_service.py][_resolve_scan_targets][단일 파일 추가] file=%s",
                    str(saved_path),
                )

        if not targets:
            logger.warning(
                "[exact_grep_service.py][_resolve_scan_targets][스캔 대상 없음] project_id=%s project_name=%s saved_path=%s",
                project_id,
                pname,
                saved_path_raw,
            )

        return targets

    # ─────────────────────────────────────────────────────────────
    # 파일 단위 grep
    # ─────────────────────────────────────────────────────────────
    def _grep_file(
            self,
            file_path: Path,
            base_root: Path,
            needle_norm: str,
            needle: str,
            results: list[dict[str, Any]],
            remaining: int,
    ) -> int:
        """
        단일 파일에서 needle(소문자 기준)을 라인 단위로 검색.
        results 에 hit dict 를 append 하고, 이번 호출에서 수집된 건수를 반환.
        """
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as error:
            logger.warning(
                "[exact_grep_service.py][_grep_file][파일 읽기 실패] file=%s error=%s",
                str(file_path),
                error,
            )
            return 0

        lines = text.splitlines()
        collected = 0

        try:
            relative_path = str(file_path.relative_to(base_root)).replace("\\", "/")
        except ValueError:
            relative_path = file_path.name

        file_name = file_path.name
        extension = file_path.suffix.lower().lstrip(".")
        saved_path = str(file_path)

        for idx, line in enumerate(lines):
            if needle_norm not in line.lower():
                continue

            line_no = idx + 1
            snippet_start = max(0, idx - 2)
            snippet_end = min(len(lines), idx + 3)
            snippet_lines = lines[snippet_start:snippet_end]

            # snippet 앞에 라인 번호 표기 (LLM 이 줄 위치를 인식하기 쉽게)
            numbered = []
            for offset, snippet_line in enumerate(snippet_lines, start=snippet_start + 1):
                marker = ">>" if offset == line_no else "  "
                numbered.append(f"{marker} L{offset}: {snippet_line}")
            snippet = "\n".join(numbered)

            results.append(
                {
                    "text": snippet,
                    "relative_path": relative_path,
                    "file_name": file_name,
                    "extension": extension,
                    "saved_path": saved_path,
                    "file_path": saved_path,
                    # chunk_index 는 라인번호 기반 → (relative_path, chunk_index) dedupe 가
                    # 동일 파일의 서로 다른 라인을 각각 보존하도록 함 (edit_text_all 대응)
                    "chunk_index": line_no,
                    "start_line": line_no,
                    "end_line": line_no,
                    "matched_line": line_no,
                    "line_text": line,
                    "match_type": "exact_grep",
                    "score": 1.0,
                    "source_type": "grep",
                    "chunk_type": "grep",
                }
            )
            collected += 1

            if len(results) >= remaining:
                break
            if collected >= _MAX_MATCHES_PER_FILE:
                break

        return collected

    # ─────────────────────────────────────────────────────────────
    # 메인 진입점
    # ─────────────────────────────────────────────────────────────
    def search(
            self,
            needle: str,
            *,
            project_id: str | None,
            project_name: str | None = None,
            max_matches: int = _MAX_TOTAL_MATCHES,
    ) -> list[dict[str, Any]]:
        """
        needle(변경 전 문자열)을 프로젝트 소스에서 리터럴 grep.

        반환값: hit dict 리스트(vector hit 과 동일 구조). match_type="exact_grep".
        """
        needle = (needle or "").strip()
        if not needle:
            logger.info("[exact_grep_service.py][search][needle 비어있음] skip")
            return []
        if not project_id:
            logger.info("[exact_grep_service.py][search][project_id 없음] skip")
            return []

        logger.info(
            "[exact_grep_service.py][search][grep 시작] project_id=%s project_name=%s needle_len=%d needle=%s",
            project_id,
            project_name,
            len(needle),
            needle[:120],
        )

        targets = self._resolve_scan_targets(project_id, project_name)
        if not targets:
            return []

        needle_norm = needle.lower()
        results: list[dict[str, Any]] = []
        cap = min(max_matches, _MAX_TOTAL_MATCHES)

        for file_root, base_root in targets:
            if len(results) >= cap:
                break

            if file_root.is_file():
                self._grep_file(file_root, base_root, needle_norm, needle, results, cap - len(results))
            elif file_root.is_dir():
                for path in file_root.rglob("*"):
                    if not path.is_file():
                        continue
                    if path.suffix.lower() not in GREP_EXTENSIONS:
                        continue
                    if len(results) >= cap:
                        break
                    self._grep_file(path, base_root, needle_norm, needle, results, cap - len(results))

        logger.info(
            "[exact_grep_service.py][search][grep 완료] project_id=%s match_count=%d",
            project_id,
            len(results),
        )
        return results
