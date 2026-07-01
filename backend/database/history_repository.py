"""
사용자 및 채팅 히스토리 데이터 접근 계층 (Repository)
- 모든 함수는 독립적인 커넥션을 사용 (with 블록으로 자동 commit/rollback)
- user_id 는 호출 전 _require_user() 로 검증된다고 가정
"""
import logging
from typing import Any

from database.init_db import get_connection

logger = logging.getLogger(__name__)


# ── 사용자 ───────────────────────────────────────────────────────
def upsert_user(user_id: str) -> None:
    """사용자가 없으면 INSERT, 있으면 무시."""
    if not user_id or not user_id.strip():
        raise ValueError("user_id는 비어 있을 수 없습니다.")
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
                (user_id.strip(),),
            )
    except Exception:
        logger.exception("upsert_user 실패: user_id=%s", user_id)
        raise


# ── 채팅 히스토리 ────────────────────────────────────────────────
def save_history(user_id: str, question: str, answer: str) -> int:
    """질문/답변 저장 후 생성된 row id 반환."""
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO chat_history (user_id, question, answer) VALUES (?, ?, ?)",
                (user_id, question, answer),
            )
            return cur.lastrowid
    except Exception:
        logger.exception("save_history 실패: user_id=%s", user_id)
        raise


def get_history(user_id: str, limit: int) -> list[dict[str, Any]]:
    """특정 사용자의 최근 히스토리 반환 (최신순)."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, question, answer, created_at
                FROM chat_history
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]
    except Exception:
        logger.exception("get_history 실패: user_id=%s", user_id)
        return []


def delete_history(user_id: str) -> int:
    """특정 사용자의 히스토리 전체 삭제. 삭제된 건수 반환."""
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM chat_history WHERE user_id = ?", (user_id,)
            )
            return cur.rowcount
    except Exception:
        logger.exception("delete_history 실패: user_id=%s", user_id)
        raise


# ── 공통 업로드 파일 ─────────────────────────────────────────────
def save_uploaded_file(project_id: str, project_name: str, saved_path: str) -> str:
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO uploaded_files (project_id, project_name, saved_path) VALUES (?, ?, ?)",
                (project_id, project_name, saved_path),
            )
    except Exception:
        logger.exception("save_uploaded_file 실패: project_id=%s", project_id)
        raise


def get_all_projects() -> list[dict[str, Any]]:
    """전체 프로젝트 목록 반환."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT project_id, project_name, uploaded_at
                FROM uploaded_files
                ORDER BY uploaded_at DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]
    except Exception:
        logger.exception("get_all_projects 실패")
        return []


# ── file_index ───────────────────────────────────────────────────
def bulk_insert_file_index(files: list[dict[str, Any]]) -> int:
    """
    인덱싱된 파일 메타데이터를 file_index 테이블에 일괄 저장.
    동일 project_name + relative_path 조합은 IGNORE(중복 재인덱싱 허용).
    반환값: 실제 삽입된 행 수.
    """
    if not files:
        return 0
    rows = [
        (
            f["project_id"],
            f["project_name"],
            f["file_name"],
            f["relative_path"],
            f["extension"],
        )
        for f in files
    ]
    try:
        with get_connection() as conn:
            project_ids = list({r[0] for r in rows})
            for pid in project_ids:
                conn.execute("DELETE FROM file_index WHERE project_id = ?", (pid,))
            conn.executemany(
                """
                INSERT INTO file_index
                    (project_id, project_name, file_name, relative_path, extension)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            return len(rows)
    except Exception:
        logger.exception("bulk_insert_file_index 실패")
        raise


def get_file_index(
    project_id: str,
    extension: str | None = None,
) -> list[dict[str, Any]]:
    """
    특정 프로젝트의 파일 메타데이터 목록 반환.
    extension 지정 시 해당 확장자 파일만 반환 (예: 'xml', 'java').
    """
    try:
        with get_connection() as conn:
            if extension:
                rows = conn.execute(
                    """
                    SELECT file_name, relative_path, extension, indexed_at
                    FROM file_index
                    WHERE project_id = ? AND extension = ?
                    ORDER BY relative_path
                    """,
                    (project_id, extension.lower().lstrip(".")),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT file_name, relative_path, extension, indexed_at
                    FROM file_index
                    WHERE project_id = ?
                    ORDER BY extension, relative_path
                    """,
                    (project_id,),
                ).fetchall()
            return [dict(row) for row in rows]
    except Exception:
        logger.exception("get_file_index 실패: project_id=%s", project_id)
        return []


def get_file_index_summary(project_id: str) -> dict[str, Any]:
    """
    프로젝트 파일 구조 요약: 확장자별 파일 수 + 전체 목록.
    /ask 에서 소스코드 열거 질문에 컨텍스트로 주입할 때 사용.
    """
    try:
        with get_connection() as conn:
            # 확장자별 카운트
            ext_rows = conn.execute(
                """
                SELECT extension, COUNT(*) as cnt
                FROM file_index
                WHERE project_id = ?
                GROUP BY extension
                ORDER BY cnt DESC
                """,
                (project_id,),
            ).fetchall()

            # 전체 파일 목록 (relative_path 기준 정렬)
            file_rows = conn.execute(
                """
                SELECT file_name, relative_path, extension
                FROM file_index
                WHERE project_id = ?
                ORDER BY extension, relative_path
                """,
                (project_id,),
            ).fetchall()

        ext_summary = {row["extension"]: row["cnt"] for row in ext_rows}
        files = [dict(r) for r in file_rows]
        return {
            "total": len(files),
            "by_extension": ext_summary,
            "files": files,
        }
    except Exception:
        logger.exception("get_file_index_summary 실패: project_id=%s", project_id)
        return {"total": 0, "by_extension": {}, "files": []}
