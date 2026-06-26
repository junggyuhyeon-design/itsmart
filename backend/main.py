"""
IT-Smart CodeMind — FastAPI 백엔드 진입점
"""
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

import aiofiles
from config import get_settings
from database.history_repository import (
    delete_history,
    get_all_projects,
    get_file_index,
    get_file_index_summary,
    get_history,
    save_history,
    save_uploaded_file,
    upsert_user,
)
from database.init_db import init_db
from fastapi import Body, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from health_service import build_system_status
from rag.query_analyzer import QueryAnalyzer
from rag.rag_service import RAGService
from utils.file_utils import (
    is_allowed_upload_extension,
    process_uploads_and_collect,
    safe_filename,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

# ── 로깅 설정 ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("main").setLevel(logging.INFO)
logging.getLogger("rag").setLevel(logging.INFO)
logging.getLogger("database").setLevel(logging.INFO)


class _AccessLogFilter(logging.Filter):
    _SKIP = ("/health", "/status", "/collections/")

    def filter(self, record: logging.LogRecord) -> bool:
        return not any(s in record.getMessage() for s in self._SKIP)


_access_logger = logging.getLogger("uvicorn.access")
_access_logger.addFilter(_AccessLogFilter())
_access_logger.setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ── 앱 전역 ──────────────────────────────────────────────────────
logger    = logging.getLogger("main")
settings  = get_settings()
UPLOAD_DIR = settings.upload_dir
_analyzer  = QueryAnalyzer(default_top_k=settings.top_k)


# ── Lifespan ─────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up...")
    try:
        init_db()
        rag = RAGService(settings)
        app.state.rag_service     = rag
        app.state.rag_initialized = True
        app.state.init_error      = None
        logger.info("Startup complete")
    except Exception as e:
        logger.exception("Startup failed: %s", e)
        raise RuntimeError(f"Startup failed: {e}") from e
    yield
    logger.info("Shutdown complete")


app = FastAPI(title="IT-Smart CodeMind API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"],
    allow_headers=["*"], allow_credentials=True,
)


