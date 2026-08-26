"""Клавиатуры бота.

Меню участника — одно сообщение-хаб с инлайн-кнопками: разделы редактируют
его на месте, чат не засоряется. Фото (QR, карта) улетают отдельными
сообщениями — телеграм не умеет превращать текст в фото редактированием.
"""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import Activity, Faculty, Prize

remove_keyboard = ReplyKeyboardRemove()


def menu_keyboard(is_staff: bool = False) -> InlineKeyboardMarkup:
    """Главное меню участника — живёт одним сообщением, разделы правят его."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🎫 Мой QR", callback_data="menu:qr")
    builder.button(text="✅ Мои зоны", callback_data="menu:zones")
    builder.button(text="🎓 Мастер-классы", callback_data="menu:ws")
    builder.button(text="🎁 Призы", callback_data="menu:prizes")
    builder.button(text="❓ Помощь", callback_data="menu:help")
    if is_staff:
        builder.button(text="🛠 Режим организатора", callback_data="mode:staff")
        builder.adjust(2, 3, 1, 1)
    else:
        builder.adjust(2, 3, 1)
    return builder.as_markup()


def menu_button_keyboard() -> InlineKeyboardMarkup:
    """Одна кнопка «Меню»: превращает текущее сообщение (например, результат
    скана) в хаб, не плодя новых сообщений."""
    builder = InlineKeyboardBuilder()
    builder.button(text="☰ Меню", callback_data="menu:main")
    return builder.as_markup()


def mode_choice_keyboard() -> InlineKeyboardMarkup:
    """Выбор режима для того, кто и организатор, и участник."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🛠 Войти как организатор", callback_data="mode:staff")
    builder.button(text="👤 Войти как участник", callback_data="mode:participant")
    builder.adjust(1)
    return builder.as_markup()


def switch_to_participant_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Открыть участника", callback_data="mode:participant")
    return builder.as_markup()


def with_back(
    markup: InlineKeyboardMarkup,
    callback_data: str = "menu:main",
    text: str = "← В меню",
) -> InlineKeyboardMarkup:
    """Добавить кнопку «назад» нижним рядом к готовой клавиатуре."""
    rows = [list(row) for row in markup.inline_keyboard]
    rows.append([InlineKeyboardButton(text=text, callback_data=callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def back_keyboard(
    callback_data: str = "menu:main", text: str = "← В меню"
) -> InlineKeyboardMarkup:
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
