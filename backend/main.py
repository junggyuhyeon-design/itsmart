from __future__ import annotations

import logging
import re
import sys
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import BackgroundTasks, Body, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse

from config import get_settings
from database.history_repository import (
    create_index_job,
    delete_history,
    get_all_projects,
    get_code_elements,
    get_file_index,
    get_file_index_summary,
    get_history,
    get_index_job,
    get_recent_entities,
    get_table_rows_for_admin,
    init_index_jobs_table,
    list_db_tables,
    list_index_jobs,
    purge_all_runtime_data,
    save_history,
    save_turn_entities,
    save_uploaded_file,
    update_index_job,
    upsert_user,
)
from database.init_db import init_db
from health_service import build_system_status
from rag.query_analyzer import QueryAnalyzer
from rag.rag_service import RAGService
from utils.file_utils import ensure_dir, is_allowed_upload_extension, process_uploads_and_collect, safe_filename

root_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(root_dir))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logging.getLogger("main").setLevel(logging.INFO)
logging.getLogger("rag").setLevel(logging.INFO)
logging.getLogger("database").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("main")
settings = get_settings()
upload_dir = Path(settings.upload_dir)
extract_dir = Path(settings.extract_dir)
query_analyzer = QueryAnalyzer(default_top_k=settings.top_k)

table_patterns = [
    r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)",
    r"\bJOIN\s+([A-Za-z_][A-Za-z0-9_]*)",
    r"\bUPDATE\s+([A-Za-z_][A-Za-z0-9_]*)",
    r"\bINTO\s+([A-Za-z_][A-Za-z0-9_]*)",
    r"\bTABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
]


class AccessLogFilter(logging.Filter):
    skip_keywords = {"health", "status", "collections"}

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(keyword in message for keyword in self.skip_keywords)


access_logger = logging.getLogger("uvicorn.access")
access_logger.addFilter(AccessLogFilter())
access_logger.setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup begin")
    try:
        ensure_dir(upload_dir)
        ensure_dir(extract_dir)
        init_db()
        init_index_jobs_table()

        rag_service = RAGService(settings)
        app.state.rag_service = rag_service
        app.state.rag_initialized = True
        app.state.init_error = None

        logger.info("startup completed")
    except Exception as error:
        logger.exception("startup failed: %s", error)
        app.state.rag_service = None
        app.state.rag_initialized = False
        app.state.init_error = str(error)
        raise RuntimeError(f"startup failed: {error}") from error

    yield

    logger.info("shutdown completed")


app = FastAPI(
    title="IT-Smart CodeMind API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_rag_service(request: Request) -> RAGService:
    rag_service = getattr(request.app.state, "rag_service", None)
    if rag_service is None:
        raise HTTPException(status_code=503, detail="RAG service is not ready")
    return rag_service


def require_user(x_user_id: str | None) -> str:
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(status_code=400, detail="X-User-Id header is required")

    user_id = x_user_id.strip()
    try:
        upsert_user(user_id)
    except Exception as error:
        logger.exception("upsert_user failed user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="failed to ensure user") from error

    return user_id


async def save_upload_stream(upload_file: UploadFile, destination: Path) -> None:
    total_written = 0

    try:
        async with aiofiles.open(destination, "wb") as output_file:
            while True:
                chunk = await upload_file.read(settings.upload_chunk_size)
                if not chunk:
                    break

                total_written += len(chunk)
                if total_written > settings.max_file_size:
                    destination.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"{upload_file.filename} exceeds max file size",
                    )

                await output_file.write(chunk)

    except HTTPException:
        raise
    except Exception as error:
        destination.unlink(missing_ok=True)
        logger.exception("save_upload_stream failed file=%s", upload_file.filename)
        raise HTTPException(status_code=500, detail=f"failed to save upload: {error}") from error


def normalize_project_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": item.get("project_id", ""),
        "project_name": item.get("project_name", ""),
        "uploaded_at": item.get("uploaded_at", ""),
    }


