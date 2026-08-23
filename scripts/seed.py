"""Демо-данные для показа и разработки.

    python -m scripts.seed

Заполняет базу примерами зон, мастер-классов, призов и факультетов,
рисует placeholder-карту. Всё это потом правится и удаляется через админку.
Повторный запуск ничего не дублирует.
"""

import asyncio
from datetime import timedelta

from PIL import Image, ImageDraw
from sqlalchemy import func, select

from app.config import MEDIA_DIR
from app.db import init_db, session_scope
from app.models import Activity, ActivityKind, Faculty, Prize, Staff, StaffRole, utcnow
from app.services.event import get_event_settings
from app.services.fonts import get_font
from app.utils import gen_activity_code, gen_token

FACULTIES = [
    "Институт информационных технологий",
    "Институт экономики и управления",
    "Юридический институт",
    "Гуманитарный институт",
    "Инженерная школа",
    "Институт биологии и химии",
]

ZONES = [
    ("Фотозона у главного входа", 1, 18.0, 22.0),
    ("Танцпол", 1, 42.0, 30.0),
    ("Кибер-арена", 1, 70.0, 25.0),
    ("Гончарная мастерская", 1, 25.0, 58.0),
    ("Спортивный уголок", 1, 55.0, 65.0),
    ("Фудкорт и настолки", 1, 80.0, 60.0),
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


def make_placeholder_map(path) -> None:
    """Схематичная карта-заглушка, чтобы функцию можно было показать
    до того, как дизайнеры отдадут настоящую."""
    width, height = 1280, 900
    image = Image.new("RGB", (width, height), (243, 244, 248))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((60, 60, width - 60, height - 60), radius=28, fill=(255, 255, 255))
    for x, y, w, h, label in [
        (140, 140, 380, 240, "Главный холл"),
        (560, 140, 300, 240, "Сцена"),
        (900, 140, 280, 240, "Кибер-зона"),
        (140, 440, 340, 280, "Мастерские"),
        (520, 440, 300, 280, "Спорт"),
        (860, 440, 320, 280, "Фудкорт"),
    ]:
        draw.rounded_rectangle((x, y, x + w, y + h), radius=18, fill=(233, 236, 245))
        draw.text(
            (x + w / 2, y + h - 34), label, font=get_font(26), fill=(110, 114, 130), anchor="ms"
        )

    draw.text(
        (width / 2, 104),
        "СХЕМА ПЛОЩАДКИ · placeholder",
        font=get_font(30, bold=True),
        fill=(150, 154, 170),
        anchor="ms",
    )
    image.save(path, format="JPEG", quality=90)


async def seed() -> None:
    await init_db()

    async with session_scope() as session:
        event = await get_event_settings(session)

        faculty_count = await session.scalar(select(func.count(Faculty.id)))
        if not faculty_count:
            for order, title in enumerate(FACULTIES, start=1):
                session.add(Faculty(title=title, sort_order=order * 10))
            print(f"  факультеты: {len(FACULTIES)}")

        activity_count = await session.scalar(select(func.count(Activity.id)))
        if not activity_count:
            for order, (title, points, map_x, map_y) in enumerate(ZONES, start=1):
                session.add(
                    Activity(
                        kind=ActivityKind.ZONE,
                        code=gen_activity_code(),
                        title=title,
                        points=points,
                        sort_order=order * 10,
                        map_x=map_x,
                        map_y=map_y,
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

        map_path = MEDIA_DIR / "map-placeholder.jpg"
        if not map_path.exists():
            make_placeholder_map(map_path)
            print("  карта-заглушка нарисована")
        if not event.map_image:
            event.map_image = map_path.name
            event.map_caption = "Это временная схема. Заменим на макет от дизайнеров."

        if not event.feedback_url:
            event.feedback_url = "https://forms.yandex.ru/"
        event.registration_bonus = event.registration_bonus or 1
        event.all_zones_bonus = event.all_zones_bonus or 5

    print("Готово.")


if __name__ == "__main__":
    asyncio.run(seed())
