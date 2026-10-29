"""Router registry."""

from __future__ import annotations

from aiogram import Dispatcher

from app.bot.handlers.chat import build_router as build_chat_router
from app.bot.handlers.common import build_router as build_common_router
from app.bot.handlers.drill import build_router as build_drill_router
from app.bot.handlers.onboarding import build_router as build_onboarding_router


def register_routers(dispatcher: Dispatcher) -> None:
    dispatcher.include_router(build_common_router())
    dispatcher.include_router(build_onboarding_router())
    dispatcher.include_router(build_drill_router())
    dispatcher.include_router(build_chat_router())