def normalize_job_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": item.get("job_id"),
        "project_id": item.get("project_id"),
        "project_name": item.get("project_name"),
        "status": item.get("status"),
        "total_targets": int(item.get("total_targets", 0) or 0),
        "processed_targets": int(item.get("processed_targets", 0) or 0),
        "success_count": int(item.get("success_count", 0) or 0),
        "failed_count": int(item.get("failed_count", 0) or 0),
        "total_chunks": int(item.get("total_chunks", 0) or 0),
        "message": item.get("message", ""),
        "error": item.get("error", ""),
        "logs": item.get("logs", []),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "finished_at": item.get("finished_at"),
    }


def normalize_table_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "table_name": item.get("table_name", ""),
        "row_count": int(item.get("row_count", 0) or 0),
    }


def normalize_target_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": item.get("project_id"),
        "project_name": item.get("project_name"),
        "saved_path": item.get("saved_path"),
        "relative_path": item.get("relative_path"),
        "original_name": item.get("original_name") or item.get("file_name"),
        "file_name": item.get("file_name") or item.get("original_name"),
        "extension": item.get("extension"),
        "file_size": int(item.get("file_size") or item.get("size") or 0),
        "file_path": item.get("file_path") or item.get("saved_path"),
        "source_type": item.get("source_type", ""),
        "root_container_name": item.get("root_container_name", ""),
    }


def build_listing_context_summary(summary: dict[str, Any], extension_filter: str | None) -> str:
    lines: list[str] = []
    files = summary.get("files", [])

    if extension_filter:
        filtered_files = [item for item in files if item.get("extension") == extension_filter]
        lines.append(f"{extension_filter.upper()} files: {len(filtered_files)}")
        for item in filtered_files:
            lines.append(f"- {item.get('relative_path')}")
        return "\n".join(lines)

    lines.append(f"Total files: {summary.get('total', 0)}")

    by_extension: dict[str, list[str]] = {}
    for item in files:
        extension = item.get("extension", "")
        relative_path = item.get("relative_path", "")
        by_extension.setdefault(extension, []).append(relative_path)

    for extension, paths in sorted(by_extension.items()):
        lines.append(f"{extension.upper()}: {len(paths)}")
        for relative_path in paths:
            lines.append(f"- {relative_path}")

    return "\n".join(lines)


def extract_tables_from_text(text: str) -> list[str]:
    found_tables: list[str] = []

    for pattern in table_patterns:
        matches = re.findall(pattern, text or "", flags=re.IGNORECASE)
        for match in matches:
            table_name = (match or "").strip()
            if not table_name:
                continue

            upper_name = table_name.upper()
            if upper_name in {"SELECT", "FROM", "WHERE", "AND", "OR", "SET", "VALUES", "RESULTMAP", "DUAL"}:
                continue

            found_tables.append(upper_name)

    deduped: list[str] = []
    seen = set()
    for table_name in found_tables:
        if table_name not in seen:
            seen.add(table_name)
            deduped.append(table_name)

    return deduped


def build_table_listing(project_id: str, project_name: str) -> str:
    code_elements = get_code_elements(project_id)
    if not code_elements:
        return f"{project_name} has no code elements."

    table_to_files: dict[str, set[str]] = {}
    table_counter: Counter[str] = Counter()

    for element in code_elements:
        tables = element.get("table_names") or []
        if not tables:
            raw_text = element.get("raw_text") or element.get("raw_text_preview") or ""
            tables = extract_tables_from_text(raw_text)

        relative_path = element.get("relative_path") or ""
        for table_name in tables:
            normalized_name = (table_name or "").strip().upper()
            if not normalized_name:
                continue

            table_counter[normalized_name] += 1
            table_to_files.setdefault(normalized_name, set()).add(relative_path)

    if not table_counter:
        return f"{project_name} has no detected DB tables."

    lines = [f"{project_name} detected tables: {len(table_counter)}"]
    for table_name, count in table_counter.most_common():
        files = sorted(path for path in table_to_files.get(table_name, set()) if path)
        lines.append(f"- {table_name} ({count})")
        for path in files[:10]:
            lines.append(f"  - {path}")
        if len(files) > 10:
            lines.append(f"  - ... {len(files) - 10} more")

    return "\n".join(lines)


