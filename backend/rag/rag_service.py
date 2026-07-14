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

        logger.info(
            "[rag_service.py][__init__] initialized services top_k=%s embedding_dimension=%s",
            getattr(settings, "top_k", None),
            getattr(self.embedding_service, "dimension", None),
        )

        # Qdrant 컬렉션 보장
        self.qdrant_service.ensure_collection(self.embedding_service.dimension)

        logger.info(
            "[rag_service.py][__init__] ensure_collection completed dimension=%s",
            self.embedding_service.dimension,
        )

    def index_files(
            self,
            targets: list[dict[str, Any]],
            progress_callback: Callable[..., None] | None = None,
    ) -> dict[str, Any]:
        logger.info(
            "[rag_service.py][index_files] start total_targets=%d progress_callback=%s",
            len(targets or []),
            progress_callback is not None,
            )

        # ? targets 정보 : /upload 응답 -> /index-jobs 로 넘어온 인덱싱 대상 리스트
        # project_id         : 프로젝트 아이디
        # project_name       : 프로젝트명
        # saved_path         : 업로드된 원본 zip/파일 저장 경로
        # relative_path      : 프로젝트 내부 상대경로 (예: backend/main.py)
        # original_name      : 원본 파일명
        # filename           : 파일명
        # extension          : 확장자
        # filesize           : 파일 크기
        # source_type        : 원본 유형 (zip_entry, single_file 등)
        # root_container_name: 루트 zip 이름 등

        if not targets:
            logger.warning("[rag_service.py][index_files] no targets")
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

        # ? file_index_rows 정보 : SQLite file_index 테이블 저장용 메타데이터
        # project_id    : 프로젝트 아이디
        # project_name  : 프로젝트명
        # file_name     : 파일명
        # relative_path : 프로젝트 내부 상대경로
        # extension     : 확장자
        # file_size     : 파일 크기
        file_index_rows: list[dict[str, Any]] = []

        # ? code_elements_rows_by_project 정보 : SQLite code_elements 저장용 정적분석 결과 모음
        # key = (project_id, project_name)
        # value = [analysis, analysis, ...]
        code_elements_rows_by_project: dict[tuple[str, str], list[dict[str, Any]]] = {}

        for index, target in enumerate(targets, start=1):
            relative_path = target.get("relative_path") or target.get("file_name") or target.get("filename") or "unknown"

            logger.info(
                "[rag_service.py][index_files] target start step=%d/%d project_id=%s project_name=%s relative_path=%s extension=%s filesize=%s source_type=%s",
                index,
                total_targets,
                target.get("project_id"),
                target.get("project_name"),
                relative_path,
                target.get("extension"),
                target.get("filesize"),
                target.get("source_type"),
            )

            try:
                # 파일 읽기 + 기본 메타/언어/계층/클래스 정보 파싱
                parsed = parse_text_file(target)

                logger.info(
                    "[rag_service.py][index_files] parse_text_file returned relative_path=%s parsed=%s",
                    relative_path,
                    parsed is not None,
                    )

                # ? parsed 정보 :
                # raw_text             : 파일 원문 텍스트
                # project_id           : 프로젝트 아이디
                # project_name         : 프로젝트명
                # file_name            : 파일명
                # extension            : 확장자
                # language             : 감지된 언어
                # mime_type            : MIME 타입
                # relative_path        : 상대경로
                # saved_path           : 저장 경로
                # file_path            : 실제 파일 경로
                # file_size            : 파일 크기
                # source_type          : 업로드 원본 타입
                # root_container_name  : 루트 zip 이름
                # layer_type           : 계층 타입 (controller/service/repository/mapper/config 등)
                # content_type         : 내용 타입 (api_endpoint/sql_select/ddl_create 등)
                # class_name           : 클래스명
                # package              : 패키지명
                # xml_namespace        : XML mapper namespace
                # xml_sql_fragments    : XML <sql id="..."> fragment 목록
                # xml_statements       : XML select/insert/update/delete id 목록
                # template_meta        : 템플릿 파일 관련 메타데이터
                # sql_meta             : SQL statement type / table names 등 상세 SQL 메타데이터

                if not parsed:
                    failed += 1
                    logs.append(f"[skip] parse failed: {relative_path}")
                    logger.warning(
                        "[rag_service.py][index_files] skip parse failed relative_path=%s success=%d failed=%d",
                        relative_path,
                        success,
                        failed,
                    )
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

                logger.info(
                    "[rag_service.py][index_files] parsed summary relative_path=%s language=%s layer_type=%s content_type=%s class_name=%s package=%s",
                    parsed.get("relative_path"),
                    parsed.get("language"),
                    parsed.get("layer_type"),
                    parsed.get("content_type"),
                    parsed.get("class_name"),
                    parsed.get("package"),
                )

                # 파일 청킹
                chunks = self.chunk_service.chunk_parsed_file(parsed)

                logger.info(
                    "[rag_service.py][index_files] chunk_parsed_file completed relative_path=%s chunk_count=%d",
                    relative_path,
                    len(chunks or []),
                )

                # ? chunks 정보 : 벡터DB(Qdrant)에 저장할 청크 리스트
                # project_id         : 프로젝트 아이디
                # project_name       : 프로젝트명
                # file_name          : 파일명
                # extension          : 확장자
                # relative_path      : 상대경로
                # saved_path         : 저장 경로
                # file_path          : 실제 파일 경로
                # file_size          : 파일 크기
                # source_type        : 업로드 원본 타입
                # root_container_name: 루트 zip 이름
                # layer_type         : 계층 타입
                # class_name         : 클래스명
                # package            : 패키지명
                # content_type       : 내용 타입
                # text               : 청크 텍스트
                # chunk_index        : 청크 인덱스
                # start_line         : 시작 라인
                # end_line           : 끝 라인
                # chunk_type         : 청크 타입(text 등)

                if not chunks:
                    failed += 1
                    logs.append(f"[skip] no chunks: {relative_path}")
                    logger.warning(
                        "[rag_service.py][index_files] skip no chunks relative_path=%s success=%d failed=%d",
                        relative_path,
                        success,
                        failed,
                    )
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

                # 공백 청크 제거
                valid_chunks = [chunk for chunk in chunks if (chunk.get("text") or "").strip()]

                logger.info(
                    "[rag_service.py][index_files] valid_chunks filtered relative_path=%s raw_chunk_count=%d valid_chunk_count=%d",
                    relative_path,
                    len(chunks or []),
                    len(valid_chunks),
                )

                if not valid_chunks:
                    failed += 1
                    logs.append(f"[skip] empty chunk texts: {relative_path}")
                    logger.warning(
                        "[rag_service.py][index_files] skip empty chunk texts relative_path=%s success=%d failed=%d",
                        relative_path,
                        success,
                        failed,
                    )
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

                # ? vectors 정보 : 각 청크 텍스트를 임베딩한 벡터 리스트
                # [
                #   [0.0123, -0.4421, ...],
                #   [0.2871,  0.0311, ...],
                #   ...
                # ]
                vectors = self.embedding_service.embed_texts(
                    [(chunk.get("text") or "").strip() for chunk in valid_chunks]
                )

                logger.info(
                    "[rag_service.py][index_files] embed_texts completed relative_path=%s vector_count=%d",
                    relative_path,
                    len(vectors or []),
                )

                # Qdrant 저장
                upserted = self.qdrant_service.upsert_chunks(valid_chunks, vectors)
                total_chunks += upserted

                logger.info(
                    "[rag_service.py][index_files] upsert_chunks completed relative_path=%s upserted=%d accumulated_total_chunks=%d",
                    relative_path,
                    upserted,
                    total_chunks,
                )

                project_id = parsed.get("project_id", "") or target.get("project_id", "")
                project_name = parsed.get("project_name", "") or target.get("project_name", "")

                # SQLite file_index 저장용 메타데이터 누적
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

                logger.info(
                    "[rag_service.py][index_files] file_index_rows appended project_id=%s project_name=%s relative_path=%s current_file_index_rows=%d",
                    project_id,
                    project_name,
                    parsed.get("relative_path", "") or target.get("relative_path", ""),
                    len(file_index_rows),
                    )

                try:
                    # 정적 분석 추출
                    analysis = extract_static_analysis(parsed)

                    logger.info(
                        "[rag_service.py][index_files] extract_static_analysis returned relative_path=%s analysis=%s",
                        relative_path,
                        analysis is not None,
                        )

                    # ? analysis 정보        : SQLite code_elements 저장용 정적 분석 결과
                    # raw_text              : 원문 텍스트
                    # project_id            : 프로젝트 아이디
                    # project_name          : 프로젝트명
                    # file_name             : 파일명
                    # extension             : 확장자
                    # language              : 감지 언어
                    # mime_type             : MIME 타입
                    # relative_path         : 상대경로
                    # saved_path            : 저장 경로
                    # layer_type            : 계층 타입
                    # content_type          : 내용 타입
                    # class_name            : 클래스명
                    # package               : 패키지명
                    # xml_namespace         : XML namespace
                    # xml_sql_fragments     : XML sql fragment id 목록
                    # xml_statements        : xml select/insert/update/delete id 목록
                    # template_meta         : JSP/HTML/Vue 등 템플릿 메타데이터
                    # table_names           : SQL/DDL 등에서 추출한 테이블명 목록
                    # imports               : import 목록
                    # methods               : 메서드 목록

                    if analysis:
                        key = (project_id, project_name)
                        code_elements_rows_by_project.setdefault(key, []).append(analysis)
                        logger.debug(
                            "static analysis prepared: %s / layer=%s / class=%s",
                            analysis.get("relative_path"),
                            analysis.get("layer_type"),
                            analysis.get("class_name"),
                        )
                        logger.info(
                            "[rag_service.py][index_files] code_elements_rows_by_project appended project_id=%s project_name=%s current_project_elements=%d",
                            project_id,
                            project_name,
                            len(code_elements_rows_by_project.get(key, [])),
                        )
                except Exception:
                    logger.exception("[rag_service.py][index_files] extract_static_analysis failed relative_path=%s", relative_path)

                success += 1
                logs.append(f"[ok] indexed {relative_path} ({upserted} chunks)")

                logger.info(
                    "[rag_service.py][index_files] target completed step=%d/%d relative_path=%s success=%d failed=%d total_chunks=%d",
                    index,
                    total_targets,
                    relative_path,
                    success,
                    failed,
                    total_chunks,
                )

                if progress_callback:
                    # ? progress_callback kwargs :
                    # processed_targets : 현재까지 처리한 파일 수
                    # total_targets     : 전체 대상 파일 수
                    # success_count     : 성공 파일 수
                    # failed_count      : 실패 파일 수
                    # total_chunks      : 누적 저장 청크 수
                    # message           : 현재 진행 메시지
                    # logs              : 최근 로그 목록
                    progress_callback(
                        processed_targets=index,
                        total_targets=total_targets,
                        success_count=success,
                        failed_count=failed,
                        total_chunks=total_chunks,
                        message=f"indexed: {relative_path}",
                        logs=logs[-20:],
                    )

                    logger.info(
                        "[rag_service.py][index_files] progress_callback called step=%d/%d success=%d failed=%d total_chunks=%d",
                        index,
                        total_targets,
                        success,
                        failed,
                        total_chunks,
                    )

            except Exception as error:
                failed += 1
                logger.exception("[rag_service.py][index_files] target failed relative_path=%s", relative_path)
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

                    logger.info(
                        "[rag_service.py][index_files] progress_callback called after error step=%d/%d success=%d failed=%d total_chunks=%d",
                        index,
                        total_targets,
                        success,
                        failed,
                        total_chunks,
                    )

        indexed_files = 0
        code_elements_count = 0

        logger.info(
            "[rag_service.py][index_files] before sqlite save file_index_rows=%d project_groups=%d",
            len(file_index_rows),
            len(code_elements_rows_by_project),
        )

        try:
            # SQLite file_index 테이블 일괄 저장
            indexed_files = bulk_insert_file_index(file_index_rows)
            logger.info(
                "[rag_service.py][index_files] bulk_insert_file_index completed indexed_files=%d",
                indexed_files,
            )
        except Exception as error:
            logger.exception("[rag_service.py][index_files] bulk_insert_file_index failed")
            logs.append(f"[error] bulk_insert_file_index: {error}")

        try:
            # SQLite code_elements 테이블 프로젝트별 일괄 저장
            for (project_id, project_name), elements in code_elements_rows_by_project.items():
                inserted = insert_code_elements(project_id, project_name, elements)
                code_elements_count += inserted
                logger.info(
                    "[rag_service.py][index_files] insert_code_elements completed project_id=%s project_name=%s inserted=%d accumulated_code_elements=%d",
                    project_id,
                    project_name,
                    inserted,
                    code_elements_count,
                )
        except Exception as error:
            logger.exception("[rag_service.py][index_files] insert_code_elements failed")
            logs.append(f"[error] insert_code_elements: {error}")

        result = {
            "success": success,
            "failed": failed,
            "total_chunks": total_chunks,
            "indexed_files": indexed_files,
            "code_elements": code_elements_count,
            "logs": logs[-100:],
        }

        logger.info(
            "[rag_service.py][index_files] completed success=%d failed=%d total_chunks=%d indexed_files=%d code_elements=%d log_count=%d",
            result["success"],
            result["failed"],
            result["total_chunks"],
            result["indexed_files"],
            result["code_elements"],
            len(result["logs"]),
        )

        return result

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

        logger.info(
            "[rag_service.py][ask_with_context_stream] start query_type=%s project_id=%s project_name=%s top_k=%s layer_filter=%s extension_filter=%s question_len=%d retrieval_question_len=%d chat_history_count=%d recent_entities_count=%d",
            query_type,
            project_id,
            project_name,
            top_k,
            layer_filter,
            extension_filter,
            len(question or ""),
            len(retrieval_question or ""),
            len(chat_history or []),
            len(recent_entities or []),
        )

        # diagram 요청이면 LLM 대신 mermaid를 바로 생성 시도
        if query_type == "diagram" and project_id:
            try:
                q = (question or "").lower()

                logger.info(
                    "[rag_service.py][ask_with_context_stream] diagram branch entered project_id=%s question=%s",
                    project_id,
                    question,
                )

                if any(token in q for token in ["erd", "db", "table", "schema", "mermaid"]):
                    logger.info(
                        "[rag_service.py][ask_with_context_stream] build_table_erd called project_id=%s",
                        project_id,
                    )
                    mermaid = self.diagram_service.build_table_erd(project_id)
                else:
                    logger.info(
                        "[rag_service.py][ask_with_context_stream] build_flow_mermaid called project_id=%s",
                        project_id,
                    )
                    mermaid = self.diagram_service.build_flow_mermaid(project_id)

                if mermaid and len(mermaid.splitlines()) > 1:
                    logger.info(
                        "[rag_service.py][ask_with_context_stream] diagram generated line_count=%d",
                        len(mermaid.splitlines()),
                    )

                    async def mermaid_generator():
                        yield "```mermaid\n"
                        yield mermaid
                        yield "\n```"

                    return mermaid_generator(), []
            except Exception as error:
                logger.warning("[rag_service.py][ask_with_context_stream] DiagramService fallback to LLM error=%s", error)

        # 검색용 질의문 결정
        retrieval_text = (retrieval_question or question or "").strip()
        logger.info("검색용 질의문 ::: %s", retrieval_text)
        if not retrieval_text:
            retrieval_text = (question or "").strip()

        logger.info(
            "[rag_service.py][ask_with_context_stream] retrieval_text prepared length=%d preview=%s",
            len(retrieval_text),
            retrieval_text[:200],
        )

        # 질의 임베딩
        query_vector = self.embedding_service.embed_query(retrieval_text)

        logger.info(
            "[rag_service.py][ask_with_context_stream] embed_query completed vector_dim=%d",
            len(query_vector or []),
        )

        # ? query_vector 정보 :
        # 사용자의 질문을 임베딩 모델로 변환한 검색용 벡터
        # Qdrant 유사도 검색 입력값으로 사용됨

        # Qdrant 유사도 검색
        hits = self.qdrant_service.search(
            query_vector,
            project_id=project_id,
            top_k=top_k,
            layer_filter=layer_filter,
            extension_filter=extension_filter,
        )

        logger.info(
            "[rag_service.py][ask_with_context_stream] qdrant search completed hit_count=%d",
            len(hits or []),
        )

        if hits:
            first_hit = hits[0]
            logger.info(
                "[rag_service.py][ask_with_context_stream] first_hit relative_path=%s file_name=%s extension=%s layer_type=%s class_name=%s chunk_index=%s",
                first_hit.get("relative_path"),
                first_hit.get("file_name"),
                first_hit.get("extension"),
                first_hit.get("layer_type"),
                first_hit.get("class_name"),
                first_hit.get("chunk_index"),
            )

        # ? hits 정보 : Qdrant 검색 결과 문서/청크 리스트
        # 각 hit에는 보통 청크 본문(text)과 메타(project_id, file_name, relative_path,
        # extension, layer_type, class_name, chunk_index 등)가 포함됨
        # 이후 Ollama 프롬프트 컨텍스트로 전달됨

        # LLM 스트리밍 응답 생성
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

        logger.info(
            "[rag_service.py][ask_with_context_stream] generate_response_stream created query_type=%s hit_count=%d",
            query_type,
            len(hits or []),
        )

        # ? generator 정보 :
        # Ollama에서 생성되는 스트리밍 텍스트 generator
        # FastAPI StreamingResponse를 통해 프론트로 chunk 단위 전송됨

        logger.info("[rag_service.py][ask_with_context_stream] return generator,hits")

        return generator, hits