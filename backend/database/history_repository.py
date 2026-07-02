from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from database.init_db import get_connection

logger = logging.getLogger(__name__)


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return "[]"


def _json_loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _make_raw_text_preview(text: str | None, limit: int = 1000) -> str:
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit]


def _make_content_hash(text: str | None) -> str:
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _count_lines(text: str | None) -> int:
    if not text:
        return 0
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return 0
    return normalized.count("\n") + 1


def upsert_user(user_id: str) -> None:
    if not user_id or not user_id.strip():
        raise ValueError("user_id is required")

    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO users (user_id)
                VALUES (?)
                """,
                (user_id.strip(),),
            )
    except Exception:
        logger.exception("upsert_user failed: user_id=%s", user_id)
        raise


def user_exists(user_id: str) -> bool:
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            return row is not None
    except Exception:
        logger.exception("user_exists failed: user_id=%s", user_id)
        return False


def save_history(user_id: str, question: str, answer: str) -> int:
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO chat_history (user_id, question, answer)
                VALUES (?, ?, ?)
                """,
                (user_id, question, answer),
            )
            return cur.lastrowid
    except Exception:
        logger.exception("save_history failed: user_id=%s", user_id)
        raise


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
        logger.exception("get_history failed: user_id=%s", user_id)
        return []


