from __future__ import annotations

import logging
from typing import Any

from config import Settings
from database.history_repository import bulkinsertfileindex, insertcodeelements
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

        self._ensure_collection()

    def _ensure_collection(self) -> None:
        try:
            self.qdrant_service.ensure_collection(self.embedding_service.dimension)
            logger.info("Qdrant collection ready: %s", self.settings.qdrant_collection)
        except Exception:
            logger.exception("Failed to ensure Qdrant collection")
            raise

    async def askwithcontextstream(
            self,
            question: str,
            projectid: str | None = None,
            projectname: str | None = None,
            extracontext: str = "",
            sqlitecontext: str = "",
            topk: int | None = None,
            layerfilter: str | None = None,
            extensionfilter: str | None = None,
            querytype: str = "qa",
            chathistory: list[dict] | None = None,
    ):
        if topk is None:
            topk = self.settings.top_k

        if querytype == "diagram" and projectid:
            q = (question or "").lower()
            try:
                if any(k in q for k in ("erd", "db", "table", "schema", "mermaid")):
                    mermaid = self.diagram_service.build_table_erd(projectid)
                else:
                    mermaid = self.diagram_service.build_flow_mermaid(projectid)

                if mermaid and len(mermaid.splitlines()) > 1:
                    async def mermaid_gen():
                        yield "```mermaid\n"
                        yield f"{mermaid}\n"
                        yield "```"

                    return mermaid_gen(), []
            except Exception as e:
                logger.warning("DiagramService fallback to LLM: %s", e)

        query_vector = self.embedding_service.embed_query(question)
        hits = self.qdrant_service.search(
            query_vector,
            project_id=projectid,
            top_k=topk,
            layer_filter=layerfilter,
            extension_filter=extensionfilter,
        )

        gen = self.ollama_service.generate_response_stream(
            question=question,
            hits=hits,
            query_type=querytype,
            project_name=projectname,
            struct_context=extracontext,
            chat_history=chathistory,
            sqlite_context=sqlitecontext,
        )
        return gen, hits

    async def ask_with_context_stream(
            self,
            question: str,
            projectid: str | None = None,
            projectname: str | None = None,
            extracontext: str = "",
            sqlitecontext: str = "",
            topk: int | None = None,
            layerfilter: str | None = None,
            extensionfilter: str | None = None,
            querytype: str = "qa",
            chathistory: list[dict] | None = None,
    ):
        return await self.askwithcontextstream(
            question=question,
            projectid=projectid,
            projectname=projectname,
            extracontext=extracontext,
            sqlitecontext=sqlitecontext,
            topk=topk,
            layerfilter=layerfilter,
            extensionfilter=extensionfilter,
            querytype=querytype,
            chathistory=chathistory,
        )

    def indexfiles(
            self,
            targets: list[dict],
            progresscallback=None,
    ) -> dict[str, Any]:
        if not targets:
            return {
                "success": 0,
                "failed": 0,
                "totalchunks": 0,
                "logs": ["no targets"],
            }

        success_count = 0
        failed_count = 0
        total_chunks = 0
        logs: list[str] = []

        file_index_rows: list[dict[str, Any]] = []
        code_elements_by_project: dict[tuple[str, str], list[dict[str, Any]]] = {}

        total_targets = len(targets)

        def report(message: str, error: str | None = None) -> None:
            if progresscallback:
                progresscallback(
                    processedtargets=success_count + failed_count,
                    successcount=success_count,
                    failedcount=failed_count,
                    totalchunks=total_chunks,
                    message=message,
                    error=error,
                    logs=logs[-200:],
                )

        for idx, target in enumerate(targets, start=1):
            project_id = (target.get("projectid") or target.get("project_id") or "").strip()
            project_name = (target.get("projectname") or target.get("project_name") or "").strip()
            saved_path = target.get("savedpath") or target.get("saved_path") or ""
            relative_path = target.get("relativepath") or target.get("relative_path") or ""
            filename = (
                    target.get("filename")
                    or target.get("originalname")
                    or target.get("original_name")
                    or ""
            )
            extension = (target.get("extension") or "").lower().strip(".")
            size = int(target.get("size") or target.get("file_size") or 0)
            source_type = target.get("sourcetype") or target.get("source_type") or ""
            root_container_name = (
                    target.get("rootcontainername")
                    or target.get("root_container_name")
                    or ""
            )

            file_label = relative_path or filename or saved_path or f"target-{idx}"
            report(f"[{idx}/{total_targets}] indexing {file_label}")

            try:
                parsed = parse_text_file(
                    {
                        "project_id": project_id,
                        "project_name": project_name,
                        "saved_path": saved_path,
                        "relative_path": relative_path,
                        "original_name": filename,
                        "extension": extension,
                        "size": size,
                        "source_type": source_type,
                        "root_container_name": root_container_name,
                    }
                )

                if not parsed:
                    failed_count += 1
                    logs.append(f"SKIP parse failed: {file_label}")
                    report(f"parse failed: {file_label}")
                    continue

                chunks = self.chunk_service.split_text(
                    parsed["raw_text"],
                    {
                        "project_id": parsed.get("project_id", project_id),
                        "project_name": parsed.get("project_name", project_name),
                        "file_name": parsed.get("file_name", filename),
                        "extension": parsed.get("extension", extension),
                        "relative_path": parsed.get("relative_path", relative_path),
                        "saved_path": parsed.get("saved_path", saved_path),
                        "file_path": parsed.get("file_path", saved_path),
                        "file_size": parsed.get("file_size", size),
                        "source_type": parsed.get("source_type", source_type),
                        "root_container_name": parsed.get("root_container_name", root_container_name),
                        "layer_type": parsed.get("layer_type", ""),
                        "class_name": parsed.get("class_name", ""),
                        "package": parsed.get("package", ""),
                        "content_type": parsed.get("content_type", ""),
                    },
                )

                if chunks:
                    vectors = self.embedding_service.embed_texts([c["text"] for c in chunks])
                    self.qdrant_service.upsert_chunks(chunks, vectors)
                    total_chunks += len(chunks)

                analysis = extract_static_analysis(
                    {
                        "project_id": project_id,
                        "project_name": project_name,
                        "saved_path": saved_path,
                        "relative_path": relative_path,
                        "original_name": filename,
                        "extension": extension,
                        "size": size,
                        "source_type": source_type,
                        "root_container_name": root_container_name,
                    }
                )

                if analysis:
                    key = (project_id, project_name)
                    code_elements_by_project.setdefault(key, []).append(analysis)

                file_index_rows.append(
                    {
                        "project_id": project_id,
                        "project_name": project_name,
                        "file_name": filename,
                        "relative_path": relative_path,
                        "extension": extension,
                        "file_size": size,
                    }
                )

                success_count += 1
                logs.append(f"OK {file_label} ({len(chunks)} chunks)")
                report(f"indexed: {file_label}")

            except Exception as e:
                failed_count += 1
                logger.exception("index target failed: %s", file_label)
                logs.append(f"FAIL {file_label}: {e}")
                report(f"failed: {file_label}", error=str(e))

        try:
            if file_index_rows:
                bulkinsertfileindex(file_index_rows)
        except Exception:
            logger.exception("bulk file index insert failed")
            logs.append("FAIL bulk_insert_file_index")

        try:
            for (project_id, project_name), elements in code_elements_by_project.items():
                insertcodeelements(project_id, project_name, elements)
        except Exception:
            logger.exception("insert code elements failed")
            logs.append("FAIL insert_code_elements")

        final_message = (
            f"indexing completed: success={success_count}, "
            f"failed={failed_count}, chunks={total_chunks}"
        )
        logs.append(final_message)
        report(final_message)

        return {
            "success": success_count,
            "failed": failed_count,
            "totalchunks": total_chunks,
            "logs": logs[-500:],
        }

    def index_files(
            self,
            targets: list[dict],
            progress_callback=None,
    ) -> dict[str, Any]:
        return self.indexfiles(targets, progresscallback=progress_callback)

    def reset(self) -> None:
        try:
            if hasattr(self.qdrant_service, "reset_collection"):
                self.qdrant_service.reset_collection(self.embedding_service.dimension)
            elif hasattr(self.qdrant_service, "recreate_collection"):
                self.qdrant_service.recreate_collection(self.embedding_service.dimension)
            else:
                self.qdrant_service.ensure_collection(self.embedding_service.dimension)
        except Exception:
            logger.exception("RAG reset failed")
            raise