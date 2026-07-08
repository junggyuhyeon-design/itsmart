from __future__ import annotations

import logging
from typing import Any, Callable

from config import Settings
from database.history_repository import bulk_insert_file_index, insert_code_elements
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
        self.embedding_service = EmbeddingService(settings)
        self.qdrant_service = QdrantService(settings)
        self.ollama_service = OllamaService(settings)
        self.diagram_service = DiagramService()
        self.chunk_service = ChunkService(settings)

        self.qdrant_service.ensure_collection(self.embedding_service.dimension)

    def index_files(
            self,
            targets: list[dict[str, Any]],
            progress_callback: Callable[..., None] | None = None,
    ) -> dict[str, Any]:
        if not targets:
            return {
                "success": 0,
                "failed": 0,
                "total_chunks": 0,
                "indexed_files": 0,
                "code_elements": 0,
                "logs": ["no targets"],
            }

        success = 0
        failed = 0
        total_chunks = 0
        logs: list[str] = []

        total_targets = len(targets)
        file_index_rows: list[dict[str, Any]] = []
        code_elements_rows_by_project: dict[tuple[str, str], list[dict[str, Any]]] = {}

        for index, target in enumerate(targets, start=1):
            relative_path = target.get("relative_path") or target.get("file_name") or target.get("filename") or "unknown"

            try:
                parsed = parse_text_file(target)
                if not parsed:
                    failed += 1
                    logs.append(f"[skip] parse failed: {relative_path}")
                    if progress_callback:
                        progress_callback(
                            processed_targets=index,
                            total_targets=total_targets,
                            success_count=success,
                            failed_count=failed,
                            total_chunks=total_chunks,
                            message=f"parse failed: {relative_path}",
                            logs=logs[-20:],
                        )
                    continue

                chunks = self.chunk_service.chunk_parsed_file(parsed)
                if not chunks:
                    failed += 1
                    logs.append(f"[skip] no chunks: {relative_path}")
                    if progress_callback:
                        progress_callback(
                            processed_targets=index,
                            total_targets=total_targets,
                            success_count=success,
                            failed_count=failed,
                            total_chunks=total_chunks,
                            message=f"no chunks: {relative_path}",
                            logs=logs[-20:],
                        )
                    continue

                valid_chunks = [chunk for chunk in chunks if (chunk.get("text") or "").strip()]
                if not valid_chunks:
                    failed += 1
                    logs.append(f"[skip] empty chunk texts: {relative_path}")
                    if progress_callback:
                        progress_callback(
                            processed_targets=index,
                            total_targets=total_targets,
                            success_count=success,
                            failed_count=failed,
                            total_chunks=total_chunks,
                            message=f"empty chunk texts: {relative_path}",
                            logs=logs[-20:],
                        )
                    continue

                vectors = self.embedding_service.embed_texts(
                    [(chunk.get("text") or "").strip() for chunk in valid_chunks]
                )

                upserted = self.qdrant_service.upsert_chunks(valid_chunks, vectors)
                total_chunks += upserted

                project_id = parsed.get("project_id", "") or target.get("project_id", "")
                project_name = parsed.get("project_name", "") or target.get("project_name", "")

                file_index_rows.append(
                    {
                        "project_id": project_id,
                        "project_name": project_name,
                        "file_name": parsed.get("file_name", "") or target.get("file_name", ""),
                        "relative_path": parsed.get("relative_path", "") or target.get("relative_path", ""),
                        "extension": parsed.get("extension", "") or target.get("extension", ""),
                        "file_size": int(parsed.get("file_size", 0) or target.get("file_size", 0) or 0),
                    }
                )

                try:
                    analysis = extract_static_analysis(target)
                    if analysis:
                        key = (project_id, project_name)
                        code_elements_rows_by_project.setdefault(key, []).append(analysis)
                        logger.debug(
                            "static analysis prepared: %s / layer=%s / class=%s",
                            analysis.get("relative_path"),
                            analysis.get("layer_type"),
                            analysis.get("class_name"),
                        )
                except Exception:
                    logger.exception("extract_static_analysis failed: %s", relative_path)

                success += 1
                logs.append(f"[ok] indexed {relative_path} ({upserted} chunks)")

                if progress_callback:
                    progress_callback(
                        processed_targets=index,
                        total_targets=total_targets,
                        success_count=success,
                        failed_count=failed,
                        total_chunks=total_chunks,
                        message=f"indexed: {relative_path}",
                        logs=logs[-20:],
                    )

            except Exception as error:
                failed += 1
                logger.exception("index_files failed: %s", relative_path)
                logs.append(f"[error] {relative_path}: {error}")

                if progress_callback:
                    progress_callback(
                        processed_targets=index,
                        total_targets=total_targets,
                        success_count=success,
                        failed_count=failed,
                        total_chunks=total_chunks,
                        message=f"failed: {relative_path}",
                        error=str(error),
                        logs=logs[-20:],
                    )

        indexed_files = 0
        code_elements_count = 0

        try:
            indexed_files = bulk_insert_file_index(file_index_rows)
        except Exception as error:
            logger.exception("bulk_insert_file_index failed: %s", error)
            logs.append(f"[error] bulk_insert_file_index: {error}")

        try:
            for (project_id, project_name), elements in code_elements_rows_by_project.items():
                code_elements_count += insert_code_elements(project_id, project_name, elements)
        except Exception as error:
            logger.exception("insert_code_elements failed: %s", error)
            logs.append(f"[error] insert_code_elements: {error}")

        return {
            "success": success,
            "failed": failed,
            "total_chunks": total_chunks,
            "indexed_files": indexed_files,
            "code_elements": code_elements_count,
            "logs": logs[-100:],
        }

    async def ask_with_context_stream(
            self,
            question: str,
            retrieval_question: str | None = None,
            project_id: str | None = None,
            project_name: str | None = None,
            extra_context: str = "",
            sqlite_context: str = "",
            top_k: int | None = None,
            layer_filter: str | None = None,
            extension_filter: str | None = None,
            query_type: str = "qa",
            chat_history: list[dict[str, Any]] | None = None,
            recent_entities: list[dict[str, Any]] | None = None,
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

        retrieval_text = (retrieval_question or question or "").strip()
        if not retrieval_text:
            retrieval_text = (question or "").strip()

        query_vector = self.embedding_service.embed_query(retrieval_text)

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

    # def index_files(self, targets: list[dict[str, Any]], progress_callback=None) -> dict[str, Any]:
    #     if not targets:
    #         return {"success": 0, "failed": 0, "total_chunks": 0, "logs": ["no targets"]}

    #     success = 0
    #     failed = 0
    #     total_chunks = 0
    #     logs: list[str] = []

    #     file_index_rows = []
    #     code_elements = []
    #     all_chunks = []

    #     for index, target in enumerate(targets, start=1):
    #         try:
    #             parsed = parse_text_file(target)
    #             if not parsed:
    #                 failed += 1
    #                 logs.append(f"parse failed: {target.get('saved_path')}")
    #                 continue

    #             project_id = parsed["project_id"]
    #             project_name = parsed["project_name"]

    #             file_index_rows.append(
    #                 {
    #                     "project_id": project_id,
    #                     "project_name": project_name,
    #                     "file_name": parsed["file_name"],
    #                     "relative_path": parsed["relative_path"],
    #                     "extension": parsed["extension"],
    #                     "file_size": parsed.get("file_size", 0),
    #                 }
    #             )

    #             static_analysis = extract_static_analysis(target)
    #             if static_analysis:
    #                 code_elements.append(static_analysis)

    #             chunks = self.chunk_service.chunk_parsed_file(parsed)
    #             all_chunks.extend(chunks)

    #             success += 1
    #             total_chunks += len(chunks)
    #             logs.append(f"indexed: {parsed['relative_path']} ({len(chunks)} chunks)")

    #             if progress_callback:
    #                 progress_callback(
    #                     processed_targets=index,
    #                     success_count=success,
    #                     failed_count=failed,
    #                     total_chunks=total_chunks,
    #                     message=f"indexed {index}/{len(targets)}",
    #                     logs=logs[-20:],
    #                 )
    #             logger.info(
    #                 "---- 인덱싱 진행중... ::: processed=%d success=%d failed=%d total_chunks=%d",
    #                 index,
    #                 success,
    #                 failed,
    #                 total_chunks,
    #             )
    #         except Exception as error:
    #             failed += 1
    #             logs.append(f"failed: {target.get('saved_path')} - {error}")
    #             logger.exception("index_files failed target=%s", target.get("saved_path"))

    #     if file_index_rows:
    #         from database.history_repository import bulk_insert_file_index
    #         bulk_insert_file_index(file_index_rows)
    #         logger.info("SQLite 파일인덱스 목록 저장 %d file index rows", len(file_index_rows))

    #     if code_elements:
    #         grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    #         for element in code_elements:
    #             key = (element["project_id"], element["project_name"])
    #             grouped.setdefault(key, []).append(element)

    #         from database.history_repository import insert_code_elements
    #         for (project_id, project_name), elements in grouped.items():
    #             insert_code_elements(project_id, project_name, elements)
    #         logger.info("SQLite 코드 요소 저장 %d code elements", len(code_elements))

    #     if all_chunks:
    #         vectors = self.embedding_service.embed_texts([chunk["text"] for chunk in all_chunks])
    #         self.qdrant_service.upsert_chunks(all_chunks, vectors)
    #         logger.info("Qdrant 청크 저장 %d chunks", len(all_chunks))

    #     return {
    #         "success": success,
    #         "failed": failed,
    #         "total_chunks": total_chunks,
    #         "logs": logs,
    #     }