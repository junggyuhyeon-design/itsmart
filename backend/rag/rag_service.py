from __future__ import annotations

import logging
from typing import Any

from config import Settings
from embedder.embedder import EmbeddingService
from parser.chunk_service import ChunkService
from parser.file_parser import extract_static_analysis, parse_text_file
from rag.diagram_service import DiagramService
from rag.ollama_service import OllamaService
from rag.qdrant_service import QdrantService

logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.chunk_service = ChunkService(settings)
        self.embedding_service = EmbeddingService(settings)
        self.qdrant_service = QdrantService(settings)
        self.ollama_service = OllamaService(settings)
        self.diagram_service = DiagramService()
        self.ensure_collection()

    def ensure_collection(self) -> None:
        self.qdrant_service.ensure_collection(self.embedding_service.dimension)

    async def ask_with_context_stream(
            self,
            question: str,
            project_id: str | None = None,
            project_name: str | None = None,
            extra_context: str = "",
            sqlite_context: str = "",
            top_k: int | None = None,
            layer_filter: str | None = None,
            extension_filter: str | None = None,
            query_type: str = "qa",
            chat_history: list[dict] | None = None,
            recent_entities: list[dict] | None = None,
    ):
        if top_k is None:
            top_k = self.settings.top_k

        if query_type == "diagram" and project_id:
            try:
                q = (question or "").lower()
                if any(token in q for token in ["erd", "db", "table", "schema", "mermaid"]):
                    mermaid = self.diagram_service.build_table_erd(project_id)
                else:
                    mermaid = self.diagram_service.build_flow_mermaid(project_id)

                if mermaid and len(mermaid.splitlines()) > 1:
                    async def mermaid_generator():
                        yield "```mermaid\n"
                        yield mermaid
                        yield "\n```"
                    return mermaid_generator(), []
            except Exception as error:
                logger.warning("DiagramService fallback to LLM: %s", error)

        query_vector = self.embedding_service.embed_query(question)
        hits = self.qdrant_service.search(
            query_vector,
            project_id=project_id,
            top_k=top_k,
            layer_filter=layer_filter,
            extension_filter=extension_filter,
        )

        generator = self.ollama_service.generate_response_stream(
            question=question,
            hits=hits,
            query_type=query_type,
            project_name=project_name,
            struct_context=extra_context,
            chat_history=chat_history,
            recent_entities=recent_entities,
            sqlite_context=sqlite_context,
        )
        return generator, hits

    def index_files(self, targets: list[dict[str, Any]], progress_callback=None) -> dict[str, Any]:
        if not targets:
            return {"success": 0, "failed": 0, "total_chunks": 0, "logs": ["no targets"]}

        success = 0
        failed = 0
        total_chunks = 0
        logs: list[str] = []

        file_index_rows = []
        code_elements = []
        all_chunks = []

        for index, target in enumerate(targets, start=1):
            try:
                parsed = parse_text_file(target)
                if not parsed:
                    failed += 1
                    logs.append(f"parse failed: {target.get('saved_path')}")
                    continue

                project_id = parsed["project_id"]
                project_name = parsed["project_name"]

                file_index_rows.append(
                    {
                        "project_id": project_id,
                        "project_name": project_name,
                        "file_name": parsed["file_name"],
                        "relative_path": parsed["relative_path"],
                        "extension": parsed["extension"],
                        "file_size": parsed.get("file_size", 0),
                    }
                )

                static_analysis = extract_static_analysis(target)
                if static_analysis:
                    code_elements.append(static_analysis)

                chunks = self.chunk_service.chunk_parsed_file(parsed)
                all_chunks.extend(chunks)

                success += 1
                total_chunks += len(chunks)
                logs.append(f"indexed: {parsed['relative_path']} ({len(chunks)} chunks)")

                if progress_callback:
                    progress_callback(
                        processed_targets=index,
                        success_count=success,
                        failed_count=failed,
                        total_chunks=total_chunks,
                        message=f"indexed {index}/{len(targets)}",
                        logs=logs[-20:],
                    )
            except Exception as error:
                failed += 1
                logs.append(f"failed: {target.get('saved_path')} - {error}")
                logger.exception("index_files failed target=%s", target.get("saved_path"))

        if file_index_rows:
            from database.history_repository import bulk_insert_file_index
            bulk_insert_file_index(file_index_rows)

        if code_elements:
            grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for element in code_elements:
                key = (element["project_id"], element["project_name"])
                grouped.setdefault(key, []).append(element)

            from database.history_repository import insert_code_elements
            for (project_id, project_name), elements in grouped.items():
                insert_code_elements(project_id, project_name, elements)

        if all_chunks:
            vectors = self.embedding_service.embed_texts([chunk["text"] for chunk in all_chunks])
            self.qdrant_service.upsert_chunks(all_chunks, vectors)

        return {
            "success": success,
            "failed": failed,
            "total_chunks": total_chunks,
            "logs": logs,
        }