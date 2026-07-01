"""
SQLite 데이터베이스 초기화 모듈

테이블 구조:
- users         : UUID 기반 사용자 등록 테이블
- uploaded_files: 프로젝트(ZIP) 단위 등록 테이블
- file_index    : ZIP 내 개별 파일 메타데이터 (uploaded_files 1:N)
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
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(DB_PATH),
        check_same_thread=False,
        timeout=10,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """
    앱 시작 시 1회 호출.
    - 테이블이 없으면 생성
    """
    try:
        with get_connection() as conn:
            conn.executescript("""
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS users (
                    user_id    TEXT PRIMARY KEY,
                    created_at DATETIME DEFAULT (datetime('now', 'localtime'))
                );

                CREATE TABLE IF NOT EXISTS uploaded_files (
                    project_id   TEXT     PRIMARY KEY,
                    project_name TEXT     NOT NULL,
                    saved_path   TEXT     NOT NULL,
                    uploaded_at  DATETIME DEFAULT (datetime('now', 'localtime'))
                );

                -- ZIP 내 개별 파일 메타데이터 (uploaded_files 와 1:N)
                CREATE TABLE IF NOT EXISTS file_index (
                    project_id    TEXT     NOT NULL
                                           REFERENCES uploaded_files(project_id)
                                           ON DELETE CASCADE,
                    project_name  TEXT     NOT NULL,
                    file_name     TEXT     NOT NULL,
                    relative_path TEXT     NOT NULL,
                    extension     TEXT     NOT NULL,
                    indexed_at    DATETIME DEFAULT (datetime('now', 'localtime'))
                );

                CREATE INDEX IF NOT EXISTS idx_file_project
                    ON file_index(project_id, extension);

                CREATE TABLE IF NOT EXISTS chat_history (
                    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
                    user_id    TEXT     NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    question   TEXT     NOT NULL,
                    answer     TEXT     NOT NULL,
                    created_at DATETIME DEFAULT (datetime('now', 'localtime'))
                );

                CREATE INDEX IF NOT EXISTS idx_chat_user
                    ON chat_history(user_id, created_at DESC);
            """)
        logger.info("Database initialized: %s", DB_PATH)
    except Exception:
        logger.exception("Database initialization failed")
        raise
