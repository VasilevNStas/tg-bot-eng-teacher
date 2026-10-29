"""Composition root: wires services for the application lifetime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.core.config import Settings
from app.db.database import Database
from app.db.enums import CEFRLevel, ConversationMode
from app.llm.openrouter import OpenRouterClient
from app.llm.port import LLMClient
from app.services.conversation.service import ConversationService
from app.services.exercises.service import ExerciseService
from app.services.prompts.builder import PromptBuilder
from app.services.prompts.registry import PromptRegistry
from app.services.usage.service import UsageService


@dataclass(slots=True)
class AppServices:
    settings: Settings
    database: Database
    llm: LLMClient
    usage: UsageService
    prompts: PromptRegistry
    builder: PromptBuilder
    conversation: ConversationService
    exercise: ExerciseService

    async def aclose(self) -> None:
        await self.llm.aclose()


def build_services(database: Database, settings: Settings) -> AppServices:
    prompts = PromptRegistry(settings.prompts_dir)
    prompts.persona()
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
    usage = UsageService(database, daily_budget=settings.daily_token_budget)
    conversation = ConversationService(
        database,
        llm,
        usage,
        builder,
        model_fast=settings.llm_model_fast,
        model_smart=settings.llm_model_smart,
        max_output_tokens=settings.llm_max_output_tokens,
        idle_timeout=timedelta(minutes=settings.conversation_idle_minutes),
    )
    exercise = ExerciseService(
        database,
        session_size=settings.drill_session_size,
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
    )
