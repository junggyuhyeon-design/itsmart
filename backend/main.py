from __future__ import annotations

import logging
import re
import sys
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

import aiofiles
from fastapi import BackgroundTasks, Body, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse

from config import get_settings
from database.history_repository import (
    createindexjob,
    deletehistory,
    getallprojects,
    getcodeelements,
    getfileindex,
    getfileindexsummary,
    gethistory,
    getindexjob,
    gettablerowsforadmin,
    initindexjobstable,
    listdbtables,
    listindexjobs,
    purgeallruntimedata,
    savehistory,
    saveuploadedfile,
    updateindexjob,
    upsertuser,
)
from database.init_db import init_db
from health_service import build_system_status
from rag.query_analyzer import QueryAnalyzer
from rag.rag_service import RAGService
from utils.file_utils import ensure_dir, is_allowed_upload_extension, process_uploads_and_collect, safe_filename

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

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

UPLOAD_DIR = Path(settings.upload_dir)
EXTRACT_DIR = Path("/data/extracted")
analyzer = QueryAnalyzer(default_top_k=settings.top_k)

TABLE_PATTERNS = [
    r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)",
    r"\bJOIN\s+([A-Za-z_][A-Za-z0-9_]*)",
    r"\bUPDATE\s+([A-Za-z_][A-Za-z0-9_]*)",
    r"\bINTO\s+([A-Za-z_][A-Za-z0-9_]*)",
    r"\bTABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
]


class AccessLogFilter(logging.Filter):
    SKIP = ("health", "status", "collections")

    def filter(self, record: logging.LogRecord) -> bool:
        return not any(s in record.getMessage() for s in self.SKIP)


access_logger = logging.getLogger("uvicorn.access")
access_logger.addFilter(AccessLogFilter())
access_logger.setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up...")
    try:
        ensure_dir(UPLOAD_DIR)
        ensure_dir(EXTRACT_DIR)
        init_db()
        initindexjobstable()
        rag = RAGService(settings)
        app.state.ragservice = rag
        app.state.raginitialized = True
        app.state.initerror = None
        logger.info("Startup complete")
    except Exception as e:
        logger.exception("Startup failed: %s", e)
        app.state.ragservice = None
        app.state.raginitialized = False
        app.state.initerror = str(e)
        raise RuntimeError(f"Startup failed: {e}") from e
    yield
    logger.info("Shutdown complete")


app = FastAPI(title="IT-Smart CodeMind API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


def get_rag_service(request: Request) -> RAGService:
    svc = getattr(request.app.state, "ragservice", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="RAGService is not ready")
    return svc


def require_user(x_user_id: str | None) -> str:
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(status_code=400, detail="X-User-Id header is required")
    uid = x_user_id.strip()
    try:
        upsertuser(uid)
    except Exception as e:
        logger.error("upsertuser failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to ensure user") from e
    return uid


async def save_upload_stream(upload: UploadFile, dest: Path) -> None:
    total_written = 0
    try:
        async with aiofiles.open(dest, "wb") as outfile:
            while True:
                chunk = await upload.read(settings.upload_chunk_size)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > settings.max_file_size:
                    dest.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail=f"{upload.filename} exceeds max file size")
                await outfile.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        dest.unlink(missing_ok=True)
        logger.error("save_upload_stream failed for %s: %s", dest.name, e)
        raise HTTPException(status_code=500, detail=f"failed to save upload: {e}") from e


def normalize_project_item(p: dict) -> dict:
    return {
        "project_id": p.get("projectid") or p.get("project_id") or "",
        "project_name": p.get("projectname") or p.get("project_name") or "",
        "uploaded_at": p.get("uploadedat") or p.get("uploaded_at") or "",
    }


