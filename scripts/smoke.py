"""Сквозная проверка бизнес-логики без Telegram.

    python -m scripts.smoke

Прогоняет полный путь участника на отдельной временной базе и печатает отчёт.
Запускать после любых изменений в services/ — ловит регрессии за пару секунд.
"""

import asyncio
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TEST_DB = BASE / "data" / "smoke-test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"

from app.db import init_db, session_scope  # noqa: E402
from app.models import (  # noqa: E402
    Activity,
    ActivityKind,
    Faculty,
    Prize,
    RedemptionStatus,
    Staff,
    StaffRole,
)
from app.services.event import get_event_settings  # noqa: E402
from app.services.maps import build_progress_map  # noqa: E402
from app.services.participants import (  # noqa: E402
    complete_registration,
    get_or_create_participant,
    is_student_id_taken,
)
from app.services.points import (  # noqa: E402
    ScanStatus,
    get_balance,
    register_scan,
    zone_progress,
)
from app.services.prizes import (  # noqa: E402
    RedeemStatus,
    confirm_redemption,
    create_redemption,
    revert_redemption,
)
from app.services.qr import make_poster_png, make_qr_png, participant_link  # noqa: E402
from app.utils import gen_activity_code, gen_token  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "✓" if condition else "✗"
    print(f"  {mark} {label}" + (f"  — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


async def main() -> None:
    TEST_DB.unlink(missing_ok=True)
    await init_db()

    async with session_scope() as session:
        event = await get_event_settings(session)
        event.registration_bonus = 1
        event.all_zones_bonus = 5
        event.map_image = "map-placeholder.jpg"

        faculty = Faculty(title="Институт информационных технологий")
        session.add(faculty)

        zones = [
            Activity(kind=ActivityKind.ZONE, code=gen_activity_code(), title=f"Зона {i}",
                     points=2, map_x=20.0 * i, map_y=30.0)
            for i in range(1, 4)
        ]
        workshop = Activity(
            kind=ActivityKind.WORKSHOP, code=gen_activity_code(),
            title="Каллиграфия", points=3,
        )
        prize_cheap = Prize(title="Наклейки", cost_points=3, stock_total=10, stock_left=10,
                            per_user_limit=0)
        prize_rare = Prize(title="Худи", cost_points=100, stock_total=1, stock_left=1)
        staff = Staff(name="Организатор", role=StaffRole.PRIZE_DESK,
                      invite_token=gen_token(), tg_id=999_000_1)
        session.add_all([*zones, workshop, prize_cheap, prize_rare, staff])
        await session.flush()

        zone_codes = [z.code for z in zones]
        workshop_code = workshop.code
        cheap_id, rare_id = prize_cheap.id, prize_rare.id
        staff_id = staff.id
        faculty_id = faculty.id

    print("\n1. Регистрация")
    async with session_scope() as session:
        participant, created = await get_or_create_participant(
            session, tg_id=555_001, username="tester"
        )
        check("участник создан", created)
        check("выдан личный QR-токен", bool(participant.qr_token))
        check("выдан короткий код из 6 цифр",
              len(participant.short_code) == 6 and participant.short_code.isdigit(),
              participant.short_code)

        bonus = await complete_registration(
            session, participant,
            first_name="Егор", last_name="Тестов",
            faculty_id=faculty_id, faculty_other=None, student_id="21ИТ042",
        )
        check("регистрация завершена", participant.is_registered)
        check("начислен приветственный бонус", bonus == 1, f"{bonus} б.")
        check("баланс после регистрации", await get_balance(session, participant.id) == 1)
        pid = participant.id

    print("\n2. Защита от дублей студбилета")
    async with session_scope() as session:
        other, _ = await get_or_create_participant(session, tg_id=555_002)
        taken = await is_student_id_taken(session, "21ИТ042", exclude_participant_id=other.id)
        check("чужой номер студбилета занят", taken)
        free = await is_student_id_taken(session, "21ИТ999", exclude_participant_id=other.id)
        check("свободный номер проходит", not free)

    print("\n3. Сканирование зон")
    async with session_scope() as session:
        participant = await _get(session, pid)
        result = await register_scan(session, participant, zone_codes[0])
        check("первый скан начисляет баллы", result.status == ScanStatus.OK,
              f"+{result.points}, баланс {result.balance}")

        repeat = await register_scan(session, participant, zone_codes[0])
        check("повторный скан не начисляет", repeat.status == ScanStatus.ALREADY,
              f"баланс {repeat.balance}")

        bad = await register_scan(session, participant, "НЕСУЩЕСТВУЮЩИЙ")
        check("несуществующий код отклонён", bad.status == ScanStatus.NOT_FOUND)

    print("\n4. Бонус за все зоны")
    async with session_scope() as session:
        participant = await _get(session, pid)
        await register_scan(session, participant, zone_codes[1])
        last = await register_scan(session, participant, zone_codes[2])
        check("бонус выдан на последней зоне", last.bonus == 5, f"+{last.bonus} б.")
        visited, total = await zone_progress(session, participant.id)
        check("прогресс посчитан", (visited, total) == (3, 3), f"{visited}/{total}")

        again = await register_scan(session, participant, workshop_code)
        check("мастер-класс не ломает счётчик зон", again.visited_zones == 3)
        check("бонус повторно не начисляется", again.bonus == 0)

        balance = await get_balance(session, participant.id)
        # 1 регистрация + 3 зоны * 2 + бонус 5 + мастер-класс 3
        check("итоговый баланс верный", balance == 15, f"{balance} б.")

    print("\n5. Выдача приза")
    async with session_scope() as session:
        participant = await _get(session, pid)

        too_rare = await create_redemption(session, participant, rare_id, staff_id=staff_id)
        check("дорогой приз недоступен", too_rare.status == RedeemStatus.NOT_ENOUGH_POINTS,
              f"не хватает {too_rare.missing}")

        offer = await create_redemption(session, participant, cheap_id, staff_id=staff_id)
        check("запрос на выдачу создан", offer.ok)
        check("товар зарезервирован", offer.prize.stock_left == 9,
              f"осталось {offer.prize.stock_left}")

        dup = await create_redemption(session, participant, cheap_id, staff_id=staff_id)
        check("второй запрос не создаётся", dup.status == RedeemStatus.HAS_PENDING)

        confirmed = await confirm_redemption(session, offer.redemption)
        check("участник подтвердил, баллы списаны", confirmed.ok,
              f"баланс {confirmed.balance}")
        check("баланс уменьшился на цену", confirmed.balance == 12, f"{confirmed.balance} б.")
        rid = offer.redemption.id

    print("\n6. Откат ошибочной выдачи")
    async with session_scope() as session:
        from app.models import Redemption

        redemption = await session.get(Redemption, rid)
        await revert_redemption(session, redemption, staff_id=staff_id,
                                comment="Приз не был выдан")
        check("статус изменился на «откачен»", redemption.status == RedemptionStatus.REVERTED)
        balance = await get_balance(session, pid)
        check("баллы вернулись", balance == 15, f"{balance} б.")
        prize = await session.get(Prize, cheap_id)
        check("товар вернулся на склад", prize.stock_left == 10, f"{prize.stock_left} шт.")

    print("\n7. Картинки")
    async with session_scope() as session:
        participant = await _get(session, pid)
        image, caption = await build_progress_map(session, participant.id)
        check("карта отрисована", image is not None and len(image) > 5000,
              f"{len(image) // 1024} КБ" if image else "нет файла карты")
        check("подпись содержит прогресс", "Пройдено" in caption)

        qr = make_qr_png(participant_link(participant.qr_token))
        check("QR участника сгенерирован", len(qr) > 200, f"{len(qr)} байт")

        poster = make_poster_png(
            "https://t.me/Festart_bot?start=z_TEST", "Фотозона у главного входа",
            subtitle="+2 балла",
        )
        check("плакат А4 сгенерирован", len(poster) > 20_000, f"{len(poster) // 1024} КБ")

    print("\n8. Блокировка участника")
    async with session_scope() as session:
        participant = await _get(session, pid)
        participant.is_blocked = True
        await session.flush()

        blocked_scan = await register_scan(session, participant, zone_codes[0])
        check("заблокированному не начисляют", blocked_scan.status == ScanStatus.BLOCKED)

        blocked_prize = await create_redemption(session, participant, cheap_id, staff_id=staff_id)
        check("заблокированному не выдают приз", blocked_prize.status == RedeemStatus.BLOCKED)

        participant.is_blocked = False

    TEST_DB.unlink(missing_ok=True)

    print()
    if failures:
        print(f"ПРОВАЛЕНО: {len(failures)}")
        for name in failures:
            print(f"  - {name}")
        sys.exit(1)
    print("Все проверки пройдены.")


async def _get(session, participant_id):
    from app.models import Participant

    return await session.get(Participant, participant_id)


if __name__ == "__main__":
    asyncio.run(main())
