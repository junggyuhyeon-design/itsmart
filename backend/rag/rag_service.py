import logging
import re
from pathlib import Path

import sqlglot
import sqlglot.expressions as sg_exp
from sqlglot import ErrorLevel

from config import Settings
from database.history_repository import bulk_insert_file_index
from embedder.embedder import EmbeddingService
from parser.chunk_service import ChunkService
from parser.file_parser import parse_text_file
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

    # ── 인덱싱 ──────────────────────────────────────────────────
    def index_files(self, targets: list) -> dict:
        self.qdrant_service.ensure_collection(self.embedding_service.dimension)
        results: dict = {"success": 0, "failed": 0, "total_chunks": 0, "logs": []}
        indexed_meta: list[dict] = []

        for t in targets:
            # ? target 정보 :
            # original_name : 파일명
            # saved_path    : 저장경로(절대)
            # relative_path : 저장경로(상대)
            # extension     : 확장자
            # project_id    : 프로젝트아이디
            # project_name  : 프로젝트명
            rel_path = t.get("relative_path", "unknown")
            try:
                # 파일 파싱
                parsed = parse_text_file(t)
                if not parsed:
                    results["logs"].append(f"⚠️ {rel_path}: 파싱 결과 없음")
                    continue
                # ? parsed 정보 :
                # raw_text      : 파일 원문데이터
                # project_id    : 프로젝트아이디
                # project_name  : 프로젝트명
                # file_name     : 파일명
                # extension     : 확장자
                # relative_path : 저장경로
                # layer_type    : 계층 타입
                # class_name    : 클래스명

                # 파일 청킹
                chunks = self.chunk_service.split_text(parsed["raw_text"], parsed)
                if not chunks:
                    results["logs"].append(f"⚠️ {rel_path}: 생성된 청크 없음")
                    continue
                # ? chunk 정보 :
                # project_id    : 프로젝트아이디
                # project_name  : 프로젝트명
                # text          : seg 텍스트
                # file_name     : 파일명
                # extension     : 확장자
                # relative_path : 저장경로
                # chunk_index   : 인덱스
                # layer_type    : 계층 타입
                # class_name    : 클래스명

                # 파일 벡터화(BAAI/bge-m3)
                vectors = self.embedding_service.embed_texts(
                    [c["text"] for c in chunks]
                )

                # Qdrant 저장(Qdrant)
                count = self.qdrant_service.upsert_chunks(chunks, vectors)

                results["success"] += 1
                results["total_chunks"] += count
                results["logs"].append(f"✅ {rel_path} ({count} chunks)")

                # SQLite file_index 저장용 메타데이터 수집
                indexed_meta.append(
                    {
                        "project_id": parsed["project_id"],
                        "project_name": parsed["project_name"],
                        "file_name": parsed["file_name"],
                        "relative_path": parsed["relative_path"],
                        "extension": parsed["extension"],
                    }
                )
            except Exception as e:
                results["failed"] += 1
                results["logs"].append(f"❌ {rel_path}: {e}")
                logger.exception("index_files 실패: %s", rel_path)

        if indexed_meta:
            try:
                saved = bulk_insert_file_index(indexed_meta)  # SQLite 저장
                logger.info("file_index 저장 완료: %d건", saved)
            except Exception:
                logger.exception("file_index 저장 실패 — Qdrant 인덱싱은 이미 완료됨")

        return results

    # ── 질문 스트리밍 ────────────────────────────────────────────
    async def ask_with_context_stream(
        self,
        question: str,
        search_query: str,  # ← 정제된 검색 쿼리 (노이즈 제거)
        project_id: str | None,
        project_name: str | None,
        chat_history: list[dict] | None = None,
        top_k: int | None = None,
        layer_filter: str | None = None,
        extension_filter: str | None = None,
        query_type: str = "qa",
    ):
        """Qdrant 검색 → OllamaService 스트리밍."""
        top_k = top_k or self.settings.top_k

        # 정제된 질문을 BAAI/bge-m3 로 벡터화
        query_vector = self.embedding_service.embed_query(search_query)

        # Qdrant 유사 벡터 검색
        # ? hits
        # [{"score": r.score, **r.payload} for r in results]
        hits = self.qdrant_service.search(
            query_vector,
            project_id=project_id,
            top_k=top_k,
            layer_filter=layer_filter,
            extension_filter=extension_filter,
        )

        gen = self.ollama_service.generate_response_stream(
            question=question,         # LLM에는 원문 질문 전달
            hits=hits,                 # Qdrant 에서 조회된 청크 데이터
            query_type=query_type,     # 질문 유형
            project_name=project_name,
            chat_history=chat_history, # 대화 이력
        )
        return gen, hits

    # ── 전체 초기화 ──────────────────────────────────────────────
    def reset(self) -> None:
        try:
            self.qdrant_service.reset_collection(self.embedding_service.dimension)
            logger.info("RAGService reset 완료")
        except Exception:
            logger.exception("RAGService reset 실패")
            raise

    # ── DB 관계 분석 (Mermaid 생성용) ────────────────────────────
    def analyze_db_relations(
        self,
        targets: list,
        entity_filter: str | None = None,
    ) -> dict:
        """
        Qdrant 청크를 조회해 테이블 정의와 소스↔테이블 관계를 추출한다.

        entity_filter 가 있으면:
        1. scroll_all 단계에서 relative_path/file_name 에 키워드 포함 파일만 로드
        2. 관계 추출 단계에서 해당 키워드를 테이블명에 포함하는 관계만 추적
        → 전체 스캔 대신 관련 청크만 처리해 성능 및 정확도 향상
        """
        from collections import defaultdict

        project_id = targets[0].get("project_id") if targets else None
        ef = entity_filter.upper() if entity_filter else None

        logger.info(f"Analyzing DB relations for project: {project_id}, entity filter: {ef}")

        if ef:
            # 필터 대상 파일 청크 (relative_path/file_name/class_name 키워드 포함)
            filtered_chunks = self.qdrant_service.scroll_all(
                project_id=project_id,
                keyword_hint=ef,
            )
            # SQL 파일 청크 (테이블 정의 확보 — 키워드 무관)
            sql_chunks = self.qdrant_service.scroll_all(
                project_id=project_id,
                keyword_hint=".sql",  # .sql 파일만
            )
            # 중복 제거 후 합산 (relative_path + chunk_index 기준)
            seen: set[str] = set()
            all_chunks: list[dict] = []
            for chunk in filtered_chunks + sql_chunks:
                key = f"{chunk.get('relative_path', '')}:{chunk.get('chunk_index', '')}"
                if key not in seen:
                    seen.add(key)
                    all_chunks.append(chunk)
        else:
            all_chunks = self.qdrant_service.scroll_all(project_id=project_id)

        # 2. 청크 → 파일 단위 raw_text 합산
        # scroll_all 반환 payload의 텍스트 키는 "text" (chunk_service._make_chunk 기준)
        parsed_files: list[dict] = []
        for chunk in all_chunks:
            chunk_text = chunk.get("text") or ""
            if not chunk_text.strip():
                continue
            parsed_files.append(
                {
                    "relative_path": chunk.get("relative_path", ""),
                    "file_name": chunk.get("file_name", ""),
                    "extension": (chunk.get("extension") or "").lower(),
                    "raw_text": chunk_text,
                }
            )

        if not parsed_files:
            return {
                "tables": [],
                "table_definitions": {},
                "relations": [],
                "source_to_tables": {},
            }

        file_texts: dict[str, dict] = {}
        for pf in parsed_files:
            path = pf["relative_path"]
            if path not in file_texts:
                file_texts[path] = dict(pf)
            else:
                file_texts[path]["raw_text"] += "\n" + pf["raw_text"]

        merged_files = list(file_texts.values())

        # 3. 테이블 정의 추출 (조합된 파일)
        # 테이블 이름, 테이블정의 파일매핑, 테이블 상세정보 리스트
        table_names, table_definitions, _ = self._extract_table_definitions(
            merged_files
        )

        logger.info(f"Extracted {len(table_names)} tables from {len(merged_files)} files.")

        target_tables = table_names  # 기본: 전체
        if ef:
            target_tables = {t for t in table_names if ef in t}
            if not target_tables:
                # 테이블명 매칭 없으면 전체 테이블로 폴백 (파일 경로 필터만 적용)
                target_tables = table_names

        # 4. 관계 추출
        relations: list[dict] = []
        source_to_tables: dict = defaultdict(
            lambda: defaultdict(
                lambda: {
                    "ops": set(),
                    "categories": set(),
                    "scopes": set(),
                }
            )
        )

        for file_info in merged_files:
            entities = self._extract_entities(
                file_info["raw_text"], file_info["extension"]
            )
            for entity in entities:
                for table in target_tables:
                    # 파일 내에서 target 테이블이 어떻게 연산되고있는지 추출
                    # sqlglot 기반으로 교체 — 원문 텍스트 그대로 전달
                    usage_ops = self._detect_table_usage(entity["text"], table)
                    if not usage_ops:
                        continue
                    categories = {self._map_op_category(op) for op in usage_ops}
                    relations.append(
                        {
                            "file": file_info["relative_path"],
                            "file_name": file_info["file_name"],
                            "entity_type": entity["type"],
                            "entity_name": entity["name"],
                            "table": table,
                            "operations": sorted(
                                usage_ops
                            ),  # insert, update, delete, merge ..
                            "categories": sorted(categories),  # READS, WRITES, JOINS ..
                        }
                    )
                    bucket = source_to_tables[file_info["relative_path"]][table]
                    bucket["ops"].update(usage_ops)
                    bucket["categories"].update(categories)
                    bucket["scopes"].add(f"{entity['type']}:{entity['name']}")

            if file_info["relative_path"] not in source_to_tables:
                for table in target_tables:
                    if re.search(rf"\b{re.escape(table)}\b", file_info["raw_text"], re.I):
                        bucket = source_to_tables[file_info["relative_path"]][table]
                        if not bucket["ops"]:
                            bucket["ops"].add("REF")
                            bucket["categories"].add("REF")
                            bucket["scopes"].add(
                                f"file:{Path(file_info['relative_path']).name}"
                            )

        normalized: dict = {}
        for file_path, table_map in source_to_tables.items():
            normalized[file_path] = {
                table: {
                    "operations": sorted(meta["ops"]),
                    "categories": sorted(meta["categories"]),
                    "scopes": sorted(meta["scopes"]),
                }
                for table, meta in table_map.items()
            }

        return {
            "tables": sorted(target_tables),
            "table_definitions": table_definitions,
            "relations": relations,
            "source_to_tables": normalized,
        }

    def generate_source_to_table_mermaid(
        self,
        db_data: dict,
    ) -> str:
        """
        analyze_db_relations() 결과를 Mermaid flowchart 코드로 변환.
        entity_filter 가 있으면 관련 relation / node 만 남겨서 부분 구조도를 만든다.
        """
        relations = db_data.get("relations", []) or []
        source_to_tables = db_data.get("source_to_tables", {}) or {}
        tables_from_data = db_data.get("tables", []) or []

        def _safe_node_text(value: str) -> str:
            text = str(value or "").strip()
            if not text:
                text = "UNKNOWN"
            text = Path(text).name if "/" in text or "\\" in text else text
            text = text.replace(" ", "_")
            return self._escape_mermaid(text)

        def _safe_edge_label(
            categories: list[str] | None, operations: list[str] | None
        ) -> str:
            categories = categories or []
            operations = operations or []

            label_parts: list[str] = []
            for c in categories:
                if c and c not in label_parts:
                    label_parts.append(c)

            detail_ops = [op for op in operations if op and op != "REF"]
            if detail_ops:
                op_label = "/".join(dict.fromkeys(detail_ops))
                if op_label not in label_parts:
                    label_parts.append(op_label)

            if not label_parts:
                label_parts = ["REF"]

            return self._escape_mermaid(", ".join(label_parts))

        def _is_sql_definition_file(file_path: str) -> bool:
            """
            DDL(CREATE TABLE) 전용 파일만 노드에서 제외한다.
            DML(.sql)이 섞인 파일은 관계 노드로 포함해야 하므로
            파일명 키워드 기반으로 한정 필터링한다.
            """
            name = Path(file_path or "").name.lower()
            ddl_names = (
                "init.sql",
                "schema.sql",
                "ddl.sql",
                "create.sql",
                "tables.sql",
            )
            return name in ddl_names

        # 1) 실제 사용할 파일/테이블/엔티티 수집
        file_set: set[str] = set()
        table_set: set[str] = set(t for t in tables_from_data if t)

        entity_keys: list[tuple[str, str, str]] = []
        entity_seen: set[tuple[str, str, str]] = set()

        edge_rows: list[dict] = []

        # relations 기준 edge 우선 구성
        for r in relations:
            file_path = r.get("file", "") or ""
            table = r.get("table", "") or ""
            entity_type = r.get("entity_type", "") or "file"
            entity_name = r.get("entity_name", "") or Path(file_path).name
            categories = r.get("categories", []) or []
            operations = r.get("operations", []) or []

            if not file_path or not table:
                continue

            if _is_sql_definition_file(file_path):
                continue

            file_set.add(file_path)
            table_set.add(table)

            if entity_type != "file":
                key = (file_path, entity_type, entity_name)
                if key not in entity_seen:
                    entity_seen.add(key)
                    entity_keys.append(key)

            edge_rows.append(
                {
                    "file": file_path,
                    "table": table,
                    "entity_type": entity_type,
                    "entity_name": entity_name,
                    "categories": categories,
                    "operations": operations,
                }
            )

        # source_to_tables 기준으로 빠진 file -> table 관계 보강
        # relations 에 이미 같은 file/table/entity_type=file 조합이 있으면 중복 추가하지 않음
        existing_file_table_edges = {
            (e["file"], e["table"]) for e in edge_rows if e["entity_type"] == "file"
        }

        for file_path, table_map in source_to_tables.items():
            if not file_path or _is_sql_definition_file(file_path):
                continue

            file_set.add(file_path)

            for table, meta in (table_map or {}).items():
                if not table:
                    continue

                table_set.add(table)

                scopes = meta.get("scopes", []) or []
                operations = meta.get("operations", []) or []
                categories = meta.get("categories", []) or []

                # file 스코프가 명시되어 있으면 file -> table 관계로 보강
                has_file_scope = any(str(s).startswith("file:") for s in scopes)

                if (
                    has_file_scope
                    and (file_path, table) not in existing_file_table_edges
                ):
                    edge_rows.append(
                        {
                            "file": file_path,
                            "table": table,
                            "entity_type": "file",
                            "entity_name": Path(file_path).name,
                            "categories": categories,
                            "operations": operations,
                        }
                    )
                    existing_file_table_edges.add((file_path, table))

        # relation/보강 결과가 전혀 없으면 최소 Mermaid 반환
        if not edge_rows and not table_set:
            return 'flowchart LR\n    N0["NO_RELATIONS"]'

        all_tables = sorted(table_set)
        all_files = sorted(file_set)

        table_ids: dict[str, str] = {t: f"T{i}" for i, t in enumerate(all_tables)}
        file_ids: dict[str, str] = {f: f"F{i}" for i, f in enumerate(all_files)}
        entity_ids: dict[tuple[str, str, str], str] = {
            k: f"E{i}" for i, k in enumerate(entity_keys)
        }

        lines: list[str] = ["flowchart LR"]

        # 2) 테이블 노드
        for table in all_tables:
            tid = table_ids[table]
            lines.append(f'    {tid}["{_safe_node_text(table)}"]')

        # 3) 파일 노드
        for file_path in all_files:
            fid = file_ids[file_path]
            file_label = _safe_node_text(Path(file_path).name)
            lines.append(f'    {fid}["{file_label}"]')

        # 4) 엔티티 노드
        for (file_path, entity_type, entity_name), eid in entity_ids.items():
            label = _safe_node_text(entity_name)
            lines.append(f'    {eid}["{label}"]')

        # 5) 파일 -> 엔티티 포함선
        for (file_path, _, _), eid in entity_ids.items():
            fid = file_ids.get(file_path)
            if fid:
                lines.append(f"    {fid} -. contains .-> {eid}")

        # 6) 엔티티/파일 -> 테이블 관계선
        emitted_edges: set[tuple[str, str, str]] = set()

        for e in edge_rows:
            file_path = e["file"]
            table = e["table"]
            entity_type = e["entity_type"]
            entity_name = e["entity_name"]

            target_id = table_ids.get(table)
            if not target_id:
                continue

            if entity_type == "file":
                source_id = file_ids.get(file_path)
            else:
                source_id = entity_ids.get((file_path, entity_type, entity_name))

            if not source_id:
                continue

            edge_label = _safe_edge_label(e.get("categories"), e.get("operations"))
            edge_key = (source_id, target_id, edge_label)
            if edge_key in emitted_edges:
                continue
            emitted_edges.add(edge_key)

            lines.append(f"    {source_id} -->|{edge_label}| {target_id}")

        return "\n".join(lines)

    def _extract_table_definitions(self, parsed_files: list[dict]):
        """
        .sql 파일에서 CREATE TABLE 구문을 파싱해 테이블명·컬럼 정보를 추출한다.

        [sqlglot 교체]
        기존: 정규식 + _find_balanced_paren_end() + _parse_column_names() 직접 구현
        변경: sqlglot.parse() → exp.Create / exp.Schema AST 노드 탐색
            - 중첩 괄호·문자열 리터럴 처리를 sqlglot에 위임
            - IF NOT EXISTS, 스키마 한정자(schema.table), 백틱·따옴표 식별자 자동 처리
            - 30개 이상의 SQL dialect 자동 대응
        """
        table_names: set[str] = set()
        table_definitions: dict[str, str] = {}
        table_details: dict[str, dict] = {}

        sql_candidates = sorted(
            [f for f in parsed_files if f["extension"] == "sql"],
            key=lambda x: (
                0 if Path(x["relative_path"]).name.lower() == "init.sql" else 1,
                x["relative_path"].lower(),
            ),
        )

        for file_info in sql_candidates:
            source_file = file_info["relative_path"]
            try:
                stmts = sqlglot.parse(
                    file_info["raw_text"],
                    error_level=ErrorLevel.IGNORE,
                )
            except Exception:
                logger.debug("sqlglot 파싱 실패 (무시): %s", source_file)
                continue

            for stmt in stmts:
                if stmt is None:
                    continue
                # CREATE TABLE … (…) 구문만 대상
                if not isinstance(stmt, sg_exp.Create):
                    continue
                schema_node = stmt.this
                if not isinstance(schema_node, sg_exp.Schema):
                    continue

                # 테이블명 추출 (schema.table → table 부분만)
                table_node = schema_node.this
                table_upper = table_node.name.upper()
                if not table_upper:
                    continue

                # 컬럼명 추출 — ColumnDef 노드만 (PRIMARY KEY, CONSTRAINT 등 제외)
                columns: list[str] = []
                for expr in schema_node.expressions:
                    if isinstance(expr, sg_exp.ColumnDef):
                        col_name = expr.this.name.upper()
                        if col_name:
                            columns.append(col_name)

                table_names.add(table_upper)
                table_definitions.setdefault(table_upper, source_file)

                if table_upper not in table_details:
                    table_details[table_upper] = {
                        "table_name": table_upper,
                        "source_file": source_file,
                        "columns": columns,
                        "column_count": len(columns),
                    }
                elif columns:
                    existing = table_details[table_upper]
                    merged = list(dict.fromkeys(existing["columns"] + columns))
                    existing["columns"] = merged
                    existing["column_count"] = len(merged)

        return table_names, table_definitions, list(table_details.values())

    def _extract_entities(self, text: str, extension: str) -> list[dict]:
        """파일 안에 어떤 클래스와 어떤 함수가 있는지 추출해서 각각을 하나의 분석 단위로 만든다"""
        extension = (extension or "").lower().strip().lstrip(".")
        entities = [{"type": "file", "name": "FILE_SCOPE", "text": text}]
        added: set[tuple] = set()

        if extension == "py":
            class_patterns = [
                r"(?ms)^class\s+([A-Za-z_][A-Za-z0-9_]*)[^\n]*:\s*(.*?)"
                r"(?=^class\s+[A-Za-z_]|^def\s+[A-Za-z_]|$\Z)"
            ]
            func_patterns = [
                r"(?ms)^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*:\s*(.*?)"
                r"(?=^def\s+[A-Za-z_]|^class\s+[A-Za-z_]|$\Z)"
            ]
        elif extension == "java":
            class_patterns = [
                r"(?ms)\bclass\s+([A-Za-z_][A-Za-z0-9_]*)[^{]*\{(.*?)"
                r"(?=\n\s*(?:public\s+)?class\s+[A-Za-z_]|$\Z)"
            ]
            func_patterns = [
                r"(?ms)(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\], ?]+\s+"
                r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*\{(.*?)"
                r"(?=\n\s*(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\], ?]+"
                r"\s+[A-Za-z_][A-Za-z0-9_]*\s*\(|$\Z)"
            ]
        else:
            class_patterns = []
            func_patterns = []

        for pattern in class_patterns:
            for m in re.finditer(pattern, text, re.I):
                name = m.group(1).strip()
                key = ("class", name)
                if key not in added:
                    entities.append({"type": "class", "name": name, "text": m.group(0)})
                    added.add(key)

        for pattern in func_patterns:
            for m in re.finditer(pattern, text, re.I):
                name = m.group(1).strip()
                key = ("function", name)
                if key not in added:
                    entities.append(
                        {"type": "function", "name": name, "text": m.group(0)}
                    )
                    added.add(key)

        if extension == "xml":
            mapper_match = re.search(r'<mapper[^>]*namespace="([^"]+)"', text, re.I)
            if mapper_match:
                entities.append(
                    {"type": "class", "name": mapper_match.group(1), "text": text}
                )
            for tag in ["select", "insert", "update", "delete"]:
                for m in re.finditer(
                    rf'(?is)<{tag}\b[^>]*id="([^"]+)"[^>]*>(.*?)</{tag}>', text
                ):
                    name = f"{tag}:{m.group(1)}"
                    key = ("function", name)
                    if key not in added:
                        entities.append(
                            {"type": "function", "name": name, "text": m.group(0)}
                        )
                        added.add(key)

        return entities

    def _detect_table_usage(self, text: str, table_upper: str) -> set[str]:
        """
        소스 텍스트 안에서 특정 테이블이 어떤 SQL 연산으로 사용되는지 추출한다.

        전략:
        1. 순수 SQL(extension=sql) 청크 → sqlglot AST 파싱으로 정확히 분류
        2. MyBatis XML 청크(#{} 파라미터, <where> 태그 혼재) → sqlglot이 Alias 등
           잘못된 노드로 파싱해 테이블을 못 찾음 → 정규식으로만 처리
        3. Java 소스 → 문자열 리터럴 안 SQL이므로 정규식으로만 처리

        따라서:
        - sqlglot이 실제 테이블을 찾은 경우에만 AST 결과를 사용
        - 못 찾은 경우(ops 비어있음) → 정규식 폴백 (XML/Java 포함 모든 케이스 커버)
        """
        ops: set[str] = set()

        # ── 1. sqlglot AST 파싱 시도 (순수 SQL에만 효과적) ────────
        try:
            stmts = sqlglot.parse(text, error_level=ErrorLevel.IGNORE)
            for stmt in stmts:
                if stmt is None:
                    continue
                # SELECT: From 노드 직계 Table만 (Join Table 중복 방지)
                for frm in stmt.find_all(sg_exp.From):
                    for tbl in frm.find_all(sg_exp.Table):
                        if tbl.name.upper() == table_upper:
                            ops.add("SELECT")
                # JOIN: Join 노드 직계 Table
                for jn in stmt.find_all(sg_exp.Join):
                    for tbl in jn.find_all(sg_exp.Table):
                        if tbl.name.upper() == table_upper:
                            ops.add("JOIN")
                # INSERT/UPDATE/DELETE/MERGE: 각 노드의 직계 Table
                for node_type, op_name in [
                    (sg_exp.Insert, "INSERT"),
                    (sg_exp.Update, "UPDATE"),
                    (sg_exp.Delete, "DELETE"),
                    (sg_exp.Merge,  "INSERT"),
                ]:
                    for node in stmt.find_all(node_type):
                        # Insert.this / Update.this / Delete.this 가 Table
                        target = node.find(sg_exp.Table)
                        if target and target.name.upper() == table_upper:
                            ops.add(op_name)
        except Exception:
            pass

        # ── 2. 정규식 폴백 ─────────────────────────────────────────
        # sqlglot이 테이블을 못 찾은 경우 항상 실행
        # (MyBatis XML #{} 파라미터, Java 문자열 리터럴 안 SQL, 복잡한 힌트 구문 등)
        if not ops:
            text_upper = text.upper()
            escaped    = re.escape(table_upper)
            ws         = r"[\s\n\r]+"
            if re.search(rf"\bFROM\b{ws}{escaped}\b",          text_upper): ops.add("SELECT")
            if re.search(rf"\bJOIN\b{ws}{escaped}\b",           text_upper): ops.add("JOIN")
            if re.search(rf"\bINSERT\s+INTO\b{ws}{escaped}\b",  text_upper): ops.add("INSERT")
            if re.search(rf"\bUPDATE\b{ws}{escaped}\b",         text_upper): ops.add("UPDATE")
            if re.search(rf"\bDELETE\s+FROM\b{ws}{escaped}\b",  text_upper): ops.add("DELETE")
            if re.search(rf"\bMERGE\s+INTO\b{ws}{escaped}\b",   text_upper): ops.add("INSERT")

        # ── 3. 단순 참조 폴백 ──────────────────────────────────────
        if not ops:
            if re.search(rf"\b{re.escape(table_upper)}\b", text.upper()):
                ops.add("REF")

        return ops

    def _map_op_category(self, op: str) -> str:
        return {
            "SELECT": "READS",
            "INSERT": "WRITES",
            "UPDATE": "WRITES",
            "DELETE": "WRITES",
            "JOIN": "JOINS",
        }.get(op, "REF")


    def _escape_mermaid(self, text: str) -> str:
        return (
            str(text)
            .replace('"', "'")
            .replace("{", "(")
            .replace("}", ")")
            .replace("[", "(")
            .replace("]", ")")
            .replace("\n", " ")
        )