def normalize_job_item(job: dict) -> dict:
    return {
        "job_id": job.get("jobid") or job.get("job_id"),
        "project_id": job.get("projectid") or job.get("project_id"),
        "project_name": job.get("projectname") or job.get("project_name"),
        "status": job.get("status"),
        "total_targets": job.get("totaltargets") or job.get("total_targets", 0),
        "processed_targets": job.get("processedtargets") or job.get("processed_targets", 0),
        "success_count": job.get("successcount") or job.get("success_count", 0),
        "failed_count": job.get("failedcount") or job.get("failed_count", 0),
        "total_chunks": job.get("totalchunks") or job.get("total_chunks", 0),
        "message": job.get("message", ""),
        "error": job.get("error", ""),
        "logs": job.get("logs", []),
        "created_at": job.get("createdat") or job.get("created_at"),
        "updated_at": job.get("updatedat") or job.get("updated_at"),
        "finished_at": job.get("finishedat") or job.get("finished_at"),
    }


def normalize_table_item(t: dict) -> dict:
    return {
        "table_name": t.get("tablename") or t.get("table_name"),
        "row_count": t.get("rowcount") or t.get("row_count", 0),
    }


def build_listing_context(summary: dict, extension_filter: str | None) -> str:
    lines: list[str] = []
    files = summary.get("files", []) or []

    if extension_filter:
        filtered = [f for f in files if (f.get("extension") or "") == extension_filter]
        lines.append(f"{extension_filter.upper()} files: {len(filtered)}")
        for f in filtered:
            lines.append(f"- {f.get('relativepath') or f.get('relative_path')}")
    else:
        lines.append(f"Total files: {summary.get('total', 0)}")
        by_ext: dict[str, list[str]] = {}
        for f in files:
            ext = f.get("extension", "")
            rel = f.get("relativepath") or f.get("relative_path")
            by_ext.setdefault(ext, []).append(rel)
        for ext, paths in sorted(by_ext.items()):
            lines.append(f"{ext.upper()} ({len(paths)})")
            for p in paths:
                lines.append(f"- {p}")

    return "\n".join(lines)


def extract_tables_from_text(text: str) -> list[str]:
    found = []
    for pattern in TABLE_PATTERNS:
        for match in re.findall(pattern, text or "", flags=re.IGNORECASE):
            name = (match or "").strip()
            if not name:
                continue
            upper_name = name.upper()
            if upper_name in {"SELECT", "FROM", "WHERE", "AND", "OR", "SET", "VALUES", "RESULTMAP", "DUAL"}:
                continue
            found.append(upper_name)

    deduped = []
    seen = set()
    for t in found:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped


def build_table_listing(project_id: str, project_name: str) -> str:
    elements = getcodeelements(project_id)
    if not elements:
        return f"{project_name} has no code elements."

    table_to_files: dict[str, set[str]] = {}
    table_counter: Counter = Counter()

    for el in elements:
        tables = el.get("tablenames") or el.get("table_names") or []
        if not tables:
            raw_text = el.get("rawtext") or el.get("raw_text_preview") or el.get("rawtextpreview") or ""
            tables = extract_tables_from_text(raw_text)
        rel_path = el.get("relativepath") or el.get("relative_path") or ""
        for table in tables:
            normalized = (table or "").strip().upper()
            if not normalized:
                continue
            table_counter[normalized] += 1
            table_to_files.setdefault(normalized, set()).add(rel_path)

    if not table_counter:
        return f"{project_name} has no detected DB tables."

    lines = [f"{project_name} detected tables: {len(table_counter)}"]
    for table_name, count in table_counter.most_common():
        files = sorted(x for x in table_to_files.get(table_name, set()) if x)
        lines.append(f"- {table_name} ({count})")
        for path in files[:10]:
            lines.append(f"  - {path}")
        if len(files) > 10:
            lines.append(f"  - ... {len(files) - 10} more")

    return "\n".join(lines)


def detect_meta_request(question: str) -> bool:
    q = (question or "").lower()
    keywords = [
        "count", "controller", "service", "repository", "mapper", "xml", "java", "sql",
        "목록", "개수", "테이블", "파일", "구조", "분석", "설명", "소스",
    ]
    return any(k in q for k in keywords)


