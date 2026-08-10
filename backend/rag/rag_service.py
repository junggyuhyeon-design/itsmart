from __future__ import annotations

import logging
import re
from typing import Any, Callable
from collections.abc import Iterable
from config import Settings
from database.history_repository import bulk_insert_file_index, insert_code_elements
from embedder.embedder import EmbeddingService
from parser.chunk_service import ChunkService
from parser.file_parser import extract_static_analysis, parse_text_file
from rag.ollama_service import OllamaService
from rag.qdrant_service import QdrantService
from rag.reranker_service import RerankerService  #Reranker
from rag.exact_grep_service import ExactGrepService  #exact grep (독립 리터럴 검색)

logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.embedding_service = EmbeddingService(settings)
        self.qdrant_service = QdrantService(settings)
        self.ollama_service = OllamaService(settings)
        self.chunk_service = ChunkService(settings)
        self.reranker_service = RerankerService(settings)  #Reranker
        self.exact_grep_service = ExactGrepService(settings)  #exact grep (독립 리터럴 검색)

        logger.info(
            "[rag_service.py][__init__] initialized services top_k=%s embedding_dimension=%s",
            getattr(settings, "top_k", None),
            getattr(self.embedding_service, "dimension", None),
        )

        # 개발/테스트 단계: 기존 컬렉션 스키마를 무시하고 강제로 재생성
        # 주의: 서버 재시작 시 Qdrant 컬렉션 데이터가 삭제되므로 반드시 재인덱싱 필요
        logger.warning(
            "[rag_service.py][__init__][개발모드 ★] Qdrant collection will be recreated. all previous vector data will be removed."
        )
        if getattr(self.settings, "qdrant_force_recreate", False):
            self.qdrant_service.recreate_collection(self.embedding_service.dimension)
        else:
            self.qdrant_service.ensure_collection(self.embedding_service.dimension)

        logger.info(
            "[rag_service.py][__init__] recreate_collection completed dimension=%s",
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
        # file_name           : 파일명
        # extension          : 확장자
        # file_size           : 파일 크기

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
            relative_path = target.get("relative_path")

            # logger.info(
            #     "[rag_service.py][index_files] target start step=%d/%d project_id=%s project_name=%s relative_path=%s extension=%s file_size=%s",
            #     index,
            #     total_targets,
            #     target.get("project_id"),
            #     target.get("project_name"),
            #     relative_path,
            #     target.get("extension"),
            #     target.get("filesize"),
            # )

            try:
                # 파일 읽기 + 기본 메타/언어/계층/클래스 정보 파싱
                parsed = parse_text_file(target)
                # logger.info(
                #     "[rag_service.py][index_files] parse_text_file returned relative_path=%s parsed=%s",
                #     relative_path,
                #     parsed is not None,
                #     )
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

                # logger.info(
                #     "[rag_service.py][index_files] parsed summary relative_path=%s language=%s layer_type=%s content_type=%s class_name=%s package=%s",
                #     parsed.get("relative_path"),
                #     parsed.get("language"),
                #     parsed.get("layer_type"),
                #     parsed.get("content_type"),
                #     parsed.get("class_name"),
                #     parsed.get("package"),
                # )

                # 파일 청킹
                chunks = self.chunk_service.chunk_parsed_file(parsed)

                # logger.info(
                #     "[rag_service.py][index_files] chunk_parsed_file completed relative_path=%s chunk_count=%d",
                #     relative_path,
                #     len(chunks or []),
                # )

                # ? chunks 정보 : 벡터DB(Qdrant)에 저장할 청크 리스트
                # project_id         : 프로젝트 아이디
                # project_name       : 프로젝트명
                # file_name          : 파일명
                # extension          : 확장자
                # relative_path      : 상대경로
                # saved_path         : 저장 경로
                # file_path          : 실제 파일 경로
                # file_size          : 파일 크기
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

                # logger.info(
                #     "[rag_service.py][index_files] valid_chunks filtered relative_path=%s raw_chunk_count=%d valid_chunk_count=%d",
                #     relative_path,
                #     len(chunks or []),
                #     len(valid_chunks),
                # )

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

                texts = [(chunk.get("text") or "").strip() for chunk in valid_chunks]

                # ? vectors 정보 : 각 청크 텍스트를 임베딩한 벡터 리스트
                # [
                #   [0.0123, -0.4421, ...],
                #   [0.2871,  0.0311, ...],
                #   ...
                # ]
                dense_vectors = self.embedding_service.embed_texts_dense(texts)
                sparse_vectors = self.embedding_service.embed_texts_sparse(texts)

                logger.info(
                    "[rag_service.py][index_files][Sparse 벡터 생성 확인 ★] relative_path=%s sparse_vector_count=%d first_indices_len=%s",
                    relative_path,
                    len(sparse_vectors or []),
                    len(sparse_vectors[0].indices) if sparse_vectors else 0,
                )
                logger.info("============================================================")

                # Qdrant 저장
                upserted = self.qdrant_service.upsert_chunks(valid_chunks, dense_vectors, sparse_vectors)
                total_chunks += upserted

                logger.info(
                    "[rag_service.py][index_files] upsert_chunks completed relative_path=%s upserted=%d accumulated_total_chunks=%d",
                    relative_path,
                    upserted,
                    total_chunks,
                )
                logger.info("============================================================")

                project_id = parsed.get("project_id", "")
                project_name = parsed.get("project_name", "")

                # SQLite file_index 저장용 메타데이터 누적
                file_index_rows.append(
                    {
                        "project_id": project_id,
                        "project_name": project_name,
                        "file_name": parsed.get("file_name", ""),
                        "relative_path": parsed.get("relative_path", ""),
                        "extension": parsed.get("extension", ""),
                        "file_size": int(parsed.get("file_size", 0)),
                    }
                )

                # logger.info(
                #     "[rag_service.py][index_files] file_index_rows appended project_id=%s project_name=%s relative_path=%s current_file_index_rows=%d",
                #     project_id,
                #     project_name,
                #     parsed.get("relative_path", "") or target.get("relative_path", ""),
                #     len(file_index_rows),
                #     )

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
                        # logger.debug(
                        #     "static analysis prepared: %s / layer=%s / class=%s",
                        #     analysis.get("relative_path"),
                        #     analysis.get("layer_type"),
                        #     analysis.get("class_name"),
                        # )
                        # logger.info(
                        #     "[rag_service.py][index_files] code_elements_rows_by_project appended project_id=%s project_name=%s current_project_elements=%d",
                        #     project_id,
                        #     project_name,
                        #     len(code_elements_rows_by_project.get(key, [])),
                        # )
                except Exception:
                    logger.exception("[rag_service.py][index_files] extract_static_analysis failed relative_path=%s", relative_path)

                success += 1
                logs.append(f"[ok] indexed {relative_path} ({upserted} chunks)")

                # logger.info(
                #     "[rag_service.py][index_files] target completed step=%d/%d relative_path=%s success=%d failed=%d total_chunks=%d",
                #     index,
                #     total_targets,
                #     relative_path,
                #     success,
                #     failed,
                #     total_chunks,
                # )

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

        # logger.info(
        #     "[rag_service.py][index_files] before sqlite save file_index_rows=%d project_groups=%d",
        #     len(file_index_rows),
        #     len(code_elements_rows_by_project),
        # )

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
                # logger.info(
                #     "[rag_service.py][index_files] insert_code_elements completed project_id=%s project_name=%s inserted=%d accumulated_code_elements=%d",
                #     project_id,
                #     project_name,
                #     inserted,
                #     code_elements_count,
                # )
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

        # logger.info(
        #     "[rag_service.py][index_files] completed success=%d failed=%d total_chunks=%d indexed_files=%d code_elements=%d log_count=%d",
        #     result["success"],
        #     result["failed"],
        #     result["total_chunks"],
        #     result["indexed_files"],
        #     result["code_elements"],
        #     len(result["logs"]),
        # )

        return result

    def _normalize_hit(self, hit: Any) -> dict[str, Any] | None:
        if hit is None:
            return None

        if isinstance(hit, dict):
            return hit

        payload = getattr(hit, "payload", None)
        score = getattr(hit, "score", None)
        point_id = getattr(hit, "id", None)

        if isinstance(payload, dict):
            normalized = dict(payload)
            if score is not None and "score" not in normalized:
                normalized["score"] = score
            if point_id is not None and "id" not in normalized:
                normalized["id"] = point_id
            return normalized

        return None

    def _make_exact_match_hits(self, hits: list[dict[str, Any]] | None, needle: str) -> list[dict[str, Any]]:
        """
        검색 결과 중 exact/substring match를 우선 추려서 앞쪽으로 재배치한다.
        """

        hits = self._flatten_hits(hits)

        if not hits:
            logger.info(
                "[rag_service.py][_make_exact_match_hits] needle=%s exact_count=0 partial_count=0 other_count=0",
                needle,
            )
            return []

        if not needle:
            return hits

        needle_norm = needle.strip().lower()
        if not needle_norm:
            return hits

        exact_hits: list[dict[str, Any]] = []
        partial_hits: list[dict[str, Any]] = []
        others: list[dict[str, Any]] = []

        logger.info("=== for 문 실행중 ===")
        for hit in hits:
            text = str(hit.get("text") or "").strip()
            text_norm = text.lower()

            copied = dict(hit)

            logger.info("text_norm :: %s", text_norm)
            logger.info("needle_norm :: %s", needle_norm)

            if text_norm == needle_norm:
                copied["match_type"] = "exact"
                exact_hits.append(copied)
            elif needle_norm in text_norm:
                copied["match_type"] = "substring"
                partial_hits.append(copied)
            else:
                others.append(copied)

        merged = exact_hits + partial_hits + others
        logger.info("=== merged 완성 ===")

        # logger.info(
        #     "[rag_service.py][_make_exact_match_hits] needle=%s exact_count=%d partial_count=%d other_count=%d",
        #     needle,
        #     len(exact_hits),
        #     len(partial_hits),
        #     len(others),
        # )
        return merged

    def _make_line_level_exact_hits(self, hits: list[dict[str, Any]] | None, needle: str) -> list[dict[str, Any]]:
        """
        청크 내부 라인 기준으로 exact/substring match가 보이는 경우 해당 라인 주변만 evidence로 축약한다.
        """

        hits = self._flatten_hits(hits)

        if not hits:
            logger.info(
                "[rag_service.py][_make_line_level_exact_hits] needle=%s result_count=0",
                needle,
            )
            return []

        if not needle:
            return hits

        needle_norm = needle.strip().lower()
        if not needle_norm:
            return hits

        results: list[dict[str, Any]] = []
        used_keys: set[tuple[str, Any]] = set()

        for hit in hits:
            text = str(hit.get("text") or "").strip()
            if not text:
                results.append(hit)
                continue

            lines = text.splitlines()
            matched = False

            for idx, line in enumerate(lines):
                line_norm = line.lower()
                if needle_norm not in line_norm:
                    continue

                start = max(0, idx - 2)
                end = min(len(lines), idx + 3)
                snippet = "\n".join(lines[start:end]).strip()

                copied = dict(hit)
                copied["text"] = snippet
                copied["match_type"] = "exact_line" if line_norm.strip() == needle_norm else "substring_line"
                copied["matched_line"] = idx + 1

                dedupe_key = (
                    copied.get("relative_path") or copied.get("file_name"),
                    copied.get("chunk_index"),
                )
                if dedupe_key not in used_keys:
                    used_keys.add(dedupe_key)
                    results.append(copied)

                matched = True
                break

            if not matched:
                results.append(hit)

        logger.info(
            "[rag_service.py][_make_line_level_exact_hits] needle=%s result_count=%d",
            needle,
            len(results),
        )
        return results

    def _flatten_hits(self, hits: Any) -> list[dict[str, Any]]:
        """
        검색 결과가 list[dict]가 아니라 list[list[dict]] 형태로 들어오는 경우를 대비해 평탄화한다.
        dict 타입만 최종 hit로 인정한다.
        """
        result: list[dict[str, Any]] = []

        def _walk(value: Any) -> None:
            if value is None:
                return

            normalized = self._normalize_hit(value)
            if normalized is not None:
                result.append(normalized)
                return

            if isinstance(value, (str, bytes)):
                return

            if isinstance(value, Iterable):
                for item in value:
                    _walk(item)

        _walk(hits)

        logger.info(
            "[rag_service.py][_flatten_hits] input_type=%s flattened_count=%d",
            type(hits).__name__,
            len(result),
        )
        return result

    def _safe_first_hit(self, hits: list[dict[str, Any]] | None) -> dict[str, Any] | None:
        """
        첫 번째 hit를 안전하게 반환한다.
        """
        if not hits:
            return None

        first = hits[0]
        if isinstance(first, dict):
            return first

        logger.warning(
            "[rag_service.py][_safe_first_hit] first hit is not dict type=%s",
            type(first).__name__,
        )
        return None

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
            edit_source: str | None = None, #exact grep (독립 리터럴 검색)
            edit_target: str | None = None, #step3 변경 후 문자열
    ):
        if top_k is None:
            top_k = self.settings.top_k

        # logger.info(
        #     "[rag_service.py][ask_with_context_stream] start query_type=%s project_id=%s project_name=%s top_k=%s layer_filter=%s extension_filter=%s question_len=%d retrieval_question_len=%d chat_history_count=%d recent_entities_count=%d",
        #     query_type,
        #     project_id,
        #     project_name,
        #     top_k,
        #     layer_filter,
        #     extension_filter,
        #     len(question or ""),
        #     len(retrieval_question or ""),
        #     len(chat_history or []),
        #     len(recent_entities or []),
        # )

        # 검색용 질의문 결정
        retrieval_text = (retrieval_question or question or "").strip()
        # if not retrieval_text:
        #     retrieval_text = (question or "").strip()

        # logger.info(
        #     "[rag_service.py][ask_with_context_stream] retrieval_text prepared length=%d preview=%s",
        #     len(retrieval_text),
        #     retrieval_text[:200],
        # )

        # 질의 임베딩
        dense_query_vector = self.embedding_service.embed_query_dense(retrieval_text)
        sparse_query_vector = self.embedding_service.embed_query_sparse(retrieval_text)

        logger.info(
            "[rag_service.py][ask_with_context_stream][Sparse 질의 벡터 확인 ★] indices_len=%s values_len=%s",
            len(sparse_query_vector.indices) if sparse_query_vector else 0,
            len(sparse_query_vector.values) if sparse_query_vector else 0,
        )

        # ? query_vector 정보 :
        # 사용자의 질문을 임베딩 모델로 변환한 검색용 벡터
        # Qdrant 유사도 검색 입력값으로 사용됨

        logger.info("intent 에서 넘어온 top_k : %s", top_k)
        logger.info("default top_k : %s", self.settings.top_k)
        logger.info("reranker_candidate_top_k : %s", self.settings.reranker_candidate_top_k)

        candidate_top_k = max(top_k or self.settings.top_k, self.settings.reranker_candidate_top_k) # Reranker
        logger.info("최종 셋팅 top_k :: %s", candidate_top_k)

        # Qdrant 유사도 검색
        hits = self.qdrant_service.search(
            dense_query_vector,
            sparse_query_vector,
            project_id=project_id,
            top_k=candidate_top_k,  # Reranker
            layer_filter=layer_filter,
            extension_filter=extension_filter,
        )

        # raw_hit_count = len(hits or []) if isinstance(hits, list) else 0
        # if isinstance(hits, list) and hits:
        #     logger.info(
        #         "[rag_service.py][ask_with_context_stream] raw first hit type=%s repr=%s",
        #         type(hits[0]).__name__,
        #         str(hits[0])[:500],
        #     )

        hits = self._flatten_hits(hits)

        # logger.info(
        #     "[rag_service.py][ask_with_context_stream] qdrant search completed raw_hit_count=%d flattened_hit_count=%d",
        #     raw_hit_count,
        #     len(hits or []),
        # )

        # exact grep (독립 리터럴 검색), Reranker
        # edit 계열 요청: vector 전(우선) exact grep 리터럴 검색 → 최우선 증거로 병합
        if query_type in {"edit_text_one", "edit_text_all"}:
            grep_needle = (edit_source or retrieval_text or "").strip()

            logger.info("변경 요청 질의 확인 (grep_needle) :: %s", grep_needle)

            # 1) vector 검색과 무관하게 프로젝트 전체 소스에서 리터럴 grep
            grep_hits: list[dict[str, Any]] = []
            if grep_needle and project_id:
                try:
                    grep_hits = self.exact_grep_service.search(
                        grep_needle,
                        project_id=project_id,
                        project_name=project_name,
                    )
                    grep_hits = self._flatten_hits(grep_hits)
                except Exception:
                    logger.exception(
                        "[rag_service.py][ask_with_context_stream] exact grep failed needle=%s",
                        grep_needle,
                    )
                    grep_hits = []

                logger.info(
                    "[rag_service.py][ask_with_context_stream] exact grep completed query_type=%s grep_hit_count=%d vector_hit_count=%d",
                    query_type,
                    len(grep_hits),
                    len(hits),
                )

            # 2) vector hit 에 대해서만 기존 exact-first 라인레벨 재정렬 적용
            #    (grep hit 의 text/line_no 는 그대로 보존하여 step3 정확한 줄/전후값 답변에 사용)
            vector_hits = self._make_exact_match_hits(hits, grep_needle)
            vector_hits = self._make_line_level_exact_hits(vector_hits, grep_needle)
            vector_hits = self._flatten_hits(vector_hits) 

            # 3) 병합: grep 결과 최우선. grep hit 있으면 vector 후보는 보조(fallback)로 제한.
            if grep_hits:
                if query_type == "edit_text_all":
                    kept_grep = grep_hits  # 전체 발생 위치 유지
                else:
                    kept_grep = grep_hits[:3]  # edit_text_one: 상위 3건
                target_total = top_k or self.settings.top_k
                remaining = max(0, target_total - len(kept_grep))
                hits = kept_grep + vector_hits[:remaining]
            else:
                # grep 미발견: vector 후보를 그대로 사용(정확한 문자열 일치 미발견 fallback)
                hits = vector_hits[: top_k or self.settings.top_k]

            logger.info(
                "[rag_service.py][ask_with_context_stream] exact-first merge completed query_type=%s hit_count=%d grep_count=%d",
                query_type,
                len(hits or []),
                len(grep_hits),
            )

        if self.settings.reranker_enabled and hits and query_type not in {"edit_text_one", "edit_text_all"}:
            reranked_hits = self.reranker_service.rerank(
                query=retrieval_text,
                hits=hits,
                final_top_n=top_k or self.settings.reranker_final_top_n,
            )
            hits = self._flatten_hits(reranked_hits)
            # logger.info(
            #     "ragservice.py ask_with_context_stream rerank completed before=%d after=%d",
            #     len(reranked_hits or []),
            #     len(hits or []),
            # )
        else:
            hits = list(hits or [])
            # edit_text_all 은 grep 발생 위치 전체를 보존 (step3 전체 변경 답변용). 그 외만 top_k 로 절단.
            if query_type != "edit_text_all":
                hits = hits[: top_k or self.settings.top_k]

            # logger.info(
            #     "ragservice.py ask_with_context_stream rerank skipped enabled=%s query_type=%s hit_count=%d final_hit_count=%d",
            #     self.settings.reranker_enabled,
            #     query_type,
            #     len(hits or []),
            #     len(hits or []),
            # )

        first_hit = self._safe_first_hit(hits)
        if first_hit:
            logger.info(
                "[rag_service.py][ask_with_context_stream] first_hit relative_path=%s file_name=%s extension=%s layer_type=%s class_name=%s chunk_index=%s match_type=%s",
                first_hit.get("relative_path"),
                first_hit.get("file_name"),
                first_hit.get("extension"),
                first_hit.get("layer_type"),
                first_hit.get("class_name"),
                first_hit.get("chunk_index"),
                first_hit.get("match_type"),
            )
        else:
            logger.info(
                "[rag_service.py][ask_with_context_stream] first_hit unavailable hit_count=%d",
                len(hits or []),
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
            edit_source=edit_source, #변경 파일/줄/전후값 중심 답변 패치
            edit_target=edit_target, #변경 파일/줄/전후값 중심 답변 패치
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