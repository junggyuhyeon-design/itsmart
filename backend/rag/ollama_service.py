from __future__ import annotations

import json
import logging

import httpx

from config import Settings
from rag.prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)


def _preview_text(value: str, limit: int = 300) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"...(truncated {len(text) - limit} chars)"


class OllamaService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.prompt_builder = PromptBuilder()

    async def generate_response_stream(
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
        logger.info(
            "[ollama_service.py][generate_response_stream][1.시작] query_type=%s project_name=%s question_len=%d hits=%d struct_context_len=%d chat_history_count=%d recent_entities_count=%d sqlite_context_len=%d question_preview=%s",
            query_type,
            project_name,
            len(question or ""),
            len(hits or []),
            len(struct_context or ""),
            len(chat_history or []),
            len(recent_entities or []),
            len(sqlite_context or ""),
            _preview_text(question, 300),
        )

        messages = self.prompt_builder.build_messages(
            question=question,
            hits=hits,
            query_type=query_type,
            project_name=project_name,
            struct_context=struct_context,
            chat_history=chat_history,
            recent_entities=recent_entities,
            sqlite_context=sqlite_context,
            max_history_chars=self.settings.chat_history_max_chars,
        )

        logger.info(
            "[ollama_service.py][generate_response_stream][2.messages 생성완료] message_count=%d last_message_role=%s last_message_len=%d last_message_preview=%s",
            len(messages or []),
            messages[-1].get("role") if messages else "",
            len((messages[-1].get("content") if messages else "") or ""),
            _preview_text((messages[-1].get("content") if messages else "") or "", 500),
        )


        logger.info("--------------------------규현---------------------------------------------------------------------------")
        logger.info(
            "[ollama_service.py][generate_response_stream][messages 전체 덤프] %s",
            json.dumps(messages, ensure_ascii=False, indent=2),
        )
        logger.info("--------------------------규현---------------------------------------------------------------------------")

        url = f"{self.settings.ollama_base_url}/api/chat"
        payload = {
            "model": self.settings.ollama_model,
            "messages": messages,
            "stream": True,
        }

        logger.info(
            "[ollama_service.py][generate_response_stream][3.ollama 요청준비] url=%s model=%s stream=%s message_count=%d",
            url,
            payload.get("model"),
            payload.get("stream"),
            len(payload.get("messages") or []),
        )

        async with httpx.AsyncClient(timeout=300.0) as client:
            logger.info(
                "[ollama_service.py][generate_response_stream][4.http client 생성] timeout=300.0"
            )

            async with client.stream("POST", url, json=payload) as response:
                logger.info(
                    "[ollama_service.py][generate_response_stream][5.ollama 응답수신] status_code=%d",
                    response.status_code,
                )
                response.raise_for_status()

                chunk_count = 0
                total_content_length = 0

                async for line in response.aiter_lines():
                    if not line:
                        continue

                    chunk = json.loads(line)
                    content = chunk.get("message", {}).get("content", "")

                    if content:
                        chunk_count += 1
                        total_content_length += len(content)

                        logger.info(
                            "[ollama_service.py][generate_response_stream][6.stream 청크수신] chunk_count=%d content_len=%d content_preview=%s",
                            chunk_count,
                            len(content),
                            _preview_text(content, 200),
                        )
                        yield content

                    if chunk.get("done"):
                        logger.info(
                            "[ollama_service.py][generate_response_stream][7.stream 종료] chunk_count=%d total_content_length=%d done=%s done_reason=%s",
                            chunk_count,
                            total_content_length,
                            chunk.get("done"),
                            chunk.get("done_reason"),
                        )
                        break