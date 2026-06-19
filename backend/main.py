import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

import aiofiles
from config import get_settings
from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import (
    run_in_threadpool,  # 파일 I/O를 비동기로 처리하기 위한 유틸리티
)
from fastapi.responses import StreamingResponse
from rag.rag_service import RAGService
from utils.file_utils import (
    is_allowed_upload_extension,
    process_uploads_and_collect,
    safe_filename,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── /health 요청만 access log에서 제외 ──────────────────────
class HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())
# ────────────────────────────────────────────────────────────

settings = get_settings()         # 설정 로드
UPLOAD_DIR = settings.upload_dir  # 업로드된 파일을 저장할 디렉토리

rag_service = None
INIT_ERROR = None


# Lifespan 이벤트를 사용하여 RAGService 초기화 및 종료 처리
@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag_service, INIT_ERROR
    try:
        rag_service = RAGService(settings)
        logger.info("RAGService init success")
    except Exception as e:
        logger.exception("RAGService init failed")
        INIT_ERROR = str(e)
        rag_service = None
    yield

    logger.info("Application shutdown")


app = FastAPI(title="IT-Smart CodeMind API", lifespan=lifespan)


# RAGService 인스턴스가 초기화되지 않았을 때 예외 처리
def get_rag_service():
    if rag_service is None:
        raise HTTPException(
            status_code=500, detail=f"RAGService init failed: {INIT_ERROR}"
        )
    return rag_service


async def _save_upload_stream(upload: UploadFile, dest: Path) -> int:
    """UploadFile을 청크 단위로 비동기 스트리밍하여 디스크에 저장한다.

    기존 코드 (await f.read() 한 번에 전체 로드)와 차이 :
    - 메모리에는 항상 UPLOAD_CHUNK_SIZE 정도만 적재됨 (대용량 ZIP 에도 안전)
    - aiofiles 사용으로 디스크 쓰기 자체도 이벤트 루프를 막지 않음
    - MAX_FILE_SIZE 초과 시 즉시 중단하고 부분 파일을 정리
    """
    total_written = 0
    try:
        async with aiofiles.open(dest, "wb") as out_file:  # aiofiles로 비동기 파일 열기
            while True:
                chunk = await upload.read(settings.upload_chunk_size)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > settings.max_file_size:
                    raise HTTPException(
                        status_code=413,  # Payload Too Large
                        detail=f"'{upload.filename}' 파일이 허용 크기"
                        f"{settings.max_file_size} bytes 를 초과했습니다.",
                    )
                await out_file.write(chunk)
    except Exception as e:
        if dest.exists():
            dest.unlink(missing_ok=True)  # 부분 파일 삭제
        raise HTTPException(status_code=500, detail=f"파일 저장 중 오류: {e}") from e
    return total_written


# file upload endpoint
@app.post("/upload")
async def upload(files: List[UploadFile] = File(...)):
    """파일을 백엔드(도커 볼륨)에 저장하고 분석 대상 목록을 반환"""

    if len(files) > settings.max_files_per_request:
        raise HTTPException(
            status_code=400,  # Bad Request
            detail=f"한 번에 업로드 가능한 파일 수는 최대 {settings.max_files_per_request}개 입니다.",
        )

    os.makedirs(UPLOAD_DIR, exist_ok=True)

    for f in files:
        if not f.filename:
            raise HTTPException(
                status_code=400, # Bad Request
                detail=f"허용되지 않는 파일 형식입니다: {f.filename}",
            )

        if not is_allowed_upload_extension(f.filename):
            raise HTTPException(
                status_code=400,  # Bad Request
                detail=f"허용되지 않는 파일 형식입니다: {f.filename}",
            )

        dest = Path(UPLOAD_DIR) / safe_filename(f.filename)

        await _save_upload_stream(f, dest)  # 비동기 스트리밍 저장

    # 파일 I/O를 별도 스레드에서 처리
    targets = await run_in_threadpool(process_uploads_and_collect, Path(UPLOAD_DIR))
    return {"targets": [t.__dict__ for t in targets], "count": len(targets)}


@app.post("/index")
def index(targets: List = Body(...)):
    service = get_rag_service()
    result = service.index_files(targets)
    result["total_chunks"] = int(result.get("total_chunks") or 0)
    return result


@app.post("/summary")
def summary(targets: List = Body(...)):
    service = get_rag_service()
    return service.generate_project_summary(targets)


@app.post("/analyze-db")
def analyze_db(targets: List = Body(...)):
    service = get_rag_service()
    db_data = service.analyze_db_relations(targets)
    mermaid = service.generate_source_to_table_mermaid(db_data)
    return {"db_data": db_data, "mermaid": mermaid}


@app.get("/ask")
async def ask(question: str, top_k: int = 3, extra_context: str = ""):
    service = get_rag_service()
    gen, hits = await service.ask_with_context_stream(question + extra_context, top_k)
    return StreamingResponse(gen, media_type="text/plain")


@app.get("/status")
def status():
    service = get_rag_service()
    return {"chunk_count": int(service.qdrant_service.count_points() or 0)}


@app.delete("/reset")
def reset():
    service = get_rag_service()
    service.qdrant_service.delete_all()
    return {"status": "success"}


@app.get("/health")
def health():
    return {"status": "ok", "rag_initialized": rag_service is not None}
