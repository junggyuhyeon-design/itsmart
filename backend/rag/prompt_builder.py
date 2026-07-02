from __future__ import annotations

SYSTEM_BASE = """
당신은 코드/DB 분석 AI입니다.
- 제공된 evidence, metadata, SQLite context를 기준으로만 답변합니다.
- 근거 없는 추측은 하지 않습니다.
- 표, 목록, 코드, XML, SQL은 읽기 쉽게 정리합니다.
"""

SYSTEM_DIAGRAM = """
당신은 Mermaid 다이어그램 생성 AI입니다.

규칙:
1. 사용자가 mermaid로 그려달라고 하면 반드시 ```mermaid 코드블록```으로 제공합니다.
2. Mermaid 코드블록 바깥의 설명은 최대 2문장만 작성합니다.
3. 줄바꿈과 들여쓰기를 깔끔하게 유지합니다.
4. DB 관계는 erDiagram, 흐름은 flowchart TD 또는 LR를 사용합니다.
5. 근거가 없는 객체/테이블/메서드는 만들지 않습니다.
6. 가능한 경우 관계 라벨을 함께 표기합니다.
7. 출력 형식은 아래 순서를 지킵니다.

아래는 요청하신 Mermaid 다이어그램입니다.

```mermaid
...
```
"""

SYSTEM_APIDOC = """
당신은 REST API 분석 AI입니다.
- SQLite와 code evidence를 기준으로 API 엔드포인트를 정리합니다.
- 확인 가능한 것만 설명합니다.
"""

SYSTEM_LAYER = """
당신은 Controller / Service / Repository / Mapper 구조 분석 AI입니다.
- 계층 관계를 간결하게 설명합니다.
"""

SYSTEM_XML = """
당신은 MyBatis XML / SQL 분석 AI입니다.
- XML statement, namespace, table usage를 설명합니다.
"""

SYSTEM_TABLE = """
당신은 DB 테이블 분석 AI입니다.
- SQLite table usage, references evidence를 기준으로 설명합니다.
"""

SYSTEM_PROMPTS = {
    "diagram": SYSTEM_DIAGRAM,
    "tableanalysis": SYSTEM_TABLE,
    "apidoc": SYSTEM_APIDOC,
    "layersearch": SYSTEM_LAYER,
    "xmlanalysis": SYSTEM_XML,
    "architecture": SYSTEM_LAYER,
    "qa": SYSTEM_BASE,
    "listing": SYSTEM_BASE,
}


class PromptBuilder:
    def build_messages(
            self,
            question: str,
            hits: list[dict],
            querytype: str,
            projectname: str | None = None,
            structcontext: str = "",
            chathistory: list[dict] | None = None,
            recententities: list[dict] | None = None,
            sqlitecontext: str = "",
            maxhistorychars: int = 4000,
    ) -> list[dict]:
        systemprompt = SYSTEM_PROMPTS.get(querytype, SYSTEM_BASE)
        messages: list[dict] = [{"role": "system", "content": systemprompt}]

        trimmed = self.trim_history(chathistory or [], maxhistorychars)
        for row in trimmed:
            messages.append({"role": "user", "content": row["question"]})
            messages.append({"role": "assistant", "content": row["answer"]})

        parts: list[str] = []

        if projectname:
            parts.append(f"[project]\n- {projectname}")

        if recententities:
            entity_lines = []
            seen = set()
            for e in recententities[:12]:
                key = (e.get("entitytype"), e.get("entityname"), e.get("relativepath"))
                if key in seen:
                    continue
                seen.add(key)
                label = f"- {e.get('entitytype', '')}: {e.get('entityname', '')}"
                if e.get("relativepath"):
                    label += f" ({e.get('relativepath')})"
                entity_lines.append(label)
            if entity_lines:
                parts.append("[recent_entities]\n" + "\n".join(entity_lines))

        if structcontext:
            parts.append("[structure]\n" + structcontext)

        if sqlitecontext:
            parts.append("[sqlite]\n" + sqlitecontext)

        metasummary = self.build_metadata_summary(hits)
        if metasummary:
            parts.append("[metadata]\n" + metasummary)

        chunkcontext = self.build_chunk_context(hits)
        if chunkcontext:
            parts.append("[evidence]\n" + chunkcontext)

        parts.append("[question]\n" + question)

        messages.append({"role": "user", "content": "\n\n".join(parts)})
        return messages

    def build_metadata_summary(self, hits: list[dict]) -> str:
        if not hits:
            return ""
        seen: set[str] = set()
        lines: list[str] = []
        for h in hits:
            key = h.get("relativepath") or h.get("filename") or ""
            if key in seen:
                continue
            seen.add(key)
            metaparts = []
            if h.get("layertype"):
                metaparts.append(f"layer={h['layertype']}")
            if h.get("classname"):
                metaparts.append(f"class={h['classname']}")
            if h.get("package"):
                metaparts.append(f"package={h['package']}")
            if h.get("contenttype"):
                metaparts.append(f"type={h['contenttype']}")
            if h.get("chunktype"):
                metaparts.append(f"chunktype={h['chunktype']}")
            metastr = f" ({', '.join(metaparts)})" if metaparts else ""
            lines.append(f"- {key}{metastr}")
        return "\n".join(lines)

    def build_chunk_context(self, hits: list[dict]) -> str:
        if not hits:
            return ""
        extlangmap = {
            "java": "java",
            "py": "python",
            "xml": "xml",
            "sql": "sql",
            "js": "javascript",
            "ts": "typescript",
            "md": "markdown",
            "json": "json",
            "yml": "yaml",
            "yaml": "yaml",
        }
        parts: list[str] = []
        for h in hits:
            headerparts = [h.get("relativepath") or h.get("filename") or ""]
            if h.get("classname"):
                headerparts.append(f"class={h['classname']}")
            if h.get("layertype"):
                headerparts.append(f"layer={h['layertype']}")
            if h.get("chunkindex") is not None:
                headerparts.append(f"chunk={h['chunkindex']}")
            if h.get("startline") and h.get("endline"):
                headerparts.append(f"line={h['startline']}-{h['endline']}")
            lang = extlangmap.get(h.get("extension", ""), "")
            parts.append(f"--- {' | '.join(headerparts)} ---\n```{lang}\n{h.get('text', '')}\n```")
        return "\n\n".join(parts)

    def trim_history(self, history: list[dict], maxchars: int) -> list[dict]:
        if maxchars <= 0 or not history:
            return []
        trimmed: list[dict] = []
        total = 0
        for row in reversed(history):
            q = (row.get("question") or "").strip()
            a = (row.get("answer") or "").strip()
            if not q or not a:
                continue
            pairlen = len(q) + len(a)
            if trimmed and total + pairlen > maxchars:
                break
            trimmed.append({"question": q, "answer": a})
            total += pairlen
        trimmed.reverse()
        return trimmed