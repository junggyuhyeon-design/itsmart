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

        # Qdrant 컬렉션 보장
        self.qdrant_service.ensure_collection(self.embedding_service.dimension)

    def index_files(
            self,
            targets: list[dict[str, Any]],
            progress_callback: Callable[..., None] | None = None,
    ) -> dict[str, Any]:
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

            try:
                # 파일 읽기 + 기본 메타/언어/계층/클래스 정보 파싱
                parsed = parse_text_file(target)

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
                # content_type         : 내용 타입 (apiendpoint/sqlselect/ddlcreate 등)
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

                # 파일 청킹
                chunks = self.chunk_service.chunk_parsed_file(parsed)

                # ? chunks 정보 : 벡터DB(Qdrant)에 저장할 청크 리스트
                # projectid         : 프로젝트 아이디
                # projectname       : 프로젝트명
                # filename          : 파일명
                # extension         : 확장자
                # relativepath      : 상대경로
                # savedpath         : 저장 경로
                # filepath          : 실제 파일 경로
                # filesize          : 파일 크기
                # sourcetype        : 업로드 원본 타입
                # rootcontainername : 루트 zip 이름
                # layertype         : 계층 타입
                # classname         : 클래스명
                # package           : 패키지명
                # contenttype       : 내용 타입
                # text              : 청크 텍스트
                # chunkindex        : 청크 인덱스
                # startline         : 시작 라인
                # endline           : 끝 라인
                # chunktype         : 청크 타입(text 등)

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

                # 공백 청크 제거
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

                # ? vectors 정보 : 각 청크 텍스트를 임베딩한 벡터 리스트
                # [
                #   [0.0123, -0.4421, ...],
                #   [0.2871,  0.0311, ...],
                #   ...
                # ]
                vectors = self.embedding_service.embed_texts(
                    [(chunk.get("text") or "").strip() for chunk in valid_chunks]
                )

                # Qdrant 저장
                upserted = self.qdrant_service.upsert_chunks(valid_chunks, vectors)
                total_chunks += upserted

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

                try:
                    # 정적 분석 추출
                    analysis = extract_static_analysis(parsed)

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
                except Exception:
                    logger.exception("extract_static_analysis failed: %s", relative_path)

                success += 1
                logs.append(f"[ok] indexed {relative_path} ({upserted} chunks)")

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
            # SQLite file_index 테이블 일괄 저장
            indexed_files = bulk_insert_file_index(file_index_rows)
        except Exception as error:
            logger.exception("bulk_insert_file_index failed: %s", error)
            logs.append(f"[error] bulk_insert_file_index: {error}")

        try:
            # SQLite code_elements 테이블 프로젝트별 일괄 저장
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

        # diagram 요청이면 LLM 대신 mermaid를 바로 생성 시도
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

        # 검색용 질의문 결정
        retrieval_text = (retrieval_question or question or "").strip()
        if not retrieval_text:
            retrieval_text = (question or "").strip()

        # 질의 임베딩
        query_vector = self.embedding_service.embed_query(retrieval_text)

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

        # ? generator 정보 :
        # Ollama에서 생성되는 스트리밍 텍스트 generator
        # FastAPI StreamingResponse를 통해 프론트로 chunk 단위 전송됨

        return generator, hits