def delete_history(user_id: str) -> int:
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                DELETE FROM chat_history
                WHERE user_id = ?
                """,
                (user_id,),
            )
            return cur.rowcount
    except Exception:
        logger.exception("delete_history failed: user_id=%s", user_id)
        raise


def save_uploaded_file(project_id: str, project_name: str, saved_path: str) -> str:
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO uploaded_files (project_id, project_name, saved_path)
                VALUES (?, ?, ?)
                """,
                (project_id, project_name, saved_path),
            )
        return project_id
    except Exception:
        logger.exception("save_uploaded_file failed: project_id=%s", project_id)
        raise


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
        logger.exception("get_uploaded_files_by_project_id failed: project_id=%s", project_id)
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

    normalized_rows = []
    for f in files:
        project_id = f.get("project_id", f.get("projectid", ""))
        project_name = f.get("project_name", f.get("projectname", ""))
        file_name = f.get("file_name", f.get("filename", ""))
        relative_path = f.get("relative_path", f.get("relativepath", ""))
        extension = (f.get("extension") or "").lower().lstrip(".")
        file_size = f.get("file_size", f.get("filesize", 0))

        normalized_rows.append(
            (
                project_id,
                project_name,
                file_name,
                relative_path,
                extension,
                file_size,
            )
        )

    try:
        with get_connection() as conn:
            project_ids = list({r[0] for r in normalized_rows if r[0]})
            for pid in project_ids:
                conn.execute(
                    """
                    DELETE FROM file_index
                    WHERE project_id = ?
                    """,
                    (pid,),
                )

            conn.executemany(
                """
                INSERT INTO file_index
                (project_id, project_name, file_name, relative_path, extension, file_size)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                normalized_rows,
            )
            return len(normalized_rows)
    except Exception:
        logger.exception("bulk_insert_file_index failed")
        raise


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
        logger.exception("get_file_index failed: project_id=%s", project_id)
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

            ext_summary = {row["extension"]: row["cnt"] for row in ext_rows}
            files = [dict(row) for row in file_rows]

            return {
                "total": len(files),
                "by_extension": ext_summary,
                "files": files,
            }
    except Exception:
        logger.exception("get_file_index_summary failed: project_id=%s", project_id)
        return {"total": 0, "by_extension": {}, "files": []}


def insert_code_elements(project_id: str, project_name: str, elements: list[dict[str, Any]]) -> int:
    if not elements:
        return 0

    rows = []
    for e in elements:
        raw_text = e.get("raw_text", e.get("rawtext", "")) or ""
        rows.append(
            (
                project_id,
                project_name,
                e.get("file_name", e.get("filename", "")),
                e.get("relative_path", e.get("relativepath", "")),
                e.get("extension", ""),
                e.get("layer_type", e.get("layertype", "")),
                e.get("content_type", e.get("contenttype", "")),
                e.get("class_name", e.get("classname", "")),
                e.get("package", ""),
                _json_dumps(e.get("table_names", e.get("tablenames", []))),
                _json_dumps(e.get("imports", [])),
                _json_dumps(e.get("methods", [])),
                _json_dumps(e.get("xml_statements", e.get("xmlstatements", []))),
                _make_raw_text_preview(raw_text),
                _make_content_hash(raw_text),
                _count_lines(raw_text),
            )
        )

    try:
        with get_connection() as conn:
            paths = [r[3] for r in rows if r[3]]
            for rel_path in paths:
                conn.execute(
                    """
                    DELETE FROM code_elements
                    WHERE project_id = ? AND relative_path = ?
                    """,
                    (project_id, rel_path),
                )

            conn.executemany(
                """
                INSERT INTO code_elements
                (
                    project_id,
                    project_name,
                    file_name,
                    relative_path,
                    extension,
                    layer_type,
                    content_type,
                    class_name,
                    package,
                    table_names_json,
                    imports_json,
                    methods_json,
                    xml_statements_json,
                    raw_text_preview,
                    content_hash,
                    line_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            return len(rows)
    except Exception:
        logger.exception("insert_code_elements failed: project_id=%s", project_id)
        raise


def get_code_elements(project_id: str, layer_type: str | None = None) -> list[dict[str, Any]]:
    try:
        with get_connection() as conn:
            if layer_type:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM code_elements
                    WHERE project_id = ? AND layer_type = ?
                    ORDER BY relative_path
                    """,
                    (project_id, layer_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM code_elements
                    WHERE project_id = ?
                    ORDER BY relative_path
                    """,
                    (project_id,),
                ).fetchall()

            result = []
            for row in rows:
                item = dict(row)
                item["table_names"] = _json_loads(item.pop("table_names_json", ""), [])
                item["imports"] = _json_loads(item.pop("imports_json", ""), [])
                item["methods"] = _json_loads(item.pop("methods_json", ""), [])
                item["xml_statements"] = _json_loads(item.pop("xml_statements_json", ""), [])
                result.append(item)

            return result
    except Exception:
        logger.exception("get_code_elements failed: project_id=%s", project_id)
        return []


def find_code_elements_by_name(project_id: str, keyword: str) -> list[dict[str, Any]]:
    try:
        like_kw = f"%{keyword}%"
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
                (project_id, like_kw, like_kw, like_kw, like_kw, like_kw, like_kw),
            ).fetchall()

            result = []
            for row in rows:
                item = dict(row)
                item["table_names"] = _json_loads(item.pop("table_names_json", ""), [])
                item["imports"] = _json_loads(item.pop("imports_json", ""), [])
                item["methods"] = _json_loads(item.pop("methods_json", ""), [])
                item["xml_statements"] = _json_loads(item.pop("xml_statements_json", ""), [])
                result.append(item)

            return result
    except Exception:
        logger.exception("find_code_elements_by_name failed: project_id=%s keyword=%s", project_id, keyword)
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

        rows = [
            (
                user_id,
                (e.get("entity_name", e.get("entityname", "")) or "").strip(),
                (e.get("entity_type", e.get("entitytype", "")) or "").strip(),
                project_id or "",
            )
            for e in entities
            if (e.get("entity_name", e.get("entityname", "")) or "").strip()
        ]

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
        logger.exception("save_turn_entities failed: user_id=%s", user_id)
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
        logger.exception("get_recent_entities failed: user_id=%s", user_id)
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

    try:
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

            for col_name, col_def in required_columns.items():
                if col_name not in existing_columns:
                    if "PRIMARY KEY" in col_def:
                        continue
                    conn.execute(f"ALTER TABLE index_jobs ADD COLUMN {col_name} {col_def}")
                    logger.info("index_jobs column added: %s", col_name)
    except Exception:
        logger.exception("init_index_jobs_table failed")
        raise