def build_sqlite_context(project_id: str, project_name: str, question: str) -> str:
    q = (question or "").lower()
    parts: list[str] = [f"SQLite summary for {project_name}"]

    summary = getfileindexsummary(project_id)
    total_files = int(summary.get("total", 0) or 0)
    by_ext = summary.get("byextension", {}) or summary.get("by_extension", {}) or {}
    parts.append(f"- total files: {total_files}")
    if by_ext:
        parts.append("- extensions:")
        for ext, cnt in sorted(by_ext.items(), key=lambda x: (-x[1], x[0])):
            parts.append(f"  - {ext}: {cnt}")

    elements = getcodeelements(project_id)
    if elements:
        layer_counter = Counter()
        for el in elements:
            layer = (el.get("layertype") or el.get("layer_type") or "").strip().lower()
            if layer:
                layer_counter[layer] += 1
        if layer_counter:
            parts.append("- layers:")
            for layer, cnt in layer_counter.most_common():
                parts.append(f"  - {layer}: {cnt}")

    if "table" in q or "테이블" in q:
        parts.append("")
        parts.append(build_table_listing(project_id, project_name))

    return "\n".join(parts)


def normalize_target_item(t: dict) -> dict:
    return {
        "projectid": t.get("projectid") or t.get("project_id"),
        "projectname": t.get("projectname") or t.get("project_name"),
        "savedpath": t.get("savedpath") or t.get("saved_path"),
        "relativepath": t.get("relativepath") or t.get("relative_path"),
        "originalname": t.get("originalname") or t.get("original_name") or t.get("filename"),
        "filename": t.get("filename") or t.get("originalname") or t.get("original_name"),
        "extension": t.get("extension"),
        "size": t.get("size") or t.get("file_size") or 0,
        "filepath": t.get("filepath") or t.get("savedpath") or t.get("saved_path"),
        "sourcetype": t.get("sourcetype") or t.get("source_type") or "",
        "rootcontainername": t.get("rootcontainername") or t.get("root_container_name") or "",
    }


async def call_ask_with_context_stream(
        service: RAGService,
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
        chat_history: list[dict],
):
    if hasattr(service, "askwithcontextstream"):
        return await service.askwithcontextstream(
            question=question,
            projectid=project_id,
            projectname=project_name,
            extracontext=extra_context,
            sqlitecontext=sqlite_context,
            topk=top_k,
            layerfilter=layer_filter,
            extensionfilter=extension_filter,
            querytype=query_type,
            chathistory=chat_history,
        )

    if hasattr(service, "ask_with_context_stream"):
        return await service.ask_with_context_stream(
            question=question,
            projectid=project_id,
            projectname=project_name,
            extracontext=extra_context,
            sqlitecontext=sqlite_context,
            topk=top_k,
            layerfilter=layer_filter,
            extensionfilter=extension_filter,
            querytype=query_type,
            chathistory=chat_history,
        )

    raise RuntimeError("RAGService ask stream method not found")


def summarize_methods(methods: list[dict]) -> str:
    if not methods:
        return ""
    names = []
    for m in methods[:10]:
        name = (m.get("name") or "").strip()
        if name:
            names.append(name)
    return ", ".join(names)


def build_code_element_summary(project_id: str) -> dict:
    elements = getcodeelements(project_id) or []

    layer_counter: Counter = Counter()
    ext_counter: Counter = Counter()
    class_names: list[str] = []
    files: list[str] = []
    method_names: list[str] = []
    table_names: list[str] = []

    seen_classes = set()
    seen_files = set()
    seen_methods = set()
    seen_tables = set()

    for el in elements:
        layer = (el.get("layertype") or el.get("layer_type") or "").strip().lower()
        ext = (el.get("extension") or "").strip().lower()
        rel = (el.get("relativepath") or el.get("relative_path") or "").strip()
        cls = (el.get("classname") or el.get("class_name") or "").strip()

        if layer:
            layer_counter[layer] += 1
        if ext:
            ext_counter[ext] += 1
        if rel and rel not in seen_files:
            seen_files.add(rel)
            files.append(rel)
        if cls and cls not in seen_classes:
            seen_classes.add(cls)
            class_names.append(cls)

        methods = el.get("methods") or []
        for m in methods:
            name = (m.get("name") or "").strip()
            if name and name not in seen_methods:
                seen_methods.add(name)
                method_names.append(name)

        tables = el.get("tablenames") or el.get("table_names") or []
        if not tables:
            raw_text = el.get("rawtext") or el.get("raw_text_preview") or el.get("rawtextpreview") or ""
            tables = extract_tables_from_text(raw_text)
        for table in tables:
            t = (table or "").strip().upper()
            if t and t not in seen_tables:
                seen_tables.add(t)
                table_names.append(t)

    return {
        "elements": elements,
        "layer_counter": layer_counter,
        "ext_counter": ext_counter,
        "class_names": class_names,
        "files": files,
        "method_names": method_names,
        "table_names": table_names,
    }


