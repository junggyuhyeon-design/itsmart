import httpx

from config import Settings


def check_qdrant(settings: Settings) -> dict:
    item = {
        "name": "Qdrant",
        "kind": "container",
        "container": "codeMind-qdrant",
        "url": settings.qdrant_url,
    }
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{settings.qdrant_url}/collections")
            if resp.is_success:
                collections = [
                    c["name"]
                    for c in resp.json().get("result", {}).get("collections", [])
                ]
                collection_exists = settings.qdrant_collection in collections
                item["status"] = "running"
                item["message"] = (
                    f"연결됨 · 컬렉션 '{settings.qdrant_collection}' "
                    f"{'존재' if collection_exists else '미생성'}"
                )
                item["collection_exists"] = collection_exists
            else:
                item["status"] = "error"
                item["message"] = f"HTTP {resp.status_code}"
    except Exception as e:
        item["status"] = "stopped"
        item["message"] = str(e)
    return item


def check_ollama(settings: Settings) -> dict:
    item = {
        "name": "Ollama",
        "kind": "container",
        "container": "codeMind-ollama",
        "url": settings.ollama_base_url,
    }
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{settings.ollama_base_url}/api/tags")
            if resp.is_success:
                item["status"] = "running"
                model_count = len(resp.json().get("models", []))
                item["message"] = f"연결됨 · 등록된 모델 {model_count}개"
            else:
                item["status"] = "error"
                item["message"] = f"HTTP {resp.status_code}"
    except Exception as e:
        item["status"] = "stopped"
        item["message"] = str(e)
    return item


def _ollama_model_available(settings: Settings) -> tuple[bool, list[str], str]:
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{settings.ollama_base_url}/api/tags")
            if not resp.is_success:
                return False, [], f"Ollama 조회 실패 (HTTP {resp.status_code})"
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            target = settings.ollama_model
            if target in models:
                return True, models, "모델 사용 가능"
            base_name = target.split(":")[0]
            partial = [m for m in models if m.split(":")[0] == base_name]
            if partial:
                return True, models, f"유사 모델 발견: {', '.join(partial)}"
            return False, models, "모델이 pull 되지 않았습니다"
    except Exception as e:
        return False, [], str(e)


def check_ollama_model(settings: Settings) -> dict:
    available, installed, message = _ollama_model_available(settings)
    return {
        "name": settings.ollama_model,
        "kind": "llm",
        "provider": "Ollama",
        "status": "available" if available else "missing",
        "message": message,
        "installed_models": installed,
    }


def check_embedding_model(settings: Settings, rag_initialized: bool) -> dict:
    item = {
        "name": settings.embedding_model,
        "kind": "embedding",
        "provider": "HuggingFace",
    }
    if rag_initialized:
        item["status"] = "loaded"
        item["message"] = "메모리에 로드됨"
    else:
        item["status"] = "not_loaded"
        item["message"] = "RAG 서비스 미초기화 (모델 미로드)"
    return item


def build_system_status(settings: Settings, rag_initialized: bool, init_error: str | None) -> dict:
    qdrant = check_qdrant(settings)
    ollama = check_ollama(settings)
    ollama_model = check_ollama_model(settings)
    embedding = check_embedding_model(settings, rag_initialized)

    services = [
        {
            "name": "FastAPI Backend",
            "kind": "container",
            "container": "codeMind-backend",
            "url": "http://codeMind-backend:8000",
            "status": "running",
            "message": "정상" if rag_initialized else f"API 응답 중 · RAG 미초기화: {init_error or '알 수 없음'}",
        },
        qdrant,
        ollama,
        {
            "name": "Streamlit Frontend",
            "kind": "container",
            "container": "codeMind-frontend",
            "url": "http://codeMind-frontend:8501",
            "status": "running",
            "message": "현재 페이지에서 접속 중",
        },
    ]

    models = [ollama_model, embedding]

    all_ok = (
        qdrant["status"] == "running"
        and ollama["status"] == "running"
        and ollama_model["status"] == "available"
        and embedding["status"] == "loaded"
    )

    return {
        "overall": "healthy" if all_ok else "degraded",
        "rag_initialized": rag_initialized,
        "init_error": init_error,
        "services": services,
        "models": models,
    }
