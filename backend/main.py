from __future__ import annotations

import logging
import re
import sys
import time
import uuid
import shutil
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
    get_file_index_summary,
    get_history,
    get_index_job,
    get_project_by_name,
    get_recent_entities,
    list_index_jobs,
    get_project_by_id,
    save_history,
    save_turn_entities,
    save_uploaded_file,
    update_index_job,
    upsert_user,
    user_exists,
    delete_uploaded_file,
    delete_file_index,
    delete_index_job,
    delete_code_elements,
    delete_turn_entities,
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
logging.getLogger("parser").setLevel(logging.INFO)
logging.getLogger("utils").setLevel(logging.INFO)

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


# ─────────────────────────────────────────────────────────────
# Startup / Shutdown
# ─────────────────────────────────────────────────────────────

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
    logger.info("[main.py][lifespan] startup begin")
    try:
        ensure_dir(upload_dir)
        ensure_dir(extract_dir)
        logger.info(
            "[main.py][lifespan] ensure_dir completed upload_dir=%s extract_dir=%s",
            str(upload_dir),
            str(extract_dir),
        )

        init_db()
        logger.info("[main.py][lifespan] init_db completed")

        rag_service = RAGService(settings)
        app.state.rag_service = rag_service
        app.state.rag_initialized = True
        app.state.init_error = None

        logger.info("[main.py][lifespan] RAGService initialized")
        logger.info("[main.py][lifespan] startup completed")
    except Exception as error:
        logger.exception("[main.py][lifespan] startup failed error=%s", error)
        app.state.rag_service = None
        app.state.rag_initialized = False
        app.state.init_error = str(error)
        raise RuntimeError(f"startup failed: {error}") from error

    yield

    logger.info("[main.py][lifespan] shutdown completed")


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


# ─────────────────────────────────────────────────────────────
# 공통 헬퍼
# ─────────────────────────────────────────────────────────────

def get_rag_service(request: Request) -> RAGService:
    rag_service = getattr(request.app.state, "rag_service", None)
    if rag_service is None:
        logger.error("[main.py][get_rag_service] rag_service is not ready")
        raise HTTPException(status_code=503, detail="RAG service is not ready")

    # logger.info("[main.py][get_rag_service] rag_service resolved")
    # logger.info("============================================================")
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

    # logger.info(
    #     "[main.py][save_upload_stream] start file=%s destination=%s",
    #     upload_file.filename,
    #     str(destination),
    # )

    try:
        async with aiofiles.open(destination, "wb") as output_file:
            while True:
                chunk = await upload_file.read(settings.upload_chunk_size)
                if not chunk:
                    break

                total_written += len(chunk)
                if total_written > settings.max_file_size:
                    destination.unlink(missing_ok=True)
                    logger.warning(
                        "[main.py][save_upload_stream] max_file_size exceeded file=%s total_written=%d max_file_size=%d",
                        upload_file.filename,
                        total_written,
                        settings.max_file_size,
                    )
                    raise HTTPException(
                        status_code=413,
                        detail=f"{upload_file.filename} exceeds max file size",
                    )

                await output_file.write(chunk)
        
        logger.info(
            "[main.py][save_upload_stream][파일 업로드] completed file=%s destination=%s total_written=%d",
            upload_file.filename,
            str(destination),
            total_written,
        )
        logger.info("============================================================")

    except HTTPException:
        raise
    except Exception as error:
        destination.unlink(missing_ok=True)
        logger.exception("[main.py][save_upload_stream] failed file=%s", upload_file.filename)
        raise HTTPException(status_code=500, detail=f"failed to save upload: {error}") from error


# ─────────────────────────────────────────────────────────────
# Normalizers
# ─────────────────────────────────────────────────────────────

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
        "file_name": item.get("file_name"),
        "extension": item.get("extension"),
        "file_size": int(item.get("file_size")),
    }


# ─────────────────────────────────────────────────────────────
# Context builders
# ─────────────────────────────────────────────────────────────

