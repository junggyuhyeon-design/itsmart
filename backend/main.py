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

# 확인 완료
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

# def build_project_structure_context(project_id: str, project_name: str | None = None) -> str:
#     """프로젝트 파일 구조를 조회하여 프롬프트 작성"""
#     summary = get_file_index_summary(project_id)
#     total = summary.get("total", 0)
#     by_extension = summary.get("by_extension", {})
#     files = summary.get("files", [])

#     lines: list[str] = []
#     lines.append("### 프로젝트 파일 구조 정보")
#     if project_name:
#         lines.append(f"- 프로젝트명: {project_name}")
#     lines.append(f"- 전체 파일 수: {total}")

#     if by_extension:
#         lines.append("- 확장자별 파일 수:")
#         for ext, cnt in by_extension.items():
#             lines.append(f"  - .{ext}: {cnt}")

#     if files:
#         lines.append("- 파일 목록:") # 최대 300개로 제한.
#         for f in files[:300]:
#             rel = f.get("relative_path", "")
#             ext = f.get("extension", "")
#             lines.append(f"  - [{ext}] {rel}")

#         if len(files) > 300:
#             lines.append(f"  - ... 생략 {len(files) - 300}건")

#     lines.append("")
#     return "\n".join(lines)


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

# 확인 완료
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


# @app.get("/projects/{project_id}/files")
# def list_project_files(project_id: str, project_name: str, extension: str | None = None):
#     """
#     특정 프로젝트의 인덱싱된 파일 목록 반환.
#     extension 파라미터로 필터 가능 (예: ?extension=xml).
#     """
#     try:
#         pid   = project_id
#         pnm   = project_name
#         files = get_file_index(pid, extension)
#         return {
#             "project_id":       pid,
#             "project_name":     pnm,
#             "file_name":        files.file_name,
#             "relative_path":    files.relative_path,
#             "extension":        files.extension,
#             "files":            files,
#             "count":            len(files),
#         }
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.exception("프로젝트 파일 조회 실패")
#         raise HTTPException(status_code=500, detail=f"조회 중 오류: {e}") from e


# ── 질문 ─────────────────────────────────────────────────────────
# 확인 완료
@app.get("/ask")
async def ask(
    request:       Request,
    question:      str,
    project_id:    str | None = None,
    project_name:  str | None = None,
    x_user_id:     str | None = Header(default=None),
):
    """질문에 대한 RAG 스트리밍 응답."""
    user_id = _require_user(x_user_id)
    if not question or not question.strip():
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")

    # 1. 질문 분석 (검색 쿼리 정제 + 전략 결정)
    intent = _analyzer.analyze(question)
    logger.info(
        "질문 분석 — type=%s top_k=%d layer=%s ext=%s hint=%r search_query=%r",
        intent.query_type, intent.top_k,
        intent.layer_filter, intent.extension_filter, intent.search_query,
    )

    # 2. 히스토리 조회 (DB)
    chat_history = list(reversed(get_history(user_id, limit=settings.chat_history_turns)))

    # 3. 파일구조: diagram 타입일 때만 주입 (소형 모델은 파일구조가 크면 소스코드를 밀어냄)
    extra_context = ""
    # if project_id and intent.query_type == "diagram":
    #     extra_context = build_project_structure_context(project_id, project_name)

    # 4. Qdrant 검색 + Ollama 스트리밍
    service = get_rag_service(request)
    try:
        gen, _ = await service.ask_with_context_stream(
            question=question.strip(),
            search_query=intent.search_query,
            project_id=project_id,
            project_name=project_name,
            extra_context=extra_context,
            chat_history=chat_history,
            top_k=intent.top_k,
            layer_filter=intent.layer_filter,
            extension_filter=intent.extension_filter,
            query_type=intent.query_type,
        )
        return StreamingResponse(gen, media_type="text/plain")

    except Exception as e:
        logger.error("ask 처리 실패: %s", e)
        raise HTTPException(status_code=500, detail=f"질문 처리 중 오류: {e}") from e

# 확인 완료
@app.get("/diagram")
async def diagram(
    request:       Request,
    project_id:    str,
    project_name:  str | None = None,
    entity_filter: str | None = None,
    x_user_id:     str | None = Header(default=None),
):
    """
    프로젝트 소스를 정적 분석해 Mermaid 다이어그램(소스↔테이블 관계도)을 반환.
    entity_filter 가 있으면 해당 테이블/클래스와 관련된 노드만 추출.
    LLM 없이 RAGService.analyze_db_relations() 로 직접 생성.
    """
    _require_user(x_user_id)
    service = get_rag_service(request)

    try:
        files = get_file_index(project_id)
        if not files:
            raise HTTPException(
                status_code=404,
                detail="인덱싱된 파일이 없습니다. 먼저 인덱싱을 실행하세요.",
            )

        targets = [{"project_id": project_id, "project_name": project_name}]
        db_data = await run_in_threadpool(service.analyze_db_relations, targets)

        if not db_data.get("tables"):
            return {
                "mermaid": None,
                "message": (
                    "SQL 테이블 정의를 찾지 못했습니다. "
                    ".sql 파일이 인덱싱되었는지 확인하세요."
                ),
                "tables": [],
            }

        # entity_filter 가 있으면 관련 노드만 추출
        entity_upper = entity_filter.strip().upper() if entity_filter else None
        # TODO : 하단 부터 개선 필요. 중복되는 내용 제거해야함.
        mermaid_code = service.generate_source_to_table_mermaid(db_data)

        # 필터 적용 후 실제 포함된 테이블만 집계
        filtered_tables = (
            [t for t in db_data["tables"] if entity_upper in t]
            if entity_upper else db_data["tables"]
        )
        filtered_relations = (
            [r for r in db_data["relations"]
             if entity_upper in r["table"] or entity_upper in r.get("entity_name", "").upper()]
            if entity_upper else db_data["relations"]
        )

        return {
            "mermaid":        mermaid_code,
            "tables":         filtered_tables if entity_upper else db_data["tables"],
            "relation_count": len(filtered_relations),
            "entity_filter":  entity_upper,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("diagram 생성 실패")
        raise HTTPException(status_code=500, detail=f"다이어그램 생성 중 오류: {e}") from e


# ── 히스토리 ─────────────────────────────────────────────────────
# 확인 완료
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

# 확인 완료
@app.get("/history")
def list_history(limit: int, x_user_id: str | None = Header(default=None)):
    """History 복원(최초 1회) 해당 사용자의 채팅 히스토리 반환 (최신순)."""
    user_id = _require_user(x_user_id)
    return {"history": get_history(user_id, limit=limit)}

# 확인 완료
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
