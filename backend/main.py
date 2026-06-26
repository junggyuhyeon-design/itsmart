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
    level=logging.WARNING,                          # 기본: WARNING 이상만 출력
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# 앱 자체 로거는 INFO 유지 (startup·shutdown·오류 등 핵심 메시지)
logging.getLogger("main").setLevel(logging.INFO)
logging.getLogger("rag").setLevel(logging.INFO)
logging.getLogger("database").setLevel(logging.INFO)

# uvicorn access log: /health, /status, Qdrant count 요청 제외
class _AccessLogFilter(logging.Filter):
    _SKIP = ("/health", "/status", "/collections/")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(s in msg for s in self._SKIP)

_access_logger = logging.getLogger("uvicorn.access")
_access_logger.addFilter(_AccessLogFilter())
_access_logger.setLevel(logging.WARNING)   # 나머지 access log 도 경고 이상만

# httpx 내부 로그(Qdrant HTTP 통신 등) 억제
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ── 앱 로거 ──────────────────────────────────────────────────────
logger = logging.getLogger("main")

settings = get_settings()
UPLOAD_DIR = settings.upload_dir


# ── Lifespan ─────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up...")
    try:
        init_db()
        rag = RAGService(settings)
        app.state.rag_service = rag
        app.state.rag_initialized = True
        app.state.init_error = None
        logger.info("Startup complete")
    except Exception as e:
        logger.exception("Startup failed: %s", e)
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


# ── 공통 헬퍼 ────────────────────────────────────────────────────
# 확인 완료.
def get_rag_service(request: Request) -> RAGService:
    """app.state 에서 RAGService 를 꺼낸다."""
    svc = getattr(request.app.state, "rag_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="RAGService 초기화 중입니다. 잠시 후 다시 시도하세요.")
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

# 확인 완료.
async def _save_upload_stream(upload: UploadFile, dest: Path) -> int:
    """UploadFile 을 청크 단위로(1MB) 비동기 스트리밍하여 디스크에 저장."""
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
                        detail=(
                            f"'{upload.filename}' 파일이 허용 크기 "
                            f"{settings.max_file_size} bytes 를 초과했습니다."
                        ),
                    )
                await out_file.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        dest.unlink(missing_ok=True)
        logger.error("파일 저장 실패: %s — %s", dest.name, e)
        raise HTTPException(status_code=500, detail=f"파일 저장 중 오류: {e}") from e
    # 현재 return 값에 대해서 사용하지 않으므로 주석.
    # return total_written


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
            detail=f"한 번에 업로드 가능한 파일 수는 최대 {settings.max_files_per_request}개입니다.",
        )

    os.makedirs(UPLOAD_DIR, exist_ok=True)

    saved_filenames: list[str] = []   # 이번 요청에서 실제 저장된 파일명만 추적
    for f in files:
        if not f.filename or not f.filename.strip():
            raise HTTPException(
                status_code=400,
                detail="파일명이 없습니다."
            )

        if not is_allowed_upload_extension(f.filename):
            raise HTTPException(
                status_code=400,
                detail=f"허용되지 않는 파일 형식입니다: {f.filename}",
            )
        safe_name = safe_filename(f.filename)
        dest = Path(UPLOAD_DIR) / safe_name
        await _save_upload_stream(f, dest)
        saved_filenames.append(safe_name)   # 저장 성공한 파일명 기록

    # 이번에 저장된 파일만 수집 (기존 파일 재수집 방지)
    targets = await run_in_threadpool(
        process_uploads_and_collect, Path(UPLOAD_DIR), saved_filenames
    )

    # 각 프로젝트별 정보 저장
    projects_created = {}
    for target in targets:
        if target.project_id not in projects_created:
            projects_created[target.project_id] = target.project_id
            try:
                zip_file_path = Path(UPLOAD_DIR) / f"{target.project_name}.zip"
                # SQLite 에 프로젝트 정보 저장.
                save_uploaded_file(target.project_id, target.project_name, str(zip_file_path))
            except Exception as e:
                logger.error("프로젝트 정보 DB 저장 실패: %s", e)

    logger.info("업로드 완료: %d개 파일 수집, %d개 프로젝트", len(targets), len(projects_created))
    return {"targets": [t.__dict__ for t in targets], "count": len(targets), "projects": len(projects_created)}


