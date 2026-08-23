"""Клавиатуры бота.

Главное меню — reply-клавиатура: на фестивале человек идёт, смотрит в телефон
одним глазом, крупные постоянно видимые кнопки надёжнее инлайновых.
Внутри разделов используем инлайн.
"""

from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from app.models import Activity, Faculty, Prize

BTN_POINTS = "🏆 Мои баллы"
BTN_QR = "🎫 Мой QR"
BTN_MAP = "📍 Карта"
BTN_ZONES = "✅ Мои зоны"
BTN_WORKSHOPS = "🎓 Мастер-классы"
BTN_PRIZES = "🎁 Призы"
BTN_HELP = "❓ Помощь"

MENU_BUTTONS = [
    BTN_POINTS,
    BTN_QR,
    BTN_ZONES,
    BTN_MAP,
    BTN_WORKSHOPS,
    BTN_PRIZES,
    BTN_HELP,
]

remove_keyboard = ReplyKeyboardRemove()


def main_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for text in MENU_BUTTONS:
        builder.button(text=text)
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="Выбери раздел")


def faculties_keyboard(faculties: list[Faculty]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for faculty in faculties:
        builder.button(text=faculty.title, callback_data=f"fac:{faculty.id}")
    builder.button(text="Другое / не из списка", callback_data="fac:other")
    builder.adjust(1)
    return builder.as_markup()


def prizes_keyboard(prizes: list[Prize], balance: int) -> InlineKeyboardMarkup:
    """Список призов для участника — только просмотр, выдаёт организатор."""
    builder = InlineKeyboardBuilder()
    for prize in prizes:
        if not prize.in_stock:
            mark = "⛔"
        elif balance >= prize.cost_points:
            mark = "✅"
        else:
            mark = "🔒"
        builder.button(
            text=f"{mark} {prize.title} — {prize.cost_points}",
            callback_data=f"prize:{prize.id}",
        )
    builder.adjust(1)
    return builder.as_markup()


def workshops_keyboard(workshops: list[Activity]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for workshop in workshops:
        builder.button(text=workshop.title, callback_data=f"ws:{workshop.id}")
    builder.adjust(1)
    return builder.as_markup()


def back_keyboard(callback_data: str, text: str = "← Назад") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=text, callback_data=callback_data)
    return builder.as_markup()


def confirm_redemption_keyboard(redemption_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=f"rd:ok:{redemption_id}")
    builder.button(text="❌ Отменить", callback_data=f"rd:no:{redemption_id}")
    builder.adjust(1)
    return builder.as_markup()


def staff_prizes_keyboard(
    participant_id: int, prizes: list[Prize], balance: int
) -> InlineKeyboardMarkup:
    """Призы глазами организатора на стойке: недоступные скрыты, чтобы не промахнуться."""
    builder = InlineKeyboardBuilder()
    for prize in prizes:
        if not prize.in_stock or balance < prize.cost_points:
            continue
        builder.button(
            text=f"{prize.title} — {prize.cost_points} ({prize.stock_left} шт.)",
            callback_data=f"give:{participant_id}:{prize.id}",
        )
    builder.button(text="➕ Начислить баллы", callback_data=f"award:{participant_id}")
    builder.adjust(1)
    return builder.as_markup()