def create_index_job(
        job_id: str,
        user_id: str,
        project_id: str | None,
        project_name: str | None,
        total_targets: int,
        message: str = "",
) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO index_jobs
                (
                    job_id,
                    user_id,
                    project_id,
                    project_name,
                    status,
                    total_targets,
                    processed_targets,
                    success_count,
                    failed_count,
                    total_chunks,
                    message,
                    error,
                    logs_json
                )
                VALUES (?, ?, ?, ?, 'queued', ?, 0, 0, 0, 0, ?, '', '[]')
                """,
                (job_id, user_id, project_id, project_name, total_targets, message),
            )
    except Exception:
        logger.exception("create_index_job failed: job_id=%s", job_id)
        raise


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

    try:
        with get_connection() as conn:
            conn.execute(
                f"""
                UPDATE index_jobs
                SET {", ".join(fields)}
                WHERE job_id = ?
                """,
                tuple(values),
            )
    except Exception:
        logger.exception("update_index_job failed: job_id=%s", job_id)
        raise


def get_index_job(job_id: str, user_id: str) -> dict[str, Any] | None:
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM index_jobs
                WHERE job_id = ? AND user_id = ?
                """,
                (job_id, user_id),
            ).fetchone()

            if not row:
                return None

            item = dict(row)
            item["logs"] = _json_loads(item.pop("logs_json", ""), [])
            return item
    except Exception:
        logger.exception("get_index_job failed: job_id=%s", job_id)
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
                item["logs"] = _json_loads(item.pop("logs_json", ""), [])
                result.append(item)

            return result
    except Exception:
        logger.exception("list_index_jobs failed: user_id=%s", user_id)
        return []


def purge_all_runtime_data() -> dict[str, int]:
    try:
        ensure_turn_entities_table()

        with get_connection() as conn:
            chat_deleted = conn.execute("DELETE FROM chat_history").rowcount
            uploaded_deleted = conn.execute("DELETE FROM uploaded_files").rowcount
            file_index_deleted = conn.execute("DELETE FROM file_index").rowcount
            code_elements_deleted = conn.execute("DELETE FROM code_elements").rowcount
            turn_entities_deleted = conn.execute("DELETE FROM turn_entities").rowcount
            index_jobs_deleted = conn.execute("DELETE FROM index_jobs").rowcount

            return {
                "chat_history_deleted": int(chat_deleted or 0),
                "uploaded_files_deleted": int(uploaded_deleted or 0),
                "file_index_deleted": int(file_index_deleted or 0),
                "code_elements_deleted": int(code_elements_deleted or 0),
                "turn_entities_deleted": int(turn_entities_deleted or 0),
                "index_jobs_deleted": int(index_jobs_deleted or 0),
            }
    except Exception:
        logger.exception("purge_all_runtime_data failed")
        raise