def build_listing_context_summary(summary: dict[str, Any], extension_filter: str | None) -> str:
    lines: list[str] = []
    files = summary.get("files", [])

    if extension_filter:
        logger.info("[main.py][build_listing_context_summary][확장자 필터] exist filter=%s", extension_filter)

        filtered_files = [item for item in files if item.get("extension") == extension_filter]
        lines.append(f"{extension_filter.upper()} files: {len(filtered_files)}")
        for item in filtered_files:
            lines.append(f"- {item.get('relative_path')}")
        return "\n".join(lines)

    lines.append(f"Total files: {summary.get('total', 0)}")
    logger.info("[main.py][build_listing_context_summary][확장자 NO 필터] total filter=%d", summary.get('total', 0))

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
            raw_text = element.get("raw_text_preview") or ""
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
        "소스",
        "파일",
        "설명",
        "프로젝트",
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


# ─────────────────────────────────────────────────────────────
# Index job runner
# ─────────────────────────────────────────────────────────────

async def call_ask_with_context_stream(
        rag_service: RAGService,
        *,
        question: str,
        retrieval_question: str | None,
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
        edit_source: str | None = None,     #exact grep (독립 리터럴 검색)
        edit_target: str | None = None,     #변경 파일/줄/전후값 중심 답변 패치
):
    return await rag_service.ask_with_context_stream(
        question=question,
        retrieval_question=retrieval_question,
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
        edit_source=edit_source,            #exact grep (독립 리터럴 검색)
        edit_target=edit_target,            #변경 파일/줄/전후값 중심 답변 패치
    )


def run_index_job(rag_service: RAGService, job_id: str, targets: list[dict[str, Any]]) -> None:
    logger.info(
        "[main.py][run_index_job] start job_id=%s target_count=%d",
        job_id,
        len(targets or []),
    )

    try:
        update_index_job(job_id, status="running", message="indexing started")
        logger.info("[main.py][run_index_job] update_index_job running set job_id=%s", job_id)

        def progress_callback(**kwargs):
#             logger.info(
#                 "[main.py][run_index_job.progress_callback] job_id=%s processed_targets=%s total_chunks=%s success_count=%s failed_count=%s message=%s error=%s",
#                 job_id,
#                 kwargs.get("processed_targets"),
#                 kwargs.get("total_chunks"),
#                 kwargs.get("success_count"),
#                 kwargs.get("failed_count"),
#                 kwargs.get("message"),
#                 kwargs.get("error"),
#             )
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

        # logger.info(
        #     "[main.py][run_index_job] rag_service.index_files completed job_id=%s success=%s failed=%s total_chunks=%s indexed_files=%s code_elements=%s",
        #     job_id,
        #     result.get("success", 0),
        #     result.get("failed", 0),
        #     result.get("total_chunks", 0),
        #     result.get("indexed_files", 0),
        #     result.get("code_elements", 0),
        # )

        update_index_job(
            job_id,
            status="completed",
            processed_targets=int(result.get("success", 0) or 0) + int(result.get("failed", 0) or 0),
            success_count=int(result.get("success", 0) or 0),
            failed_count=int(result.get("failed", 0) or 0),
            total_chunks=int(result.get("total_chunks", 0) or 0),
            message=(
                f"success={result.get('success', 0)} "
                f"failed={result.get('failed', 0)} "
                f"indexed_files={result.get('indexed_files', 0)} "
                f"code_elements={result.get('code_elements', 0)}"
            ),
            logs=result.get("logs", []),
            finished=True,
        )

        # logger.info("[main.py][run_index_job] completed job_id=%s", job_id)

    except Exception as error:
        logger.exception("[main.py][run_index_job] failed job_id=%s", job_id)
        update_index_job(
            job_id,
            status="failed",
            message="indexing failed",
            error=str(error),
            finished=True,
        )


# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return PlainTextResponse("CodeMind backend is running.")


@app.get("/status")
def status():
    rag_initialized = getattr(app.state, "rag_initialized", False)
    init_error = getattr(app.state, "init_error", None)
    return build_system_status(settings, rag_initialized, init_error)


@app.get("/users/verify")
def verify_user(user_id: str = Query(...)):
    """사용자 ID가 SQLite users 테이블에 존재하는지 확인합니다."""
    if not user_id or not user_id.strip():
        raise HTTPException(status_code=400, detail="user_id is required")
    uid = user_id.strip()
    exists = user_exists(uid)
    if not exists:
        user_exists(uid)
    return {"user_id": uid, "exists": exists}


