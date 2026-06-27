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

    async def ask_with_context_stream(
        self,
        question:         str,
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

        query_vector = self.embedding_service.embed_query(question)
        hits = self.qdrant_service.search(
            query_vector,
            project_id=project_id,
            top_k=top_k,
            layer_filter=layer_filter,
            extension_filter=extension_filter,
        )

        gen = self.ollama_service.generate_response_stream(
            question=question,
            hits=hits,
            query_type=query_type,
            project_name=project_name,
            struct_context=extra_context,
            chat_history=chat_history,
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

    # ── SQL 파싱 유틸 (Mermaid 생성 보조) ────────────────────────

    def _find_mentioned_tables(self, text_upper: str, table_names: list[str]) -> list[str]:
        return [t for t in table_names if re.search(rf"\b{re.escape(t)}\b", text_upper)]

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

    def _extract_entities(self, text: str, extension: str) -> list[dict]:
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

    def _detect_table_usage(self, text_upper: str, table_upper: str) -> set[str]:
        escaped = re.escape(table_upper)
        ops: set[str] = set()
        if re.search(rf"\bFROM\b\s+{escaped}\b",          text_upper): ops.add("SELECT")
        if re.search(rf"\bJOIN\b\s+{escaped}\b",           text_upper): ops.add("JOIN")
        if re.search(rf"\bINSERT\s+INTO\b\s+{escaped}\b",  text_upper): ops.add("INSERT")
        if re.search(rf"\bUPDATE\b\s+{escaped}\b",         text_upper): ops.add("UPDATE")
        if re.search(rf"\bDELETE\s+FROM\b\s+{escaped}\b",  text_upper): ops.add("DELETE")
        if not ops and re.search(rf"\b{escaped}\b",         text_upper): ops.add("REF")
        return ops

    def _map_op_category(self, op: str) -> str:
        return {"SELECT": "READS", "INSERT": "WRITES", "UPDATE": "WRITES",
                "DELETE": "WRITES", "JOIN": "JOINS"}.get(op, "REF")

    def _escape_mermaid(self, text: str) -> str:
        return (
            str(text)
            .replace('"', "'").replace("{", "(").replace("}", ")")
            .replace("[", "(").replace("]", ")").replace("\n", " ")
        )