def build_project_fallback_answer(
        *,
        question: str,
        project_id: str | None,
        project_name: str | None,
        stream_error: str | None = None,
) -> str:
    q = (question or "").strip()
    selected_name = project_name or "선택 프로젝트"

    if not project_id:
        return (
            "질문 처리 중 스트리밍 응답이 끊어졌습니다.\n\n"
            "현재는 프로젝트가 특정되지 않아 코드 기반 분석 fallback을 만들 수 없습니다.\n"
            "사이드바에서 프로젝트를 선택한 뒤 다시 질문해주세요.\n"
            f"- 질문: {q}\n"
            f"- 오류: {stream_error or 'unknown'}"
        )

    summary = getfileindexsummary(project_id) or {}
    meta = build_code_element_summary(project_id)

    total_files = int(summary.get("total", 0) or 0)
    by_ext = summary.get("byextension", {}) or summary.get("by_extension", {}) or {}
    files = meta["files"]
    class_names = meta["class_names"]
    method_names = meta["method_names"]
    layer_counter: Counter = meta["layer_counter"]
    table_names = meta["table_names"]

    lines: list[str] = []
    lines.append(f"{selected_name} 소스를 기준으로 답변합니다.")
    lines.append("")
    lines.append("현재 AI 스트리밍 응답이 중간에 끊어져서, 인덱싱된 코드 메타데이터 기반 fallback 설명으로 전환했습니다.")
    if stream_error:
        lines.append(f"- 스트림 오류: {stream_error}")
    lines.append("")

    lines.append("프로젝트 개요")
    lines.append(f"- 총 파일 수: {total_files}")
    if by_ext:
        ext_text = ", ".join(f"{ext}:{cnt}" for ext, cnt in sorted(by_ext.items(), key=lambda x: (-x[1], x[0])))
        lines.append(f"- 확장자 분포: {ext_text}")

    if layer_counter:
        layer_text = ", ".join(f"{layer}:{cnt}" for layer, cnt in layer_counter.most_common())
        lines.append(f"- 계층 추정: {layer_text}")

    if files:
        lines.append("- 파일 목록:")
        for path in files[:20]:
            lines.append(f"  - {path}")
        if len(files) > 20:
            lines.append(f"  - ... {len(files) - 20}개 추가")

    if class_names:
        lines.append("")
        lines.append("주요 클래스")
        for cls in class_names[:10]:
            lines.append(f"- {cls}")

    if method_names:
        lines.append("")
        lines.append("주요 메서드")
        for m in method_names[:15]:
            lines.append(f"- {m}")

    if table_names:
        lines.append("")
        lines.append("감지된 테이블")
        for t in table_names[:15]:
            lines.append(f"- {t}")

    q_lower = q.lower()

    if any(k in q_lower for k in ["설명", "무슨", "소스", "구조", "hello", "world", "java"]):
        lines.append("")
        lines.append("질문 해석")
        if total_files == 1 and any((ext == "java" and cnt == 1) for ext, cnt in by_ext.items()):
            lines.append("- 이 프로젝트는 단일 Java 파일 중심의 매우 작은 예제로 보입니다.")
        else:
            lines.append("- 이 프로젝트는 업로드된 파일 목록과 코드 요소 기준으로 구조를 요약할 수 있습니다.")

        if class_names:
            lines.append(f"- 대표 클래스: {class_names[0]}")
        if "main" in [m.lower() for m in method_names]:
            lines.append("- main 메서드가 존재하므로 실행 진입점 형태의 예제일 가능성이 높습니다.")

        hello_candidates = []
        for el in meta["elements"][:20]:
            raw = (
                    el.get("rawtext")
                    or el.get("raw_text_preview")
                    or el.get("rawtextpreview")
                    or ""
            )
            if "hello" in raw.lower() or "world" in raw.lower():
                rel = el.get("relativepath") or el.get("relative_path") or el.get("filename") or "unknown"
                hello_candidates.append(rel)

        if hello_candidates:
            unique_hello = []
            seen = set()
            for item in hello_candidates:
                if item not in seen:
                    seen.add(item)
                    unique_hello.append(item)
            lines.append(f"- Hello/World 문자열이 감지된 파일: {', '.join(unique_hello[:5])}")

        lines.append("- 따라서 이 소스는 콘솔에 문자열을 출력하는 기본 Java 학습용/테스트용 예제로 해석할 수 있습니다.")

    if any(k in q_lower for k in ["파일", "목록", "개수", "count"]):
        lines.append("")
        lines.append("파일/개수 기준 답변")
        lines.append(f"- 총 분석 대상 파일 수는 {total_files}개입니다.")
        if files:
            lines.append(f"- 첫 번째 파일은 {files[0]} 입니다.")

    if any(k in q_lower for k in ["테이블", "db", "sql", "schema"]):
        lines.append("")
        if table_names:
            lines.append("DB/테이블 기준 답변")
            for t in table_names[:15]:
                lines.append(f"- {t}")
        else:
            lines.append("DB/테이블 기준 답변")
            lines.append("- 현재 인덱싱된 코드에서는 명시적인 DB 테이블이 감지되지 않았습니다.")

    lines.append("")
    lines.append("권장 조치")
    lines.append("- 이 fallback 답변이 나온다면 Ollama 스트리밍 연결이 불안정한 상태일 가능성이 큽니다.")
    lines.append("- 그래도 이제는 스트림 실패 시에도 메타데이터 기반 기본 설명은 항상 반환됩니다.")

    return "\n".join(lines)