# ── Upload ────────────────────────────────────────────────────

@app.post("/upload")
async def upload(
        files: list[UploadFile] = File(...),
        x_user_id: str | None = Header(default=None),
):
    user_id = require_user(x_user_id)

    if not files:
        raise HTTPException(status_code=400, detail="files are required")

    if len(files) > settings.max_files_per_request:
        logger.warning(
            "[main.py][upload] max_files_per_request exceeded file_count=%d max=%d",
            len(files),
            settings.max_files_per_request,
        )
        raise HTTPException(
            status_code=400,
            detail=f"max {settings.max_files_per_request} files are allowed",
        )

    saved_filenames: list[str] = []
    upload_name_map: dict[str, str] = {}

    for upload_file in files:
        logger.info("[main.py][upload] validating file=%s", upload_file.filename)

        if not upload_file.filename or not upload_file.filename.strip():
            logger.warning("[main.py][upload] empty filename detected")
            raise HTTPException(status_code=400, detail="empty filename is not allowed")

        if not is_allowed_upload_extension(upload_file.filename):
            logger.warning("[main.py][upload] unsupported extension file=%s", upload_file.filename)
            raise HTTPException(
                status_code=400,
                detail=f"unsupported upload extension: {upload_file.filename}",
            )
        sanitized_name = safe_filename(upload_file.filename)

        user_upload_dir = upload_dir / user_id
        ensure_dir(user_upload_dir)

        destination = user_upload_dir / sanitized_name
        
        if destination.is_dir():
            logger.warning(
                "[main.py][upload] removing existing directory before save file=%s destination=%s",
                upload_file.filename,
                str(destination),
            )
            shutil.rmtree(destination, ignore_errors=True)
        
        # logger.info("destination path ::: %s", destination)

        await save_upload_stream(upload_file, destination)
        saved_filenames.append(sanitized_name)
        upload_name_map[sanitized_name] = str(destination)

        # logger.info(
        #     "[main.py][upload] saved file original_name=%s sanitized_name=%s",
        #     upload_file.filename,
        #     sanitized_name,
        # )

    raw_targets = await run_in_threadpool(process_uploads_and_collect, destination, saved_filenames, user_id)

    # logger.info(
    #     "[main.py][upload] process_uploads_and_collect completed saved_file_count=%d raw_target_count=%d",
    #     len(saved_filenames),
    #     len(raw_targets or []),
    # )
    # logger.info("============================================================")

    projects_created: dict[str, dict[str, str]] = {}
    normalized_targets: list[dict[str, Any]] = []

    for target in raw_targets:
        project_id = getattr(target, "project_id", None)
        project_name = getattr(target, "project_name", None)
        saved_path = getattr(target, "saved_path", None)

        # logger.info(
        #     "[main.py][upload] raw target project_id=%s project_name=%s relative_path=%s saved_path=%s extension=%s",
        #     project_id,
        #     project_name,
        #     getattr(target, "relative_path", None),
        #     saved_path,
        #     getattr(target, "extension", None),
        # )
        # logger.info("============================================================")

        if project_id and project_id not in projects_created:
            origin_saved_path = upload_name_map.get(project_name, "")
            projects_created[project_id] = {
                "project_name": project_name or "",
                "saved_path": origin_saved_path,
            }
            # logger.info(
            #     "[main.py][upload] project created project_id=%s project_name=%s root_container_name=%s",
            #     project_id,
            #     project_name,
            #     root_container_name,
            # )
            # logger.info("============================================================")

        normalized_targets.append(
            {
                "project_id": project_id,
                "project_name": project_name,
                "saved_path": saved_path,
                "relative_path": getattr(target, "relative_path", None),
                "file_name": getattr(target, "original_name", None),
                "extension": getattr(target, "extension", None),
                "file_size": getattr(target, "size", 0),
            }
        )

    # logger.info(
    #     "[main.py][upload] normalized targets completed project_count=%d target_count=%d",
    #     len(projects_created),
    #     len(normalized_targets),
    # )
    # logger.info("============================================================")

    for project_id, project_info in projects_created.items():
        try:
            # SQLite에 업로드된 파일 정보 저장.
            save_uploaded_file(project_id, project_info["project_name"], user_id, project_info["saved_path"])
        except Exception as error:
            logger.exception("save_uploaded_file failed user_id=%s, project_id=%s error=%s", user_id, project_id, error)

    # logger.info(
    #     "[main.py][upload] completed project_count=%d target_count=%d",
    #     len(projects_created),
    #     len(normalized_targets),
    # )
    # logger.info("============================================================")

    return {
        "targets": normalized_targets,
    }

