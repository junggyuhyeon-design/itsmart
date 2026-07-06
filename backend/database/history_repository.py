from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from database.init_db import get_connection

logger = logging.getLogger(__name__)


def json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return "[]"


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def make_raw_text_preview(text: str | None, limit: int = 1000) -> str:
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalized[:limit]


def make_content_hash(text: str | None) -> str:
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def count_lines(text: str | None) -> int:
    if not text:
        return 0
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return 0
    return normalized.count("\n") + 1


def upsert_user(user_id: str) -> None:
    if not user_id or not user_id.strip():
        raise ValueError("user_id is required")
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id.strip(),))


def user_exists(user_id: str) -> bool:
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
            return row is not None
    except Exception:
        logger.exception("user_exists failed user_id=%s", user_id)
        return False


def save_history(user_id: str, question: str, answer: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO chat_history (user_id, question, answer) VALUES (?, ?, ?)",
            (user_id, question, answer),
        )
        return int(cur.lastrowid)


def get_history(user_id: str, limit: int) -> list[dict[str, Any]]:
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, question, answer, created_at
                FROM chat_history
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]
    except Exception:
        logger.exception("get_history failed user_id=%s", user_id)
        return []


def delete_history(user_id: str) -> int:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
        return int(cur.rowcount or 0)