def detect_meta_request(question: str) -> bool:
    lowered = (question or "").lower()
    keywords = [
        "count",
        "controller",
        "service",
        "repository",
        "mapper",
        "xml",
        "java",
        "sql",
        "구조",
        "레이어",
        "테이블",
        "db",
    ]
    return any(keyword in lowered for keyword in keywords)


def build_sqlite_context(project_id: str, project_name: str, question: str) -> str:
    lowered = (question or "").lower()
    parts = [f"SQLite summary for {project_name}"]

    summary = get_file_index_summary(project_id)
    total_files = int(summary.get("total", 0) or 0)
    by_extension = summary.get("by_extension", {}) or {}

    parts.append(f"- total files: {total_files}")

    if by_extension:
        parts.append("- extensions:")
        for extension, count in sorted(by_extension.items(), key=lambda item: (-item[1], item[0])):
            parts.append(f"  - {extension}: {count}")

    code_elements = get_code_elements(project_id)
    if code_elements:
        layer_counter: Counter[str] = Counter()
        for element in code_elements:
            layer_type = (element.get("layer_type") or "").strip().lower()
            if layer_type:
                layer_counter[layer_type] += 1

        if layer_counter:
            parts.append("- layers:")
            for layer_type, count in layer_counter.most_common():
                parts.append(f"  - {layer_type}: {count}")

    if "table" in lowered or "db" in lowered or "테이블" in lowered:
        parts.append("")
        parts.append(build_table_listing(project_id, project_name))

    return "\n".join(parts)


async def call_ask_with_context_stream(
        rag_service: RAGService,
        *,
        question: str,
        project_id: str | None,
        project_name: str | None,
        extra_context: str,
        sqlite_context: str,
        top_k: int,
        layer_filter: str | None,
        extension_filter: str | None,
        query_type: str,
        chat_history: list[dict[str, Any]],
        recent_entities: list[dict[str, Any]],
):
    return await rag_service.ask_with_context_stream(
        question=question,
        project_id=project_id,
        project_name=project_name,
        extra_context=extra_context,
        sqlite_context=sqlite_context,
        top_k=top_k,
        layer_filter=layer_filter,
        extension_filter=extension_filter,
        query_type=query_type,
        chat_history=chat_history,
        recent_entities=recent_entities,
    )


def run_index_job(rag_service: RAGService, job_id: str, targets: list[dict[str, Any]]) -> None:
    try:
        update_index_job(job_id, status="running", message="indexing started")

        def progress_callback(**kwargs):
            update_index_job(
                job_id,
                status="running",
                processed_targets=kwargs.get("processed_targets"),
                success_count=kwargs.get("success_count"),
                failed_count=kwargs.get("failed_count"),
                total_chunks=kwargs.get("total_chunks"),
                message=kwargs.get("message"),
                error=kwargs.get("error"),
                logs=kwargs.get("logs"),
            )

        result = rag_service.index_files(targets, progress_callback=progress_callback)

        update_index_job(
            job_id,
            status="completed",
            processed_targets=int(result.get("success", 0) or 0) + int(result.get("failed", 0) or 0),
            success_count=int(result.get("success", 0) or 0),
            failed_count=int(result.get("failed", 0) or 0),
            total_chunks=int(result.get("total_chunks", 0) or 0),
            message=f"success={result.get('success', 0)} failed={result.get('failed', 0)}",
            logs=result.get("logs", []),
            finished=True,
        )
    except Exception as error:
        logger.exception("run_index_job failed job_id=%s", job_id)
        update_index_job(
            job_id,
            status="failed",
            message="indexing failed",
            error=str(error),
            finished=True,
        )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
def status():
    rag_initialized = getattr(app.state, "rag_initialized", False)
    init_error = getattr(app.state, "init_error", None)
    return build_system_status(settings, rag_initialized, init_error)


@app.get("/")
def root():
    return PlainTextResponse("CodeMind backend is running.")


