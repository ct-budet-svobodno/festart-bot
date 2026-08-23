"""Разделы главного меню участника."""

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.render import profile_text, workshop_card, zones_text
from app.models import Activity, ActivityKind, Participant
from app.services.event import get_event_settings
from app.services.maps import build_progress_map
from app.services.points import get_balance
from app.services.prizes import active_prizes
from app.services.qr import make_qr_png, participant_link
from app.utils import fmt_points

router = Router()


async def _require_registration(message: Message, participant: Participant) -> bool:
    if participant.is_blocked:
        await message.answer(
            "Твой профиль приостановлен. Подойди к стойке организаторов — разберёмся."
        )
        return False
    if participant.is_registered:
        return True
    await message.answer(
        "Сначала нужно зарегистрироваться — это меньше минуты.\nНажми /start."
    )
    return False


@router.message(F.text == kb.BTN_POINTS)
async def show_points(
    message: Message, session: AsyncSession, participant: Participant
) -> None:
    if not await _require_registration(message, participant):
        return
    await message.answer(await profile_text(session, participant))


@router.message(F.text == kb.BTN_QR)
async def show_qr(
    message: Message, session: AsyncSession, participant: Participant
) -> None:
    if not await _require_registration(message, participant):
        return

    event = await get_event_settings(session)
    png = make_qr_png(participant_link(participant.qr_token), box_size=12, border=3)
    caption = event.qr_hint_text.format(short_code=participant.short_code)

    await message.answer_photo(
        BufferedInputFile(png, filename="my-qr.png"),
        caption=f"<b>{participant.full_name}</b>\n\n{caption}",
    )


@router.message(F.text == kb.BTN_ZONES)
async def show_zones(
    message: Message, session: AsyncSession, participant: Participant
) -> None:
    if not await _require_registration(message, participant):
        return
    await message.answer(await zones_text(session, participant))


@router.message(F.text == kb.BTN_MAP)
async def show_map(
    message: Message, session: AsyncSession, participant: Participant
) -> None:
    if not await _require_registration(message, participant):
        return

    image, caption = await build_progress_map(session, participant.id)
    if image is None:
        await message.answer(caption or "Карта пока не загружена.")
        return
    await message.answer_photo(
        BufferedInputFile(image, filename="map.jpg"), caption=caption
    )


@router.message(F.text == kb.BTN_WORKSHOPS)
async def show_workshops(
    message: Message, session: AsyncSession, participant: Participant
) -> None:
    if not await _require_registration(message, participant):
        return

    rows = await session.scalars(
        select(Activity)
        .where(Activity.kind == ActivityKind.WORKSHOP, Activity.is_active.is_(True))
        .order_by(Activity.starts_at, Activity.sort_order, Activity.id)
    )
    workshops = list(rows.all())
    if not workshops:
        await message.answer("Расписание пока не опубликовано. Загляни чуть позже.")
        return

    await message.answer(
        "<b>Мастер-классы и активности</b>\nНажми, чтобы прочитать подробности.",
        reply_markup=kb.workshops_keyboard(workshops),
    )


@router.callback_query(F.data.startswith("ws:"))
async def workshop_details(callback: CallbackQuery, session: AsyncSession) -> None:
    workshop_id = int(callback.data.split(":")[1])
    workshop = await session.get(Activity, workshop_id)
    await callback.answer()
    if workshop is None:
        return
    await callback.message.answer(workshop_card(workshop))


@router.message(F.text == kb.BTN_PRIZES)
async def show_prizes(
    message: Message, session: AsyncSession, participant: Participant
) -> None:
    if not await _require_registration(message, participant):
        return

    prizes = await active_prizes(session)
    if not prizes:
        await message.answer("Призы пока не добавлены.")
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

    await message.answer(
        "\n".join(lines), reply_markup=kb.prizes_keyboard(prizes, balance)
    )


@router.callback_query(F.data.startswith("prize:"))
async def prize_details(
    callback: CallbackQuery, session: AsyncSession, participant: Participant
) -> None:
    from app.models import Prize

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

    await callback.answer()
    await callback.message.answer(text)


@router.message(F.text == kb.BTN_HELP)
async def show_help(message: Message, session: AsyncSession) -> None:
    event = await get_event_settings(session)
    text = event.help_text
    if event.privacy_url:
        text += f'\n\n<a href="{event.privacy_url}">Как мы обращаемся с данными</a>'
    await message.answer(text, reply_markup=kb.main_menu())
