from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from database.init_db import get_connection

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 공통 유틸
# ─────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────
# Users
# ─────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────
# Chat History  (project_id 기준)
# ─────────────────────────────────────────────────────────────

def save_history(user_id: str, project_id: str, question: str, answer: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO chat_history (user_id, project_id, question, answer) VALUES (?, ?, ?, ?)",
            (user_id, project_id, question, answer),
        )
        return int(cur.lastrowid)


def get_history(user_id: str, project_id: str, limit: int) -> list[dict[str, Any]]:
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, project_id, question, answer, created_at
                FROM chat_history
                WHERE user_id = ? AND project_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (user_id, project_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]
    except Exception:
        logger.exception("get_history failed user_id=%s project_id=%s", user_id, project_id)
        return []


def delete_history(project_id: str) -> int:
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM chat_history WHERE project_id = ?",
                (project_id,),
            )
            return int(cur.rowcount or 0)
    except Exception:
        logger.exception("delete_history failed project_id=%s", project_id)
        return 0


# ─────────────────────────────────────────────────────────────
# Uploaded Files / Projects
# ─────────────────────────────────────────────────────────────

def save_uploaded_file(project_id: str, project_name: str, user_id: str, saved_path: str) -> str:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO uploaded_files (project_id, project_name, user_id, saved_path)
            VALUES (?, ?, ?, ?)
            """,
            (project_id, project_name, user_id, saved_path),
        )
    return project_id

def delete_uploaded_file(project_id: str) -> int:
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM uploaded_files WHERE project_id = ?",
                (project_id,),
            )
            return int(cur.rowcount or 0)
    except Exception:
        logger.exception("delete_uploaded_file failed project_id=%s", project_id)
        return 0


def get_all_projects(user_id: str) -> list[dict[str, Any]]:
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT project_id, project_name, uploaded_at
                FROM uploaded_files
                WHERE user_id = ?
                ORDER BY uploaded_at DESC
                """,
                (user_id,)
            ).fetchall()
            return [dict(row) for row in rows]
    except Exception:
        logger.exception("get_all_projects failed")
        return []


def get_project_by_name(user_id: str, project_name: str) -> dict[str, Any] | None:
    """사용자별 동일한 프로젝트명 존재여부 확인 후 ID 반환"""
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT project_id, project_name, uploaded_at
                FROM uploaded_files
                WHERE user_id = ? AND project_name = ?
                """,
                (user_id, project_name.strip(),),
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        logger.exception("get_project_by_name failed user_id=%s ,project_name=%s", user_id, project_name)
        return None


def get_project_by_id(project_id: str) -> dict[str, Any] | None:
    """프로젝트 ID 로 프로젝트 NAME 조회"""
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT project_name, saved_path
                FROM uploaded_files
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        logger.exception("get_project_by_id failed user_id=%s ,project_id=%s", project_id)
        return None

# ─────────────────────────────────────────────────────────────
# File Index
# ─────────────────────────────────────────────────────────────

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

def delete_file_index(project_id: str) -> int:
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM file_index WHERE project_id = ?",
                (project_id,),
            )
            return int(cur.rowcount or 0)
    except Exception:
        logger.exception("delete_file_index failed project_id=%s", project_id)
        return 0


# ─────────────────────────────────────────────────────────────
# Code Elements
# ─────────────────────────────────────────────────────────────

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

def delete_code_elements(project_id: str) -> int:
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM code_elements WHERE project_id = ?",
                (project_id,),
            )
            return int(cur.rowcount or 0)
    except Exception:
        logger.exception("delete_code_elements failed project_id=%s", project_id)
        return 0


# ─────────────────────────────────────────────────────────────
# Turn Entities  (turn_entities 테이블은 init_db 에서 생성)
# ─────────────────────────────────────────────────────────────

def save_turn_entities(user_id: str, entities: list[dict[str, Any]], project_id: str | None = None) -> int:
    if not entities:
        return 0

    try:
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


def delete_turn_entities(project_id: str) -> int:
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM turn_entities WHERE project_id = ?",
                (project_id,),
            )
            return int(cur.rowcount or 0)
    except Exception:
        logger.exception("delete_turn_entities failed project_id=%s", project_id)
        return 0


# ─────────────────────────────────────────────────────────────
# Index Jobs
# ─────────────────────────────────────────────────────────────

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

def delete_index_job(project_id: str) -> int:
    """해당 project_id 의 모든 index_jobs 를 삭제합니다."""
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM index_jobs WHERE project_id = ?",
                (project_id,),
            )
            return int(cur.rowcount or 0)
    except Exception:
        logger.exception("delete_index_job failed project_id=%s", project_id)
        return 0


# ─────────────────────────────────────────────────────────────
# Admin / Purge
# ─────────────────────────────────────────────────────────────


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