# ── 공통 헬퍼 ────────────────────────────────────────────────────
# 확인 완료.
def get_rag_service(request: Request) -> RAGService:
    """app.state 에서 RAGService 를 꺼낸다."""
    svc = getattr(request.app.state, "rag_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="RAGService 초기화 중입니다.")
    return svc

# 확인 완료.
def _require_user(x_user_id: str | None) -> str:
    """헤더에서 user_id 추출 후 users 테이블에 upsert."""
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(status_code=400, detail="X-User-Id 헤더가 필요합니다.")
    uid = x_user_id.strip()
    try:
        upsert_user(uid) # SQLite User 테이블에 user_id 저장.
    except Exception as e:
        logger.error("upsert_user 실패: %s", e)
        raise HTTPException(status_code=500, detail="사용자 등록 중 오류가 발생했습니다.")
    return uid


async def _save_upload_stream(upload: UploadFile, dest: Path) -> None:
    total_written = 0
    try:
        async with aiofiles.open(dest, "wb") as out_file:
            while True:
                chunk = await upload.read(settings.upload_chunk_size)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > settings.max_file_size:
                    dest.unlink(missing_ok=True) # 업로드 실패 시 중간에 생성된 불완전한 파일을 정리(삭제)
                    raise HTTPException(
                        status_code=413,
                        detail=f"'{upload.filename}' 파일이 허용 크기를 초과했습니다.",
                    )
                await out_file.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        dest.unlink(missing_ok=True)
        logger.error("파일 저장 실패: %s — %s", dest.name, e)
        raise HTTPException(status_code=500, detail=f"파일 저장 중 오류: {e}") from e


def _build_listing_context(summary: dict, extension_filter: str | None) -> str:
    """file_index_summary → 사람이 읽을 수 있는 텍스트."""
    lines: list[str] = []
    if extension_filter:
        filtered = [f for f in summary["files"] if f["extension"] == extension_filter]
        lines.append(f"[{extension_filter.upper()} 파일 목록 — 총 {len(filtered)}개]")
        for f in filtered:
            lines.append(f"  - {f['relative_path']}")
    else:
        lines.append(f"[프로젝트 전체 파일 목록 — 총 {summary['total']}개]")
        by_ext: dict[str, list] = {}
        for f in summary["files"]:
            by_ext.setdefault(f["extension"], []).append(f["relative_path"])
        for ext, paths in sorted(by_ext.items()):
            lines.append(f"\n  [{ext.upper()}] {len(paths)}개")
            for p in paths:
                lines.append(f"    - {p}")
    return "\n".join(lines)


# ── 업로드 & 인덱싱 ──────────────────────────────────────────────
# 확인 완료
@app.post("/upload")
async def upload(
    files: List[UploadFile] = File(...),
    x_user_id: str | None = Header(default=None),
):
    """ZIP 파일을 서버에 저장. 각 ZIP마다 project_id 생성."""
    _require_user(x_user_id)

    if not files:
        raise HTTPException(status_code=400, detail="업로드할 파일이 없습니다.")
    if len(files) > settings.max_files_per_request:
        raise HTTPException(
            status_code=400,
            detail=f"한 번에 최대 {settings.max_files_per_request}개까지 업로드 가능합니다.",
        )

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    saved_filenames: list[str] = []  # 이번 요청에서 실제 저장된 파일명만 추적

    for f in files:
        if not f.filename or not f.filename.strip():
            raise HTTPException(status_code=400, detail="파일명이 없습니다.")
        if not is_allowed_upload_extension(f.filename):
            raise HTTPException(
                status_code=400,
                detail=f"허용되지 않는 파일 형식입니다: {f.filename}",
            )
        safe_name = safe_filename(f.filename)
        await _save_upload_stream(f, Path(UPLOAD_DIR) / safe_name)
        saved_filenames.append(safe_name)  # 저장 성공한 파일명 기록

    # 이번에 저장된 파일만 수집 (기존 파일 재수집 방지)
    targets = await run_in_threadpool(
        process_uploads_and_collect, Path(UPLOAD_DIR), saved_filenames
    )

    # 각 프로젝트별 정보 저장
    projects_created = {}
    for target in targets:
        if target.project_id not in projects_created:
            projects_created[target.project_id] = target.project_name
            try:
                zip_file_path = Path(UPLOAD_DIR) / f"{target.project_name}.zip"
                # SQLite 에 프로젝트 정보 저장.
                save_uploaded_file(target.project_id, target.project_name, str(zip_file_path))
            except Exception as e:
                logger.error("프로젝트 DB 저장 실패: %s", e)

    logger.info("업로드 완료: %d개 파일, %d개 프로젝트", len(targets), len(projects_created))
    return {
        "targets":  [t.__dict__ for t in targets],
        "count":    len(targets),
        "projects": len(projects_created),
    }


@app.post("/index")
async def index(request: Request, targets: List = Body(...)):
    """수집된 파일 목록을 Qdrant 에 인덱싱."""
    if not targets:
        raise HTTPException(status_code=400, detail="인덱싱할 파일 목록이 없습니다.")
    service = get_rag_service(request)
    try:
        result = await run_in_threadpool(service.index_files, targets)
        result["total_chunks"] = int(result.get("total_chunks") or 0)
        logger.info("인덱싱 완료: %d chunks", result["total_chunks"])
        return result
    except Exception as e:
        logger.exception("인덱싱 실패")
        raise HTTPException(status_code=500, detail=f"인덱싱 중 오류: {e}") from e


# ── 프로젝트 관리 ─────────────────────────────────────────────────
# 확인 완료
@app.get("/projects")
def list_projects():
    """전체 프로젝트 목록 반환."""
    try:
        projects = get_all_projects()
        return {"projects": projects, "count": len(projects)}
    except Exception as e:
        logger.exception("프로젝트 목록 조회 실패")
        raise HTTPException(status_code=500, detail=f"프로젝트 조회 중 오류: {e}") from e


@app.get("/projects/{project_name}/files")
def list_project_files(project_name: str, extension: str | None = None):
    """
    특정 프로젝트의 인덱싱된 파일 목록 반환.
    extension 파라미터로 필터 가능 (예: ?extension=xml).
    """
    try:
        project_info = next(
            (p for p in get_all_projects() if p["project_name"] == project_name.strip()),
            None,
        )
        if not project_info:
            raise HTTPException(status_code=404, detail=f"프로젝트 '{project_name}'을(를) 찾을 수 없습니다.")
        pid   = project_info["project_id"]
        files = get_file_index(pid, extension)
        return {
            "project_id":       pid,
            "project_name":     project_info["project_name"],
            "uploaded_at":      project_info["uploaded_at"],
            "extension_filter": extension,
            "files":            files,
            "count":            len(files),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("프로젝트 파일 조회 실패")
        raise HTTPException(status_code=500, detail=f"조회 중 오류: {e}") from e


# ── 질문 ─────────────────────────────────────────────────────────

@app.get("/ask")
async def ask(
    request:       Request,
    question:      str,
    project_name:  str | None = None,
    top_k:         int        = 5,
    extra_context: str        = "",
    x_user_id:     str | None = Header(default=None),
):
    """질문에 대한 RAG 스트리밍 응답. project_name 이 없으면 전체 프로젝트 대상."""
    user_id = _require_user(x_user_id)
    if not question or not question.strip():
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")
    if top_k < 1 or top_k > 20:
        top_k = settings.top_k

    # 전체 프로젝트 목록 조회
    all_projects = get_all_projects()

    # project_name → project_id 변환
    project_id            = None
    selected_project_name = project_name
    if project_name:
        for proj in all_projects:
            if proj["project_name"] == project_name.strip():
                project_id            = proj["project_id"]
                selected_project_name = proj["project_name"]
                break
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"프로젝트 '{project_name}'을(를) 찾을 수 없습니다. "
                    f"사용 가능: {[p['project_name'] for p in all_projects]}"
                ),
            )

    history_limit = max(1, min(settings.chat_history_turns, 20))
    chat_history  = list(reversed(get_history(user_id, limit=history_limit)))

    intent  = _analyzer.analyze(question)
    service = get_rag_service(request)

    try:
        # ── listing: Qdrant 없이 SQLite 직접 스트리밍 ────────────
        if intent.query_type == "listing" and project_id:
            summary = get_file_index_summary(project_id)
            if summary["total"] > 0:
                listing_text = _build_listing_context(summary, intent.extension_filter)

                async def _listing_stream():
                    ext_label = intent.extension_filter.upper() if intent.extension_filter else "전체"
                    yield f"프로젝트 '{selected_project_name}'의 {ext_label} 파일 목록입니다.\n\n{listing_text}"

                return StreamingResponse(_listing_stream(), media_type="text/plain")
            logger.info("file_index 없음 — qa fallback (project_id=%s)", project_id)

        # ── diagram: file_index 전체 구조 컨텍스트 추가 ──────────
        struct_ctx = ""
        if intent.query_type == "diagram" and project_id:
            summary = get_file_index_summary(project_id)
            if summary["total"] > 0:
                struct_ctx = _build_listing_context(summary, None)

        # ── Qdrant + Ollama 스트리밍 ──────────────────────────────
        gen, _ = await service.ask_with_context_stream(
            question=question.strip(),
            project_id=project_id,
            project_name=selected_project_name,
            extra_context=struct_ctx or extra_context,
            top_k=intent.top_k,
            layer_filter=intent.layer_filter,
            extension_filter=intent.extension_filter,
            query_type=intent.query_type,
            chat_history=chat_history,
        )
        return StreamingResponse(gen, media_type="text/plain")

    except Exception as e:
        logger.error("ask 처리 실패: %s", e)
        raise HTTPException(status_code=500, detail=f"질문 처리 중 오류: {e}") from e