def run_index_job(service: RAGService, job_id: str, targets: list[dict]):
    try:
        updateindexjob(job_id, status="running", message="indexing started")

        def progress(**kwargs):
            updateindexjob(
                job_id,
                status="running",
                processedtargets=kwargs.get("processedtargets"),
                successcount=kwargs.get("successcount"),
                failedcount=kwargs.get("failedcount"),
                totalchunks=kwargs.get("totalchunks"),
                message=kwargs.get("message"),
                error=kwargs.get("error"),
                logs=kwargs.get("logs"),
            )

        result = service.indexfiles(targets, progresscallback=progress)
        final_status = "completed"
        final_message = f"success={result.get('success', 0)}, failed={result.get('failed', 0)}"

        updateindexjob(
            job_id,
            status=final_status,
            processedtargets=result.get("success", 0) + result.get("failed", 0),
            successcount=result.get("success", 0),
            failedcount=result.get("failed", 0),
            totalchunks=result.get("totalchunks", 0),
            message=final_message,
            logs=result.get("logs", []),
            finished=True,
        )
    except Exception as e:
        logger.exception("run_index_job failed job_id=%s", job_id)
        updateindexjob(
            job_id,
            status="failed",
            message="indexing failed",
            error=str(e),
            finished=True,
        )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
def status():
    rag_initialized = getattr(app.state, "raginitialized", False)
    init_error = getattr(app.state, "initerror", None)
    return build_system_status(settings, rag_initialized, init_error)


