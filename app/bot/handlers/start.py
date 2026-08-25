"""Точка входа: /start, разбор QR-кодов из deep-link и регистрация."""

import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.handlers.menu import drop_hub, send_hub
from app.bot.states import (
    HUB_KEY,
    PRESERVED_KEYS,
    Registration,
    VIEW_KEY,
    VIEW_PARTICIPANT,
    VIEW_STAFF,
    reset_state,
)
from app.models import Participant, Staff, StaffRole
from app.services.event import get_event_settings
from app.services.participants import (
    complete_registration,
    get_faculties,
    is_student_id_taken,
)
from app.services.points import ScanStatus, register_scan
from app.utils import PREFIX_ACTIVITY, PREFIX_PARTICIPANT, PREFIX_STAFF, fmt_points

router = Router()

ADMIN_HINT_ROLES = {StaffRole.SUPERADMIN, StaffRole.ADMIN}

NAME_RE = re.compile(r"^[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z\- ]{0,49}$")
STUDENT_ID_RE = re.compile(r"^[A-Za-z0-9\-/]{3,32}$")


async def _preserve(state: FSMContext, **updates) -> None:
    """Перезаписать FSM-данные, сохранив служебные ключи (id хаба)."""
    data = await state.get_data()
    preserved = {k: v for k, v in data.items() if k in PRESERVED_KEYS}
    preserved.update(updates)
    await state.set_data(preserved)


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session: AsyncSession,
    participant: Participant,
    staff: Staff | None,
) -> None:
    payload = (command.args or "").strip()

    if payload.startswith(f"{PREFIX_STAFF}_"):
        from app.bot.handlers.staff import handle_staff_invite

        await reset_state(state)
        await handle_staff_invite(message, session, payload[2:])
        return

    if payload.startswith(f"{PREFIX_PARTICIPANT}_"):
        from app.bot.handlers.staff import handle_participant_scan

        await reset_state(state)
        await handle_participant_scan(message, session, staff, payload[2:])
        return

    if payload.startswith(f"{PREFIX_ACTIVITY}_"):
        # Регистрацию не трогаем: человек мог сканировать зону прямо
        # посреди анкеты — код досчитается после её завершения.
        await reset_state(state, keep_registration=True)
        await _handle_activity_scan(message, state, session, participant, payload[2:])
        return

    await reset_state(state, keep_registration=True)
    await _greet(message, state, session, participant, staff)


async def _preserve(state: FSMContext, **updates) -> None:
    """Перезаписать FSM-данные, сохранив служебные ключи (id хаба)."""
    data = await state.get_data()
    preserved = {k: v for k, v in data.items() if k == HUB_KEY}
    preserved.update(updates)
    await state.set_data(preserved)


async def _greet(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    participant: Participant,
    staff: Staff | None = None,
) -> None:
    event = await get_event_settings(session)

    if staff is not None and staff.is_active:
        view = (await state.get_data()).get(VIEW_KEY)
        if view == VIEW_STAFF:
            await _preserve(state, **{VIEW_KEY: VIEW_STAFF})
            await _send_staff_card(message, staff)
            return
        if view == VIEW_PARTICIPANT:
            await _participant_greet(
                message, state, session, participant, event, is_staff=True
            )
            return
        # Режим ещё не выбран — спрашиваем. Прежний хаб убираем,
        # чтобы чат не засорялся.
        await drop_hub(message, state)
        await state.set_data({})
        await message.answer(
            f"<b>{staff.name}</b> · {staff.role_label}\n\nВ каком режиме зайти?",
            reply_markup=kb.mode_choice_keyboard(),
        )
        return

    await _participant_greet(message, state, session, participant, event)


async def _participant_greet(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    participant: Participant,
    event,
    *,
    is_staff: bool = False,
) -> None:
    if participant.is_registered:
        updates = {VIEW_KEY: VIEW_PARTICIPANT} if is_staff else {}
        await _preserve(state, **updates)
        await send_hub(message, state, session, participant, is_staff=is_staff)
        return

    await state.set_data({})
    await message.answer(event.welcome_text)
    await _ask_first_name(message, state, session)


async def _send_staff_card(message: Message, staff: Staff) -> None:
    hint = "\n\nУправление мероприятием — /admin" if staff.role in ADMIN_HINT_ROLES else ""
    await message.answer(
        f"<b>{staff.name}</b>\nРоль: {staff.role_label}\n\n"
        "Наведи камеру на QR участника, чтобы открыть его карточку.\n"
        "/find — поиск по коду" + hint,
        reply_markup=kb.switch_to_participant_keyboard(),
    )