def list_db_tables() -> list[dict[str, Any]]:
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()

            result: list[dict[str, Any]] = []
            for row in rows:
                table_name = row["name"]
                count_row = conn.execute(
                    f'SELECT COUNT(*) AS cnt FROM "{table_name}"'
                ).fetchone()

                result.append(
                    {
                        "table_name": table_name,
                        "row_count": int(count_row["cnt"] if count_row else 0),
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

    try:
        with get_connection() as conn:
            allowed_tables = {
                row["name"]
                for row in conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                      AND name NOT LIKE 'sqlite_%'
                    """
                ).fetchall()
            }

            if safe_table_name not in allowed_tables:
                raise ValueError(f"unknown table: {safe_table_name}")

            safe_limit = max(1, min(int(limit or 200), 1000))

            rows = conn.execute(
                f'SELECT * FROM "{safe_table_name}" ORDER BY 1 DESC LIMIT ?',
                (safe_limit,),
            ).fetchall()

            return [dict(row) for row in rows]
    except ValueError:
        raise
    except Exception:
        logger.exception("get_table_rows_for_admin failed: table=%s", safe_table_name)
        raise


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
                    WHERE type = 'table'
                      AND name NOT LIKE 'sqlite_%'
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

            return [
                {
                    "srcname": row["src_name"],
                    "dstname": row["dst_name"],
                    "relation": row["relation"],
                    "project_id": row["project_id"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
    except Exception:
        logger.exception("get_relationship_edges failed: project_id=%s relation=%s", project_id, relation)
        return []


# ------------------------------------------------------------------
# backward-compatible aliases for old imports
# ------------------------------------------------------------------

def upsertuser(user_id: str) -> None:
    return upsert_user(user_id)


def userexists(user_id: str) -> bool:
    return user_exists(user_id)


def savehistory(user_id: str, question: str, answer: str) -> int:
    return save_history(user_id, question, answer)


def gethistory(user_id: str, limit: int) -> list[dict[str, Any]]:
    return get_history(user_id, limit)


def deletehistory(user_id: str) -> int:
    return delete_history(user_id)


def saveuploadedfile(project_id: str, project_name: str, saved_path: str) -> str:
    return save_uploaded_file(project_id, project_name, saved_path)


def getuploadedfiles() -> list[dict[str, Any]]:
    return get_uploaded_files()


def getuploadedfilesbyprojectid(project_id: str) -> dict[str, Any] | None:
    return get_uploaded_files_by_project_id(project_id)


def getallprojects() -> list[dict[str, Any]]:
    projects = get_all_projects()
    converted = []
    for p in projects:
        converted.append(
            {
                "projectid": p.get("project_id", ""),
                "projectname": p.get("project_name", ""),
                "uploadedat": p.get("uploaded_at", ""),
            }
        )
    return converted


def bulkinsertfileindex(files: list[dict[str, Any]]) -> int:
    normalized = []
    for f in files:
        normalized.append(
            {
                "project_id": f.get("project_id", f.get("projectid", "")),
                "project_name": f.get("project_name", f.get("projectname", "")),
                "file_name": f.get("file_name", f.get("filename", "")),
                "relative_path": f.get("relative_path", f.get("relativepath", "")),
                "extension": f.get("extension", ""),
                "file_size": f.get("file_size", f.get("filesize", 0)),
            }
        )
    return bulk_insert_file_index(normalized)


def getfileindex(project_id: str, extension: str | None = None) -> list[dict[str, Any]]:
    rows = get_file_index(project_id, extension)
    converted = []
    for r in rows:
        converted.append(
            {
                "filename": r.get("file_name", ""),
                "relativepath": r.get("relative_path", ""),
                "extension": r.get("extension", ""),
                "filesize": r.get("file_size", 0),
                "indexedat": r.get("indexed_at", ""),
            }
        )
    return converted


def getfileindexsummary(project_id: str) -> dict[str, Any]:
    data = get_file_index_summary(project_id)
    files = []
    for f in data.get("files", []):
        files.append(
            {
                "filename": f.get("file_name", ""),
                "relativepath": f.get("relative_path", ""),
                "extension": f.get("extension", ""),
            }
        )
    return {
        "total": data.get("total", 0),
        "byextension": data.get("by_extension", {}),
        "files": files,
    }


def insertcodeelements(project_id: str, project_name: str, elements: list[dict[str, Any]]) -> int:
    normalized = []
    for e in elements:
        normalized.append(
            {
                "file_name": e.get("file_name", e.get("filename", "")),
                "relative_path": e.get("relative_path", e.get("relativepath", "")),
                "extension": e.get("extension", ""),
                "layer_type": e.get("layer_type", e.get("layertype", "")),
                "content_type": e.get("content_type", e.get("contenttype", "")),
                "class_name": e.get("class_name", e.get("classname", "")),
                "package": e.get("package", ""),
                "table_names": e.get("table_names", e.get("tablenames", [])),
                "imports": e.get("imports", []),
                "methods": e.get("methods", []),
                "xml_statements": e.get("xml_statements", e.get("xmlstatements", [])),
                "raw_text": e.get("raw_text", e.get("rawtext", "")),
            }
        )
    return insert_code_elements(project_id, project_name, normalized)


def getcodeelements(project_id: str, layertype: str | None = None) -> list[dict[str, Any]]:
    rows = get_code_elements(project_id, layertype)
    converted = []
    for r in rows:
        converted.append(
            {
                **r,
                "filename": r.get("file_name", ""),
                "relativepath": r.get("relative_path", ""),
                "layertype": r.get("layer_type", ""),
                "contenttype": r.get("content_type", ""),
                "classname": r.get("class_name", ""),
                "tablenames": r.get("table_names", []),
                "xmlstatements": r.get("xml_statements", []),
                "rawtext": r.get("raw_text_preview", ""),
                "createdat": r.get("created_at", ""),
            }
        )
    return converted


def findcodeelementsbyname(project_id: str, keyword: str) -> list[dict[str, Any]]:
    rows = find_code_elements_by_name(project_id, keyword)
    converted = []
    for r in rows:
        converted.append(
            {
                **r,
                "filename": r.get("file_name", ""),
                "relativepath": r.get("relative_path", ""),
                "layertype": r.get("layer_type", ""),
                "contenttype": r.get("content_type", ""),
                "classname": r.get("class_name", ""),
                "tablenames": r.get("table_names", []),
                "xmlstatements": r.get("xml_statements", []),
                "rawtext": r.get("raw_text_preview", ""),
                "createdat": r.get("created_at", ""),
            }
        )
    return converted


def ensureturnentitiestable() -> None:
    return ensure_turn_entities_table()


def saveturnentities(user_id: str, entities: list[dict[str, Any]], project_id: str | None = None) -> int:
    return save_turn_entities(user_id, entities, project_id)


def getrecententities(user_id: str, limit: int = 20, project_id: str | None = None) -> list[dict[str, Any]]:
    rows = get_recent_entities(user_id, limit, project_id)
    converted = []
    for r in rows:
        converted.append(
            {
                "entityname": r.get("entity_name", ""),
                "entitytype": r.get("entity_type", ""),
                "projectid": r.get("project_id", ""),
                "createdat": r.get("created_at", ""),
            }
        )
    return converted


def initindexjobstable() -> None:
    return init_index_jobs_table()


def createindexjob(
        jobid: str,
        userid: str,
        projectid: str | None,
        projectname: str | None,
        totaltargets: int,
        message: str = "",
) -> None:
    return create_index_job(
        job_id=jobid,
        user_id=userid,
        project_id=projectid,
        project_name=projectname,
        total_targets=totaltargets,
        message=message,
    )


def updateindexjob(
        jobid: str,
        *,
        status: str | None = None,
        processedtargets: int | None = None,
        successcount: int | None = None,
        failedcount: int | None = None,
        totalchunks: int | None = None,
        message: str | None = None,
        error: str | None = None,
        logs: list[str] | None = None,
        finished: bool = False,
) -> None:
    return update_index_job(
        job_id=jobid,
        status=status,
        processed_targets=processedtargets,
        success_count=successcount,
        failed_count=failedcount,
        total_chunks=totalchunks,
        message=message,
        error=error,
        logs=logs,
        finished=finished,
    )


def getindexjob(jobid: str, userid: str) -> dict[str, Any] | None:
    item = get_index_job(jobid, userid)
    if not item:
        return None
    return {
        "jobid": item.get("job_id"),
        "userid": item.get("user_id"),
        "projectid": item.get("project_id"),
        "projectname": item.get("project_name"),
        "status": item.get("status"),
        "totaltargets": item.get("total_targets", 0),
        "processedtargets": item.get("processed_targets", 0),
        "successcount": item.get("success_count", 0),
        "failedcount": item.get("failed_count", 0),
        "totalchunks": item.get("total_chunks", 0),
        "message": item.get("message", ""),
        "error": item.get("error", ""),
        "logs": item.get("logs", []),
        "createdat": item.get("created_at"),
        "updatedat": item.get("updated_at"),
        "finishedat": item.get("finished_at"),
    }


def listindexjobs(userid: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = list_index_jobs(userid, limit)
    converted = []
    for item in rows:
        converted.append(
            {
                "jobid": item.get("job_id"),
                "userid": item.get("user_id"),
                "projectid": item.get("project_id"),
                "projectname": item.get("project_name"),
                "status": item.get("status"),
                "totaltargets": item.get("total_targets", 0),
                "processedtargets": item.get("processed_targets", 0),
                "successcount": item.get("success_count", 0),
                "failedcount": item.get("failed_count", 0),
                "totalchunks": item.get("total_chunks", 0),
                "message": item.get("message", ""),
                "error": item.get("error", ""),
                "logs": item.get("logs", []),
                "createdat": item.get("created_at"),
                "updatedat": item.get("updated_at"),
                "finishedat": item.get("finished_at"),
            }
        )
    return converted


def purgeallruntimedata() -> dict[str, int]:
    return purge_all_runtime_data()


def listdbtables() -> list[dict[str, Any]]:
    rows = list_db_tables()
    return [
        {
            "tablename": r.get("table_name", ""),
            "rowcount": r.get("row_count", 0),
        }
        for r in rows
    ]


def gettablerowsforadmin(tablename: str, limit: int = 200) -> list[dict[str, Any]]:
    return get_table_rows_for_admin(tablename, limit)


def getrelationshipedges(projectid: str, relation: str | None = None) -> list[dict[str, Any]]:
    return get_relationship_edges(projectid, relation)