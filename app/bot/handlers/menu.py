"""Меню участника: одно сообщение-хаб с инлайн-кнопками.

Разделы не шлют новые сообщения, а редактируют хаб на месте — чат
остаётся чистым. Исключение — QR и карта: это фото, телеграм не умеет
превращать текст в фото редактированием, поэтому они улетают отдельно.
"""

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.render import profile_text, workshop_card, zones_text
from app.bot.states import HUB_KEY, reset_state
from app.models import Activity, ActivityKind, Participant, Prize, Staff
from app.services.event import get_event_settings
from app.services.maps import build_progress_map
from app.services.points import get_balance
from app.services.prizes import active_prizes
from app.services.qr import make_qr_png, participant_link
from app.utils import fmt_points

router = Router()


async def _edit(
    callback: CallbackQuery, text: str, markup, alert: str | None = None
) -> None:
    """Отредактировать сообщение с кнопками.

    «message is not modified» — норма (перерисовали то же самое). Остальные
    ошибки редактирования (сообщение старше 48 часов и т.п.) не глотаем:
    отправляем раздел новым сообщением, иначе кнопки «перестают работать».
    """
    if alert:
        await callback.answer(alert, show_alert=True)
        return
    await callback.answer()
    if not callback.message:
        return
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        try:
            await callback.message.answer(text, reply_markup=markup)
        except Exception:
            pass


async def drop_hub(message: Message, state: FSMContext) -> None:
    """Удалить прошлый хаб, если помним его id."""
    data = await state.get_data()
    old_hub = data.get(HUB_KEY)
    if old_hub and message.bot is not None:
        try:
            await message.bot.delete_message(message.chat.id, old_hub)
        except TelegramBadRequest:
            pass
        except Exception:
            pass


async def send_hub(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    participant: Participant,
    *,
    is_staff: bool = False,
) -> None:
    """Отправить сообщение-хаб: профиль + инлайн-меню. Прежний хаб удаляем."""
    await drop_hub(message, state)
    text = await profile_text(session, participant)
    sent = await message.answer(text, reply_markup=kb.menu_keyboard(is_staff=is_staff))
    await state.update_data(**{HUB_KEY: sent.message_id})


def _is_staff(staff: Staff | None) -> bool:
    return staff is not None and staff.is_active


async def _require_registration(
    callback: CallbackQuery, participant: Participant
) -> bool:
    if participant.is_blocked:
        await callback.answer(
            "Профиль приостановлен. Подойди к стойке организаторов.", show_alert=True
        )
        return False
    if participant.is_registered:
        return True
    await callback.answer("Сначала зарегистрируйся — нажми /start", show_alert=True)
    return False


# --- Хаб и разделы ---


@router.callback_query(F.data == "menu:main")
async def cb_menu_main(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    participant: Participant,
    staff: Staff | None,
) -> None:
    await reset_state(state)
    if not await _require_registration(callback, participant):
        return
    await _edit(
        callback,
        await profile_text(session, participant),
        kb.menu_keyboard(is_staff=_is_staff(staff)),
    )


@router.callback_query(F.data == "menu:qr")
async def cb_menu_qr(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession, participant: Participant
) -> None:
    await reset_state(state)
    if not await _require_registration(callback, participant):
        return
    event = await get_event_settings(session)
    png = make_qr_png(participant_link(participant.qr_token), box_size=12, border=3)
    caption = event.qr_hint_text.format(short_code=participant.short_code)
    await callback.answer()
    if callback.message:
        await callback.message.answer_photo(
            BufferedInputFile(png, filename="my-qr.png"),
            caption=f"<b>{participant.full_name}</b>\n\n{caption}",
        )


@router.callback_query(F.data == "menu:zones")
async def cb_menu_zones(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession, participant: Participant
) -> None:
    await reset_state(state)
    if not await _require_registration(callback, participant):
        return
    await _edit(
        callback, await zones_text(session, participant), kb.back_keyboard()
    )


