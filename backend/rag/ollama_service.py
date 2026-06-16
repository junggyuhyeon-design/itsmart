import httpx
import json
from backend.config import Settings


class OllamaService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _build_system_prompt(self, question: str) -> str:
        question_lower = (question or "").lower()

        mermaid_keywords = [
            "mermaid",
            "diagram",
            "flowchart",
            "graph td",
            "graph lr",
            "관계도",
            "다이어그램",
            "그려",
            "그려줘",
            "표현해",
        ]

        wants_mermaid = any(keyword in question_lower for keyword in mermaid_keywords)

        if wants_mermaid:
            return """
당신은 소스 코드와 DB 구조를 분석하는 도우미입니다.

사용자가 Mermaid 다이어그램을 요청한 경우 반드시 아래 규칙을 따른다.

[DDL 해석 규칙]
1. 업로드된 SQL 파일들 중 CREATE TABLE 등 DDL 문이 포함된 파일을 테이블 정의 기준으로 사용한다.
2. 특정 파일명(init.sql 등)을 가정하지 않는다.
3. 테이블 정의용 SQL 파일은 참고 자료일 뿐이며, 다이어그램 노드로 포함하지 않는다.
4. DDL 파일, schema 파일, SQL 정의 파일 자체는 Mermaid에 그리지 않는다.
5. 실제 다이어그램에는 소스 파일, 클래스, 함수(메서드), 그리고 실제 사용되는 테이블만 포함한다.
6. DDL에 정의된 테이블 중에서도 소스 코드에서 실제 참조/사용되는 테이블만 포함한다.
7. 단순히 정의만 있고 사용되지 않는 테이블은 제외한다.

[관계 해석 규칙]
- SELECT 는 READS
- INSERT, UPDATE, DELETE 는 WRITES
- JOIN 은 JOINS
- 명확하지 않으면 REF

[출력 규칙]
1. 응답은 오직 하나의 ```mermaid 코드블록만 출력한다.
2. 코드블록 밖의 설명, 제목, 해설, 리스트는 절대 출력하지 않는다.
3. 첫 줄은 반드시 flowchart LR 로 시작한다.
4. Mermaid 10.9.6에서 문법 오류 없이 동작해야 한다.
5. classDef, style, subgraph, click, %% 주석은 사용하지 않는다.
6. 노드명은 단순하게 만든다.
7. 파일/클래스/함수 이름에 공백이 있으면 언더스코어(_)로 바꾼다.
8. edge 라벨은 READS, WRITES, JOINS, REF 만 사용한다.
9. SQL 파일명 자체(init.sql, schema.sql, ddl.sql 등)은 노드로 만들지 않는다.

[출력 예시]
```mermaid
flowchart LR
F1[UserService_java] -->|READS| T1[TB_USER]
M1[UserMapper_xml] -->|WRITES| T2[TB_LOGIN_LOG]
```
""".strip()

        return "당신은 소스 코드 분석 도우미입니다. 한국어로 답변하세요."

    async def generate_response_stream(self, question: str, hits: list[dict]):
        url = f"{self.settings.ollama_base_url}/api/chat"
        context_parts = [f"File: {h['file_name']}\nContent: {h['text']}" for h in hits]
        prompt = f"Context:\n{chr(10).join(context_parts)}\n\nQuestion: {question}"

        system_prompt = self._build_system_prompt(question)

        payload = {
            "model": self.settings.ollama_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "stream": True
        }

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", url, json=payload) as response:
                async for line in response.aiter_lines():
                    if line:
                        chunk = json.loads(line)
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            yield content
                        if chunk.get("done"):
                            break