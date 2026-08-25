"""Клавиатуры админки внутри бота.

Схема callback_data: ad:<действие>:<аргументы>. Короткие коды, чтобы уложиться
в лимит Telegram в 64 байта.
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.admin.fields import display_value
from app.bot.admin.specs import Spec

BACK = "ad:menu"


def main_menu(is_superadmin: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Сводка", callback_data="ad:stats")
    builder.button(text="⚡️ Режимы", callback_data="ad:modes")
    builder.button(text="🎁 Призы", callback_data="ad:list:p")
    builder.button(text="📍 Зоны", callback_data="ad:list:z")
    builder.button(text="🎓 Мастер-классы", callback_data="ad:list:w")
    builder.button(text="✍️ Тексты и бонусы", callback_data="ad:card:s:1")
    builder.button(text="🗺 Карта", callback_data="ad:map")
    builder.button(text="🔍 Найти участника", callback_data="ad:find")
    builder.button(text="📤 Выгрузки", callback_data="ad:export")
    if is_superadmin:
        builder.button(text="👥 Организаторы", callback_data="ad:staff")
    builder.adjust(2, 2, 2, 2, 1)
    return builder.as_markup()


def map_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🗺 Карта с сеткой (для X/Y зон)", callback_data="ad:mapgrid")
    builder.button(text="← Меню", callback_data=BACK)
    builder.adjust(1)
    return builder.as_markup()


def item_list(spec: Spec, items: list, extra_note: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        mark = "" if getattr(item, "is_active", True) else "⏸ "
        suffix = _list_suffix(spec, item)
        builder.button(
            text=f"{mark}{item.title}{suffix}",
            callback_data=f"ad:card:{spec.code}:{item.id}",
        )
    builder.button(text="➕ Добавить", callback_data=f"ad:new:{spec.code}")
    builder.button(text="← Меню", callback_data=BACK)
    builder.adjust(1)
    return builder.as_markup()


def _list_suffix(spec: Spec, item) -> str:
    if spec.code == "p":
        return f" · {item.cost_points}б · {item.stock_left}шт"
    return f" · {item.points}б"


def item_card(spec: Spec, item, *, with_qr: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for field in spec.fields:
        value = display_value(field, getattr(item, field.key, None))
        builder.button(
            text=f"{field.label}: {value}",
            callback_data=f"ad:ed:{spec.code}:{item.id}:{field.key}",
        )
    for field in spec.toggles:
        state = "✅" if getattr(item, field.key, False) else "⬜️"
        builder.button(
            text=f"{state} {field.label}",
            callback_data=f"ad:tg:{spec.code}:{item.id}:{field.key}",
        )
    if with_qr:
        builder.button(text="🖨 Плакат с QR", callback_data=f"ad:qr:{item.id}")
    if spec.code != "s":
        builder.button(text="🗑 Удалить", callback_data=f"ad:del:{spec.code}:{item.id}")
        builder.button(text="← К списку", callback_data=f"ad:list:{spec.code}")
    else:
        builder.button(text="← Меню", callback_data=BACK)
    builder.adjust(1)
    return builder.as_markup()


def toggles_only(spec: Spec, item) -> InlineKeyboardMarkup:
    """Экран рубильников: только переключатели, крупно, без лишнего."""
    builder = InlineKeyboardBuilder()
    for field in spec.toggles:
        state = "✅ включено" if getattr(item, field.key, False) else "⛔️ выключено"
        builder.button(
            text=f"{field.label} — {state}",
            callback_data=f"ad:tgm:{spec.code}:{item.id}:{field.key}",
        )
    builder.button(text="← Меню", callback_data=BACK)
    builder.adjust(1)
    return builder.as_markup()


def confirm_delete(spec: Spec, item_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Да, удалить", callback_data=f"ad:delok:{spec.code}:{item_id}")
    builder.button(text="Отмена", callback_data=f"ad:card:{spec.code}:{item_id}")
    builder.adjust(1)
    return builder.as_markup()


def back_only(target: str = BACK, text: str = "← Меню") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=text, callback_data=target)
    return builder.as_markup()


def exports_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📄 Участники (CSV)", callback_data="ad:exp:csv")
    builder.button(text="🖨 Все плакаты зон (ZIP)", callback_data="ad:exp:zones")
    builder.button(text="🖨 Все плакаты МК (ZIP)", callback_data="ad:exp:ws")
    builder.button(text="← Меню", callback_data=BACK)
    builder.adjust(1)
    return builder.as_markup()


def faculties_kb(faculties: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for faculty in faculties:
        builder.button(text=f"🗑 {faculty.title}", callback_data=f"ad:facdel:{faculty.id}")
    builder.button(text="➕ Добавить факультет", callback_data="ad:facadd")
    builder.button(text="← Меню", callback_data=BACK)
    builder.adjust(1)
    return builder.as_markup()


def staff_kb(members: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for member in members:
        mark = "✅" if member.is_activated else "⏳"
        if not member.is_active:
            mark = "⏸"
        if member.is_env_admin:
            mark = "🔑"
        builder.button(
            text=f"{mark} {member.name} · {member.role_label}",
            callback_data=f"ad:st:{member.id}",
        )
    builder.button(text="➕ Добавить организатора", callback_data="ad:stadd")
    builder.button(text="← Меню", callback_data=BACK)
    builder.adjust(1)
    return builder.as_markup()


def staff_card_kb(member) -> InlineKeyboardMarkup:
    """У постоянных админов из .env кнопок понижения и удаления нет —
    их всё равно нельзя применить, а показывать неработающее вредно."""
    builder = InlineKeyboardBuilder()
    if not member.is_env_admin:
        builder.button(text="🔗 Прислать приглашение", callback_data=f"ad:stlink:{member.id}")
        builder.button(text="🎭 Сменить роль", callback_data=f"ad:strole:{member.id}")
        builder.button(
            text="⏸ Отключить" if member.is_active else "▶️ Включить",
            callback_data=f"ad:stoff:{member.id}",
        )
        builder.button(text="🗑 Удалить", callback_data=f"ad:stdel:{member.id}")
    builder.button(text="← К списку", callback_data="ad:staff")
    builder.adjust(1)
    return builder.as_markup()


def new_staff_roles_kb(roles: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value, label in roles:
        builder.button(text=label, callback_data=f"ad:stnew:{value}")
    builder.button(text="← Назад", callback_data="ad:staff")
    builder.adjust(1)
    return builder.as_markup()


def roles_kb(member_id: int, roles: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value, label in roles:
        builder.button(text=label, callback_data=f"ad:strset:{member_id}:{value}")
    builder.button(text="← Назад", callback_data=f"ad:st:{member_id}")
    builder.adjust(1)
    return builder.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    """Кнопка под сообщением-подсказкой: удаляет его и гасит сценарий ввода.

    Экран, с которого ввод начали (карточка, список), никуда не девался —
    дублировать его заново не нужно.
    """
    return back_only("ad:cancel", "✖ Отмена")