# ── 히스토리 ─────────────────────────────────────────────────────

@app.post("/history")
def add_history(
    payload:   dict      = Body(...),
    x_user_id: str | None = Header(default=None),
):
    user_id  = _require_user(x_user_id)
    question = (payload.get("question") or "").strip()
    answer   = (payload.get("answer")   or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 이 비어 있습니다.")
    if not answer:
        raise HTTPException(status_code=400, detail="answer 가 비어 있습니다.")
    try:
        row_id = save_history(user_id, question, answer)
        return {"id": row_id, "status": "saved"}
    except Exception as e:
        logger.error("히스토리 저장 실패: %s", e)
        raise HTTPException(status_code=500, detail=f"히스토리 저장 중 오류: {e}") from e


@app.get("/history")
def list_history(limit: int, x_user_id: str | None = Header(default=None)):
    """History 복원(최초 1회) 해당 사용자의 채팅 히스토리 반환 (최신순)."""
    user_id = _require_user(x_user_id)
    return {"history": get_history(user_id, limit=limit)}


@app.delete("/history")
def clear_history(x_user_id: str | None = Header(default=None)):
    """해당 사용자의 채팅 히스토리 전체 삭제."""
    user_id = _require_user(x_user_id)
    try:
        deleted = delete_history(user_id)
        return {"deleted": deleted, "status": "ok"}
    except Exception as e:
        logger.exception("히스토리 삭제 실패")
        raise HTTPException(status_code=500, detail=f"히스토리 삭제 중 오류: {e}") from e


# ── 상태 / 초기화 ─────────────────────────────────────────────────

@app.get("/status")
def status(request: Request):
    """전체 시스템 상태 반환 (Qdrant, Ollama, 모델, 청크 수, 프로젝트 정보)."""
    rag_initialized = getattr(request.app.state, "rag_initialized", False)
    init_error      = getattr(request.app.state, "init_error", None)
    base = build_system_status(settings, rag_initialized, init_error)

    # 청크 수를 직접 조회해 추가
    try:
        base["chunk_count"] = get_rag_service(request).qdrant_service.count_points()
    except Exception:
        base["chunk_count"] = 0

    try:
        projects            = get_all_projects()
        base["projects"]    = projects
        base["project_count"] = len(projects)
    except Exception:
        base["projects"]      = []
        base["project_count"] = 0

    return base


@app.delete("/reset")
async def reset(request: Request, confirm_text: str):
    if confirm_text != "RESET":
        raise HTTPException(status_code=400, detail="초기화하려면 confirm_text=RESET 을 전달하세요.")
    service = get_rag_service(request)
    try:
        await run_in_threadpool(service.reset)
        logger.info("벡터 DB 초기화 완료")
        return {"status": "success", "message": "벡터 DB 초기화 완료"}
    except Exception as e:
        logger.error("reset 실패: %s", e)
        raise HTTPException(status_code=500, detail=f"초기화 중 오류: {e}") from e


@app.get("/health")
def health(request: Request):
    """기본 헬스체크 (로드밸런서/Docker healthcheck 용)."""
    rag_ok = getattr(request.app.state, "rag_initialized", False)
    if not rag_ok:
        raise HTTPException(status_code=503, detail="RAGService 초기화 중")
    try:
        chunk_count = get_rag_service(request).qdrant_service.count_points()
        return {"status": "ok", "qdrant": "connected", "chunk_count": chunk_count}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
