import sys
import os
import logging
from pathlib import Path
from fastapi import FastAPI, Body, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from typing import List

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from rag.rag_service import RAGService
from config import get_settings
from health_service import build_system_status
from utils.file_utils import process_uploads_and_collect

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── /health 요청만 access log에서 제외 ──────────────────────
class HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())
# ────────────────────────────────────────────────────────────

app = FastAPI(title="IT-Smart Bot API")

settings = get_settings()
UPLOAD_DIR = settings.upload_dir
os.makedirs(UPLOAD_DIR, exist_ok=True)

rag_service = None

try:
    rag_service = RAGService(settings)
except Exception as e:
    logger.error(f"초기화 에러: {e}")
    # 모듈 로드 시점에 HTTPException을 raise하면 uvicorn이 앱 자체를 띄우지 못하고 죽음.
    # 여기서는 raise하지 않고 None으로 두고, 각 엔드포인트에서 체크함.
    rag_service = None
    INIT_ERROR = str(e)
else:
    INIT_ERROR = None


def get_rag_service():
    if rag_service is None:
        raise HTTPException(status_code=500, detail=f"서비스 초기화 실패: {INIT_ERROR}")
    return rag_service


@app.post("/upload")
async def upload(files: List[UploadFile] = File(...)):
    """파일을 백엔드(도커 볼륨)에 저장하고 분석 대상 목록을 반환"""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    for f in files:
        dest = Path(UPLOAD_DIR) / f.filename
        content = await f.read()
        dest.write_bytes(content)

    targets = process_uploads_and_collect(Path(UPLOAD_DIR))
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
    chunk_count = 0
    if rag_service is not None:
        try:
            chunk_count = int(rag_service.qdrant_service.count_points() or 0)
        except Exception:
            chunk_count = 0

    result = build_system_status(settings, rag_service is not None, INIT_ERROR)
    result["chunk_count"] = chunk_count
    return result

@app.delete("/reset")
def reset():
    service = get_rag_service()
    service.qdrant_service.delete_all()
    return {"status": "success"}

@app.get("/health")
def health():
    return {"status": "ok", "rag_initialized": rag_service is not None}