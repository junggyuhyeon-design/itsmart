import logging
import re
from pathlib import Path

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
        self.settings          = settings
        self.chunk_service     = ChunkService(settings)
        self.embedding_service = EmbeddingService(settings)
        self.qdrant_service    = QdrantService(settings)
        self.ollama_service    = OllamaService(settings)

    # ── 인덱싱 ──────────────────────────────────────────────────
    # 확인 완료
    def index_files(self, targets: list) -> dict:
        self.qdrant_service.ensure_collection(self.embedding_service.dimension)
        results: dict = {"success": 0, "failed": 0, "total_chunks": 0, "logs": []}
        indexed_meta: list[dict] = []

        for t in targets:
            rel_path = t.get("relative_path", "unknown")
            try:
                # 파일 파싱
                parsed = parse_text_file(t)
                if not parsed:
                    results["logs"].append(f"⚠️ {rel_path}: 파싱 결과 없음")
                    continue

                # 파일 청킹
                chunks = self.chunk_service.split_text(parsed["raw_text"], parsed)
                if not chunks:
                    results["logs"].append(f"⚠️ {rel_path}: 생성된 청크 없음")
                    continue

                # 파일 벡터화
                vectors = self.embedding_service.embed_texts([c["text"] for c in chunks])

                # Qdrant 저장
                count   = self.qdrant_service.upsert_chunks(chunks, vectors)

                results["success"]      += 1
                results["total_chunks"] += count
                results["logs"].append(f"✅ {rel_path} ({count} chunks)")

                # SQLite file_index 저장용 메타데이터 수집
                indexed_meta.append({
                    "project_id":    parsed["project_id"],
                    "project_name":  parsed["project_name"],
                    "file_name":     parsed["file_name"],
                    "relative_path": parsed["relative_path"],
                    "extension":     parsed["extension"],
                    "file_size":     parsed.get("file_size", 0),
                })
            except Exception as e:
                results["failed"] += 1
                results["logs"].append(f"❌ {rel_path}: {e}")
                logger.exception("index_files 실패: %s", rel_path)

        if indexed_meta:
            try:
                saved = bulk_insert_file_index(indexed_meta)
                logger.info("file_index 저장 완료: %d건", saved)
            except Exception:
                logger.exception("file_index 저장 실패 — Qdrant 인덱싱은 이미 완료됨")

        return results

    # ── 질문 스트리밍 ────────────────────────────────────────────
    # 확인 완료
    async def ask_with_context_stream(
        self,
        question:         str,
        search_query:     str,               # ← 정제된 검색 쿼리 (노이즈 제거)
        project_id:       str | None,
        project_name:     str | None,
        extra_context:    str               = "",
        chat_history:     list[dict] | None = None,
        top_k:            int | None        = None,
        layer_filter:     str | None        = None,
        extension_filter: str | None        = None,
        query_type:       str               = "qa",
    ):
        """Qdrant 검색 → OllamaService 스트리밍."""
        top_k = top_k or self.settings.top_k

        query_vector = self.embedding_service.embed_query(search_query)
        hits = self.qdrant_service.search(
            query_vector,
            project_id=project_id,
            top_k=top_k,
            layer_filter=layer_filter,
            extension_filter=extension_filter,
        )

        gen = self.ollama_service.generate_response_stream(
            question=question,       # LLM에는 원문 질문 전달
            hits=hits,
            query_type=query_type,
            project_name=project_name,
            struct_context=extra_context,
            chat_history=chat_history,
        )
        return gen, hits

    # ── 전체 초기화 ──────────────────────────────────────────────
    # 확인 완료
    def reset(self) -> None:
        try:
            self.qdrant_service.reset_collection(self.embedding_service.dimension)
            logger.info("RAGService reset 완료")
        except Exception:
            logger.exception("RAGService reset 실패")
            raise

    # ── DB 관계 분석 (Mermaid 생성용) ────────────────────────────
    # 확인 완료
    def analyze_db_relations(
        self,
        targets: list,
        entity_filter: str | None = None,  # 대문자, 파일경로/테이블명 부분 매칭
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
        ef         = entity_filter.upper() if entity_filter else None

        # 1. Qdrant 스크롤 — entity_filter 있으면 파일 경로 기준 1차 필터
        #    단, SQL 파일(테이블 정의)은 entity_filter 무관하게 항상 포함해야
        #    테이블명을 인식할 수 있으므로 두 번 조회 후 합산
        if ef:
            # 필터 대상 파일 청크 (relative_path/file_name 키워드 포함)
            filtered_chunks = self.qdrant_service.scroll_all(
                project_id=project_id,
                relative_path_keyword=ef,
            )
            # SQL 파일 청크 (테이블 정의 확보 — 키워드 무관)
            sql_chunks = self.qdrant_service.scroll_all(
                project_id=project_id,
                relative_path_keyword=".sql",  # .sql 파일만
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
            parsed_files.append({
                "relative_path": chunk.get("relative_path", ""),
                "file_name":     chunk.get("file_name", ""),
                "extension":     (chunk.get("extension") or "").lower(),
                "raw_text":      chunk_text,
                "raw_upper":     chunk_text.upper(),
            })

        if not parsed_files:
            return {"tables": [], "table_definitions": {}, "relations": [], "source_to_tables": {}}

        file_texts: dict[str, dict] = {}
        for pf in parsed_files:
            path = pf["relative_path"]
            if path not in file_texts:
                file_texts[path] = dict(pf)
            else:
                # 텍스트 누적 후 raw_upper 재계산
                file_texts[path]["raw_text"]  += "\n" + pf["raw_text"]
                file_texts[path]["raw_upper"]  = file_texts[path]["raw_text"].upper()

        merged_files = list(file_texts.values())

        # 3. 테이블 정의 추출 (전체 SQL 파일 기준)
        table_names, table_definitions, _ = self._extract_table_definitions(merged_files)

        # entity_filter 가 있으면 관련 테이블만 추적
        #   - 테이블명에 ef 포함 → 직접 대상
        #   - 나머지는 1-hop 확장(직접 대상 테이블을 사용하는 파일이 쓰는 다른 테이블)은
        #     generate_source_to_table_mermaid 의 후처리에서 처리
        target_tables = table_names  # 기본: 전체
        if ef:
            target_tables = {t for t in table_names if ef in t}
            if not target_tables:
                # 테이블명 매칭 없으면 전체 테이블로 폴백 (파일 경로 필터만 적용)
                target_tables = table_names

        # 4. 관계 추출
        relations: list[dict] = []
        source_to_tables: dict = defaultdict(lambda: defaultdict(lambda: {
            "ops": set(), "categories": set(), "scopes": set(),
        }))

        for file_info in merged_files:
            entities = self._extract_entities(file_info["raw_text"], file_info["extension"])
            for entity in entities:
                entity_text_upper = entity["text"].upper()
                for table in target_tables:
                    usage_ops = self._detect_table_usage(entity_text_upper, table)
                    if not usage_ops:
                        continue
                    categories = {self._map_op_category(op) for op in usage_ops}
                    relations.append({
                        "file":        file_info["relative_path"],
                        "file_name":   Path(file_info["relative_path"]).name
                                       if file_info["relative_path"] else file_info["file_name"],
                        "entity_type": entity["type"],
                        "entity_name": entity["name"],
                        "table":       table,
                        "operations":  sorted(usage_ops),
                        "categories":  sorted(categories),
                    })
                    bucket = source_to_tables[file_info["relative_path"]][table]
                    bucket["ops"].update(usage_ops)
                    bucket["categories"].update(categories)
                    bucket["scopes"].add(f"{entity['type']}:{entity['name']}")

            if file_info["relative_path"] not in source_to_tables:
                for table in target_tables:
                    if re.search(rf"\b{re.escape(table)}\b", file_info["raw_upper"]):
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
                    "scopes":     sorted(meta["scopes"]),
                }
                for table, meta in table_map.items()
            }

        return {
            "tables":            sorted(target_tables),
            "table_definitions": table_definitions,
            "relations":         relations,
            "source_to_tables":  normalized,
        }

    # 확인 완료 [개선 필요]
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

        def _safe_edge_label(categories: list[str] | None, operations: list[str] | None) -> str:
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
            ddl_names = ("init.sql", "schema.sql", "ddl.sql", "create.sql", "tables.sql")
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
            (e["file"], e["table"])
            for e in edge_rows
            if e["entity_type"] == "file"
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

                if has_file_scope and (file_path, table) not in existing_file_table_edges:
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

    # def _find_mentioned_tables(self, text_upper: str, table_names: list[str]) -> list[str]:
    #     return [t for t in table_names if re.search(rf"\b{re.escape(t)}\b", text_upper)]

    # 확인 완료
    def _extract_table_definitions(self, parsed_files: list[dict]):
        table_names:       set[str]        = set()
        table_definitions: dict[str, str]  = {}
        table_details:     dict[str, dict] = {}

        sql_candidates = sorted(
            [f for f in parsed_files if f["extension"] == "sql"],
            key=lambda x: (
                0 if Path(x["relative_path"]).name.lower() == "init.sql" else 1,
                x["relative_path"].lower(),
            ),
        )

        create_table_header = re.compile(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
            r"(?:[`\"\[]?[\w]+[`\"\]]?\.)?"
            r"[`\"\[]?([a-zA-Z0-9_]+)[`\"\]]?\s*\(",
            re.I,
        )

        for file_info in sql_candidates:
            text        = file_info["raw_text"]
            source_file = file_info["relative_path"]
            for match in create_table_header.finditer(text):
                table_upper = match.group(1).upper()
                open_paren  = match.end() - 1
                close_paren = self._find_balanced_paren_end(text, open_paren)
                if close_paren is None:
                    continue
                body    = text[open_paren + 1: close_paren]
                columns = self._parse_column_names(body)
                table_names.add(table_upper)
                table_definitions.setdefault(table_upper, source_file)
                if table_upper not in table_details:
                    table_details[table_upper] = {
                        "table_name":   table_upper,
                        "source_file":  source_file,
                        "columns":      columns,
                        "column_count": len(columns),
                    }
                elif columns:
                    existing = table_details[table_upper]
                    merged   = list(dict.fromkeys(existing["columns"] + columns))
                    existing["columns"]      = merged
                    existing["column_count"] = len(merged)

        return table_names, table_definitions, list(table_details.values())

    # 확인 완료
    def _find_balanced_paren_end(self, text: str, open_index: int) -> int | None:
        if open_index >= len(text) or text[open_index] != "(":
            return None
        depth, in_single, in_double = 0, False, False
        i = open_index
        while i < len(text):
            ch = text[i]
            if in_single:
                if ch == "'" and i + 1 < len(text) and text[i + 1] == "'":
                    i += 2; continue
                if ch == "'": in_single = False
            elif in_double:
                if ch == '"' and i + 1 < len(text) and text[i + 1] == '"':
                    i += 2; continue
                if ch == '"': in_double = False
            else:
                if   ch == "'": in_single = True
                elif ch == '"': in_double = True
                elif ch == "(": depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0: return i
            i += 1
        return None

    # 확인 완료
    def _parse_column_names(self, table_body: str) -> list[str]:
        columns = []
        skip_prefixes = (
            "PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK",
            "CONSTRAINT", "INDEX", "KEY ",
        )
        for line in table_body.splitlines():
            line = line.strip().rstrip(",").strip()
            if not line or line.startswith("--"):
                continue
            if line.upper().startswith(skip_prefixes):
                continue
            m = re.match(r"^[`\"\[]?([a-zA-Z0-9_]+)[`\"\]]?", line)
            if m:
                columns.append(m.group(1).upper())
        return columns

    # 확인 완료
    def _extract_entities(self, text: str, extension: str) -> list[dict]:
        # extension 정규화: None/"" 방어 및 소문자 보장
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
            func_patterns  = []

        for pattern in class_patterns:
            for m in re.finditer(pattern, text, re.I):
                name = m.group(1).strip()
                key  = ("class", name)
                if key not in added:
                    entities.append({"type": "class", "name": name, "text": m.group(0)})
                    added.add(key)

        for pattern in func_patterns:
            for m in re.finditer(pattern, text, re.I):
                name = m.group(1).strip()
                key  = ("function", name)
                if key not in added:
                    entities.append({"type": "function", "name": name, "text": m.group(0)})
                    added.add(key)

        if extension == "xml":
            mapper_match = re.search(r'<mapper[^>]*namespace="([^"]+)"', text, re.I)
            if mapper_match:
                entities.append({"type": "class", "name": mapper_match.group(1), "text": text})
            for tag in ["select", "insert", "update", "delete"]:
                for m in re.finditer(
                    rf'(?is)<{tag}\b[^>]*id="([^"]+)"[^>]*>(.*?)</{tag}>', text
                ):
                    name = f"{tag}:{m.group(1)}"
                    key  = ("function", name)
                    if key not in added:
                        entities.append({"type": "function", "name": name, "text": m.group(0)})
                        added.add(key)

        return entities

    # 확인 완료
    def _detect_table_usage(self, text_upper: str, table_upper: str) -> set[str]:
        """table 이 text 내에서 어떤 연산으로 사용되었는지를 추출"""
        escaped = re.escape(table_upper)
        ops: set[str] = set()
        # 개행·공백 여러 칸을 허용하는 패턴으로 변경
        ws = r"[\s\n\r]+"
        if re.search(rf"\bFROM\b{ws}{escaped}\b",         text_upper): ops.add("SELECT")
        if re.search(rf"\bJOIN\b{ws}{escaped}\b",          text_upper): ops.add("JOIN")
        if re.search(rf"\bINSERT\s+INTO\b{ws}{escaped}\b", text_upper): ops.add("INSERT")
        if re.search(rf"\bUPDATE\b{ws}{escaped}\b",        text_upper): ops.add("UPDATE")
        if re.search(rf"\bDELETE\s+FROM\b{ws}{escaped}\b", text_upper): ops.add("DELETE")
        # MERGE INTO (Oracle/SQL Server 호환)
        if re.search(rf"\bMERGE\s+INTO\b{ws}{escaped}\b",  text_upper): ops.add("INSERT")
        # 연산 없이 단순 참조만 있는 경우
        if not ops and re.search(rf"\b{escaped}\b", text_upper): ops.add("REF")
        return ops

    # 확인 완료
    def _map_op_category(self, op: str) -> str:
        return {"SELECT": "READS", "INSERT": "WRITES", "UPDATE": "WRITES",
                "DELETE": "WRITES", "JOIN": "JOINS"}.get(op, "REF")

    # 확인 완료
    def _escape_mermaid(self, text: str) -> str:
        return (
            str(text)
            .replace('"', "'").replace("{", "(").replace("}", ")")
            .replace("[", "(").replace("]", ")").replace("\n", " ")
        )
