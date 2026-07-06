from __future__ import annotations

import re

from database.history_repository import get_relationship_edges


class DiagramService:
    allowed_relations = {
        "READS",
        "WRITES",
        "JOINS",
        "CALLS",
        "EXTENDS",
        "IMPLEMENTS",
        "REFERENCES",
        "IMPORTS",
    }

    def build_flow_mermaid(self, project_id: str) -> str:
        edges = get_relationship_edges(project_id)
        lines = ["flowchart LR"]
        seen = set()

        for edge in edges:
            relation = (edge.get("relation") or "CALLS").upper()
            if relation not in self.allowed_relations:
                continue

            src = self.safe_name(edge.get("src_name") or "UNKNOWN_SRC")
            dst = self.safe_name(edge.get("dst_name") or "UNKNOWN_DST")
            label = relation if relation in {"READS", "WRITES", "JOINS", "CALLS", "EXTENDS", "IMPLEMENTS"} else "CALLS"

            line = f"    {src} -->|{label}| {dst}"
            if line not in seen:
                seen.add(line)
                lines.append(line)

        return "\n".join(lines)

    def build_table_erd(self, project_id: str) -> str:
        edges = get_relationship_edges(project_id, relation="REFERENCES")
        lines = ["erDiagram"]
        seen = set()

        for edge in edges:
            src = self.safe_name(edge.get("src_name") or "UNKNOWN_SRC")
            dst = self.safe_name(edge.get("dst_name") or "UNKNOWN_DST")
            line = f"    {src} ||--o{{ {dst} : REFERENCES"
            if line not in seen:
                seen.add(line)
                lines.append(line)

        return "\n".join(lines)

    def safe_name(self, name: str) -> str:
        name = re.sub(r"[^A-Za-z0-9_]", "_", name)
        name = re.sub(r"_+", "_", name).strip("_")
        return name or "UNKNOWN"