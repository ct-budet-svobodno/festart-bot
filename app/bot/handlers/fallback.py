"""Всё, что не подошло под другие хендлеры."""

from aiogram import F, Router
from aiogram.types import Message

from app.bot import keyboards as kb
from app.models import Participant

router = Router()


@router.message(F.text)
async def unknown_text(message: Message, participant: Participant) -> None:
    if not participant.is_registered:
        await message.answer("Чтобы начать, нажми /start")
        return
    await message.answer(
        "Не понял. Выбери раздел кнопкой ниже 👇", reply_markup=kb.main_menu()
    )
