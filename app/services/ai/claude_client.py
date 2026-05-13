"""
app/services/ai/claude_client.py
─────────────────────────────────
LangChain LLM abstraction with Claude Sonnet 4.5 as primary model
and Groq Llama-3.3-70B as automatic fallback.

Usage:
    client = LLMClient()
    result = await client.invoke_with_fallback(prompt)
    # result.content, result.model_used
"""

from dataclasses import dataclass

from langchain_anthropic import ChatAnthropic
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models.chat_models import BaseChatModel

from app.core.config import get_settings
from app.core.logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)


@dataclass
class LLMResult:
    content: str
    model_used: str


class LLMClient:
    """
    Provides a unified interface over Claude (primary) and Groq (fallback).

    Fallback chain:
        1. Attempt with Claude Sonnet 4.5
        2. On ANY exception → retry with Groq Llama-3.3-70B

    Both models are initialised once and reused.
    """

    def __init__(self) -> None:
        self._claude: BaseChatModel = ChatAnthropic(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
            temperature=0.1,  # low temp for consistent scoring
            max_tokens=1024,
        )

        self._groq: BaseChatModel = ChatGroq(
            model=settings.groq_model,
            api_key=settings.groq_api_key,
            temperature=0.1,
            max_tokens=1024,
        )

        # LangChain with_fallbacks – automatically falls back to Groq on error
        self._chain: BaseChatModel = self._claude.with_fallbacks(
            [self._groq],
            exceptions_to_handle=(Exception,),
        )

    async def invoke_with_fallback(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMResult:
        """
        Send a two-message chat (system + user) through Claude → Groq fallback.

        Returns LLMResult with the response content and the name of the model
        that actually produced the response.
        """
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        # Try primary (Claude) first
        try:
            response = await self._claude.ainvoke(messages)
            return LLMResult(
                content=str(response.content),
                model_used=settings.anthropic_model,
            )
        except Exception as primary_exc:
            logger.warning(
                "claude_failed_using_groq_fallback",
                error=str(primary_exc),
            )

        # Fallback to Groq
        try:
            response = await self._groq.ainvoke(messages)
            return LLMResult(
                content=str(response.content),
                model_used=settings.groq_model,
            )
        except Exception as fallback_exc:
            logger.error("groq_fallback_also_failed", error=str(fallback_exc))
            raise
