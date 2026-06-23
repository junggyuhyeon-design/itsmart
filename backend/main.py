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
from fastapi import Body, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from config import get_settings
from database.history_repository import (
    delete_history,
    get_history,
    save_history,
    save_uploaded_file,
    upsert_user,
)
from database.init_db import init_db
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
)


# ── 공통 헬퍼 ────────────────────────────────────────────────────

def get_rag_service(request: Request) -> RAGService:
    """app.state 에서 RAGService 를 꺼낸다."""
    svc = getattr(request.app.state, "rag_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="RAGService 초기화 중입니다. 잠시 후 다시 시도하세요.")
    return svc


def _require_user(x_user_id: str | None) -> str:
    """헤더에서 user_id 추출 후 users 테이블에 upsert."""
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(status_code=400, detail="X-User-Id 헤더가 필요합니다.")
    uid = x_user_id.strip()
    try:
        upsert_user(uid)
    except Exception as e:
        logger.error("upsert_user 실패: %s", e)
        raise HTTPException(status_code=500, detail="사용자 등록 중 오류가 발생했습니다.")
    return uid


async def _save_upload_stream(upload: UploadFile, dest: Path) -> int:
    """UploadFile 을 청크 단위로 비동기 스트리밍하여 디스크에 저장."""
    total_written = 0
    try:
        async with aiofiles.open(dest, "wb") as out_file:
            while True:
                chunk = await upload.read(settings.upload_chunk_size)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > settings.max_file_size:
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"'{upload.filename}' 파일이 허용 크기 "
                            f"{settings.max_file_size:,} bytes 를 초과했습니다."
                        ),
                    )
                await out_file.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        dest.unlink(missing_ok=True)
        logger.error("파일 저장 실패: %s — %s", dest.name, e)
        raise HTTPException(status_code=500, detail=f"파일 저장 중 오류: {e}") from e
    return total_written


# ── 업로드 & 인덱싱 ──────────────────────────────────────────────

@app.post("/upload")
async def upload(
    request: Request,
    files: List[UploadFile] = File(...),
    x_user_id: str | None = Header(default=None),
):
    """ZIP 파일을 서버에 저장. 업로드 파일은 모든 사용자가 공유."""
    _require_user(x_user_id)

    if not files:
        raise HTTPException(status_code=400, detail="업로드할 파일이 없습니다.")
    if len(files) > settings.max_files_per_request:
        raise HTTPException(
            status_code=400,
            detail=f"한 번에 업로드 가능한 파일 수는 최대 {settings.max_files_per_request}개입니다.",
        )

    os.makedirs(UPLOAD_DIR, exist_ok=True)

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
        dest = Path(UPLOAD_DIR) / safe_filename(f.filename)
        await _save_upload_stream(f, dest)
        try:
            save_uploaded_file(f.filename, str(dest))
        except Exception as e:
            logger.error("업로드 이력 DB 저장 실패: %s", e)

    targets = await run_in_threadpool(process_uploads_and_collect, Path(UPLOAD_DIR))
    logger.info("업로드 완료: %d개 파일 수집", len(targets))
    return {"targets": [t.__dict__ for t in targets], "count": len(targets)}


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


# ── 질문 / 히스토리 ──────────────────────────────────────────────

@app.get("/ask")
async def ask(
    request: Request,
    question: str,
    top_k: int = 5,
    extra_context: str = "",
    x_user_id: str | None = Header(default=None),
):
    """질문에 대한 RAG 스트리밍 응답."""
    _require_user(x_user_id)
    if not question or not question.strip():
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")
    if top_k < 1 or top_k > 20:
        top_k = settings.top_k

    service = get_rag_service(request)
    try:
        gen, _ = await service.ask_with_context_stream(
            question=question.strip(),
            extra_context=extra_context,
            top_k=top_k,
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
    limit: int = 50,
    x_user_id: str | None = Header(default=None),
):
    """해당 사용자의 채팅 히스토리 반환 (최신순)."""
    user_id = _require_user(x_user_id)
    limit = max(1, min(limit, 200))
    rows = get_history(user_id, limit=limit)
    return {"history": rows, "count": len(rows)}


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
    """전체 시스템 상태 반환 (Qdrant, Ollama, 모델, 청크 수)."""
    rag_initialized = getattr(request.app.state, "rag_initialized", False)
    init_error      = getattr(request.app.state, "init_error", None)
    base = build_system_status(settings, rag_initialized, init_error)

    # 청크 수를 직접 조회해 추가
    try:
        svc = get_rag_service(request)
        base["chunk_count"] = svc.qdrant_service.count_points()
    except Exception:
        base["chunk_count"] = 0
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
