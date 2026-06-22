"""
SQLite 데이터베이스 초기화 모듈

테이블 구조:
- users         : UUID 기반 사용자 등록 테이블
- uploaded_files: 모든 사용자 공통 업로드 파일 테이블 (공유 소스)
- chat_history  : 사용자별 질문/답변 히스토리 테이블
"""
import sqlite3
from pathlib import Path

DB_PATH = Path("/data/db/codemind.db")


def get_connection() -> sqlite3.Connection:
    """SQLite 커넥션 반환 (WAL 모드, Row 팩토리 적용)"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """앱 시작 시 1회 호출 — 테이블이 없으면 생성한다."""
    with get_connection() as conn:
        conn.executescript("""
            PRAGMA journal_mode=WAL;

            -- 사용자 테이블: UUID를 PK로 사용
            CREATE TABLE IF NOT EXISTS users (
                user_id    TEXT PRIMARY KEY,
                created_at DATETIME DEFAULT (datetime('now','localtime'))
            );

            -- 공통 업로드 파일 테이블: 모든 사용자가 공유
            CREATE TABLE IF NOT EXISTS uploaded_files (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT    NOT NULL,
                saved_path  TEXT    NOT NULL,
                uploaded_at DATETIME DEFAULT (datetime('now','localtime'))
            );

            -- 사용자별 채팅 히스토리
            CREATE TABLE IF NOT EXISTS chat_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT    NOT NULL REFERENCES users(user_id),
                question   TEXT    NOT NULL,
                answer     TEXT    NOT NULL,
                created_at DATETIME DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_chat_user
                ON chat_history(user_id, created_at DESC);
        """)
