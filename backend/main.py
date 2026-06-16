import sys
import os
from pathlib import Path
from fastapi import FastAPI, Body, HTTPException
from fastapi.responses import StreamingResponse
from typing import List

# 현재 파일(backend/main.py)의 부모의 부모인 루트 폴더를 경로에 추가
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

# 패키지 경로로 임포트
from backend.rag.rag_service import RAGService
from backend.config import get_settings

app = FastAPI(title="IT-Smart Bot API")

# 업로드 디렉토리 설정: 환경 변수 UPLOAD_DIR 사용
# 기본값을 /data/uploads로 설정하여 로컬 프로젝트 폴더(/app)와 분리합니다.
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/data/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 전역 서비스 초기화
try:
    settings = get_settings()
    rag_service = RAGService(settings)
except Exception as e:
    print(f"초기화 에러: {e}")
    raise HTTPException(status_code=500, detail=f"서비스 초기화 실패: {e}")

@app.post("/index")
def index(targets: List = Body(...)):
    """파일 인덱싱 엔드포인트"""
    return rag_service.index_files(targets)

@app.post("/summary")
def summary(targets: List = Body(...)):
    """프로젝트 구조 요약 엔드포인트"""
    return rag_service.generate_project_summary(targets)

@app.post("/analyze-db")
def analyze_db(targets: List = Body(...)):
    """DB 관계 분석 엔드포인트"""
    db_data = rag_service.analyze_db_relations(targets)
    mermaid = rag_service.generate_source_to_table_mermaid(db_data)
    return {"db_data": db_data, "mermaid": mermaid}

@app.get("/ask")
async def ask(question: str, top_k: int = 3, extra_context: str = ""):
    """RAG 질문 답변 엔드포인트 (스트리밍)"""
    gen, hits = await rag_service.ask_with_context_stream(question + extra_context, top_k)
    return StreamingResponse(gen, media_type="text/plain")

@app.get("/status")
def status():
    """Qdrant 상태 조회 엔드포인트"""
    return {"chunk_count": rag_service.qdrant_service.count_points()}

@app.delete("/reset")
def reset():
    """데이터 전체 초기화 엔드포인트"""
    rag_service.qdrant_service.delete_all()
    return {"status": "success"}

@app.get("/health")
def health():
    """헬스체크 엔드포인트"""
    return {"status": "ok"}