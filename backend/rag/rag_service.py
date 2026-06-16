import re
import logging
from pathlib import Path
from typing import Any
from collections import Counter, defaultdict
from backend.config import Settings
from backend.parser.file_parser import parse_text_file
from backend.parser.chunk_service import ChunkService
from backend.embedder.embedder import EmbeddingService
from backend.rag.qdrant_service import QdrantService
from backend.rag.ollama_service import OllamaService

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
                if not parsed: continue
                chunks = self.chunk_service.split_text(parsed["raw_text"], parsed)
                vectors = self.embedding_service.embed_texts([c["text"] for c in chunks])
                count = self.qdrant_service.upsert_chunks(chunks, vectors)
                results["success"] += 1; results["total_chunks"] += count
                results["logs"].append(f"✅ {t.get('relative_path')}")
            except Exception as e:
                results["failed"] += 1; results["logs"].append(f"❌ {t.get('relative_path')}: {e}")
        return results

    def generate_project_summary(self, targets: list):
        total_files = len(targets)
        extensions = Counter([t.get('extension') for t in targets])
        tree_dict = {}
        for t in targets:
            curr = tree_dict
            for p in Path(t.get('relative_path')).parts: curr = curr.setdefault(p, {})
        def build_tree(d, indent=""):
            lines = []
            for k, v in sorted(d.items()):
                if not v: lines.append(f"{indent}📄 {k}")
                else: lines.append(f"{indent}📁 {k}/"); lines.append(build_tree(v, indent + "    "))
            return "\n".join(lines)
        return {"total_files": total_files, "extensions": dict(extensions), "tree_str": build_tree(tree_dict)}

    def analyze_db_relations(self, targets: list):
        table_names, table_definitions = set(), {}
        source_to_tables = defaultdict(lambda: defaultdict(set))
        for t in targets:
            if t.get('extension') == 'sql':
                parsed = parse_text_file(t)
                matches = re.findall(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z0-9_]+)', parsed.get("raw_text", ""), re.I)
                for table in matches:
                    table_names.add(table); table_definitions[table] = t.get('relative_path')
        for t in targets:
            parsed = parse_text_file(t); content = parsed.get("raw_text", "").upper()
            for table in table_names:
                if re.search(rf'\b{table.upper()}\b', content): source_to_tables[t.get('relative_path')][table].add("REF")
        return {"tables": list(table_names), "table_definitions": table_definitions,
                "source_to_tables": {k: {tk: list(tv) for tk, tv in v.items()} for k, v in source_to_tables.items()}}

    def generate_source_to_table_mermaid(self, db_data: dict) -> str:
        lines = ["flowchart LR"]
        node_ids = {f: f"F{i}" for i, f in enumerate(db_data["source_to_tables"].keys())}
        for f, nid in node_ids.items(): lines.append(f'    {nid}["📄 {Path(f).name}"]')
        table_ids = {t: f"T{i}" for i, t in enumerate(db_data["tables"])}
        for t, tid in table_ids.items(): lines.append(f'    {tid}[("🗄️ {t}")]')
        for f, tables in db_data["source_to_tables"].items():
            for t in tables: lines.append(f"    {node_ids[f]} -- REF --> {table_ids[t]}")
        return "\n".join(lines)

    async def ask_with_context_stream(self, question: str, top_k: int):
        query_vector = self.embedding_service.embed_query(question)
        hits = self.qdrant_service.search(query_vector, top_k=top_k)
        return self.ollama_service.generate_response_stream(question, hits), hits