@app.post("/upload")
async def upload(
        files: List[UploadFile] = File(...),
        x_user_id: str | None = Header(default=None),
):
    require_user(x_user_id)

    if not files:
        raise HTTPException(status_code=400, detail="files are required")

    if len(files) > settings.max_files_per_request:
        raise HTTPException(status_code=400, detail=f"max {settings.max_files_per_request} files are allowed")

    ensure_dir(UPLOAD_DIR)
    saved_filenames: list[str] = []

    for f in files:
        if not f.filename or not f.filename.strip():
            raise HTTPException(status_code=400, detail="empty filename is not allowed")
        if not is_allowed_upload_extension(f.filename):
            raise HTTPException(status_code=400, detail=f"unsupported upload extension: {f.filename}")

        safe_name = safe_filename(f.filename)
        await save_upload_stream(f, UPLOAD_DIR / safe_name)
        saved_filenames.append(safe_name)

    targets = await run_in_threadpool(process_uploads_and_collect, UPLOAD_DIR, saved_filenames)

    projects_created: dict[str, str] = {}
    for target in targets:
        project_id = getattr(target, "project_id", None) or getattr(target, "projectid", None)
        project_name = getattr(target, "project_name", None) or getattr(target, "projectname", None)
        saved_path = getattr(target, "saved_path", None) or getattr(target, "savedpath", None)

        if project_id and project_id not in projects_created:
            projects_created[project_id] = project_name

        origin_path = Path(saved_path) if saved_path else (UPLOAD_DIR / f"{project_name}.zip")

        try:
            saveuploadedfile(project_id, project_name, str(origin_path))
        except Exception as e:
            logger.error("saveuploadedfile failed: %s", e)

    normalized_targets = []
    for t in targets:
        normalized_targets.append(
            {
                "project_id": getattr(t, "project_id", None) or getattr(t, "projectid", None),
                "project_name": getattr(t, "project_name", None) or getattr(t, "projectname", None),
                "saved_path": getattr(t, "saved_path", None) or getattr(t, "savedpath", None),
                "relative_path": getattr(t, "relative_path", None) or getattr(t, "relativepath", None),
                "original_name": getattr(t, "original_name", None) or getattr(t, "originalname", None),
                "filename": getattr(t, "original_name", None) or getattr(t, "originalname", None),
                "extension": getattr(t, "extension", None),
                "size": getattr(t, "size", 0),
                "source_type": getattr(t, "source_type", None) or getattr(t, "sourcetype", None),
                "root_container_name": getattr(t, "root_container_name", None) or getattr(t, "rootcontainername", None),
            }
        )

    logger.info("upload complete: %d targets, %d projects", len(normalized_targets), len(projects_created))
    return {
        "targets": normalized_targets,
        "count": len(normalized_targets),
        "projects": len(projects_created),
    }


@app.post("/index")
async def index_now(request: Request, targets: List[dict] = Body(...)):
    if not targets:
        raise HTTPException(status_code=400, detail="targets are required")

    service = get_rag_service(request)
    normalized_targets = [normalize_target_item(t) for t in targets]

    try:
        result = await run_in_threadpool(service.indexfiles, normalized_targets)
        result["total_chunks"] = int(result.get("totalchunks", result.get("total_chunks", 0)) or 0)
        return result
    except Exception as e:
        logger.exception("index_now failed")
        raise HTTPException(status_code=500, detail=f"index failed: {e}") from e


@app.post("/index/jobs")
async def create_job(
        request: Request,
        background_tasks: BackgroundTasks,
        payload: dict = Body(...),
        x_user_id: str | None = Header(default=None),
):
    user_id = require_user(x_user_id)

    targets = payload.get("targets", [])
    if not targets:
        raise HTTPException(status_code=400, detail="targets are required")

    normalized_targets = [normalize_target_item(t) for t in targets]
    first = normalized_targets[0] if normalized_targets else {}
    project_id = first.get("projectid")
    project_name = first.get("projectname")

    job_id = str(uuid.uuid4())
    createindexjob(
        jobid=job_id,
        userid=user_id,
        projectid=project_id,
        projectname=project_name,
        totaltargets=len(normalized_targets),
        message="queued",
    )

    service = get_rag_service(request)
    background_tasks.add_task(run_index_job, service, job_id, normalized_targets)

    return {
        "job_id": job_id,
        "status": "queued",
        "project_id": project_id,
        "project_name": project_name,
        "total_targets": len(normalized_targets),
    }


@app.get("/index/jobs")
def list_jobs(
        limit: int = 20,
        x_user_id: str | None = Header(default=None),
):
    user_id = require_user(x_user_id)
    jobs = listindexjobs(user_id, limit=limit)
    normalized = [normalize_job_item(job) for job in jobs]
    return {"jobs": normalized, "count": len(normalized)}


@app.get("/index/jobs/{job_id}")
def get_job(
        job_id: str,
        x_user_id: str | None = Header(default=None),
):
    user_id = require_user(x_user_id)
    item = getindexjob(job_id, user_id)
    if not item:
        raise HTTPException(status_code=404, detail="job not found")
    return normalize_job_item(item)


