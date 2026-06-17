## 프로젝트 구조

```text
itsmart_codeMind/
├─ .env
├─ .gitignore
├─ docker-compose.yml
├─ backend/
│  ├─ config.py
│  ├─ Dockerfile
│  ├─ main.py
│  ├─ requirements.txt
│  ├─ embedder/
│  │  └─ embedder.py
│  ├─ parser/
│  │  ├─ chunk_service.py
│  │  └─ file_parser.py
│  ├─ rag/
│  │  ├─ ollama_service.py
│  │  ├─ qdrant_service.py
│  │  └─ rag_service.py
│  └─ utils/
│     └─ file_utils.py
└─ frontend/
   ├─ app.py
   ├─ Dockerfile
   └─ requirements.txt
```