@app.post("/upload")
async def upload(
        files: list[UploadFile] = File(...),
        x_user_id: str | None = Header(default=None),
):
    require_user(x_user_id)

    if not files:
        raise HTTPException(status_code=400, detail="files are required")

    if len(files) > settings.max_files_per_request:
        raise HTTPException(
            status_code=400,
            detail=f"max {settings.max_files_per_request} files are allowed",
        )

    ensure_dir(upload_dir)
    saved_filenames: list[str] = []

    for upload_file in files:
        if not upload_file.filename or not upload_file.filename.strip():
            raise HTTPException(status_code=400, detail="empty filename is not allowed")

        if not is_allowed_upload_extension(upload_file.filename):
            raise HTTPException(
                status_code=400,
                detail=f"unsupported upload extension: {upload_file.filename}",
            )

        sanitized_name = safe_filename(upload_file.filename)
        destination = upload_dir / sanitized_name
        await save_upload_stream(upload_file, destination)
        saved_filenames.append(sanitized_name)

    raw_targets = await run_in_threadpool(process_uploads_and_collect, upload_dir, saved_filenames)

    projects_created: dict[str, str] = {}
    normalized_targets: list[dict[str, Any]] = []

    for target in raw_targets:
        project_id = getattr(target, "project_id", None) or getattr(target, "projectid", None)
        project_name = getattr(target, "project_name", None) or getattr(target, "projectname", None)
        saved_path = getattr(target, "saved_path", None) or getattr(target, "savedpath", None)

        if project_id and project_id not in projects_created:
            projects_created[project_id] = project_name or ""

        normalized_targets.append(
            {
                "project_id": project_id,
                "project_name": project_name,
                "saved_path": saved_path,
                "relative_path": getattr(target, "relative_path", None) or getattr(target, "relativepath", None),
                "original_name": getattr(target, "original_name", None) or getattr(target, "originalname", None),
                "file_name": getattr(target, "original_name", None) or getattr(target, "originalname", None),
                "extension": getattr(target, "extension", None),
                "file_size": getattr(target, "size", 0),
                "source_type": getattr(target, "source_type", None) or getattr(target, "sourcetype", None),
                "root_container_name": getattr(target, "root_container_name", None) or getattr(target, "rootcontainername", None),
            }
        )

    for project_id, project_name in projects_created.items():
        origin_path = upload_dir / f"{project_name}.zip"
        try:
            save_uploaded_file(project_id, project_name, str(origin_path))
        except Exception as error:
            logger.exception("save_uploaded_file failed project_id=%s error=%s", project_id, error)

    return {
        "targets": normalized_targets,
        "count": len(normalized_targets),
        "projects": len(projects_created),
    }


@app.post("/index")
async def index_now(
        request: Request,
        targets: list[dict[str, Any]] = Body(...),
):
    if not targets:
        raise HTTPException(status_code=400, detail="targets are required")

    rag_service = get_rag_service(request)
    normalized_targets = [normalize_target_item(target) for target in targets]

    try:
        result = await run_in_threadpool(rag_service.index_files, normalized_targets)
        result["total_chunks"] = int(result.get("total_chunks", 0) or 0)
        return result
    except Exception as error:
        logger.exception("index_now failed")
        raise HTTPException(status_code=500, detail=f"index failed: {error}") from error


@app.post("/index-jobs")
async def create_job(
        request: Request,
        background_tasks: BackgroundTasks,
        payload: dict[str, Any] = Body(...),
        x_user_id: str | None = Header(default=None),
):
    user_id = require_user(x_user_id)

    targets = payload.get("targets", [])
    if not targets:
        raise HTTPException(status_code=400, detail="targets are required")

    normalized_targets = [normalize_target_item(target) for target in targets]
    first_target = normalized_targets[0]
    project_id = first_target.get("project_id")
    project_name = first_target.get("project_name")

    job_id = str(uuid.uuid4())
    create_index_job(
        job_id=job_id,
        user_id=user_id,
        project_id=project_id,
        project_name=project_name,
        total_targets=len(normalized_targets),
        message="queued",
    )

    rag_service = get_rag_service(request)
    background_tasks.add_task(run_index_job, rag_service, job_id, normalized_targets)

    return {
        "job_id": job_id,
        "status": "queued",
        "project_id": project_id,
        "project_name": project_name,
        "total_targets": len(normalized_targets),
    }


