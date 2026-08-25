"""Админка внутри бота: меню, сводка, режимы, редактирование записей.

Все карточки редактируются одним универсальным сценарием: нажал кнопку поля —
бот спросил значение — разобрал — сохранил — перерисовал карточку.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.admin import keyboards as akb
from app.bot.admin.fields import BOOL, ParseError, display_value, parse_value, prompt_for
from app.bot.admin.specs import (
    SETTINGS,
    SPECS,
    WORKSHOP,
    ZONE,
    Spec,
    find_field,
)
from app.bot.admin.states import AdminCreate, AdminEdit
from app.models import (
    Activity,
    ActivityKind,
    Participant,
    PointsLedger,
    Prize,
    Redemption,
    RedemptionStatus,
    Staff,
    StaffRole,
    Visit,
)
from app.services.event import get_event_settings
from app.services.qr import activity_link, make_poster_png
from app.utils import gen_activity_code

router = Router()

ADMIN_ROLES = {StaffRole.SUPERADMIN, StaffRole.ADMIN}


def is_admin(staff: Staff | None) -> bool:
    return staff is not None and staff.is_active and staff.role in ADMIN_ROLES


def kind_for(spec: Spec) -> str | None:
    if spec.code == ZONE:
        return ActivityKind.ZONE
    if spec.code == WORKSHOP:
        return ActivityKind.WORKSHOP
    return None


async def load_items(session: AsyncSession, spec: Spec) -> list:
    query = select(spec.model)
    kind = kind_for(spec)
    if kind:
        query = query.where(Activity.kind == kind)
        query = query.order_by(Activity.sort_order, Activity.id)
    else:
        query = query.order_by(Prize.sort_order, Prize.cost_points)
    return list((await session.scalars(query)).all())


async def load_item(session: AsyncSession, spec: Spec, item_id: int):
    if spec.code == SETTINGS:
        return await get_event_settings(session)
    return await session.get(spec.model, item_id)


def card_text(spec: Spec, item) -> str:
    """Полные значения — в тексте, потому что на кнопке длинный текст обрезается."""
    title = getattr(item, "title", None) or spec.title
    lines = [f"<b>{title}</b>"]

    if spec.code in (ZONE, WORKSHOP):
        lines.append(f"Код в QR: <code>{item.code}</code>")

    body = []
    for field in spec.fields:
        value = getattr(item, field.key, None)
        if field.kind == "longtext" and value:
            body.append(f"\n<b>{field.label}</b>\n{value}")
    if body:
        lines.append("".join(body))

    lines.append("\nНажми на поле, чтобы изменить.")
    return "\n".join(lines)


async def show_card(target, session: AsyncSession, spec: Spec, item) -> None:
    markup = akb.item_card(spec, item, with_qr=spec.code in (ZONE, WORKSHOP))
    text = card_text(spec, item)
    await render_screen(target, text, markup)


async def render_screen(target, text: str, markup) -> None:
    """Перерисовываем то же сообщение, если можем — чтобы не засорять чат."""
    if isinstance(target, CallbackQuery):
        if target.message and target.message.text is not None:
            try:
                await target.message.edit_text(text, reply_markup=markup)
                return
            except Exception:
                pass
        if target.message:
            await target.message.answer(text, reply_markup=markup)
        return
    await target.answer(text, reply_markup=markup)


# --- Вход ---


@router.message(Command("admin"))
async def cmd_admin(
    message: Message, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        return
    await state.clear()
    event = await get_event_settings(session)
    await message.answer(
        f"<b>{event.event_title} · управление</b>\n{staff.name}, роль: {staff.role_label}",
        reply_markup=akb.main_menu(staff.role == StaffRole.SUPERADMIN),
    )


@router.callback_query(F.data == "ad:menu")
async def back_to_menu(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    event = await get_event_settings(session)
    await render_screen(
        callback,
        f"<b>{event.event_title} · управление</b>\n{staff.name}, роль: {staff.role_label}",
        akb.main_menu(staff.role == StaffRole.SUPERADMIN),
    )


# --- Сводка ---


@router.callback_query(F.data == "ad:stats")
async def show_stats(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()

    registered = await session.scalar(
        select(func.count(Participant.id)).where(Participant.is_registered.is_(True))
    )
    started = await session.scalar(select(func.count(Participant.id)))
    visits = await session.scalar(select(func.count(Visit.id)))
    issued = await session.scalar(
        select(func.coalesce(func.sum(PointsLedger.delta), 0)).where(PointsLedger.delta > 0)
    )
    prizes_out = await session.scalar(
        select(func.count(Redemption.id)).where(
            Redemption.status == RedemptionStatus.CONFIRMED
        )
    )
    pending = await session.scalar(
        select(func.count(Redemption.id)).where(
            Redemption.status == RedemptionStatus.PENDING
        )
    )

    lines = [
        "<b>📊 Сводка</b>",
        "",
        f"Зарегистрировано: <b>{registered or 0}</b> из {started or 0} открывших бота",
        f"Отметок на зонах: <b>{visits or 0}</b>",
        f"Баллов начислено: <b>{issued or 0}</b>",
        f"Призов выдано: <b>{prizes_out or 0}</b>",
    ]
    if pending:
        lines.append(f"⏳ Ждут подтверждения: <b>{pending}</b>")

    zone_rows = await session.execute(
        select(Activity.title, func.count(Visit.id))
        .join(Visit, Visit.activity_id == Activity.id, isouter=True)
        .where(Activity.kind == ActivityKind.ZONE)
        .group_by(Activity.id)
        .order_by(func.count(Visit.id).desc())
    )
    zones = list(zone_rows.all())
    if zones:
        lines.append("\n<b>Проходимость зон</b>")
        for title, total in zones:
            lines.append(f"{total:>4} · {title}")

    low = list(
        (
            await session.scalars(
                select(Prize)
                .where(Prize.is_active.is_(True), Prize.stock_left <= 5)
                .order_by(Prize.stock_left)
            )
        ).all()
    )
    if low:
        lines.append("\n<b>⚠️ Заканчиваются</b>")
        for prize in low:
            lines.append(f"{prize.stock_left} шт · {prize.title}")

    await render_screen(callback, "\n".join(lines), akb.back_only())


# --- Режимы ---


@router.callback_query(F.data == "ad:modes")
async def show_modes(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    event = await get_event_settings(session)
    await render_screen(callback, _modes_text(), akb.toggles_only(SPECS[SETTINGS], event))


def _modes_text() -> str:
    return (
        "<b>⚡️ Режимы</b>\n\n"
        "Нажми, чтобы переключить. Применяется мгновенно.\n\n"
        "<i>Выключенное начисление баллов останавливает сканирование QR на всех зонах.</i>"
    )


@router.callback_query(F.data.startswith("ad:tgm:"))
async def toggle_mode(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return

    _, _, spec_code, _, field_key = callback.data.split(":")
    spec = SPECS[spec_code]
    field = find_field(spec, field_key)
    event = await get_event_settings(session)
    if field is None or field.kind != BOOL:
        await callback.answer("Не получилось")
        return

    setattr(event, field_key, not getattr(event, field_key))
    await session.flush()
    await callback.answer("Готово" if getattr(event, field_key) else "Выключено")
    await render_screen(callback, _modes_text(), akb.toggles_only(spec, event))


# --- Списки и карточки ---


@router.callback_query(F.data.startswith("ad:list:"))
async def show_list(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.answer()

    spec = SPECS[callback.data.split(":")[2]]
    items = await load_items(session, spec)
    if not items:
        text = f"<b>{spec.title}</b>\n\nПока пусто. Добавь первую запись."
    else:
        text = f"<b>{spec.title}</b>\n\nВсего: {len(items)}. Нажми, чтобы открыть."
    await render_screen(callback, text, akb.item_list(spec, items))


@router.callback_query(F.data.startswith("ad:card:"))
async def open_card(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.answer()

    _, _, spec_code, item_id = callback.data.split(":")
    spec = SPECS[spec_code]
    item = await load_item(session, spec, int(item_id))
    if item is None:
        await render_screen(callback, "Запись не найдена.", akb.back_only())
        return
    await show_card(callback, session, spec, item)


# --- Переключатели внутри карточки ---


@router.callback_query(F.data.startswith("ad:tg:"))
async def toggle_field(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return

    _, _, spec_code, item_id, field_key = callback.data.split(":")
    spec = SPECS[spec_code]
    item = await load_item(session, spec, int(item_id))
    field = find_field(spec, field_key)
    if item is None or field is None:
        await callback.answer("Не получилось")
        return

    setattr(item, field_key, not getattr(item, field_key))
    await session.flush()
    await callback.answer("Включено" if getattr(item, field_key) else "Выключено")
    await show_card(callback, session, spec, item)


# --- Редактирование поля ---


SRC_KEY = "src_message_id"


@router.callback_query(F.data == "ad:cancel")
async def cancel_input(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    """«✖ Отмена» под подсказкой: убрать подсказку, погасить сценарий ввода.

    Экран, с которого начали (карточка/список), остаётся в чате как был —
    поэтому никакого нового меню не рисуем. Если сообщение старше 48 часов
    и его нельзя удалить — превращаем его в меню админки.
    """
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.answer("Отменено")
    if callback.message is None:
        return
    try:
        await callback.message.delete()
        return
    except Exception:
        pass
    event = await get_event_settings(session)
    await render_screen(
        callback,
        f"<b>{event.event_title} · управление</b>\n{staff.name}, роль: {staff.role_label}",
        akb.main_menu(staff.role == StaffRole.SUPERADMIN),
    )


async def ask_input(callback: CallbackQuery, state: FSMContext, prompt_text: str) -> None:
    """Отправить подсказку для ввода и запомнить служебные id сообщений:
    куда вернуть результат и какую подсказку потом убрать."""
    src = callback.message.message_id if callback.message else None
    await state.update_data(src_message_id=src)
    if callback.message:
        sent = await callback.message.answer(prompt_text, reply_markup=akb.cancel_kb())
        await state.update_data(prompt_message_id=sent.message_id)


async def discard_input(message: Message, state: FSMContext) -> None:
    """Убрать подсказку с «Отменой» и сообщение с вводом. Результат показывается
    отдельно новыми сообщениями (например, карточка участника после поиска)."""
    data = await state.get_data()
    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass


async def finish_input(
    message: Message, state: FSMContext, text: str, markup
) -> None:
    """Завершить сценарий ввода: убрать подсказку с «Отменой» и сообщение
    с вводом, а результат показать в том экране, откуда ввод начали.
    Если тот экран недоступен (старше 48 часов) — новым сообщением."""
    data = await state.get_data()

    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass

    src = data.get(SRC_KEY)
    if src:
        try:
            await message.bot.edit_message_text(
                text, chat_id=message.chat.id, message_id=src, reply_markup=markup
            )
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ad:ed:"))
async def edit_field(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return

    _, _, spec_code, item_id, field_key = callback.data.split(":")
    spec = SPECS[spec_code]
    field = find_field(spec, field_key)
    item = await load_item(session, spec, int(item_id))
    if field is None or item is None:
        await callback.answer("Не получилось")
        return

    await state.set_state(AdminEdit.value)
    await state.update_data(spec_code=spec_code, item_id=int(item_id), field_key=field_key)
    await callback.answer()

    current = display_value(field, getattr(item, field_key, None))
    await ask_input(
        callback, state, f"{prompt_for(field)}\n\nСейчас: <code>{current}</code>"
    )


@router.message(AdminEdit.value, F.text)
async def save_field(
    message: Message, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await state.clear()
        return

    data = await state.get_data()
    spec = SPECS[data["spec_code"]]
    field = find_field(spec, data["field_key"])
    item = await load_item(session, spec, data["item_id"])
    if field is None or item is None:
        await state.clear()
        await message.answer("Запись не найдена.", reply_markup=akb.back_only())
        return

    try:
        value = parse_value(field, message.text)
    except ParseError as exc:
        await message.answer(f"⚠️ {exc}\n\nПопробуй ещё раз или /cancel")
        return

    setattr(item, field.key, value)
    await session.flush()
    await state.clear()

    # Результат — в карточку, откуда ввод начали; подсказка удаляется.
    await finish_input(
        message,
        state,
        f"✅ {field.label} — обновлено\n\n{card_text(spec, item)}",
        akb.item_card(spec, item, with_qr=spec.code in (ZONE, WORKSHOP)),
    )


# --- Создание ---


@router.callback_query(F.data.startswith("ad:new:"))
async def new_item(
    callback: CallbackQuery, state: FSMContext, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return

    spec_code = callback.data.split(":")[2]
    spec = SPECS[spec_code]
    await state.set_state(AdminCreate.title)
    await state.update_data(spec_code=spec_code)
    await callback.answer()
    await ask_input(
        callback, state, f"Название — как будет называться {spec.one}?\n\nОтмена — /cancel"
    )


@router.message(AdminCreate.title, F.text)
async def create_item(
    message: Message, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await state.clear()
        return

    title = message.text.strip()
    if len(title) < 2 or len(title) > 200:
        await message.answer("От 2 до 200 символов. Попробуй ещё раз или /cancel")
        return

    data = await state.get_data()
    spec = SPECS[data["spec_code"]]
    kind = kind_for(spec)

    if kind:
        item = Activity(kind=kind, code=gen_activity_code(), title=title, points=1)
    else:
        item = Prize(title=title, cost_points=1, stock_total=0, stock_left=0)
    session.add(item)
    await session.flush()
    await state.clear()

    await finish_input(
        message,
        state,
        f"✅ Добавлено. Теперь заполни остальные поля.\n\n{card_text(spec, item)}",
        akb.item_card(spec, item, with_qr=spec.code in (ZONE, WORKSHOP)),
    )


# --- Удаление ---


@router.callback_query(F.data.startswith("ad:del:"))
async def ask_delete(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return

    _, _, spec_code, item_id = callback.data.split(":")
    spec = SPECS[spec_code]
    item = await load_item(session, spec, int(item_id))
    if item is None:
        await callback.answer("Не найдено")
        return

    await callback.answer()
    warning = (
        "Отметки участников об этой зоне тоже пропадут."
        if spec.code in (ZONE, WORKSHOP)
        else "История уже выданных призов сохранится."
    )
    await render_screen(
        callback,
        f"Удалить <b>{item.title}</b>?\n\n{warning}",
        akb.confirm_delete(spec, item.id),
    )


@router.callback_query(F.data.startswith("ad:delok:"))
async def do_delete(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return

    _, _, spec_code, item_id = callback.data.split(":")
    spec = SPECS[spec_code]
    item = await load_item(session, spec, int(item_id))
    if item is not None:
        await session.delete(item)
        await session.flush()

    await callback.answer("Удалено")
    items = await load_items(session, spec)
    await render_screen(
        callback,
        f"<b>{spec.title}</b>\n\nВсего: {len(items)}.",
        akb.item_list(spec, items),
    )


# --- Плакат ---


@router.callback_query(F.data.startswith("ad:qr:"))
async def send_poster(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return

    activity = await session.get(Activity, int(callback.data.split(":")[2]))
    if activity is None:
        await callback.answer("Не найдено")
        return

    await callback.answer("Готовлю плакат…")
    png = make_poster_png(
        activity_link(activity.code),
        activity.title,
        subtitle=f"+{activity.points} за посещение",
    )
    await callback.message.answer_document(
        BufferedInputFile(png, filename=f"plakat-{activity.code}.png"),
        caption=(
            f"Плакат A4 для «{activity.title}».\n"
            f"Ссылка внутри: <code>{activity_link(activity.code)}</code>"
        ),
    )
