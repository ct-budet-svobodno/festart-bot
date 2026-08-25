"""Всё, что не подошло под другие хендлеры."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.menu import send_hub
from app.models import Participant, Staff

router = Router()


@router.message(F.text)
async def unknown_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    participant: Participant,
    staff: Staff | None,
) -> None:
    if not participant.is_registered:
        await message.answer("Чтобы начать, нажми /start")
        return
    # Неизвестный текст (в том числе кнопки старой reply-клавиатуры)
    # просто открывает меню заново.
    await send_hub(
        message,
        state,
        session,
        participant,
        is_staff=staff is not None and staff.is_active,
    )
