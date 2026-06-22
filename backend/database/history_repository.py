"""
사용자 및 채팅 히스토리 데이터 접근 계층 (Repository)
"""
from typing import List, Dict, Any
from database.init_db import get_connection


# ────────────────────────────────────────────
# 사용자 관련
# ────────────────────────────────────────────

def upsert_user(user_id: str) -> None:
    """사용자가 없으면 INSERT, 있으면 무시 (IGNORE)"""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (user_id,)
        )


def user_exists(user_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row is not None


# ────────────────────────────────────────────
# 채팅 히스토리 관련
# ────────────────────────────────────────────

def save_history(user_id: str, question: str, answer: str) -> int:
    """질문/답변 저장 후 생성된 row id 반환"""
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO chat_history (user_id, question, answer)
            VALUES (?, ?, ?)
            """,
            (user_id, question, answer)
        )
        return cur.lastrowid


def get_history(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """특정 사용자의 최근 히스토리 반환 (최신순)"""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, question, answer, created_at
            FROM chat_history
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit)
        ).fetchall()
        return [dict(row) for row in rows]


def delete_history(user_id: str) -> int:
    """특정 사용자의 히스토리 전체 삭제, 삭제된 건수 반환"""
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM chat_history WHERE user_id = ?",
            (user_id,)
        )
        return cur.rowcount


# ────────────────────────────────────────────
# 공통 업로드 파일 관련
# ────────────────────────────────────────────

def save_uploaded_file(filename: str, saved_path: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO uploaded_files (filename, saved_path) VALUES (?, ?)",
            (filename, saved_path)
        )
        return cur.lastrowid


def get_uploaded_files() -> List[Dict[str, Any]]:
    """전체 업로드 파일 목록 반환 (최신순)"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, filename, saved_path, uploaded_at FROM uploaded_files ORDER BY uploaded_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
