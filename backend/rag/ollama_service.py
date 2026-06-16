import httpx
import json
from backend.config import Settings # 경로 수정

class OllamaService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate_response_stream(self, question: str, hits: list[dict]):
        url = f"{self.settings.ollama_base_url}/api/chat"
        context_parts = [f"File: {h['file_name']}\nContent: {h['text']}" for h in hits]
        prompt = f"Context:\n{chr(10).join(context_parts)}\n\nQuestion: {question}"

        payload = {
            "model": self.settings.ollama_model,
            "messages": [
                {"role": "system", "content": "당신은 소스 코드 분석 도우미입니다. 한국어로 답변하세요."},
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
                        if content: yield content
                        if chunk.get("done"): break