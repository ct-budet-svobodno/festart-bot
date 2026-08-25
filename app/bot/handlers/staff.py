"""Сценарии организатора.

Камеру Telegram-бот открыть не может, поэтому организатор сканирует личный QR
участника обычной камерой телефона — ссылка открывает его собственный чат с ботом,
бот узнаёт роль и показывает карточку участника вместо участнического меню.
"""

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.render import staff_participant_card
from app.bot.states import StaffAward, StaffLookup
from app.models import Participant, Redemption, RedemptionStatus, Staff, StaffRole
from app.services.participants import find_by_qr_token, find_by_short_code
from app.services.points import award_manual, get_balance
from app.services.prizes import (
    RedeemStatus,
    active_prizes,
    cancel_redemption,
    confirm_redemption,
    create_redemption,
)
from app.services.staff import activate_staff, get_staff_by_tg
from app.utils import fmt_points

router = Router()


async def handle_staff_invite(message: Message, session: AsyncSession, token: str) -> None:
    staff = await activate_staff(
        session,
        token,
        tg_id=message.from_user.id,
        username=message.from_user.username,
    )
    if staff is None:
        await message.answer(
            "Ссылка недействительна или уже использована другим человеком.\n"
            "Попроси у координатора новую."
        )
        return

    lines = [
        f"Готово, {staff.name}.",
        f"Роль: <b>{staff.role_label}</b>",
        "",
        "Чтобы обслужить участника, наведи камеру телефона на его QR-код в боте.",
        "Если код не читается — команда /find и шестизначный номер.",
    ]
    if staff.role in {StaffRole.SUPERADMIN, StaffRole.ADMIN}:
        lines += ["", "Управление мероприятием — команда /admin"]
    await message.answer("\n".join(lines))


async def handle_participant_scan(
    message: Message, session: AsyncSession, staff: Staff | None, token: str
) -> None:
    """Организатор отсканировал личный QR участника."""
    if staff is None:
        # Обычный участник отсканировал чужой код — ничего не показываем.
        await message.answer(
            "Это личный код другого участника. Свой код — кнопка «Мой QR»."
        )
        return

    target = await find_by_qr_token(session, token)
    if target is None:
        await message.answer("Участник не найден. Возможно, код устарел.")
        return

    await _show_participant(message, session, staff, target)


async def _show_participant(
    message: Message, session: AsyncSession, staff: Staff, target: Participant
) -> None:
    if not target.is_registered:
        await message.answer(
            f"{target.full_name} ещё не завершил регистрацию в боте.\n"
            "Попроси его дойти до конца анкеты."
        )
        return

    card = await staff_participant_card(session, target)
    balance = await get_balance(session, target.id)

    if staff.can(StaffRole.CAN_REDEEM):
        prizes = await active_prizes(session)
        available = [p for p in prizes if p.in_stock and balance >= p.cost_points]
        if available:
            await message.answer(
                card, reply_markup=kb.staff_prizes_keyboard(target.id, prizes, balance)
            )
            return
        await message.answer(
            f"{card}\n\n<i>Доступных призов на этот баланс нет.</i>",
            reply_markup=kb.staff_prizes_keyboard(target.id, [], balance),
        )
        return

    await message.answer(card)


@router.message(Command("find"))
async def cmd_find(message: Message, state: FSMContext, staff: Staff | None) -> None:
    if staff is None:
        return
    await state.set_state(StaffLookup.short_code)
    await message.answer("Введи шестизначный код участника.")


