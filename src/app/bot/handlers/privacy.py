"""Privacy commands: /delete_me."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import DeleteCB
from app.bot.keyboards.privacy import delete_confirm_keyboard
from app.core.di import AppServices
from app.services.privacy.service import PrivacyService
from app.texts import ru


async def handle_delete_me(message: Message, services: AppServices) -> None:
    if message.from_user is None:
        return
    if not await PrivacyService(services.database).has_user(message.from_user.id):
        await message.answer(ru.DELETE_NOTHING)
        return
    await message.answer(ru.DELETE_WARNING, reply_markup=delete_confirm_keyboard())


async def handle_delete_callback(
    callback: CallbackQuery,
    services: AppServices,
    state: FSMContext,
) -> None:
    message = callback.message
    if callback.data is None or callback.from_user is None or not isinstance(message, Message):
        await callback.answer()
        return

    data = DeleteCB.unpack(callback.data)
    if data.action == "cancel":
        await message.edit_text(ru.DELETE_CANCELLED)
        await callback.answer()
        return

    summary = await PrivacyService(services.database).delete_user(callback.from_user.id)
    await state.clear()
    if summary is None:
        await message.edit_text(ru.DELETE_ALREADY)
    else:
        await message.edit_text(
            ru.DELETE_DONE.format(
                conversations=summary.conversations,
                messages=summary.messages,
                vocabulary=summary.vocabulary,
            )
        )
    await callback.answer()


def build_router() -> Router:
    router = Router(name="privacy")
    router.message.register(handle_delete_me, Command("delete_me"))
    router.callback_query.register(handle_delete_callback, DeleteCB.filter())
    return router


router = build_router()
