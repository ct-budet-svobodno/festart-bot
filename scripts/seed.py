"""Начальное наполнение базы.

    python -m scripts.seed              # факультеты + демо-зоны, МК и призы
    python -m scripts.seed --faculties  # только факультеты, без демо-контента

На боевом сервере нужен второй вариант: справочник факультетов без него
пустой и при регистрации бот попросит вписать факультет руками, а зоны
и призы вы заводите свои через /admin.

Повторный запуск ничего не дублирует.
"""

import argparse
import asyncio
from datetime import timedelta

from sqlalchemy import func, select

from app.db import init_db, session_scope
from app.models import Activity, ActivityKind, Faculty, Prize, Staff, StaffRole, utcnow
from app.services.event import get_event_settings
from app.utils import gen_activity_code, gen_token

FACULTIES = [
    "ИТиАБД",
    "ВШУ",
    "СНиМК",
    "ФинФак",
    "ЮрФак",
    "МЭО",
    "ФЭБ",
    "НАБ",
]

ZONES = [
    ("Фотозона у главного входа", 1),
    ("Танцпол", 1),
    ("Кибер-арена", 1),
    ("Гончарная мастерская", 1),
    ("Спортивный уголок", 1),
    ("Фудкорт и настолки", 1),
]

WORKSHOPS = [
    (
        "Мастер-класс по каллиграфии",
        "Научим держать перо и выводить первые буквы. Все материалы дадим, "
        "уносить работы можно с собой.",
        "Аудитория 204",
        2,
        0,
    ),
    (
        "Основы фотографии на телефон",
        "Композиция, свет и обработка. Приходи со своим смартфоном.",
        "Холл второго этажа",
        3,
        90,
    ),
    (
        "Импровизация и сценречь",
        "Разминка для голоса и упражнения из актёрского тренинга. Без подготовки.",
        "Актовый зал",
        1,
        180,
    ),
]

PRIZES = [
    ("Наклейки ФЕСТАРТ", "Набор из пяти виниловых стикеров", 2, 300, 0),
    ("Значок", "Металлический значок с логотипом", 3, 200, 1),
    ("Шоппер", "Хлопковая сумка с принтом", 5, 100, 1),
    ("Термокружка", "Держит тепло до шести часов", 8, 50, 1),
    ("Худи ФЕСТАРТ", "Главный приз фестиваля", 15, 15, 1),
]


async def seed(*, faculties_only: bool = False) -> None:
    await init_db()

    async with session_scope() as session:
        event = await get_event_settings(session)

        faculty_count = await session.scalar(select(func.count(Faculty.id)))
        if not faculty_count:
            for order, title in enumerate(FACULTIES, start=1):
                session.add(Faculty(title=title, sort_order=order * 10))
            print(f"  факультеты: {len(FACULTIES)}")

        if faculties_only:
            print("  демо-контент пропущен")
            return

        activity_count = await session.scalar(select(func.count(Activity.id)))
        if not activity_count:
            for order, (title, points) in enumerate(ZONES, start=1):
                session.add(
                    Activity(
                        kind=ActivityKind.ZONE,
                        code=gen_activity_code(),
                        title=title,
                        points=points,
                        sort_order=order * 10,
                    )
                )
            base = utcnow().replace(minute=0, second=0, microsecond=0)
            for order, (title, desc, place, points, offset) in enumerate(WORKSHOPS, start=1):
                session.add(
                    Activity(
                        kind=ActivityKind.WORKSHOP,
                        code=gen_activity_code(),
                        title=title,
                        description=desc,
                        location=place,
                        points=points,
                        sort_order=order * 10,
                        starts_at=base + timedelta(minutes=offset),
                        ends_at=base + timedelta(minutes=offset + 60),
                    )
                )
            print(f"  зоны: {len(ZONES)}, мастер-классы: {len(WORKSHOPS)}")

        prize_count = await session.scalar(select(func.count(Prize.id)))
        if not prize_count:
            for order, (title, desc, cost, stock, limit) in enumerate(PRIZES, start=1):
                session.add(
                    Prize(
                        title=title,
                        description=desc,
                        cost_points=cost,
                        stock_total=stock,
                        stock_left=stock,
                        per_user_limit=limit,
                        sort_order=order * 10,
                    )
                )
            print(f"  призы: {len(PRIZES)}")

        staff_count = await session.scalar(select(func.count(Staff.id)))
        if not staff_count:
            session.add(
                Staff(
                    name="Координатор (демо)",
                    role=StaffRole.SUPERADMIN,
                    invite_token=gen_token(),
                )
            )
            print("  организатор: 1 (ссылку-приглашение возьми в админке)")

        if not event.feedback_url:
            event.feedback_url = "https://forms.yandex.ru/"
        event.registration_bonus = event.registration_bonus or 1
        event.all_zones_bonus = event.all_zones_bonus or 5

    print("Готово.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Наполнение базы ФЕСТАРТа")
    parser.add_argument("--faculties", action="store_true",
                        help="только факультеты, без демо-зон, МК и призов")
    args = parser.parse_args()
    asyncio.run(seed(faculties_only=args.faculties))


if __name__ == "__main__":
    main()