def save_uploaded_file(project_id: str, project_name: str, saved_path: str) -> str:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO uploaded_files (project_id, project_name, saved_path)
            VALUES (?, ?, ?)
            """,
            (project_id, project_name, saved_path),
        )
    return project_id


def get_uploaded_files() -> list[dict[str, Any]]:
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT project_id, project_name, saved_path, uploaded_at
                FROM uploaded_files
                ORDER BY uploaded_at DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]
    except Exception:
        logger.exception("get_uploaded_files failed")
        return []


def get_uploaded_files_by_project_id(project_id: str) -> dict[str, Any] | None:
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT project_id, project_name, saved_path, uploaded_at
                FROM uploaded_files
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        logger.exception("get_uploaded_files_by_project_id failed project_id=%s", project_id)
        return None


def get_all_projects() -> list[dict[str, Any]]:
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
        logger.exception("get_all_projects failed")
        return []


def bulk_insert_file_index(files: list[dict[str, Any]]) -> int:
    if not files:
        return 0

    rows = []
    project_ids = set()

    for file in files:
        project_id = file.get("project_id", "")
        project_name = file.get("project_name", "")
        file_name = file.get("file_name", "")
        relative_path = file.get("relative_path", "")
        extension = (file.get("extension", "") or "").lower().lstrip(".")
        file_size = int(file.get("file_size", 0) or 0)

        rows.append((project_id, project_name, file_name, relative_path, extension, file_size))
        if project_id:
            project_ids.add(project_id)

    with get_connection() as conn:
        for project_id in project_ids:
            conn.execute("DELETE FROM file_index WHERE project_id = ?", (project_id,))
        conn.executemany(
            """
            INSERT INTO file_index (
                project_id, project_name, file_name, relative_path, extension, file_size
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def get_file_index(project_id: str, extension: str | None = None) -> list[dict[str, Any]]:
    try:
        with get_connection() as conn:
            if extension:
                rows = conn.execute(
                    """
                    SELECT file_name, relative_path, extension, file_size, indexed_at
                    FROM file_index
                    WHERE project_id = ? AND extension = ?
                    ORDER BY relative_path
                    """,
                    (project_id, extension.lower().lstrip(".")),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT file_name, relative_path, extension, file_size, indexed_at
                    FROM file_index
                    WHERE project_id = ?
                    ORDER BY extension, relative_path
                    """,
                    (project_id,),
                ).fetchall()
            return [dict(row) for row in rows]
    except Exception:
        logger.exception("get_file_index failed project_id=%s", project_id)
        return []


def get_file_index_summary(project_id: str) -> dict[str, Any]:
    try:
        with get_connection() as conn:
            ext_rows = conn.execute(
                """
                SELECT extension, COUNT(*) AS cnt
                FROM file_index
                WHERE project_id = ?
                GROUP BY extension
                ORDER BY cnt DESC, extension ASC
                """,
                (project_id,),
            ).fetchall()
            file_rows = conn.execute(
                """
                SELECT file_name, relative_path, extension
                FROM file_index
                WHERE project_id = ?
                ORDER BY extension, relative_path
                """,
                (project_id,),
            ).fetchall()

        return {
            "total": len(file_rows),
            "by_extension": {row["extension"]: row["cnt"] for row in ext_rows},
            "files": [dict(row) for row in file_rows],
        }
    except Exception:
        logger.exception("get_file_index_summary failed project_id=%s", project_id)
        return {"total": 0, "by_extension": {}, "files": []}


def insert_code_elements(project_id: str, project_name: str, elements: list[dict[str, Any]]) -> int:
    if not elements:
        return 0

    rows = []
    paths = []

    for element in elements:
        raw_text = element.get("raw_text", "") or ""
        relative_path = element.get("relative_path", "") or ""
        paths.append(relative_path)

        rows.append(
            (
                project_id,
                project_name,
                element.get("file_name", ""),
                relative_path,
                element.get("extension", ""),
                element.get("layer_type", ""),
                element.get("content_type", ""),
                element.get("class_name", ""),
                element.get("package", ""),
                json_dumps(element.get("table_names", [])),
                json_dumps(element.get("imports", [])),
                json_dumps(element.get("methods", [])),
                json_dumps(element.get("xml_statements", [])),
                make_raw_text_preview(raw_text),
                make_content_hash(raw_text),
                count_lines(raw_text),
            )
        )

    with get_connection() as conn:
        for relative_path in paths:
            conn.execute(
                "DELETE FROM code_elements WHERE project_id = ? AND relative_path = ?",
                (project_id, relative_path),
            )
        conn.executemany(
            """
            INSERT INTO code_elements (
                project_id, project_name, file_name, relative_path, extension,
                layer_type, content_type, class_name, package,
                table_names_json, imports_json, methods_json, xml_statements_json,
                raw_text_preview, content_hash, line_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def get_code_elements(project_id: str, layer_type: str | None = None) -> list[dict[str, Any]]:
    try:
        with get_connection() as conn:
            if layer_type:
                rows = conn.execute(
                    "SELECT * FROM code_elements WHERE project_id = ? AND layer_type = ? ORDER BY relative_path",
                    (project_id, layer_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM code_elements WHERE project_id = ? ORDER BY relative_path",
                    (project_id,),
                ).fetchall()

        result = []
        for row in rows:
            item = dict(row)
            item["table_names"] = json_loads(item.pop("table_names_json", "[]"), [])
            item["imports"] = json_loads(item.pop("imports_json", "[]"), [])
            item["methods"] = json_loads(item.pop("methods_json", "[]"), [])
            item["xml_statements"] = json_loads(item.pop("xml_statements_json", "[]"), [])
            result.append(item)
        return result
    except Exception:
        logger.exception("get_code_elements failed project_id=%s", project_id)
        return []


def find_code_elements_by_name(project_id: str, keyword: str) -> list[dict[str, Any]]:
    try:
        like_keyword = f"%{keyword}%"
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM code_elements
                WHERE project_id = ?
                  AND (
                    file_name LIKE ?
                    OR relative_path LIKE ?
                    OR class_name LIKE ?
                    OR package LIKE ?
                    OR raw_text_preview LIKE ?
                    OR content_hash LIKE ?
                  )
                ORDER BY relative_path
                LIMIT 100
                """,
                (
                    project_id,
                    like_keyword,
                    like_keyword,
                    like_keyword,
                    like_keyword,
                    like_keyword,
                    like_keyword,
                ),
            ).fetchall()

        result = []
        for row in rows:
            item = dict(row)
            item["table_names"] = json_loads(item.pop("table_names_json", "[]"), [])
            item["imports"] = json_loads(item.pop("imports_json", "[]"), [])
            item["methods"] = json_loads(item.pop("methods_json", "[]"), [])
            item["xml_statements"] = json_loads(item.pop("xml_statements_json", "[]"), [])
            result.append(item)
        return result
    except Exception:
        logger.exception("find_code_elements_by_name failed project_id=%s keyword=%s", project_id, keyword)
        return []


def ensure_turn_entities_table() -> None:
    with get_connection() as conn:
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


def save_turn_entities(user_id: str, entities: list[dict[str, Any]], project_id: str | None = None) -> int:
    if not entities:
        return 0

    try:
        ensure_turn_entities_table()
        rows = []
        for entity in entities:
            entity_name = (entity.get("entity_name", "") or "").strip()
            entity_type = (entity.get("entity_type", "") or "").strip()
            if entity_name:
                rows.append((user_id, entity_name, entity_type, project_id or ""))

        if not rows:
            return 0

        with get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO turn_entities (user_id, entity_name, entity_type, project_id)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)
    except Exception:
        logger.exception("save_turn_entities failed user_id=%s", user_id)
        return 0


def get_recent_entities(user_id: str, limit: int = 20, project_id: str | None = None) -> list[dict[str, Any]]:
    try:
        ensure_turn_entities_table()
        with get_connection() as conn:
            if project_id:
                rows = conn.execute(
                    """
                    SELECT entity_name, entity_type, project_id, created_at
                    FROM turn_entities
                    WHERE user_id = ? AND project_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (user_id, project_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT entity_name, entity_type, project_id, created_at
                    FROM turn_entities
                    WHERE user_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        logger.exception("get_recent_entities failed user_id=%s", user_id)
        return []


def init_index_jobs_table() -> None:
    required_columns = {
        "job_id": "TEXT PRIMARY KEY",
        "user_id": "TEXT NOT NULL",
        "project_id": "TEXT",
        "project_name": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'queued'",
        "total_targets": "INTEGER NOT NULL DEFAULT 0",
        "processed_targets": "INTEGER NOT NULL DEFAULT 0",
        "success_count": "INTEGER NOT NULL DEFAULT 0",
        "failed_count": "INTEGER NOT NULL DEFAULT 0",
        "total_chunks": "INTEGER NOT NULL DEFAULT 0",
        "message": "TEXT DEFAULT ''",
        "error": "TEXT DEFAULT ''",
        "logs_json": "TEXT DEFAULT '[]'",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "finished_at": "TIMESTAMP",
    }

    with get_connection() as conn:
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP
            )
            """
        )

        rows = conn.execute("PRAGMA table_info(index_jobs)").fetchall()
        existing_columns = {row["name"] for row in rows}

        for column_name, column_def in required_columns.items():
            if column_name not in existing_columns and "PRIMARY KEY" not in column_def:
                conn.execute(f"ALTER TABLE index_jobs ADD COLUMN {column_name} {column_def}")


def create_index_job(
        job_id: str,
        user_id: str,
        project_id: str | None,
        project_name: str | None,
        total_targets: int,
        message: str = "",
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO index_jobs (
                job_id, user_id, project_id, project_name, status,
                total_targets, processed_targets, success_count, failed_count,
                total_chunks, message, error, logs_json
            ) VALUES (?, ?, ?, ?, 'queued', ?, 0, 0, 0, 0, ?, '', '[]')
            """,
            (job_id, user_id, project_id, project_name, total_targets, message),
        )


def update_index_job(
        job_id: str,
        *,
        status: str | None = None,
        processed_targets: int | None = None,
        success_count: int | None = None,
        failed_count: int | None = None,
        total_chunks: int | None = None,
        message: str | None = None,
        error: str | None = None,
        logs: list[str] | None = None,
        finished: bool = False,
) -> None:
    fields: list[str] = []
    values: list[Any] = []

    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if processed_targets is not None:
        fields.append("processed_targets = ?")
        values.append(processed_targets)
    if success_count is not None:
        fields.append("success_count = ?")
        values.append(success_count)
    if failed_count is not None:
        fields.append("failed_count = ?")
        values.append(failed_count)
    if total_chunks is not None:
        fields.append("total_chunks = ?")
        values.append(total_chunks)
    if message is not None:
        fields.append("message = ?")
        values.append(message)
    if error is not None:
        fields.append("error = ?")
        values.append(error)
    if logs is not None:
        fields.append("logs_json = ?")
        values.append(json.dumps(logs, ensure_ascii=False))

    if finished:
        fields.append("finished_at = CURRENT_TIMESTAMP")
    fields.append("updated_at = CURRENT_TIMESTAMP")

    if not fields:
        return

    values.append(job_id)

    with get_connection() as conn:
        conn.execute(
            f"UPDATE index_jobs SET {', '.join(fields)} WHERE job_id = ?",
            tuple(values),
        )


def get_index_job(job_id: str, user_id: str) -> dict[str, Any] | None:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM index_jobs WHERE job_id = ? AND user_id = ?",
                (job_id, user_id),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["logs"] = json_loads(item.pop("logs_json", "[]"), [])
        return item
    except Exception:
        logger.exception("get_index_job failed job_id=%s", job_id)
        return None


def list_index_jobs(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM index_jobs
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

        result = []
        for row in rows:
            item = dict(row)
            item["logs"] = json_loads(item.pop("logs_json", "[]"), [])
            result.append(item)
        return result
    except Exception:
        logger.exception("list_index_jobs failed user_id=%s", user_id)
        return []


def purge_all_runtime_data() -> dict[str, int]:
    ensure_turn_entities_table()
    with get_connection() as conn:
        chat_history_deleted = conn.execute("DELETE FROM chat_history").rowcount
        uploaded_files_deleted = conn.execute("DELETE FROM uploaded_files").rowcount
        file_index_deleted = conn.execute("DELETE FROM file_index").rowcount
        code_elements_deleted = conn.execute("DELETE FROM code_elements").rowcount
        turn_entities_deleted = conn.execute("DELETE FROM turn_entities").rowcount
        index_jobs_deleted = conn.execute("DELETE FROM index_jobs").rowcount

    return {
        "chat_history_deleted": int(chat_history_deleted or 0),
        "uploaded_files_deleted": int(uploaded_files_deleted or 0),
        "file_index_deleted": int(file_index_deleted or 0),
        "code_elements_deleted": int(code_elements_deleted or 0),
        "turn_entities_deleted": int(turn_entities_deleted or 0),
        "index_jobs_deleted": int(index_jobs_deleted or 0),
    }


def list_db_tables() -> list[dict[str, Any]]:
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()

            result = []
            for row in rows:
                table_name = row["name"]
                count_row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table_name}").fetchone()
                result.append(
                    {
                        "table_name": table_name,
                        "row_count": int(count_row["cnt"]) if count_row else 0,
                    }
                )
            return result
    except Exception:
        logger.exception("list_db_tables failed")
        return []


def get_table_rows_for_admin(table_name: str, limit: int = 200) -> list[dict[str, Any]]:
    if not table_name or not table_name.strip():
        raise ValueError("table_name is required")

    safe_table_name = table_name.strip()

    with get_connection() as conn:
        allowed_tables = {
            row["name"]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            ).fetchall()
        }

        if safe_table_name not in allowed_tables:
            raise ValueError(f"unknown table: {safe_table_name}")

        safe_limit = max(1, min(int(limit or 200), 1000))
        rows = conn.execute(
            f"SELECT * FROM {safe_table_name} ORDER BY 1 DESC LIMIT ?",
            (safe_limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_relationship_edges(project_id: str, relation: str | None = None) -> list[dict[str, Any]]:
    if not project_id or not project_id.strip():
        return []

    try:
        with get_connection() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    """
                ).fetchall()
            }

            if "relationship_edges" not in tables:
                return []

            if relation:
                rows = conn.execute(
                    """
                    SELECT src_name, dst_name, relation, project_id, created_at
                    FROM relationship_edges
                    WHERE project_id = ? AND relation = ?
                    ORDER BY id ASC
                    """,
                    (project_id, relation),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT src_name, dst_name, relation, project_id, created_at
                    FROM relationship_edges
                    WHERE project_id = ?
                    ORDER BY id ASC
                    """,
                    (project_id,),
                ).fetchall()

            return [dict(row) for row in rows]
    except Exception:
        logger.exception("get_relationship_edges failed project_id=%s relation=%s", project_id, relation)
        return []