@app.get("/projects")
def list_projects():
    try:
        projects = getallprojects()
        normalized = [normalize_project_item(p) for p in projects]
        return {"projects": normalized, "count": len(normalized)}
    except Exception as e:
        logger.exception("list_projects failed")
        raise HTTPException(status_code=500, detail=f"projects failed: {e}") from e


@app.get("/projects/{project_name}/files")
def list_project_files(project_name: str, extension: str | None = None):
    try:
        project_info = next(
            (p for p in getallprojects() if (p.get("projectname") or p.get("project_name")) == project_name.strip()),
            None,
        )
        if not project_info:
            raise HTTPException(status_code=404, detail=f"project not found: {project_name}")

        pid = project_info.get("projectid") or p.get("project_id")
        files = getfileindex(pid, extension)

        normalized_files = []
        for f in files:
            normalized_files.append(
                {
                    "filename": f.get("filename"),
                    "relative_path": f.get("relativepath") or f.get("relative_path"),
                    "extension": f.get("extension"),
                    "file_size": f.get("filesize") or f.get("file_size", 0),
                    "indexed_at": f.get("indexedat") or f.get("indexed_at"),
                }
            )

        return {
            "project_id": pid,
            "project_name": project_name,
            "extension_filter": extension,
            "files": normalized_files,
            "count": len(normalized_files),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list_project_files failed")
        raise HTTPException(status_code=500, detail=f"project files failed: {e}") from e


@app.get("/admin/db/tables")
def admin_db_tables(x_user_id: str | None = Header(default=None)):
    require_user(x_user_id)
    try:
        tables = listdbtables()
        normalized = [normalize_table_item(t) for t in tables]
        return {"tables": normalized, "count": len(normalized)}
    except Exception as e:
        logger.exception("admin_db_tables failed")
        raise HTTPException(status_code=500, detail=f"db tables failed: {e}") from e


@app.get("/admin/db/rows")
def admin_db_rows(
        table_name: str = Query(...),
        limit: int = Query(200),
        project_id: str | None = Query(default=None),
        x_user_id: str | None = Header(default=None),
):
    require_user(x_user_id)
    try:
        raw_rows = gettablerowsforadmin(tablename=table_name, limit=limit)

        rows = raw_rows
        if project_id:
            filtered = []
            for row in raw_rows:
                row_project_id = row.get("projectid") or row.get("project_id")
                if row_project_id == project_id:
                    filtered.append(row)
            rows = filtered

        columns = list(rows[0].keys()) if rows else []
        return {
            "table_name": table_name,
            "project_id": project_id or "",
            "limit": max(1, min(int(limit or 200), 1000)),
            "total_count": len(raw_rows),
            "returned_count": len(rows),
            "columns": columns,
            "rows": rows,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("admin_db_rows failed")
        raise HTTPException(status_code=500, detail=f"db rows failed: {e}") from e


@app.get("/history")
def list_history(
        limit: int = 100,
        x_user_id: str | None = Header(default=None),
):
    user_id = require_user(x_user_id)
    try:
        rows = gethistory(user_id, limit=limit)
        normalized = []
        for row in rows:
            normalized.append(
                {
                    "id": row.get("id"),
                    "question": row.get("question", ""),
                    "answer": row.get("answer", ""),
                    "created_at": row.get("createdat") or row.get("created_at"),
                }
            )
        return {"history": normalized, "count": len(normalized)}
    except Exception as e:
        logger.exception("list_history failed")
        raise HTTPException(status_code=500, detail=f"history failed: {e}") from e


@app.post("/history")
def create_history(
        payload: dict = Body(...),
        x_user_id: str | None = Header(default=None),
):
    user_id = require_user(x_user_id)
    question = (payload.get("question") or "").strip()
    answer = (payload.get("answer") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    if not answer:
        raise HTTPException(status_code=400, detail="answer is required")

    try:
        history_id = savehistory(user_id, question, answer)
        return {"id": history_id, "question": question, "answer": answer}
    except Exception as e:
        logger.exception("create_history failed")
        raise HTTPException(status_code=500, detail=f"save history failed: {e}") from e


@app.delete("/history")
def clear_history(x_user_id: str | None = Header(default=None)):
    user_id = require_user(x_user_id)
    try:
        deleted = deletehistory(user_id)
        return {"deleted_count": deleted}
    except Exception as e:
        logger.exception("clear_history failed")
        raise HTTPException(status_code=500, detail=f"delete history failed: {e}") from e


@app.delete("/reset")
def reset_all(
        confirm_text: str,
        request: Request,
        x_user_id: str | None = Header(default=None),
):
    require_user(x_user_id)

    if confirm_text != "RESET":
        raise HTTPException(status_code=400, detail="confirm_text must be RESET")

    try:
        result = purgeallruntimedata()

        rag_reset = {
            "attempted": False,
            "success": False,
            "message": "",
        }

        try:
            service = get_rag_service(request)
            if hasattr(service, "reset"):
                rag_reset["attempted"] = True
                service.reset()
                rag_reset["success"] = True
                rag_reset["message"] = "rag reset completed"
            else:
                rag_reset["message"] = "rag reset skipped: reset() not implemented"
        except Exception as rag_error:
            logger.exception("rag reset failed")
            rag_reset["attempted"] = True
            rag_reset["success"] = False
            rag_reset["message"] = f"rag reset failed: {rag_error}"

        return {
            "status": "ok",
            "result": result,
            "rag_reset": rag_reset,
        }
    except Exception as e:
        logger.exception("reset_all failed")
        raise HTTPException(status_code=500, detail=f"reset failed: {e}") from e


@app.get("/ask")
async def ask(
        request: Request,
        question: str,
        project_name: str | None = None,
        top_k: int = 5,
        extra_context: str = "",
        x_user_id: str | None = Header(default=None),
):
    user_id = require_user(x_user_id)

    if not question or not question.strip():
        raise HTTPException(status_code=400, detail="question is required")

    if top_k < 1 or top_k > 20:
        top_k = settings.top_k

    all_projects = getallprojects()
    project_id = None
    selected_project_name = project_name

    if project_name:
        for proj in all_projects:
            pname = proj.get("projectname") or proj.get("project_name")
            pid = proj.get("projectid") or proj.get("project_id")
            if pname == project_name.strip():
                project_id = pid
                selected_project_name = pname
                break
        if not project_id:
            raise HTTPException(status_code=400, detail=f"unknown project_name: {project_name}")

    history_limit = max(1, min(settings.chat_history_turns, 20))
    chat_history = list(reversed(gethistory(user_id, limit=history_limit)))

    intent = analyzer.analyze(question)
    service = get_rag_service(request)

    structure_context = ""
    if getattr(intent, "querytype", None) == "diagram" and project_id:
        summary = getfileindexsummary(project_id)
        if summary.get("total", 0) > 0:
            structure_context = build_listing_context(summary, None)

    sqlite_context = ""
    if project_id and detect_meta_request(question):
        sqlite_context = build_sqlite_context(project_id, selected_project_name, question)

    try:
        gen, _ = await call_ask_with_context_stream(
            service,
            question=question.strip(),
            project_id=project_id,
            project_name=selected_project_name,
            extra_context=structure_context or extra_context,
            sqlite_context=sqlite_context,
            top_k=int(top_k),
            layer_filter=getattr(intent, "layerfilter", None),
            extension_filter=getattr(intent, "extensionfilter", None),
            query_type=getattr(intent, "querytype", "qa"),
            chat_history=chat_history,
        )

        async def safe_stream():
            collected: list[str] = []
            try:
                async for chunk in gen:
                    if chunk:
                        collected.append(chunk)
                        yield chunk
            except Exception as stream_error:
                logger.exception("ask stream failed")
                fallback_text = build_project_fallback_answer(
                    question=question,
                    project_id=project_id,
                    project_name=selected_project_name,
                    stream_error=str(stream_error),
                )
                if collected:
                    yield "\n\n[스트림이 중간에 끊겨 fallback 설명을 이어서 제공합니다]\n\n"
                yield fallback_text

        return StreamingResponse(safe_stream(), media_type="text/plain")

    except Exception as e:
        logger.exception("ask preparation failed")
        fallback_text = build_project_fallback_answer(
            question=question,
            project_id=project_id,
            project_name=selected_project_name,
            stream_error=str(e),
        )
        return PlainTextResponse(fallback_text, status_code=200)