@router.message(StaffLookup.short_code, F.text)
async def lookup_by_code(
    message: Message, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    if staff is None:
        await state.clear()
        return

    code = message.text.strip()
    target = await find_by_short_code(session, code)
    if target is None:
        await message.answer("Участник с таким кодом не найден. Попробуй ещё раз или /cancel.")
        return

    await state.clear()
    await _show_participant(message, session, staff, target)


@router.callback_query(F.data.startswith("give:"))
async def give_prize(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None, bot: Bot
) -> None:
    if staff is None or not staff.can(StaffRole.CAN_REDEEM):
        await callback.answer("Нет прав на выдачу призов", show_alert=True)
        return

    _, participant_id, prize_id = callback.data.split(":")
    target = await session.get(Participant, int(participant_id))
    if target is None:
        await callback.answer("Участник не найден", show_alert=True)
        return

    result = await create_redemption(session, target, int(prize_id), staff_id=staff.id)

    if not result.ok:
        await callback.answer(_redeem_error(result), show_alert=True)
        return

    await callback.answer("Запрос отправлен участнику")
    await callback.message.answer(
        f"⏳ Ждём подтверждения от <b>{target.full_name}</b>\n"
        f"Приз: {result.redemption.prize_title} — {fmt_points(result.redemption.cost_points)}\n\n"
        "Приз выдавай после того, как придёт подтверждение."
    )

    try:
        await bot.send_message(
            target.tg_id,
            f"🎁 Организатор предлагает выдать тебе:\n\n"
            f"<b>{result.redemption.prize_title}</b>\n"
            f"Спишется: <b>{fmt_points(result.redemption.cost_points)}</b>\n"
            f"Останется: <b>{fmt_points(result.balance - result.redemption.cost_points)}</b>\n\n"
            "Подтверди, если всё верно.",
            reply_markup=kb.confirm_redemption_keyboard(result.redemption.id),
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        # Участник заблокировал бота: подтверждение не доставить. Откатываем
        # резерв, чтобы товар не завис и организатор не ждал зря.
        await cancel_redemption(
            session, result.redemption, comment="Участник заблокировал бота"
        )
        await callback.message.answer(
            f"⚠️ {target.full_name} заблокировал бота — подтверждение не доставить.\n"
            "Выдача отменена, товар вернулся на склад."
        )


def _redeem_error(result) -> str:
    if result.status == RedeemStatus.NOT_ENOUGH_POINTS:
        return f"Не хватает {result.missing} б."
    if result.status == RedeemStatus.OUT_OF_STOCK:
        return "Приз закончился"
    if result.status == RedeemStatus.LIMIT_REACHED:
        return "Лимит на этот приз уже исчерпан"
    if result.status == RedeemStatus.HAS_PENDING:
        return "У участника уже есть неподтверждённый запрос"
    if result.status == RedeemStatus.CLOSED:
        return "Выдача призов остановлена"
    if result.status == RedeemStatus.INACTIVE:
        return "Приз выключен"
    if result.status == RedeemStatus.BLOCKED:
        return "Профиль участника заблокирован"
    return "Не получилось. Позови координатора."


@router.callback_query(F.data.startswith("rd:"))
async def resolve_redemption(
    callback: CallbackQuery,
    session: AsyncSession,
    participant: Participant,
    bot: Bot,
) -> None:
    """Участник подтверждает или отменяет списание."""
    _, action, redemption_id = callback.data.split(":")
    redemption = await session.get(Redemption, int(redemption_id))

    if redemption is None or redemption.participant_id != participant.id:
        await callback.answer("Запрос не найден", show_alert=True)
        return

    if redemption.status != RedemptionStatus.PENDING:
        # Кнопки под сообщением живут вечно — двойное нажатие или нажатие
        # после обработки вторым устройством не должно ничего ломать.
        await callback.answer("Этот запрос уже обработан", show_alert=True)
        return

    staff_tg = None
    if redemption.staff_id:
        staff_row = await session.get(Staff, redemption.staff_id)
        staff_tg = staff_row.tg_id if staff_row else None

    if action == "no":
        await cancel_redemption(session, redemption, comment="Отменено участником")
        await callback.answer("Отменено")
        await callback.message.edit_text("❌ Отменено. Баллы остались у тебя.")
        if staff_tg:
            await _notify(
                bot,
                staff_tg,
                f"❌ {participant.full_name} отменил выдачу «{redemption.prize_title}»",
            )
        return

    result = await confirm_redemption(session, redemption)

    if not result.ok:
        await callback.answer(_redeem_error(result), show_alert=True)
        await callback.message.edit_text(
            f"Не получилось списать баллы: {_redeem_error(result).lower()}"
        )
        if staff_tg:
            await _notify(
                bot,
                staff_tg,
                f"⚠️ Выдача «{redemption.prize_title}» для {participant.full_name} "
                f"не прошла: {_redeem_error(result).lower()}. Приз не выдавай.",
            )
        return

    await callback.answer("Готово")
    await callback.message.edit_text(
        f"✅ <b>{redemption.prize_title}</b>\n"
        f"Списано: {fmt_points(redemption.cost_points)}\n"
        f"Остаток: <b>{fmt_points(result.balance)}</b>\n\n"
        "Забирай приз у организатора!"
    )
    if staff_tg:
        await _notify(
            bot,
            staff_tg,
            f"✅ Подтверждено. Выдай <b>{redemption.prize_title}</b> — {participant.full_name}.\n"
            f"Остаток баллов: {fmt_points(result.balance)}",
        )


async def _notify(bot: Bot, tg_id: int, text: str) -> None:
    """Уведомление второй стороне. Если получатель заблокировал бота —
    молча пропускаем: транзакция уже сохранена, падать на ней нельзя."""
    try:
        await bot.send_message(tg_id, text)
    except (TelegramForbiddenError, TelegramBadRequest):
        pass


@router.callback_query(F.data.startswith("award:"))
async def award_start(
    callback: CallbackQuery, state: FSMContext, staff: Staff | None
) -> None:
    if staff is None or not staff.can(StaffRole.CAN_AWARD):
        await callback.answer("Нет прав", show_alert=True)
        return

    participant_id = int(callback.data.split(":")[1])
    await state.set_state(StaffAward.amount)
    await state.update_data(participant_id=participant_id)
    await callback.answer()
    await callback.message.answer(
        "Сколько баллов начислить? Введи число.\n"
        "Можно отрицательное, чтобы списать. Отмена — /cancel"
    )


@router.message(StaffAward.amount, F.text)
async def award_finish(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    staff: Staff | None,
    bot: Bot,
) -> None:
    if staff is None:
        await state.clear()
        return

    try:
        delta = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно целое число. Например: 10 или -5")
        return
    if delta == 0:
        await message.answer("Ноль начислять смысла нет.")
        return

    data = await state.get_data()
    target = await session.get(Participant, data["participant_id"])
    if target is None:
        await state.clear()
        await message.answer("Участник не найден.")
        return

    balance = await award_manual(
        session, target, delta, staff_id=staff.id, comment=f"Вручную: {staff.name}"
    )
    await state.clear()

    await message.answer(
        f"Готово. {target.full_name}: {delta:+d} → баланс {fmt_points(balance)}"
    )
    try:
        await bot.send_message(
            target.tg_id,
            f"{'➕' if delta > 0 else '➖'} Организатор изменил твой баланс: <b>{delta:+d}</b>\n"
            f"Сейчас у тебя <b>{fmt_points(balance)}</b>",
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        await message.answer(
            "⚠️ Уведомление не доставлено — участник заблокировал бота. "
            "Баллы при этом начислены."
        )


@router.message(Command("staff"))
async def cmd_staff(message: Message, session: AsyncSession) -> None:
    staff = await get_staff_by_tg(session, message.from_user.id)
    if staff is None:
        await message.answer("Ты не в списке организаторов.")
        return
    lines = [
        f"<b>{staff.name}</b>",
        f"Роль: {staff.role_label}",
        "",
        "Наведи камеру на QR участника, чтобы открыть его карточку.",
        "/find — поиск по шестизначному коду",
    ]
    if staff.role in {StaffRole.SUPERADMIN, StaffRole.ADMIN}:
        lines.append("/admin — управление мероприятием")
    await message.answer("\n".join(lines))