@app.post("/index-jobs")
async def create_job(
        request: Request,
        background_tasks: BackgroundTasks,
        payload: dict[str, Any] = Body(...),
        x_user_id: str | None = Header(default=None),
):
    user_id = require_user(x_user_id)

    logger.info("[main.py][create_job] start user_id=%s", user_id)

    targets = payload.get("targets", [])
    # "project_id": project_id,
    # "project_name": project_name,
    # "saved_path": saved_path,
    # "relative_path": getattr(target, "relative_path", None),
    # "file_name": getattr(target, "original_name", None),
    # "extension": getattr(target, "extension", None),
    # "file_size": getattr(target, "size", 0), # TODO : pgy : 필요한가

    if not targets:
        logger.warning("[main.py][create_job] targets are required")
        raise HTTPException(status_code=400, detail="targets are required")

    logger.info("[main.py][create_job] raw targets count=%d", len(targets))
    logger.info("============================================================")

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

    # logger.info(
    #     "[main.py][create_job] create_index_job completed job_id=%s project_id=%s project_name=%s total_targets=%d",
    #     job_id,
    #     project_id,
    #     project_name,
    #     len(normalized_targets),
    # )
    # logger.info("============================================================")

    rag_service = get_rag_service(request)
    background_tasks.add_task(run_index_job, rag_service, job_id, normalized_targets)

    # logger.info(
    #     "[main.py][create_job] background task registered job_id=%s target_count=%d",
    #     job_id,
    #     len(normalized_targets),
    # )
    # logger.info("============================================================")

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


# ── Projects ──────────────────────────────────────────────────

@app.get("/projects")
def get_projects(
    x_user_id: str | None = Header(default=None),
):
    """사용자별 프로젝트 목록 조회"""
    user_id = require_user(x_user_id)

    try:
        projects = get_all_projects(user_id)
        normalized = [normalize_project_item(project) for project in projects]
        return {
            "projects": normalized,
            "count": len(normalized),
        }
    except Exception as error:
        logger.exception("get_projects failed")
        raise HTTPException(status_code=500, detail=f"projects failed: {error}") from error


@app.get("/projects/{project_name}")
def get_project(
        project_name: str,
        x_user_id: str | None = Header(default=None),
):
    """사용자별 프로젝트명으로 기존 project_id 조회 (중복 확인용)"""
    user_id = require_user(x_user_id)
    name = (project_name or "").strip()
    logger.info("/projects/%s 진입", name)
    try:
        exists = get_project_by_name(user_id=user_id, project_name=name)
        dup_project_id = exists.get("project_id") if exists else None
        logger.info("중복 확인 dup_project_id : %s", dup_project_id)
        return {"project_id": dup_project_id, "exists": dup_project_id is not None}
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("get_project failed")
        raise HTTPException(status_code=500, detail=f"project failed: {error}") from error
    