@app.get("/index-jobs")
def get_index_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        x_user_id: str | None = Header(default=None),
):
    user_id = require_user(x_user_id)
    jobs = list_index_jobs(user_id, limit=limit)
    return {
        "jobs": [normalize_job_item(job) for job in jobs],
        "count": len(jobs),
    }


@app.get("/index-jobs/{job_id}")
def get_index_job_detail(
        job_id: str,
        x_user_id: str | None = Header(default=None),
):
    user_id = require_user(x_user_id)
    job = get_index_job(job_id, user_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return normalize_job_item(job)


@app.get("/projects")
def get_projects():
    try:
        projects = get_all_projects()
        normalized = [normalize_project_item(project) for project in projects]
        return {
            "projects": normalized,
            "count": len(normalized),
        }
    except Exception as error:
        logger.exception("get_projects failed")
        raise HTTPException(status_code=500, detail=f"projects failed: {error}") from error


@app.get("/projects/{project_name}/files")
def get_project_files(
        project_name: str,
        extension: str | None = Query(default=None),
):
    try:
        matched_project = next(
            (
                project
                for project in get_all_projects()
                if project.get("project_name") == project_name.strip()
            ),
            None,
        )

        if not matched_project:
            raise HTTPException(status_code=404, detail=f"project not found: {project_name}")

        project_id = matched_project.get("project_id")
        files = get_file_index(project_id, extension)

        normalized_files = []
        for item in files:
            normalized_files.append(
                {
                    "file_name": item.get("file_name"),
                    "relative_path": item.get("relative_path"),
                    "extension": item.get("extension"),
                    "file_size": int(item.get("file_size", 0) or 0),
                    "indexed_at": item.get("indexed_at"),
                }
            )

        return {
            "project_id": project_id,
            "project_name": project_name,
            "extension_filter": extension,
            "files": normalized_files,
            "count": len(normalized_files),
        }

    except HTTPException:
        raise
    except Exception as error:
        logger.exception("get_project_files failed")
        raise HTTPException(status_code=500, detail=f"project files failed: {error}") from error


@app.post("/ask")
async def ask(
        request: Request,
        payload: dict[str, Any] = Body(...),
        x_user_id: str | None = Header(default=None),
):
    user_id = require_user(x_user_id)

    question = (payload.get("question") or "").strip()
    project_name = payload.get("project_name")
    project_id = payload.get("project_id")
    extra_context = payload.get("extra_context", "")
    top_k = int(payload.get("top_k") or settings.top_k)

    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    selected_project_name = project_name
    if project_name and not project_id:
        for project in get_all_projects():
            if project.get("project_name") == project_name.strip():
                project_id = project.get("project_id")
                selected_project_name = project.get("project_name")
                break

        if not project_id:
            raise HTTPException(status_code=400, detail=f"unknown project_name: {project_name}")

    history_limit = max(1, min(settings.chat_history_turns, 20))
    chat_history = list(reversed(get_history(user_id, limit=history_limit)))
    recent_entities = get_recent_entities(user_id, limit=20, project_id=project_id)

    intent = query_analyzer.analyze(question)
    rag_service = get_rag_service(request)

    structure_context = ""
    if intent.query_type != "diagram" and project_id:
        summary = get_file_index_summary(project_id)
        if summary.get("total", 0) > 0:
            structure_context = build_listing_context_summary(summary, intent.extension_filter)

    sqlite_context = ""
    if project_id and detect_meta_request(question):
        sqlite_context = build_sqlite_context(project_id, selected_project_name or "", question)

    generator, _hits = await call_ask_with_context_stream(
        rag_service,
        question=question,
        project_id=project_id,
        project_name=selected_project_name,
        extra_context=structure_context or extra_context,
        sqlite_context=sqlite_context,
        top_k=top_k,
        layer_filter=intent.layer_filter,
        extension_filter=intent.extension_filter,
        query_type=intent.query_type,
        chat_history=chat_history,
        recent_entities=recent_entities,
    )

    async def safe_stream():
        collected_chunks: list[str] = []
        try:
            async for chunk in generator:
                collected_chunks.append(chunk)
                yield chunk
        finally:
            answer = "".join(collected_chunks).strip()
            if answer:
                try:
                    stored_question = question
                    if selected_project_name:
                        stored_question = f"[{selected_project_name}] {question}"
                    save_history(user_id, stored_question, answer)
                except Exception:
                    logger.exception("save_history failed")

                try:
                    entities = []
                    if intent.entity_hint:
                        entities.append(
                            {
                                "entity_name": intent.entity_hint,
                                "entity_type": "hint",
                            }
                        )
                    for keyword in intent.keywords[:8]:
                        entities.append(
                            {
                                "entity_name": keyword,
                                "entity_type": "keyword",
                            }
                        )
                    if entities:
                        save_turn_entities(user_id, entities, project_id=project_id)
                except Exception:
                    logger.exception("save_turn_entities failed")

    return StreamingResponse(safe_stream(), media_type="text/plain; charset=utf-8")


@app.get("/history")
def history(
        limit: int = Query(default=20, ge=1, le=300),
        x_user_id: str | None = Header(default=None),
):
    user_id = require_user(x_user_id)
    rows = get_history(user_id, limit=limit)
    return {
        "history": rows,
        "count": len(rows),
    }


@app.delete("/history")
def clear_history(
        x_user_id: str | None = Header(default=None),
):
    user_id = require_user(x_user_id)
    deleted = delete_history(user_id)
    return {"deleted": deleted}


@app.get("/db/tables")
def db_tables():
    rows = list_db_tables()
    return {
        "tables": [normalize_table_item(row) for row in rows],
        "count": len(rows),
    }


@app.get("/db/tables/{table_name}")
def db_table_rows(
        table_name: str,
        limit: int = Query(default=200, ge=1, le=1000),
):
    try:
        rows = get_table_rows_for_admin(table_name, limit)
        return {
            "table_name": table_name,
            "rows": rows,
            "count": len(rows),
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/admin/purge")
def purge_runtime_data():
    return purge_all_runtime_data()


@app.delete("/reset")
def reset_all_data(
        confirm_text: str = Query(default=""),
):
    if confirm_text != "RESET":
        raise HTTPException(status_code=400, detail="confirm_text=RESET is required")

    try:
        result = purge_all_runtime_data()

        rag_service = getattr(app.state, "rag_service", None)
        if rag_service is not None:
            try:
                rag_service.qdrant_service.recreate_collection(
                    rag_service.embedding_service.dimension
                )
            except Exception as error:
                logger.exception("qdrant reset failed: %s", error)
                result["qdrant_reset_error"] = str(error)

        for path in upload_dir.glob("*"):
            try:
                if path.is_file():
                    path.unlink(missing_ok=True)
            except Exception:
                logger.exception("failed to remove upload file: %s", path)

        for path in extract_dir.glob("*"):
            try:
                if path.is_file():
                    path.unlink(missing_ok=True)
            except Exception:
                logger.exception("failed to remove extracted file: %s", path)

        result["reset"] = True
        return result

    except Exception as error:
        logger.exception("reset_all_data failed")
        raise HTTPException(status_code=500, detail=f"reset failed: {error}") from error


@app.get("/ask")
async def ask_get(
        request: Request,
        question: str,
        project_name: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        extra_context: str = Query(default=""),
        top_k: int = Query(default=5),
        x_user_id: str | None = Header(default=None),
):
    payload = {
        "question": question,
        "project_name": project_name,
        "project_id": project_id,
        "extra_context": extra_context,
        "top_k": top_k,
    }
    return await ask(request=request, payload=payload, x_user_id=x_user_id)