import json

import httpx

from config import Settings
from rag.prompt_builder import PromptBuilder


class OllamaService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._prompt_builder = PromptBuilder()

    async def generate_response_stream(
<<<<<<< HEAD
        self,
        question: str,
        hits: list[dict],
        query_type: str = "qa",
        project_name: str | None = None,
        chat_history: list[dict] | None = None,
    ):
        """PromptBuilder로 메시지를 조립하고 Ollama 스트리밍 응답을 yield한다."""
        messages = self._prompt_builder.build_messages(  # 프롬프트 메세지 작성
=======
            self,
            *,
            question: str,
            hits: list[dict],
            query_type: str = "qa",
            project_name: str | None = None,
            struct_context: str = "",
            chat_history: list[dict] | None = None,
            recent_entities: list[dict] | None = None,
            sqlite_context: str = "",
    ):
        messages = self._prompt_builder.build_messages(
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
            question=question,
            hits=hits,
            query_type=query_type,
            project_name=project_name,
            chat_history=chat_history,
            recent_entities=recent_entities,
            sqlite_context=sqlite_context,
            max_history_chars=self.settings.chat_history_max_chars,
        )

        url = f"{self.settings.ollama_base_url}/api/chat"
        payload = {
<<<<<<< HEAD
            "model": self.settings.ollama_model,  # Qwen2.5-coder:3b
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": 0.1,      # 사실 기반 답변 고정
                "repeat_penalty": 1.05,  # 너무 높으면 되레 다른 표현으로 같은 내용 반복함
                "repeat_last_n": 256,    # 반복 억제 탐지 범위 (토큰)
                "stop": [
                    "예상 질문",
                    "추가 질문",
                    "관련 질문",
                    "더 알아보",
                    "궁금한 점",
                ],
            },
=======
            "model": self.settings.ollama_model,
            "messages": messages,
            "stream": True,
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
        }

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", url, json=payload) as response:
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if chunk.get("done"):
                        break