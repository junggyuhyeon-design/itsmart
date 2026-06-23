import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

from config import Settings
from embedder.embedder import EmbeddingService
from parser.chunk_service import ChunkService
from parser.file_parser import parse_text_file
from rag.ollama_service import OllamaService
from rag.qdrant_service import QdrantService

logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.chunk_service = ChunkService(settings)
        self.embedding_service = EmbeddingService(settings)
        self.qdrant_service = QdrantService(settings)
        self.ollama_service = OllamaService(settings)

    # ── 인덱싱 ──────────────────────────────────────────────────

    def index_files(self, targets: list) -> dict:
        self.qdrant_service.ensure_collection(self.embedding_service.dimension)
        results: dict = {"success": 0, "failed": 0, "total_chunks": 0, "logs": []}

        for t in targets:
            rel_path = t.get("relative_path", "unknown")
            try:
                parsed = parse_text_file(t)
                if not parsed:
                    results["logs"].append(f"⚠️ {rel_path}: 파싱 결과 없음")
                    continue

                chunks = self.chunk_service.split_text(parsed["raw_text"], parsed)
                if not chunks:
                    results["logs"].append(f"⚠️ {rel_path}: 생성된 청크 없음")
                    continue

                vectors = self.embedding_service.embed_texts([c["text"] for c in chunks])
                count = self.qdrant_service.upsert_chunks(chunks, vectors)

                results["success"] += 1
                results["total_chunks"] += count
                results["logs"].append(f"✅ {rel_path} ({count} chunks)")
            except Exception as e:
                results["failed"] += 1
                results["logs"].append(f"❌ {rel_path}: {e}")
                logger.exception("index_files 실패: %s", rel_path)

        return results

    # ── 질문 스트리밍 ────────────────────────────────────────────

    async def ask_with_context_stream(
        self,
        question: str,
        extra_context: str = "",
        top_k: int | None = None,
        chat_history: list[dict] | None = None,
    ):
        """
        질문에 맞는 컨텍스트를 검색하고 Ollama 스트리밍 응답을 반환.
        벡터 검색은 현재 질문만 사용하고, 대화 기록은 LLM messages 에 주입한다.
        Returns: (async_generator, hits)
        """
        if top_k is None:
            top_k = self.settings.top_k

        llm_question = f"{question}\n{extra_context}".strip() if extra_context else question

        query_vector = self.embedding_service.embed_query(question)
        hits = self.qdrant_service.search(query_vector, top_k=top_k)

        gen = self.ollama_service.generate_response_stream(
            llm_question, hits, chat_history=chat_history
        )
        return gen, hits

    # ── 전체 초기화 ──────────────────────────────────────────────

    def reset(self) -> None:
        """Qdrant 컬렉션만 초기화 (채팅 히스토리는 API 레이어에서 별도 처리)."""
        try:
            self.qdrant_service.reset_collection(self.embedding_service.dimension)
            logger.info("RAGService reset 완료")
        except Exception:
            logger.exception("RAGService reset 실패")
            raise

    # ── SQL 파싱 유틸 ────────────────────────────────────────────

    def _find_mentioned_tables(self, text_upper: str, table_names: list[str]) -> list[str]:
        return [t for t in table_names if re.search(rf"\b{re.escape(t)}\b", text_upper)]

    def _extract_table_definitions(self, parsed_files: list[dict]):
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

        create_table_header = re.compile(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
            r"(?:[`\"\[]?[\w]+[`\"\]]?\.)?"
            r"[`\"\[]?([a-zA-Z0-9_]+)[`\"\]]?\s*\(",
            re.I,
        )

        for file_info in sql_candidates:
            text = file_info["raw_text"]
            source_file = file_info["relative_path"]
            for match in create_table_header.finditer(text):
                table_upper = match.group(1).upper()
                open_paren = match.end() - 1
                close_paren = self._find_balanced_paren_end(text, open_paren)
                if close_paren is None:
                    continue
                body = text[open_paren + 1:close_paren]
                columns = self._parse_column_names(body)
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

    def _find_balanced_paren_end(self, text: str, open_index: int) -> int | None:
        if open_index >= len(text) or text[open_index] != "(":
            return None
        depth = 0
        in_single = False
        in_double = False
        i = open_index
        while i < len(text):
            ch = text[i]
            if in_single:
                if ch == "'" and i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                if ch == "'":
                    in_single = False
            elif in_double:
                if ch == '"' and i + 1 < len(text) and text[i + 1] == '"':
                    i += 2
                    continue
                if ch == '"':
                    in_double = False
            else:
                if ch == "'":
                    in_single = True
                elif ch == '"':
                    in_double = True
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        return i
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
            col_match = re.match(r"^[`\"\[]?([a-zA-Z0-9_]+)[`\"\]]?", line)
            if col_match:
                columns.append(col_match.group(1).upper())
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
                    key = ("function", name)
                    if key not in added:
                        entities.append({"type": "function", "name": name, "text": m.group(0)})
                        added.add(key)

        return entities

    def _detect_table_usage(self, text_upper: str, table_upper: str) -> set[str]:
        escaped = re.escape(table_upper)
        ops: set[str] = set()
        if re.search(rf"\bFROM\b\s+{escaped}\b", text_upper):
            ops.add("SELECT")
        if re.search(rf"\bJOIN\b\s+{escaped}\b", text_upper):
            ops.add("JOIN")
        if re.search(rf"\bINSERT\s+INTO\b\s+{escaped}\b", text_upper):
            ops.add("INSERT")
        if re.search(rf"\bUPDATE\b\s+{escaped}\b", text_upper):
            ops.add("UPDATE")
        if re.search(rf"\bDELETE\s+FROM\b\s+{escaped}\b", text_upper):
            ops.add("DELETE")
        if not ops and re.search(rf"\b{escaped}\b", text_upper):
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
