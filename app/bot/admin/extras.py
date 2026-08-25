"""Остальные разделы админки: факультеты, организаторы, поиск, выгрузки, карта."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.admin import keyboards as akb
from app.bot.admin.core import (
    ask_input,
    discard_input,
    finish_input,
    is_admin,
    render_screen,
)
from app.bot.admin.states import AdminFaculty, AdminFind, AdminMap, AdminStaff
from app.config import MEDIA_DIR
from app.models import ActivityKind, Faculty, Staff, StaffRole
from app.services.event import get_event_settings
from app.services.exports import participants_csv, posters_zip
from app.services.maps import map_image_path, render_grid_map
from app.services.participants import find_by_short_code, find_by_student_id
from app.services.qr import staff_link
from app.services.staff import create_staff, list_staff
from app.utils import gen_token

router = Router()

MAX_MAP_BYTES = 12 * 1024 * 1024


def _superadmin(staff: Staff | None) -> bool:
    return staff is not None and staff.is_active and staff.role == StaffRole.SUPERADMIN


# --- Факультеты ---


@router.callback_query(F.data == "ad:fac")
async def faculties(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await _show_faculties(callback, session)


async def _faculties_screen(session: AsyncSession) -> tuple[str, object]:
    rows = await session.scalars(select(Faculty).order_by(Faculty.sort_order, Faculty.id))
    items = list(rows.all())
    if not items:
        text = (
            "<b>🏫 Факультеты</b>\n\nСписок пуст — бот попросит участника "
            "вписать факультет вручную."
        )
    else:
        text = (
            "<b>🏫 Факультеты</b>\n\nПоказываются кнопками при регистрации.\n"
            "Нажми на факультет, чтобы удалить."
        )
    return text, akb.faculties_kb(items)


async def _show_faculties(target, session: AsyncSession) -> None:
    text, markup = await _faculties_screen(session)
    await render_screen(target, text, markup)


@router.callback_query(F.data == "ad:facadd")
async def faculty_add(callback: CallbackQuery, state: FSMContext, staff: Staff | None) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminFaculty.title)
    await callback.answer()
    await ask_input(
        callback,
        state,
        "Название факультета.\n\nМожно прислать несколько строк — добавлю все.\nОтмена — /cancel",
    )


@router.message(AdminFaculty.title, F.text)
async def faculty_save(
    message: Message, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await state.clear()
        return

    titles = [line.strip() for line in message.text.split("\n") if line.strip()]
    existing = {
        row.title for row in (await session.scalars(select(Faculty))).all()
    }
    count = len(existing)
    added = 0
    for title in titles:
        if len(title) < 2 or len(title) > 200 or title in existing:
            continue
        count += 1
        session.add(Faculty(title=title, sort_order=count * 10))
        existing.add(title)
        added += 1

    await session.flush()
    await state.clear()
    text, markup = await _faculties_screen(session)
    await finish_input(message, state, f"✅ Добавлено: {added}\n\n{text}", markup)


@router.callback_query(F.data.startswith("ad:facdel:"))
async def faculty_delete(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return
    faculty = await session.get(Faculty, int(callback.data.split(":")[2]))
    if faculty is not None:
        await session.delete(faculty)
        await session.flush()
    await callback.answer("Удалено")
    await _show_faculties(callback, session)


# --- Организаторы ---


@router.callback_query(F.data == "ad:staff")
async def staff_list(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    if not _superadmin(staff):
        await callback.answer("Только для суперадмина", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await _show_staff(callback, session)


async def _staff_screen(session: AsyncSession) -> tuple[str, object]:
    members = await list_staff(session)
    text = (
        "<b>👥 Организаторы</b>\n\n"
        "🔑 постоянный из настроек · ✅ подключён · "
        "⏳ ждёт перехода по ссылке · ⏸ отключён"
    )
    return text, akb.staff_kb(members)


async def _show_staff(target, session: AsyncSession) -> None:
    text, markup = await _staff_screen(session)
    await render_screen(target, text, markup)


@router.callback_query(F.data == "ad:stadd")
async def staff_add(callback: CallbackQuery, staff: Staff | None) -> None:
    if not _superadmin(staff):
        await callback.answer("Только для суперадмина", show_alert=True)
        return
    await callback.answer()
    await render_screen(
        callback,
        "Какая роль у нового организатора?",
        akb.new_staff_roles_kb(StaffRole.CHOICES),
    )


@router.callback_query(F.data.startswith("ad:stnew:"))
async def staff_add_name(
    callback: CallbackQuery, state: FSMContext, staff: Staff | None
) -> None:
    if not _superadmin(staff):
        await callback.answer("Только для суперадмина", show_alert=True)
        return
    role = callback.data.split(":")[2]
    if role not in StaffRole.LABELS:
        await callback.answer("Неизвестная роль")
        return
    await state.set_state(AdminStaff.name)
    await state.update_data(role=role)
    await callback.answer()
    await ask_input(
        callback,
        state,
        f"Роль: <b>{StaffRole.LABELS[role]}</b>\n\n"
        "Теперь имя и фамилия организатора.\n\nОтмена — /cancel",
    )


@router.message(AdminStaff.name, F.text)
async def staff_save(
    message: Message, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    if not _superadmin(staff):
        await state.clear()
        return
    name = message.text.strip()
    if len(name) < 2 or len(name) > 200:
        await message.answer("От 2 до 200 символов. Ещё раз или /cancel")
        return

    data = await state.get_data()
    member = await create_staff(
        session, name=name, role=data.get("role", StaffRole.PRIZE_DESK)
    )
    await state.clear()
    # Ссылку оставляем отдельным сообщением — её пересылают новому организатору.
    await message.answer(
        f"✅ <b>{member.name}</b> добавлен, роль: {member.role_label}\n\n"
        f"Перешли ему эту ссылку — она сработает один раз и только для него:\n"
        f"{staff_link(member.invite_token)}"
    )
    text, markup = await _staff_screen(session)
    await finish_input(message, state, text, markup)


@router.callback_query(F.data.startswith("ad:st:"))
async def staff_card(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None
) -> None:
    if not _superadmin(staff):
        await callback.answer("Только для суперадмина", show_alert=True)
        return
    member = await session.get(Staff, int(callback.data.split(":")[2]))
    if member is None:
        await callback.answer("Не найдено")
        return

    await callback.answer()
    status = "не подключён" if not member.is_activated else (
        f"@{member.tg_username}" if member.tg_username else str(member.tg_id)
    )
    lines = [
        f"<b>{member.name}</b>",
        f"Роль: {member.role_label}",
        f"Telegram: {status}",
        f"Состояние: {'активен' if member.is_active else 'отключён'}",
    ]
    if member.is_env_admin:
        lines += [
            "",
            "🔑 <i>Постоянный доступ из настроек сервера. "
            "Роль и включение отсюда не меняются — только правкой ADMIN_TG_IDS.</i>",
        ]
    text = "\n".join(lines)
    await render_screen(callback, text, akb.staff_card_kb(member))


@router.callback_query(F.data.startswith("ad:stlink:"))
async def staff_send_link(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None
) -> None:
    if not _superadmin(staff):
        await callback.answer("Только для суперадмина", show_alert=True)
        return
    member = await session.get(Staff, int(callback.data.split(":")[2]))
    if member is None:
        await callback.answer("Не найдено")
        return
    if member.is_env_admin:
        await callback.answer("Этот доступ задан в настройках сервера", show_alert=True)
        return

    # Перевыпускаем токен: старая ссылка могла утечь или уже быть использована.
    member.invite_token = gen_token()
    member.tg_id = None
    member.tg_username = None
    member.activated_at = None
    await session.flush()

    await callback.answer("Новая ссылка готова")
    await callback.message.answer(
        f"Ссылка для <b>{member.name}</b> ({member.role_label}).\n"
        f"Старая больше не работает.\n\n{staff_link(member.invite_token)}"
    )


@router.callback_query(F.data.startswith("ad:strole:"))
async def staff_roles(callback: CallbackQuery, staff: Staff | None) -> None:
    if not _superadmin(staff):
        await callback.answer("Только для суперадмина", show_alert=True)
        return
    member_id = int(callback.data.split(":")[2])
    await callback.answer()
    await render_screen(callback, "Выбери роль:", akb.roles_kb(member_id, StaffRole.CHOICES))


@router.callback_query(F.data.startswith("ad:strset:"))
async def staff_set_role(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None
) -> None:
    if not _superadmin(staff):
        await callback.answer("Только для суперадмина", show_alert=True)
        return
    _, _, member_id, role = callback.data.split(":")
    member = await session.get(Staff, int(member_id))
    if member is not None and member.is_env_admin:
        await callback.answer("Роль задана в настройках сервера", show_alert=True)
        await _show_staff(callback, session)
        return
    if member is not None and role in StaffRole.LABELS:
        member.role = role
        await session.flush()
        await callback.answer(f"Роль: {member.role_label}")
    else:
        await callback.answer("Не получилось")
    await _show_staff(callback, session)


@router.callback_query(F.data.startswith("ad:stoff:"))
async def staff_toggle(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None
) -> None:
    if not _superadmin(staff):
        await callback.answer("Только для суперадмина", show_alert=True)
        return
    member = await session.get(Staff, int(callback.data.split(":")[2]))
    if member is not None and member.is_env_admin:
        await callback.answer("Нельзя отключить: доступ задан на сервере", show_alert=True)
    elif member is not None:
        member.is_active = not member.is_active
        await session.flush()
        await callback.answer("Включён" if member.is_active else "Отключён")
    await _show_staff(callback, session)


@router.callback_query(F.data.startswith("ad:stdel:"))
async def staff_delete(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None
) -> None:
    if not _superadmin(staff):
        await callback.answer("Только для суперадмина", show_alert=True)
        return
    member = await session.get(Staff, int(callback.data.split(":")[2]))
    if member is None:
        await callback.answer("Не найдено")
    elif member.id == staff.id:
        await callback.answer("Себя удалить нельзя", show_alert=True)
    elif member.is_env_admin:
        await callback.answer("Доступ задан на сервере — уберите ID из .env", show_alert=True)
    else:
        await session.delete(member)
        await session.flush()
        await callback.answer("Удалён")
    await _show_staff(callback, session)


# --- Поиск участника ---


@router.callback_query(F.data == "ad:find")
async def find_start(callback: CallbackQuery, state: FSMContext, staff: Staff | None) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminFind.query)
    await callback.answer()
    await ask_input(
        callback,
        state,
        "Пришли шестизначный код участника или номер студенческого.\n\nОтмена — /cancel",
    )


@router.message(AdminFind.query, F.text)
async def find_run(
    message: Message, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await state.clear()
        return

    query = message.text.strip()
    target = await find_by_short_code(session, query)
    if target is None:
        target = await find_by_student_id(session, query)
    if target is None:
        await message.answer("Не нашёл. Проверь код или пришли номер студенческого. /cancel")
        return

    await state.clear()
    await discard_input(message, state)
    from app.bot.handlers.staff import _show_participant

    await _show_participant(message, session, staff, target)


# --- Выгрузки ---


@router.callback_query(F.data == "ad:export")
async def exports(callback: CallbackQuery, state: FSMContext, staff: Staff | None) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await render_screen(
        callback,
        "<b>📤 Выгрузки</b>\n\nCSV открывается в Excel. "
        "Архивы с плакатами — готовые листы A4 для печати.",
        akb.exports_menu(),
    )


@router.callback_query(F.data.startswith("ad:exp:"))
async def do_export(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return

    what = callback.data.split(":")[2]
    await callback.answer("Готовлю файл…")

    if what == "csv":
        payload = await participants_csv(session)
        await callback.message.answer_document(
            BufferedInputFile(payload, filename="festart-uchastniki.csv"),
            caption="Участники со баллами и числом пройденных зон.",
        )
        return

    kind = ActivityKind.ZONE if what == "zones" else ActivityKind.WORKSHOP
    payload, count = await posters_zip(session, kind)
    if not count:
        await callback.message.answer("Нечего выгружать — нет активных записей.")
        return
    name = "plakaty-zony.zip" if what == "zones" else "plakaty-mk.zip"
    await callback.message.answer_document(
        BufferedInputFile(payload, filename=name),
        caption=f"Плакатов внутри: {count}. Формат A4, можно сразу в печать.",
    )


# --- Карта ---


@router.callback_query(F.data == "ad:map")
async def map_start(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminMap.photo)
    await callback.answer()

    event = await get_event_settings(session)
    current = event.map_image or "не загружена"
    await ask_input(
        callback,
        state,
        f"<b>🗺 Карта площадки</b>\n\n"
        f"Сейчас: <code>{current}</code>\n\n"
        f"Пришли новую картинку <b>файлом</b> — так Telegram не испортит её сжатием.\n"
        f"Оптимальная ширина 1280 пикселей.\n\n"
        f"Позиции меток — в карточке каждой зоны, поля X и Y: проценты от левого "
        f"края (X) и от верха (Y). Чтобы не угадывать, нажми «🗺 Карта с сеткой» — "
        f"пришлю твою карту с линейкой, по ней легко прикинуть цифры.\n\n"
        f"Отмена — /cancel",
    )


@router.callback_query(F.data == "ad:mapgrid")
async def map_grid(
    callback: CallbackQuery, session: AsyncSession, staff: Staff | None
) -> None:
    """Карта с сеткой 10% — чтобы X/Y зон ставились по глазомеру, а не наугад."""
    if not is_admin(staff):
        await callback.answer("Нет доступа", show_alert=True)
        return

    event = await get_event_settings(session)
    base = map_image_path(event.map_image)
    if base is None:
        await callback.answer("Сначала загрузи карту", show_alert=True)
        return

    await callback.answer()
    image = render_grid_map(base)
    await callback.message.answer_photo(
        BufferedInputFile(image, filename="map-grid.jpg"),
        caption=(
            "Красные линии — каждые 10%.\n"
            "Числа сверху = X (от левого края), числа слева = Y (от верха).\n\n"
            "Пример: зона у левого верхнего угла — примерно X=15, Y=20.\n"
            "Впиши их в карточку зоны, потом проверь карту глазами участника."
        ),
    )


@router.message(AdminMap.photo, F.document | F.photo)
async def map_upload(
    message: Message, state: FSMContext, session: AsyncSession, staff: Staff | None
) -> None:
    if not is_admin(staff):
        await state.clear()
        return

    if message.document:
        file_id = message.document.file_id
        size = message.document.file_size or 0
        name = (message.document.file_name or "").lower()
        if not name.endswith((".jpg", ".jpeg", ".png", ".webp")):
            await message.answer("Нужна картинка: JPG, PNG или WebP.")
            return
        suffix = ".png" if name.endswith(".png") else (
            ".webp" if name.endswith(".webp") else ".jpg"
        )
    else:
        largest = message.photo[-1]
        file_id = largest.file_id
        size = largest.file_size or 0
        suffix = ".jpg"

    if size > MAX_MAP_BYTES:
        await message.answer("Файл больше 12 МБ. Уменьши и пришли снова.")
        return

    filename = f"map{suffix}"
    await message.bot.download(file_id, destination=MEDIA_DIR / filename)

    event = await get_event_settings(session)
    event.map_image = filename
    await session.flush()
    await state.clear()

    note = ""
    if message.photo:
        note = "\n\n<i>Прислано фото — Telegram его сжал. Для качества пришли файлом.</i>"
    await finish_input(message, state, f"✅ Карта обновлена.{note}", akb.map_kb())