@router.callback_query(F.data == "mode:staff")
async def cb_mode_staff(
    callback: CallbackQuery, state: FSMContext, staff: Staff | None
) -> None:
    if staff is None or not staff.is_active:
        await callback.answer("Этот раздел только для организаторов", show_alert=True)
        return
    await state.set_data({VIEW_KEY: VIEW_STAFF})
    await callback.answer()
    if callback.message:
        hint = (
            "\n\nУправление мероприятием — /admin"
            if staff.role in ADMIN_HINT_ROLES
            else ""
        )
        text = (
            f"<b>{staff.name}</b>\nРоль: {staff.role_label}\n\n"
            "Наведи камеру на QR участника, чтобы открыть его карточку.\n"
            "/find — поиск по коду" + hint
        )
        try:
            # Редактируем экран выбора на месте — сообщений не плодим.
            await callback.message.edit_text(
                text, reply_markup=kb.switch_to_participant_keyboard()
            )
        except TelegramBadRequest:
            await callback.message.answer(
                text, reply_markup=kb.switch_to_participant_keyboard()
            )


@router.callback_query(F.data == "mode:participant")
async def cb_mode_participant(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    participant: Participant,
    staff: Staff | None,
) -> None:
    is_staff = staff is not None and staff.is_active
    await state.update_data(**{VIEW_KEY: VIEW_PARTICIPANT} if is_staff else {})
    await callback.answer()
    if callback.message:
        # Убираем карточку организатора, чтобы в чате остался только хаб.
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass
        event = await get_event_settings(session)
        await _participant_greet(
            callback.message, state, session, participant, event, is_staff=is_staff
        )


async def _handle_activity_scan(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    participant: Participant,
    code: str,
) -> None:
    """QR зоны. Если человек ещё не зарегистрирован — сначала регистрация,
    код запоминаем и начисляем сразу после, иначе он решит, что баллы потерялись."""
    if not participant.is_registered:
        participant.pending_activity_code = code
        await message.answer(
            "Почти! Сначала быстрая регистрация — это меньше минуты.\n"
            "Баллы за зону начислим сразу после неё."
        )
        await _ask_first_name(message, state, session)
        return

    result = await register_scan(session, participant, code)
    await message.answer(_scan_text(result), reply_markup=kb.menu_button_keyboard())


def _scan_text(result) -> str:
    if result.status == ScanStatus.OK:
        lines = [
            f"✅ <b>{result.activity.title}</b>",
            f"+{fmt_points(result.points)}",
        ]
        if result.bonus:
            lines.append(f"🎉 Бонус за все зоны: +{fmt_points(result.bonus)}")
        lines.append("")
        lines.append(f"Баланс: <b>{fmt_points(result.balance)}</b>")
        if result.total_zones:
            lines.append(f"Зоны: {result.visited_zones} из {result.total_zones}")
        return "\n".join(lines)

    if result.status == ScanStatus.ALREADY:
        return (
            f"Ты уже был здесь: <b>{result.activity.title}</b>\n"
            f"Баланс: <b>{fmt_points(result.balance)}</b>"
        )
    if result.status == ScanStatus.INACTIVE:
        return "Эта зона сейчас неактивна. Загляни позже или спроси организатора."
    if result.status == ScanStatus.CLOSED:
        return "Начисление баллов уже закрыто. Спасибо, что был с нами!"
    if result.status == ScanStatus.BLOCKED:
        return "Твой профиль приостановлен. Подойди к стойке организаторов."
    return "Код не распознан. Попробуй ещё раз или подойди к организатору."


# --- Регистрация ---


