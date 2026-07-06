from __future__ import annotations

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
            response = client.get(f"{settings.qdrant_url}/collections")
            if response.is_success:
                collections = [c["name"] for c in response.json().get("result", {}).get("collections", [])]
                collection_exists = settings.qdrant_collection in collections
                item["status"] = "running"
                item["message"] = f"collection={settings.qdrant_collection}" if collection_exists else "collection missing"
                item["collection_exists"] = collection_exists
            else:
                item["status"] = "error"
                item["message"] = f"HTTP {response.status_code}"
    except Exception as error:
        item["status"] = "stopped"
        item["message"] = str(error)
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
            response = client.get(f"{settings.ollama_base_url}/api/tags")
            if response.is_success:
                item["status"] = "running"
                item["message"] = f"models={len(response.json().get('models', []))}"
            else:
                item["status"] = "error"
                item["message"] = f"HTTP {response.status_code}"
    except Exception as error:
        item["status"] = "stopped"
        item["message"] = str(error)
    return item


def ollama_model_available(settings: Settings) -> tuple[bool, list[str], str]:
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{settings.ollama_base_url}/api/tags")
            if not response.is_success:
                return False, [], f"Ollama HTTP {response.status_code}"

            models = [model.get("name", "") for model in response.json().get("models", [])]
            target = settings.ollama_model

            if target in models:
                return True, models, ""

            base_name = target.split(":")[0]
            partial = [model for model in models if model.split(":")[0] == base_name]
            if partial:
                return True, models, f"closest models: {', '.join(partial)}"

            return False, models, "pull required"
    except Exception as error:
        return False, [], str(error)


def check_ollama_model(settings: Settings) -> dict:
    available, installed_models, message = ollama_model_available(settings)
    return {
        "name": settings.ollama_model,
        "kind": "llm",
        "provider": "Ollama",
        "status": "available" if available else "missing",
        "message": message,
        "installed_models": installed_models,
    }


def check_embedding_model(settings: Settings, rag_initialized: bool) -> dict:
    return {
        "name": settings.embedding_model,
        "kind": "embedding",
        "provider": "HuggingFace",
        "status": "loaded" if rag_initialized else "not_loaded",
        "message": "" if rag_initialized else "RAG not initialized",
    }


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
            "status": "running" if rag_initialized else "degraded",
            "message": "" if rag_initialized else f"API/RAG init failed: {init_error or ''}",
        },
        qdrant,
        ollama,
        {
            "name": "Streamlit Frontend",
            "kind": "container",
            "container": "codeMind-frontend",
            "url": "http://codeMind-frontend:8501",
            "status": "running",
            "message": "",
        },
    ]

    models = [ollama_model, embedding]
    all_ok = (
            qdrant["status"] == "running"
            and ollama["status"] == "running"
            and ollama_model["status"] == "available"
            and embedding["status"] == "loaded"
            and rag_initialized
    )

    return {
        "overall": "healthy" if all_ok else "degraded",
        "rag_initialized": rag_initialized,
        "init_error": init_error,
        "services": services,
        "models": models,
    }