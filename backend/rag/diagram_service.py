from __future__ import annotations

import re

from database.history_repository import getrelationshipedges


class DiagramService:
    _ALLOWED_RELATIONS = {
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
        edges = getrelationshipedges(project_id)
        lines = ["flowchart LR"]
        seen = set()

        for e in edges:
            rel = (e.get("relation") or "CALLS").upper()
            if rel not in self._ALLOWED_RELATIONS:
                continue

            src = self._safe(
                e.get("src_name")
                or e.get("srcname")
                or "UNKNOWN_SRC"
            )
            dst = self._safe(
                e.get("dst_name")
                or e.get("dstname")
                or "UNKNOWN_DST"
            )

            label = rel if rel in {
                "READS",
                "WRITES",
                "JOINS",
                "CALLS",
                "EXTENDS",
                "IMPLEMENTS",
            } else "CALLS"

            line = f"    {src} -->|{label}| {dst}"
            if line not in seen:
                seen.add(line)
                lines.append(line)

        return "\n".join(lines)

    def build_table_erd(self, project_id: str) -> str:
        edges = getrelationshipedges(project_id, relation="REFERENCES")
        lines = ["erDiagram"]
        seen = set()

        for e in edges:
            src = self._safe(
                e.get("src_name")
                or e.get("srcname")
                or "UNKNOWN_SRC"
            )
            dst = self._safe(
                e.get("dst_name")
                or e.get("dstname")
                or "UNKNOWN_DST"
            )

            line = f"    {src} ||--o{{ {dst} : REFERENCES"
            if line not in seen:
                seen.add(line)
                lines.append(line)

        return "\n".join(lines)

    def _safe(self, name: str) -> str:
        name = re.sub(r"[^A-Za-z0-9_]", "_", name)
        name = re.sub(r"_+", "_", name).strip("_")
        return name or "UNKNOWN"