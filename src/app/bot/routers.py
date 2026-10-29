"""Router registry."""

from __future__ import annotations

from aiogram import Dispatcher

from app.bot.handlers.chat import build_router as build_chat_router
from app.bot.handlers.common import build_router as build_common_router
from app.bot.handlers.correction import build_router as build_correction_router
from app.bot.handlers.drill import build_router as build_drill_router
from app.bot.handlers.onboarding import build_router as build_onboarding_router
from app.bot.handlers.preferences import build_router as build_preferences_router
from app.bot.handlers.privacy import build_router as build_privacy_router
from app.bot.handlers.review import build_router as build_review_router
from app.bot.handlers.roleplay import build_router as build_roleplay_router
from app.bot.handlers.stats import build_router as build_stats_router
from app.bot.handlers.vocab import build_router as build_vocab_router
from app.bot.handlers.words import build_router as build_words_router


def register_routers(dispatcher: Dispatcher) -> None:
    dispatcher.include_router(build_common_router())
    dispatcher.include_router(build_onboarding_router())
    dispatcher.include_router(build_drill_router())
    dispatcher.include_router(build_words_router())
    dispatcher.include_router(build_correction_router())
    dispatcher.include_router(build_vocab_router())
    dispatcher.include_router(build_review_router())
    dispatcher.include_router(build_stats_router())
    dispatcher.include_router(build_preferences_router())
    dispatcher.include_router(build_privacy_router())
    dispatcher.include_router(build_roleplay_router())
    dispatcher.include_router(build_chat_router())
