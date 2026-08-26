"""Проверка логики админки в боте без Telegram.

    python -m scripts.smoke_admin
"""

import asyncio
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TEST_DB = BASE / "data" / "smoke-admin.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"

from app.bot.admin.fields import ParseError, display_value, parse_value  # noqa: E402
from app.bot.admin.specs import (  # noqa: E402
    PRIZE,
    SETTINGS,
    SPECS,
    WORKSHOP,
    ZONE,
    find_field,
)
from app.bot.admin.core import kind_for, load_item, load_items  # noqa: E402
from app.db import engine, init_db, session_scope  # noqa: E402
from app.models import Activity, ActivityKind, Prize, StaffRole  # noqa: E402
from app.services.event import get_event_settings  # noqa: E402
from app.services.exports import participants_csv, posters_zip  # noqa: E402
from app.utils import gen_activity_code  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


async def main() -> None:
    TEST_DB.unlink(missing_ok=True)
    await init_db()

    print("\n1. Разбор ввода")
    field = find_field(SPECS[PRIZE], "cost_points")
    check("число принимается", parse_value(field, " 15 ") == 15)
    try:
        parse_value(field, "дорого")
        check("текст вместо числа отклонён", False)
    except ParseError as exc:
        check("текст вместо числа отклонён", True, str(exc))
    try:
        parse_value(field, "-5")
        check("отрицательная цена отклонена", False)
    except ParseError:
        check("отрицательная цена отклонена", True)

    title = find_field(SPECS[PRIZE], "title")
    try:
        parse_value(title, "   ")
        check("пустое обязательное поле отклонено", False)
    except ParseError:
        check("пустое обязательное поле отклонено", True)

    desc = find_field(SPECS[PRIZE], "description")
    check("«-» очищает необязательное поле", parse_value(desc, "-") is None)

    url = find_field(SPECS[SETTINGS], "feedback_url")
    try:
        parse_value(url, "forms.yandex.ru")
        check("ссылка без протокола отклонена", False)
    except ParseError:
        check("ссылка без протокола отклонена", True)
    check("нормальная ссылка проходит",
          parse_value(url, "https://forms.yandex.ru/x") == "https://forms.yandex.ru/x")

    from app.bot.admin.fields import PERCENT, Field

    xf = Field("x", "X", PERCENT)
    check("проценты с запятой", parse_value(xf, "42,5") == 42.5)
    try:
        parse_value(xf, "150")
        check("больше 100 отклонено", False)
    except ParseError:
        check("больше 100 отклонено", True)

    tf = find_field(SPECS[WORKSHOP], "starts_at")
    parsed = parse_value(tf, "14:30")
    check("время разобрано", parsed is not None, display_value(tf, parsed))
    check("время показывается обратно как введено", display_value(tf, parsed) == "14:30")
    try:
        parse_value(tf, "25:99")
        check("некорректное время отклонено", False)
    except ParseError:
        check("некорректное время отклонено", True)

    print("\n2. Создание и правка записей")
    async with session_scope() as session:
        spec = SPECS[ZONE]
        check("kind для зоны", kind_for(spec) == ActivityKind.ZONE)
        check("kind для МК", kind_for(SPECS[WORKSHOP]) == ActivityKind.WORKSHOP)
        check("kind для приза не задан", kind_for(SPECS[PRIZE]) is None)

        zone = Activity(kind=ActivityKind.ZONE, code=gen_activity_code(),
                        title="Танцпол", points=1)
        workshop = Activity(kind=ActivityKind.WORKSHOP, code=gen_activity_code(),
                            title="Каллиграфия", points=3)
        prize = Prize(title="Шоппер", cost_points=5, stock_total=10, stock_left=10)
        session.add_all([zone, workshop, prize])
        await session.flush()
        zid, wid, pid = zone.id, workshop.id, prize.id

    async with session_scope() as session:
        zones = await load_items(session, SPECS[ZONE])
        workshops = await load_items(session, SPECS[WORKSHOP])
        check("список зон не смешан с МК", len(zones) == 1 and zones[0].title == "Танцпол")
        check("список МК отдельный", len(workshops) == 1 and workshops[0].title == "Каллиграфия")

        prize = await load_item(session, SPECS[PRIZE], pid)
        cost = find_field(SPECS[PRIZE], "cost_points")
        setattr(prize, cost.key, parse_value(cost, "12"))
        await session.flush()

    async with session_scope() as session:
        prize = await load_item(session, SPECS[PRIZE], pid)
        check("цена сохранилась", prize.cost_points == 12, f"{prize.cost_points} б.")

        settings_obj = await load_item(session, SPECS[SETTINGS], 1)
        check("настройки грузятся как singleton", settings_obj.id == 1)
        settings_obj.is_scanning_open = not settings_obj.is_scanning_open
        await session.flush()
        flipped = settings_obj.is_scanning_open

    async with session_scope() as session:
        event = await get_event_settings(session)
        check("рубильник переключился и сохранился", event.is_scanning_open == flipped)

    print("\n3. Удаление")
    async with session_scope() as session:
        zone = await load_item(session, SPECS[ZONE], zid)
        await session.delete(zone)
        await session.flush()
    async with session_scope() as session:
        check("зона удалена", await load_item(session, SPECS[ZONE], zid) is None)
        check("мастер-класс не задет", await load_item(session, SPECS[WORKSHOP], wid) is not None)

    print("\n4. Доступ администраторов")
    from app.config import settings as cfg
    from app.services.staff import create_staff, resolve_staff

    saved = cfg.admin_tg_ids
    cfg.admin_tg_ids = "555001, 555002"
    try:
        async with session_scope() as session:
            auto = await resolve_staff(session, 555001, "coordinator")
            check("админ из .env создался сам", auto is not None)
            check("роль суперадмин", auto.role == StaffRole.SUPERADMIN)
            check("помечен как неприкосновенный", auto.is_env_admin)

            stranger = await resolve_staff(session, 999999, None)
            check("посторонний не получает прав", stranger is None)

            invited = await create_staff(session, name="Волонтёр", role=StaffRole.ADMIN)
            invited.tg_id = 777001
            invited.is_active = True
            await session.flush()
            check("обычный организатор не неприкосновенный", not invited.is_env_admin)

        async with session_scope() as session:
            # пытаемся понизить и отключить — модель должна остаться защищённой
            auto = await resolve_staff(session, 555001, "coordinator")
            auto.role = StaffRole.ADMIN
            auto.is_active = False
            await session.flush()

        async with session_scope() as session:
            auto = await resolve_staff(session, 555001, "coordinator")
            check("права восстанавливаются после понижения", auto.role == StaffRole.SUPERADMIN)
            check("активность восстанавливается", auto.is_active)

        cfg.admin_tg_ids = ""
        async with session_scope() as session:
            demoted = await resolve_staff(session, 555001, None)
            check("убрали из .env — остаётся обычным организатором",
                  demoted is not None and not demoted.is_env_admin)

        async with session_scope() as session:
            active = await resolve_staff(session, 777001, None)
            check("приглашённый организатор проходит", active is not None)
            active.is_active = False
            await session.flush()

        async with session_scope() as session:
            check("отключённый организатор не проходит",
                  await resolve_staff(session, 777001, None) is None)
    finally:
        cfg.admin_tg_ids = saved

    print("\n5. Выгрузки")
    async with session_scope() as session:
        csv_bytes = await participants_csv(session)
        check("CSV с BOM для Excel", csv_bytes.startswith("﻿".encode("utf-8")))
        check("CSV содержит заголовки", "Фамилия" in csv_bytes.decode("utf-8"))

        payload, count = await posters_zip(session, ActivityKind.WORKSHOP)
        check("ZIP плакатов собран", count == 1 and len(payload) > 10_000,
              f"{count} шт, {len(payload)//1024} КБ")

        empty, zero = await posters_zip(session, ActivityKind.ZONE)
        check("пустой ZIP не падает", zero == 0)

    await engine.dispose()  # Windows не даёт удалить открытый файл базы
    TEST_DB.unlink(missing_ok=True)

    print()
    if failures:
        print(f"ПРОВАЛЕНО: {len(failures)}")
        for name in failures:
            print(f"  - {name}")
        sys.exit(1)
    print("Все проверки пройдены.")


if __name__ == "__main__":
    asyncio.run(main())
