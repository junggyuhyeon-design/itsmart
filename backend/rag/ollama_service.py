import json
import httpx
from config import Settings
from rag.prompt_builder import PromptBuilder


class OllamaService:
    def __init__(self, settings: Settings) -> None:
        self.settings       = settings
        self._prompt_builder = PromptBuilder()

    async def generate_response_stream(
        self,
        question:       str,
        hits:           list[dict],
        query_type:     str         = "qa",
        project_name:   str | None  = None,
        struct_context: str         = "",
        chat_history:   list[dict] | None = None,
    ):
        """PromptBuilder로 메시지를 조립하고 Ollama 스트리밍 응답을 yield한다."""
        messages = self._prompt_builder.build_messages(
            question=question,
            hits=hits,
            query_type=query_type,
            project_name=project_name,
            struct_context=struct_context,
            chat_history=chat_history,
            max_history_chars=self.settings.chat_history_max_chars,
        )

        url     = f"{self.settings.ollama_base_url}/api/chat"
        payload = {
            "model":    self.settings.ollama_model,
            "messages": messages,
            "stream":   True,
        }

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", url, json=payload) as response:
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    chunk   = json.loads(line)
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if chunk.get("done"):
                        break
