"""
SQLite 데이터베이스 초기화 모듈

테이블 구조:
- users         : UUID 기반 사용자 등록 테이블
- uploaded_files: 모든 사용자 공통 업로드 파일 테이블 (공유 소스)
- chat_history  : 사용자별 질문/답변 히스토리 테이블
"""
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("/data/db/codemind.db")


def get_connection() -> sqlite3.Connection:
    """
    SQLite 커넥션 반환.
    - check_same_thread=False : FastAPI/uvicorn 멀티스레드 환경에서 안전하게 사용
    - WAL 모드는 init_db() 에서 설정하므로 여기서는 PRAGMA 미적용
    - timeout=10 : 다른 스레드 쓰기 락 대기 최대 10초
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(DB_PATH),
        check_same_thread=False,
        timeout=10,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """앱 시작 시 1회 호출 — 테이블이 없으면 생성한다."""
    try:
        with get_connection() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS users (
                    user_id    TEXT PRIMARY KEY,
                    created_at DATETIME DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS uploaded_files (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename    TEXT    NOT NULL,
                    saved_path  TEXT    NOT NULL,
                    uploaded_at DATETIME DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS chat_history (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    TEXT    NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    question   TEXT    NOT NULL,
                    answer     TEXT    NOT NULL,
                    created_at DATETIME DEFAULT (datetime('now','localtime'))
                );

                CREATE INDEX IF NOT EXISTS idx_chat_user
                    ON chat_history(user_id, created_at DESC);
            """)
        logger.info("Database initialized: %s", DB_PATH)
    except Exception:
        logger.exception("Database initialization failed")
        raise