@app.post("/index")
async def index(
    request: Request,
    targets: List = Body(...),
):
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


# ── 프로젝트 관리 ──────────────────────────
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
        all_projects = get_all_projects()
        project_info = next(
            (p for p in all_projects if p["project_name"] == project_name.strip()),
            None,
        )
        if not project_info:
            raise HTTPException(
                status_code=404,
                detail=f"프로젝트 '{project_name}'을(를) 찾을 수 없습니다."
            )

        pid = project_info["project_id"]
        files = get_file_index(pid, extension)
        return {
            "project_id":   pid,
            "project_name": project_info["project_name"],
            "uploaded_at":  project_info["uploaded_at"],
            "extension_filter": extension,
            "files": files,
            "count": len(files),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("프로젝트 파일 조회 실패")
        raise HTTPException(status_code=500, detail=f"조회 중 오류: {e}") from e


# ── 질문 유형 감지 ───────────────────────────────────────────────

_LISTING_KEYWORDS = (
    "목록", "전체", "모든", "몇 개", "몇개", "어떤 파일",
    "list", "all files", "show all", "enumerate",
)
_DIAGRAM_KEYWORDS = (
    "관계도", "다이어그램", "mermaid", "diagram", "flowchart",
    "그려", "그려줘", "표현해", "시각화",
)

def _detect_query_type(question: str) -> str:
    """
    질문 문자열을 보고 처리 전략을 결정한다.
    - 'listing' : 파일 목록/열거 요청 → SQLite file_index 로 응답
    - 'diagram' : 관계도/다이어그램 요청 → file_index 컨텍스트 + Qdrant 병행
    - 'qa'      : 일반 코드 질문 → Qdrant RAG
    """
    q = question.lower()
    if any(k in q for k in _LISTING_KEYWORDS):
        return "listing"
    if any(k in q for k in _DIAGRAM_KEYWORDS):
        return "diagram"
    return "qa"


def _build_listing_context(summary: dict, extension_filter: str | None) -> str:
    """file_index_summary 를 LLM 에 주입할 텍스트로 변환."""
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


def _extract_extension_filter(question: str) -> str | None:
    """질문에서 확장자 필터를 추출. 예: 'xml 파일 목록' → 'xml'"""
    import re
    m = re.search(r"\b(xml|java|sql|py|json|yml|yaml|md|txt)\b", question.lower())
    return m.group(1) if m else None


# ── 질문 / 히스토리 ──────────────────────────────────────────────

@app.get("/ask")
async def ask(
    request: Request,
    question: str,
    project_name: str | None = None,
    top_k: int = 5,
    extra_context: str = "",
    x_user_id: str | None = Header(default=None),
):
    """질문에 대한 RAG 스트리밍 응답. project_name이 없으면 모든 프로젝트 대상."""
    user_id = _require_user(x_user_id)
    if not question or not question.strip():
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")
    if top_k < 1 or top_k > 20:
        top_k = settings.top_k

    # 전체 프로젝트 목록 조회
    all_projects = get_all_projects()

    # project_name 검증 (지정된 경우)
    project_id = None
    selected_project_name = project_name
    if project_name:
        for proj in all_projects:
            if proj["project_name"] == project_name.strip():
                project_id = proj["project_id"]
                selected_project_name = proj["project_name"]
                break
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail=f"프로젝트 '{project_name}'을(를) 찾을 수 없습니다. 사용 가능한 프로젝트: {[p['project_name'] for p in all_projects]}"
            )

    history_limit = max(1, min(settings.chat_history_turns, 20))
    recent_rows = get_history(user_id, limit=history_limit)
    chat_history = list(reversed(recent_rows))

    service = get_rag_service(request)
    try:
        projects_info = "\n".join([f"- {p['project_name']}" for p in all_projects])
        query_type = _detect_query_type(question)

        # ── listing: Qdrant 전혀 사용 안 함, SQLite → 직접 스트리밍 ─────
        if query_type == "listing" and project_id:
            ext_filter = _extract_extension_filter(question)
            summary = get_file_index_summary(project_id)
            if summary["total"] > 0:
                listing_text = _build_listing_context(summary, ext_filter)

                async def _listing_stream():
                    # LLM 없이 file_index 결과를 그대로 스트리밍
                    prompt = (
                        f"아래는 프로젝트 '{selected_project_name}'의 "
                        f"{'전체' if not ext_filter else ext_filter.upper()} 파일 목록입니다.\n\n"
                        f"{listing_text}"
                    )
                    yield prompt

                return StreamingResponse(_listing_stream(), media_type="text/plain")
            # file_index 없음 → RAG fallback (아래 qa 분기로 계속)
            logger.info("file_index 없음 — RAG fallback (project_id=%s)", project_id)

        # ── diagram: file_index 전체 구조 + Qdrant 청크 병행 주입 ─────────
        if query_type == "diagram" and project_id:
            summary = get_file_index_summary(project_id)
            struct_ctx = _build_listing_context(summary, None) if summary["total"] > 0 else ""
            dynamic_top_k = min(settings.top_k * 10, 80)
            merged_context = "\n\n".join(filter(None, [
                f"사용 가능한 프로젝트:\n{projects_info}",
                f"현재 프로젝트: {selected_project_name}",
                struct_ctx,
                extra_context,
            ]))
            gen, _ = await service.ask_with_context_stream(
                question=question.strip(),
                project_id=project_id,
                extra_context=merged_context,
                top_k=dynamic_top_k,
                chat_history=chat_history,
            )
            return StreamingResponse(gen, media_type="text/plain")

        # ── qa (기본): 순수 Qdrant RAG ───────────────────────────────────
        if project_id:
            base_ctx = f"사용 가능한 프로젝트:\n{projects_info}\n\n현재 프로젝트: {selected_project_name}"
        else:
            base_ctx = (
                f"사용 가능한 프로젝트:\n{projects_info}\n\n"
                "질문을 분석하여 관련 프로젝트를 판단하고 답변하세요. "
                "답변할 때는 어느 프로젝트의 정보를 사용했는지 명시하세요."
            )
        context_with_projects = "\n\n".join(filter(None, [base_ctx, extra_context]))

        gen, _ = await service.ask_with_context_stream(
            question=question.strip(),
            project_id=project_id,
            extra_context=context_with_projects,
            top_k=top_k,
            chat_history=chat_history,
        )
        return StreamingResponse(gen, media_type="text/plain")
    except Exception as e:
        logger.error("ask 처리 실패: %s", e)
        raise HTTPException(status_code=500, detail=f"질문 처리 중 오류: {e}") from e

@app.post("/history")
def add_history(
    payload: dict = Body(...),
    x_user_id: str | None = Header(default=None),
):
    """스트리밍 완료 후 프론트에서 question + answer 를 저장."""
    user_id = _require_user(x_user_id)
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
def list_history(
    limit: int,
    x_user_id: str | None = Header(default=None),
):
    """History 복원(최초 1회) 해당 사용자의 채팅 히스토리 반환 (최신순)."""
    user_id = _require_user(x_user_id) # Header 에서 user_id 를 가져와 User 테이블 저장 후 리턴.
    rows = get_history(user_id, limit=limit)
    return {"history": rows}


@app.delete("/history")
def clear_history(
    x_user_id: str | None = Header(default=None),
):
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
        svc = get_rag_service(request)
        base["chunk_count"] = svc.qdrant_service.count_points()
    except Exception:
        base["chunk_count"] = 0

    # 프로젝트 정보 추가
    try:
        projects = get_all_projects()
        base["projects"] = projects
        base["project_count"] = len(projects)
    except Exception:
        base["projects"] = []
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
        svc = get_rag_service(request)
        chunk_count = svc.qdrant_service.count_points()
        return {"status": "ok", "qdrant": "connected", "chunk_count": chunk_count}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
