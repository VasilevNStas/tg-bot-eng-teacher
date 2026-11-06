"""Composition root: wires services for the application lifetime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.database import Database
from app.db.enums import CEFRLevel, ConversationMode
from app.llm.models import build_routes, validate_routes
from app.llm.openrouter import OpenRouterClient
from app.llm.port import LLMClient
from app.services.conversation.service import ConversationService
from app.services.conversation.summary import ConversationSummarizer
from app.services.exercises.service import ExerciseService
from app.services.prompts.builder import PromptBuilder
from app.services.prompts.registry import PromptRegistry
from app.services.usage.service import UsageService
from app.services.words.service import WordCardService

logger = get_logger(__name__)


@dataclass(slots=True)
class AppServices:
    """Container passed to handlers through aiogram middleware data."""

    settings: Settings
    database: Database
    llm: LLMClient
    usage: UsageService
    prompts: PromptRegistry
    builder: PromptBuilder
    conversation: ConversationService
    exercise: ExerciseService
    words: WordCardService

    async def aclose(self) -> None:
        """Release the HTTP client held by the LLM adapter."""
        await self.llm.aclose()


def build_services(database: Database, settings: Settings) -> AppServices:
    """Create the object graph once at startup.

    Prompt templates are loaded eagerly so a missing or empty file fails the
    deploy immediately, not on the first user message.
    """
    prompts = PromptRegistry(settings.prompts_dir)
    prompts.persona()
    prompts.summary()
    prompts.judge()
    for level in CEFRLevel:
        prompts.level(level)
    for mode in ConversationMode:
        prompts.mode(mode)

    builder = PromptBuilder(
        prompts,
        max_input_tokens=settings.llm_max_input_tokens,
        history_window=settings.prompt_history_window,
    )
    llm = OpenRouterClient(settings)
    routes = build_routes(settings)
    validation = validate_routes(routes)
    if not validation.ok:
        raise ValueError("invalid LLM routes: " + "; ".join(validation.errors))
    for warning in validation.warnings:
        logger.warning("llm_route_warning", warning=warning)

    usage = UsageService(
        database,
        daily_budget=settings.daily_token_budget,
        monthly_budget_usd=settings.global_monthly_budget_usd,
    )
    summarizer = ConversationSummarizer(
        database,
        llm,
        usage,
        prompts,
        routes=routes,
        history_window=builder.history_window,
        batch_size=settings.summary_batch_messages,
        max_batch_size=settings.summary_max_batch_messages,
        message_max_chars=settings.summary_message_max_chars,
        summary_max_chars=settings.summary_max_chars,
        max_input_tokens=settings.llm_max_input_tokens,
    )
    conversation = ConversationService(
        database,
        llm,
        usage,
        builder,
        routes=routes,
        summarizer=summarizer,
        repair_attempts=settings.llm_repair_attempts,
        idle_timeout=timedelta(minutes=settings.conversation_idle_minutes),
        max_conversation_messages=settings.max_conversation_messages,
    )
    exercise = ExerciseService(
        database,
        session_size=settings.drill_session_size,
    )
    words = WordCardService(
        database,
        card_size=settings.words_card_size,
    )
    return AppServices(
        settings=settings,
        database=database,
        llm=llm,
        usage=usage,
        prompts=prompts,
        builder=builder,
        conversation=conversation,
        exercise=exercise,
        words=words,
    )