@router.callback_query(F.data == "menu:map")
async def cb_menu_map(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession, participant: Participant
) -> None:
    await reset_state(state)
    if not await _require_registration(callback, participant):
        return
    image, caption = await build_progress_map(session, participant.id)
    if image is None:
        await callback.answer(caption or "Карта пока не загружена.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer_photo(
            BufferedInputFile(image, filename="map.jpg"), caption=caption
        )


@router.callback_query(F.data == "menu:ws")
async def cb_menu_workshops(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession, participant: Participant
) -> None:
    await reset_state(state)
    if not await _require_registration(callback, participant):
        return

    rows = await session.scalars(
        select(Activity)
        .where(Activity.kind == ActivityKind.WORKSHOP, Activity.is_active.is_(True))
        .order_by(Activity.starts_at, Activity.sort_order, Activity.id)
    )
    workshops = list(rows.all())
    if not workshops:
        await _edit(
        callback,
        "Расписание пока не опубликовано. Загляни чуть позже.",
        kb.back_keyboard(),
    )
        return
    await _edit(
        callback,
        "<b>Мастер-классы и активности</b>\nНажми, чтобы прочитать подробности.",
        kb.workshops_keyboard(workshops),
    )


@router.callback_query(F.data.startswith("ws:"))
async def workshop_details(callback: CallbackQuery, session: AsyncSession) -> None:
    workshop_id = int(callback.data.split(":")[1])
    workshop = await session.get(Activity, workshop_id)
    if workshop is None:
        await callback.answer("Не найдено")
        return
    markup = kb.with_back(kb.back_keyboard("menu:ws", "← К списку"))
    await _edit(callback, workshop_card(workshop), markup)


@router.callback_query(F.data == "menu:prizes")
async def cb_menu_prizes(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession, participant: Participant
) -> None:
    await reset_state(state)
    if not await _require_registration(callback, participant):
        return

    prizes = await active_prizes(session)
    if not prizes:
        await _edit(callback, "Призы пока не добавлены.", kb.back_keyboard())
        return

    balance = await get_balance(session, participant.id)
    affordable = [p for p in prizes if p.in_stock and balance >= p.cost_points]

    lines = [f"🏆 У тебя <b>{fmt_points(balance)}</b>", ""]
    if affordable:
        lines.append(f"Уже можешь забрать: <b>{len(affordable)}</b>")
    else:
        cheapest = min((p.cost_points for p in prizes if p.in_stock), default=None)
        if cheapest is not None:
            lines.append(f"До первого приза не хватает <b>{fmt_points(cheapest - balance)}</b>")
    lines.append("")
    lines.append("✅ доступно · 🔒 не хватает баллов · ⛔ закончился")
    lines.append("")
    lines.append("Приз выдаёт организатор на стойке — покажи ему свой QR.")

    await _edit(
        callback, "\n".join(lines), kb.with_back(kb.prizes_keyboard(prizes, balance))
    )


@router.callback_query(F.data.startswith("prize:"))
async def prize_details(
    callback: CallbackQuery, session: AsyncSession, participant: Participant
) -> None:
    prize_id = int(callback.data.split(":")[1])
    prize = await session.get(Prize, prize_id)
    if prize is None:
        await callback.answer("Приз не найден")
        return

    balance = await get_balance(session, participant.id)
    if not prize.in_stock:
        hint = "К сожалению, закончился."
    elif balance >= prize.cost_points:
        hint = "Можешь забрать прямо сейчас — подойди к стойке призов."
    else:
        hint = f"Не хватает {fmt_points(prize.cost_points - balance)}."

    text = f"<b>{prize.title}</b>\nЦена: {fmt_points(prize.cost_points)}"
    if prize.description:
        text += f"\n\n{prize.description}"
    text += f"\n\n{hint}"

    markup = kb.with_back(kb.back_keyboard("menu:prizes", "← К призам"))
    await _edit(callback, text, markup)


@router.callback_query(F.data == "menu:help")
async def cb_menu_help(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession, staff: Staff | None
) -> None:
    await reset_state(state)
    event = await get_event_settings(session)
    await _edit(callback, event.help_text, kb.back_keyboard())
