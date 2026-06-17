import re
import logging
from pathlib import Path
from collections import Counter, defaultdict

from config import Settings
from parser.file_parser import parse_text_file
from parser.chunk_service import ChunkService
from embedder.embedder import EmbeddingService
from rag.qdrant_service import QdrantService
from rag.ollama_service import OllamaService

logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.chunk_service = ChunkService(settings)
        self.embedding_service = EmbeddingService(settings)
        self.qdrant_service = QdrantService(settings)
        self.ollama_service = OllamaService(settings)

    def index_files(self, targets: list):
        self.qdrant_service.ensure_collection(self.embedding_service.dimension)
        results = {"success": 0, "failed": 0, "total_chunks": 0, "logs": []}

        for t in targets:
            try:
                parsed = parse_text_file(t)
                if not parsed:
                    results["logs"].append(f"⚠️ {t.get('relative_path')}: 파싱 결과 없음")
                    continue

                chunks = self.chunk_service.split_text(parsed["raw_text"], parsed)
                if not chunks:
                    results["logs"].append(f"⚠️ {t.get('relative_path')}: 생성된 청크 없음")
                    continue

                vectors = self.embedding_service.embed_texts([c["text"] for c in chunks])
                count = self.qdrant_service.upsert_chunks(chunks, vectors)

                results["success"] += 1
                results["total_chunks"] += count
                results["logs"].append(f"✅ {t.get('relative_path')}")
            except Exception as e:
                results["failed"] += 1
                results["logs"].append(f"❌ {t.get('relative_path')}: {e}")

        return results

    def generate_project_summary(self, targets: list):
        total_files = len(targets)
        extensions = Counter([t.get("extension") for t in targets])

        tree_dict = {}
        for t in targets:
            curr = tree_dict
            for p in Path(t.get("relative_path")).parts:
                curr = curr.setdefault(p, {})

        def build_tree(d, indent=""):
            lines = []
            for k, v in sorted(d.items()):
                if not v:
                    lines.append(f"{indent}📄 {k}")
                else:
                    lines.append(f"{indent}📁 {k}/")
                    lines.append(build_tree(v, indent + "  "))
            return "\n".join(lines)

        return {
            "total_files": total_files,
            "extensions": dict(extensions),
            "tree_str": build_tree(tree_dict),
        }

    def analyze_db_relations(self, targets: list):
        parsed_files = []

        for t in targets:
            parsed = parse_text_file(t)
            if not parsed:
                continue
            parsed_files.append(
                {
                    "target": t,
                    "relative_path": parsed.get("relative_path", ""),
                    "file_name": parsed.get("file_name", ""),
                    "extension": (parsed.get("extension") or "").lower(),
                    "raw_text": parsed.get("raw_text", ""),
                    "raw_upper": (parsed.get("raw_text", "") or "").upper(),
                }
            )

        table_names, table_definitions, table_details = self._extract_table_definitions(parsed_files)
        table_list = sorted(table_names)

        relations = []
        source_to_tables = defaultdict(lambda: defaultdict(lambda: {
            "ops": set(),
            "categories": set(),
            "scopes": set(),
        }))

        for file_info in parsed_files:
            if file_info["extension"] == "sql":
                continue

            entities = self._extract_entities(file_info["raw_text"], file_info["extension"])

            for entity in entities:
                entity_text_upper = entity["text"].upper()
                mentioned_tables = self._find_mentioned_tables(entity_text_upper, table_list)

                for table in mentioned_tables:
                    usage_ops = self._detect_table_usage(entity_text_upper, table)
                    if not usage_ops:
                        continue

                    categories = {self._map_op_category(op) for op in usage_ops}

                    relation = {
                        "file": file_info["relative_path"],
                        "file_name": Path(file_info["relative_path"]).name if file_info["relative_path"] else file_info["file_name"],
                        "entity_type": entity["type"],
                        "entity_name": entity["name"],
                        "table": table,
                        "operations": sorted(usage_ops),
                        "categories": sorted(categories),
                    }
                    relations.append(relation)

                    bucket = source_to_tables[file_info["relative_path"]][table]
                    bucket["ops"].update(usage_ops)
                    bucket["categories"].update(categories)
                    bucket["scopes"].add(f"{entity['type']}:{entity['name']}")

            file_path = file_info["relative_path"]
            file_tables = self._find_mentioned_tables(file_info["raw_upper"], table_list)
            for table in file_tables:
                bucket = source_to_tables[file_path][table]
                if not bucket["ops"]:
                    bucket["ops"].add("REF")
                    bucket["categories"].add("REF")
                    bucket["scopes"].add(f"file:{Path(file_path).name}")

        normalized_source_to_tables = {}
        for file_path, table_map in source_to_tables.items():
            normalized_source_to_tables[file_path] = {}
            for table, meta in table_map.items():
                normalized_source_to_tables[file_path][table] = {
                    "operations": sorted(meta["ops"]),
                    "categories": sorted(meta["categories"]),
                    "scopes": sorted(meta["scopes"]),
                }

        return {
            "tables": table_list,
            "table_definitions": table_definitions,
            "table_details": table_details,
            "relations": relations,
            "source_to_tables": normalized_source_to_tables,
        }

    def _find_mentioned_tables(self, text_upper: str, table_names: list[str]) -> list[str]:
        return [table for table in table_names if re.search(rf"\b{re.escape(table)}\b", text_upper)]

    def _extract_table_definitions(self, parsed_files: list[dict]):
        table_names = set()
        table_definitions = {}
        table_details = {}

        sql_candidates = sorted(
            [f for f in parsed_files if f["extension"] == "sql"],
            key=lambda x: (
                0 if Path(x["relative_path"]).name.lower() == "init.sql" else 1,
                x["relative_path"].lower(),
            ),
        )

        create_table_pattern = re.compile(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
            r"(?:[`\"\[]?[\w]+[`\"\]]?\.)?"
            r"[`\"\[]?([a-zA-Z0-9_]+)[`\"\]]?\s*\((.*?)\)\s*;?",
            re.I | re.S,
        )

        for file_info in sql_candidates:
            text = file_info["raw_text"]
            source_file = file_info["relative_path"]

            for match in create_table_pattern.finditer(text):
                table_upper = match.group(1).upper()
                body = match.group(2)
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

    def _parse_column_names(self, table_body: str) -> list[str]:
        columns = []
        for line in table_body.splitlines():
            line = line.strip()
            if not line or line.startswith("--"):
                continue
            line = line.rstrip(",").strip()
            upper = line.upper()
            if upper.startswith(("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK", "CONSTRAINT", "INDEX", "KEY ")):
                continue
            col_match = re.match(r"^[`\"\[]?([a-zA-Z0-9_]+)[`\"\]]?", line)
            if col_match:
                columns.append(col_match.group(1).upper())
        return columns

    def _extract_entities(self, text: str, extension: str):
        entities = [{"type": "file", "name": "FILE_SCOPE", "text": text}]
        added = set()

        if extension == "py":
            class_patterns = [
                r"(?ms)^class\s+([A-Za-z_][A-Za-z0-9_]*)[^\n]*:\s*(.*?)(?=^class\s+[A-Za-z_]|^def\s+[A-Za-z_]|$\Z)"
            ]
            func_patterns = [
                r"(?ms)^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*:\s*(.*?)(?=^def\s+[A-Za-z_]|^class\s+[A-Za-z_]|$\Z)"
            ]
        elif extension == "java":
            class_patterns = [
                r"(?ms)\bclass\s+([A-Za-z_][A-Za-z0-9_]*)[^{]*\{(.*?)(?=\n\s*(?:public\s+)?class\s+[A-Za-z_]|$\Z)"
            ]
            func_patterns = [
                r"(?ms)(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\], ?]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*\{(.*?)(?=\n\s*(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\], ?]+\s+[A-Za-z_][A-Za-z0-9_]*\s*\(|$\Z)"
            ]
        else:
            class_patterns = []
            func_patterns = []

        for pattern in class_patterns:
            for m in re.finditer(pattern, text, re.I):
                name = m.group(1).strip()
                body = m.group(0)
                key = ("class", name)
                if key not in added:
                    entities.append({"type": "class", "name": name, "text": body})
                    added.add(key)

        for pattern in func_patterns:
            for m in re.finditer(pattern, text, re.I):
                name = m.group(1).strip()
                body = m.group(0)
                key = ("function", name)
                if key not in added:
                    entities.append({"type": "function", "name": name, "text": body})
                    added.add(key)

        if extension == "xml":
            mapper_match = re.search(r'<mapper[^>]*namespace="([^"]+)"', text, re.I)
            if mapper_match:
                namespace = mapper_match.group(1)
                entities.append({"type": "class", "name": namespace, "text": text})

            for tag in ["select", "insert", "update", "delete"]:
                for m in re.finditer(rf"(?is)<{tag}\b[^>]*id=\"([^\"]+)\"[^>]*>(.*?)</{tag}>", text):
                    name = f"{tag}:{m.group(1)}"
                    body = m.group(0)
                    key = ("function", name)
                    if key not in added:
                        entities.append({"type": "function", "name": name, "text": body})
                        added.add(key)

        return entities

    def _detect_table_usage(self, text_upper: str, table_upper: str):
        escaped = re.escape(table_upper)
        ops = set()

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

    def _map_op_category(self, op: str):
        if op == "SELECT":
            return "READS"
        if op in {"INSERT", "UPDATE", "DELETE"}:
            return "WRITES"
        if op == "JOIN":
            return "JOINS"
        return "REF"

    def generate_source_to_table_mermaid(self, db_data: dict) -> str:
        lines = ["flowchart LR"]

        table_ids = {}
        file_ids = {}
        entity_ids = {}

        for idx, table in enumerate(db_data.get("tables", [])):
            table_ids[table] = f"T{idx}"

        source_to_tables = db_data.get("source_to_tables", {})
        relations = db_data.get("relations", [])

        file_list = sorted(source_to_tables.keys())
        for idx, file_path in enumerate(file_list):
            file_ids[file_path] = f"F{idx}"

        relation_entities = []
        for rel in relations:
            if rel["entity_type"] == "file":
                continue
            entity_key = (rel["file"], rel["entity_type"], rel["entity_name"])
            if entity_key not in relation_entities:
                relation_entities.append(entity_key)

        for idx, entity_key in enumerate(relation_entities):
            entity_ids[entity_key] = f"E{idx}"

        for table, tid in table_ids.items():
            lines.append(f'    {tid}[("🗄️ {table}")]')

        for file_path, fid in file_ids.items():
            file_name = Path(file_path).name
            lines.append(f'    {fid}["📄 {file_name}"]')

        for (file_path, entity_type, entity_name), eid in entity_ids.items():
            icon = "🧩" if entity_type == "class" else "⚙️"
            safe_name = self._escape_mermaid(entity_name)
            lines.append(f'    {eid}["{icon} {safe_name}"]')

        for rel in relations:
            file_path = rel["file"]
            table = rel["table"]
            entity_type = rel["entity_type"]
            entity_name = rel["entity_name"]
            categories = rel.get("categories", [])
            operations = rel.get("operations", [])

            label_parts = categories[:] if categories else []
            detail_ops = [op for op in operations if op != "REF"]
            if detail_ops:
                label_parts.append("/".join(detail_ops))
            elif not label_parts:
                label_parts.append("REF")

            edge_label = ", ".join(label_parts)
            target_id = table_ids[table]

            if entity_type == "file":
                source_id = file_ids[file_path]
            else:
                source_id = entity_ids[(file_path, entity_type, entity_name)]

            lines.append(f'    {source_id} -->|{self._escape_mermaid(edge_label)}| {target_id}')

        for (file_path, entity_type, entity_name), eid in entity_ids.items():
            fid = file_ids[file_path]
            lines.append(f"    {fid} -. contains .-> {eid}")

        lines.append("")
        lines.append("    classDef table fill:#E8F0FE,stroke:#1A73E8,stroke-width:1.5px,color:#111;")
        lines.append("    classDef file fill:#E6FFFB,stroke:#08979C,stroke-width:1.2px,color:#111;")
        lines.append("    classDef entity fill:#FFF7E6,stroke:#D46B08,stroke-width:1.2px,color:#111;")

        if table_ids:
            lines.append("    class " + ",".join(table_ids.values()) + " table;")
        if file_ids:
            lines.append("    class " + ",".join(file_ids.values()) + " file;")
        if entity_ids:
            lines.append("    class " + ",".join(entity_ids.values()) + " entity;")

        return "\n".join(lines)

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

    async def ask_with_context_stream(self, question: str, top_k: int):
        query_vector = self.embedding_service.embed_query(question)
        hits = self.qdrant_service.search(query_vector, top_k=top_k)
        return self.ollama_service.generate_response_stream(question, hits), hits