async def _ask_first_name(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    event = await get_event_settings(session)
    if not event.is_registration_open:
        await message.answer("Регистрация закрыта. Подойди к стойке организаторов.")
        return
    await state.set_state(Registration.first_name)
    await message.answer("Как тебя зовут? Напиши имя.", reply_markup=kb.remove_keyboard)


@router.message(Registration.first_name, F.text)
async def reg_first_name(message: Message, state: FSMContext) -> None:
    value = message.text.strip()
    if not NAME_RE.match(value):
        await message.answer("Похоже на опечатку. Напиши имя буквами, без цифр.")
        return
    await state.update_data(first_name=value)
    await state.set_state(Registration.last_name)
    await message.answer("Отлично. Теперь фамилия.")


@router.message(Registration.last_name, F.text)
async def reg_last_name(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    value = message.text.strip()
    if not NAME_RE.match(value):
        await message.answer("Похоже на опечатку. Напиши фамилию буквами, без цифр.")
        return
    await state.update_data(last_name=value)

    faculties = await get_faculties(session)
    if not faculties:
        await state.set_state(Registration.faculty_other)
        await message.answer("С какого ты факультета? Напиши название.")
        return

    await state.set_state(Registration.faculty)
    await message.answer(
        "С какого ты факультета?", reply_markup=kb.faculties_keyboard(faculties)
    )


@router.callback_query(Registration.faculty, F.data.startswith("fac:"))
async def reg_faculty(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    choice = callback.data.split(":", 1)[1]
    await callback.answer()

    if choice == "other":
        await state.set_state(Registration.faculty_other)
        await callback.message.answer("Напиши название факультета.")
        return

    faculties = await get_faculties(session)
    selected = next((f for f in faculties if str(f.id) == choice), None)
    if selected is None:
        await callback.message.answer("Такого варианта нет, выбери из списка.")
        return

    await state.update_data(faculty_id=selected.id, faculty_other=None)
    if callback.message:
        await callback.message.edit_text(f"Факультет: <b>{selected.title}</b>")
    await _ask_student_id(callback.message, state)


@router.message(Registration.faculty_other, F.text)
async def reg_faculty_other(message: Message, state: FSMContext) -> None:
    value = message.text.strip()
    if len(value) < 2 or len(value) > 200:
        await message.answer("Слишком коротко или слишком длинно. Напиши ещё раз.")
        return
    await state.update_data(faculty_id=None, faculty_other=value)
    await _ask_student_id(message, state)


async def _ask_student_id(message: Message, state: FSMContext) -> None:
    await state.set_state(Registration.student_id)
    await message.answer(
        "Последний шаг: номер студенческого билета.\n"
        "Он нужен, чтобы отличать участников друг от друга."
    )


@router.message(Registration.student_id, F.text)
async def reg_student_id(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    participant: Participant,
    staff: Staff | None,
) -> None:
    value = message.text.strip()
    if not STUDENT_ID_RE.match(value):
        await message.answer(
            "Не похоже на номер студенческого. Введи его так, как написано в билете."
        )
        return

    if await is_student_id_taken(session, value, exclude_participant_id=participant.id):
        await message.answer(
            "Этот номер уже зарегистрирован.\n"
            "Проверь, нет ли опечатки. Если всё верно — подойди к организаторам."
        )
        return

    data = await state.get_data()
    bonus = await complete_registration(
        session,
        participant,
        first_name=data["first_name"],
        last_name=data["last_name"],
        faculty_id=data.get("faculty_id"),
        faculty_other=data.get("faculty_other"),
        student_id=value,
    )
    await state.clear()

    event = await get_event_settings(session)
    text = event.registration_done_text.format(
        name=participant.first_name or "",
        points=fmt_points(bonus),
    )
    if bonus:
        text += f"\n\nПриветственный бонус: <b>+{fmt_points(bonus)}</b>"
    is_staff = staff is not None and staff.is_active
    await message.answer(text)
    await send_hub(message, state, session, participant, is_staff=is_staff)

    # Досчитываем зону, отсканированную до регистрации.
    pending = participant.pending_activity_code
    if pending:
        participant.pending_activity_code = None
        result = await register_scan(session, participant, pending)
        await message.answer(_scan_text(result))


@router.message(Command("menu"))
async def cmd_menu(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    participant: Participant,
    staff: Staff | None,
) -> None:
    await _greet(message, state, session, participant, staff)


@router.message(Command("id"))
async def cmd_id(message: Message, staff: Staff | None) -> None:
    """Свой Telegram ID. Нужен, чтобы координатор вписал человека
    в ADMIN_TG_IDS — сам человек свой числовой id иначе не узнает."""
    lines = [
        "Твой Telegram ID:",
        f"<code>{message.from_user.id}</code>",
        "",
        "Нажми на число, чтобы скопировать, и отправь координатору.",
    ]
    if staff is not None and staff.is_active:
        lines += ["", f"Ты в списке организаторов: <b>{staff.role_label}</b>"]
    await message.answer("\n".join(lines))


@router.message(Command("cancel"))
async def cmd_cancel(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    participant: Participant,
    staff: Staff | None,
) -> None:
    await state.clear()
    is_staff = staff is not None and staff.is_active
    if not is_staff and not participant.is_registered:
        await message.answer("Отменил. Чтобы начать заново — /start")
        return
    # Возврат в свой режим: организатору снова предложим выбор,
    # участник попадёт в меню.
    await _greet(message, state, session, participant, staff)
