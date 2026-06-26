itsmart_codeMind/
├─ .env
├─ .env.example
├─ .gitignore
├─ backend
│  ├─ __init__.py
│  ├─ config.py
│  ├─ Dockerfile
│  ├─ health_service.py
│  ├─ main.py
│  ├─ requirements.txt
│  ├─ database
│  │  ├─ database.py
│  │  ├─ history_repository.py
│  │  └─ init_db.py
│  ├─ embedder
│  │  ├─ __init__.py
│  │  └─ embedder.py
│  ├─ parser
│  │  ├─ __init__.py
│  │  ├─ chunk_service.py
│  │  └─ file_parser.py
│  ├─ rag
│  │  ├─ __init__.py
│  │  ├─ ollama_service.py
│  │  ├─ prompt_builder.py
│  │  ├─ qdrant_service.py
│  │  ├─ query_analyzer.py
│  │  └─ rag_service.py
│  └─ utils
│     ├─ __init__.py
│     └─ file_utils.py
├─ docker-compose.yml
├─ frontend
│  ├─ app.py
│  ├─ Dockerfile
│  └─ requirements.txt
└─ README.md
