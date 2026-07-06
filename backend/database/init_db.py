from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def resolve_db_path() -> Path:
    raw = (
            os.environ.get("SQLITE_DB_PATH")
            or os.environ.get("SQLITEDBPATH")
            or "data/db/app.db"
    ).strip()
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


DB_PATH = resolve_db_path()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_history_user_created
            ON chat_history(user_id, created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS uploaded_files (
                project_id TEXT PRIMARY KEY,
                project_name TEXT NOT NULL,
                saved_path TEXT NOT NULL,
                uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS file_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                project_name TEXT NOT NULL,
                file_name TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                extension TEXT NOT NULL,
                file_size INTEGER DEFAULT 0,
                indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_file_index_project_path
            ON file_index(project_id, relative_path)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_file_index_project_ext
            ON file_index(project_id, extension)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS code_elements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                project_name TEXT,
                file_name TEXT,
                relative_path TEXT,
                extension TEXT,
                layer_type TEXT,
                content_type TEXT,
                class_name TEXT,
                package TEXT,
                table_names_json TEXT DEFAULT '[]',
                imports_json TEXT DEFAULT '[]',
                methods_json TEXT DEFAULT '[]',
                xml_statements_json TEXT DEFAULT '[]',
                raw_text_preview TEXT DEFAULT '',
                content_hash TEXT DEFAULT '',
                line_count INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_code_elements_project_path
            ON code_elements(project_id, relative_path)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_code_elements_project
            ON code_elements(project_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_code_elements_layer
            ON code_elements(project_id, layer_type)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS turn_entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                entity_name TEXT NOT NULL,
                entity_type TEXT DEFAULT '',
                project_id TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_turn_entities_user_created
            ON turn_entities(user_id, created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS index_jobs (
                job_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                project_id TEXT,
                project_name TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                total_targets INTEGER NOT NULL DEFAULT 0,
                processed_targets INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                total_chunks INTEGER NOT NULL DEFAULT 0,
                message TEXT DEFAULT '',
                error TEXT DEFAULT '',
                logs_json TEXT DEFAULT '[]',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                finished_at DATETIME
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_index_jobs_user_created
            ON index_jobs(user_id, created_at DESC)
            """
        )
        conn.commit()