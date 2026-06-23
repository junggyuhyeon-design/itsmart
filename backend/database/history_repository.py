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


def user_exists(user_id: str) -> bool:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return row is not None
    except Exception:
        logger.exception("user_exists 실패: user_id=%s", user_id)
        return False


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


def get_history(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """특정 사용자의 최근 히스토리 반환 (최신순). limit 은 1~200 으로 제한."""
    limit = max(1, min(limit, 200))
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

def save_uploaded_file(filename: str, saved_path: str) -> int:
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO uploaded_files (filename, saved_path) VALUES (?, ?)",
                (filename, saved_path),
            )
            return cur.lastrowid
    except Exception:
        logger.exception("save_uploaded_file 실패: filename=%s", filename)
        raise


def get_uploaded_files() -> list[dict[str, Any]]:
    """전체 업로드 파일 목록 반환 (최신순)."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, filename, saved_path, uploaded_at
                FROM uploaded_files
                ORDER BY uploaded_at DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]
    except Exception:
        logger.exception("get_uploaded_files 실패")
        return []