@app.delete("/projects/{project_id}")
def delete_project(
        request: Request,
        project_id: str,
        x_user_id: str | None = Header(default=None),
):
    """프로젝트 모든 데이터 삭제 (SQLite 전 테이블 + Qdrant)."""
    user_id = require_user(x_user_id)
    logger.info("프로젝트 삭제 진입 user_id=%s, project_id=%s", user_id, project_id)

    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required !!!")

    try:
        project = get_project_by_id(project_id=project_id)
        pname = project.get("project_name") if project else None
        saved_path = Path(project.get("saved_path") if project else None)

        deleted = {
            "chat_history":   delete_history(project_id=project_id),       # 히스토리 삭제
            "uploaded_files": delete_uploaded_file(project_id=project_id), # 업로드 파일정보 삭제
            "file_index":     delete_file_index(project_id=project_id),    # 업로드 파일정보 삭제
            "index_jobs":     delete_index_job(project_id=project_id),
            "code_elements":  delete_code_elements(project_id=project_id),
            "turn_entities":  delete_turn_entities(project_id=project_id),
        }

        target_extract_dir = extract_dir / user_id / pname

        try:
            if saved_path.exists() and saved_path.is_file():
                saved_path.unlink()  # 파일 삭제
        except Exception:
            logger.exception("failed to remove upload file: %s", saved_path)

        try:
            if target_extract_dir.exists() and target_extract_dir.is_dir():
                shutil.rmtree(target_extract_dir)
        except Exception:
            logger.exception("failed to remove extracted file: %s", target_extract_dir)

        # Qdrant 벡터 삭제
        qdrant_deleted = 0
        try:
            rag_service = get_rag_service(request)
            qdrant_deleted = rag_service.qdrant_service.delete_by_project_id(project_id)
        except Exception as qerr:
            logger.warning("Qdrant delete_by_project_id failed (non-fatal): %s", qerr)

        deleted["qdrant_vectors"] = qdrant_deleted
        logger.info("delete_project done user_id=%s pid=%s deleted=%s", user_id, project_id, deleted)
        return {"deleted": deleted, "old_project_id": project_id}

    except HTTPException:
        raise
    except Exception as error:
        logger.exception("프로젝트 삭제 처리 실패")
        raise HTTPException(status_code=500, detail=f"duplicate project failed: {error}") from error


# ── Ask ───────────────────────────────────────────────────────

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

    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    history_limit = max(1, min(settings.chat_history_turns, 20))
    chat_history = list(reversed(get_history(user_id, project_id=project_id, limit=history_limit)))
    recent_entities = get_recent_entities(user_id, limit=20, project_id=project_id)

    intent = query_analyzer.analyze(question)
    rag_service = get_rag_service(request)

    retrieval_question = (intent.search_query or question).strip()
    logger.info("retrieval_question ::: %s", retrieval_question)

    structure_context = ""
    if intent.query_type != "diagram" and project_id:
        summary = get_file_index_summary(project_id)
        if summary.get("total", 0) > 0:
            structure_context = build_listing_context_summary(summary, intent.extension_filter)
            logger.info("============================================================")
            logger.info("structure_context ::: %s", structure_context)

    sqlite_context = ""
    if project_id and detect_meta_request(question):
        sqlite_context = build_sqlite_context(project_id, project_name or "", question)
        logger.info("============================================================")
        logger.info("sqlite_context ::: %s", sqlite_context)

    generator, hits = await call_ask_with_context_stream(
        rag_service=rag_service,
        question=question,
        retrieval_question=retrieval_question,
        project_id=project_id,
        project_name=project_name,
        extra_context=structure_context or extra_context,
        sqlite_context=sqlite_context,
        top_k=intent.top_k,
        layer_filter=intent.layer_filter,
        extension_filter=intent.extension_filter,
        query_type=intent.query_type,
        chat_history=chat_history,
        recent_entities=recent_entities,
        edit_source=intent.edit_source,         #exact grep (독립 리터럴 검색)
        edit_target=intent.edit_target,         #변경 파일/줄/전후값 중심 답변 패치
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
                    save_history(
                        user_id=user_id,
                        question=question,
                        answer=answer,
                        project_id=project_id,
                    )
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


# ── History ───────────────────────────────────────────────────

@app.get("/history")
def history(
        project_id: str = Query(...),
        limit: int = Query(default=50, ge=1, le=300),
        x_user_id: str | None = Header(default=None),
):
    user_id = require_user(x_user_id)
    logger.info("[HISTORY] GET /history user_id=%s project_id=%s limit=%d", user_id, project_id, limit)
    rows = get_history(user_id=user_id, project_id=project_id, limit=limit)
    logger.info("[HISTORY] 조회 결과 count=%d", len(rows))
    return {
        "history": rows,
        "count": len(rows),
    }


@app.delete("/history/{project_id}")
def clear_history(
        x_user_id: str | None = Header(default=None),
        project_id: str = None,
):
    logger.info("@app.delete(/history) 진입!!!")
    deleted = delete_history(project_id=project_id)
    return {"deleted": deleted}

