"""Проверка веб-панели через HTTP, без браузера.

    python -m scripts.smoke_web

Поднимает FastAPI-приложение на временной базе, логинится под пароль
из .env и прогоняет все страницы и формы: дашборд, настройки, зоны,
призы, участники, организаторы, выгрузки. Ловит поломки шаблонов и
роутов, которые не видно сервисным тестам.
"""

import asyncio
import os
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
TEST_DB = BASE / "data" / "smoke-web.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"

from app.admin.main import app  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import engine, init_db, session_scope  # noqa: E402
from app.models import Prize, RedemptionStatus, StaffRole  # noqa: E402
from app.services.participants import (  # noqa: E402
    complete_registration,
    get_or_create_participant,
)
from app.services.points import get_balance  # noqa: E402
from app.services.prizes import confirm_redemption, create_redemption  # noqa: E402
from app.utils import gen_token  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "✓" if ok else "✗"
    print(f"  {mark} {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


async def seed_world() -> dict:
    """Участник с балансом, приз, организатор и подтверждённая выдача — для веб-проверок."""
    TEST_DB.unlink(missing_ok=True)
    await init_db()
    async with session_scope() as session:
        from app.models import Activity, ActivityKind, Staff

        zone = Activity(kind=ActivityKind.ZONE, code=gen_code(), title="Веб-зона", points=2)
        prize = Prize(title="Веб-приз", cost_points=3, stock_total=5, stock_left=5)
        staff = Staff(name="Веб-орг", role=StaffRole.ADMIN,
                      invite_token=gen_token(), tg_id=777_777)
        session.add_all([zone, prize, staff])
        await session.flush()

        participant, _ = await get_or_create_participant(session, tg_id=888_888)
        await complete_registration(
            session, participant, first_name="Веб", last_name="Тестов",
            middle_name=None, faculty_id=None, faculty_other="Тестовый", student_id="WEB-1",
        )
        await session.flush()

        from app.services.points import add_points

        await add_points(session, participant_id=participant.id, delta=10,
                         reason="manual", comment="сид")
        offer = await create_redemption(session, participant, prize.id, staff_id=staff.id)
        await confirm_redemption(session, offer.redemption)

        return {
            "zone_id": zone.id, "prize_id": prize.id, "staff_id": staff.id,
            "participant_id": participant.id, "redemption_id": offer.redemption.id,
        }


def gen_code() -> str:
    from app.utils import gen_activity_code

    return gen_activity_code()


async def main() -> None:
    ids = await seed_world()

    # --- async-хелперы для проверок через базу (определяем до использования) ---
    async def check_prize_stock(prize_id: int, expected: int, label: str) -> None:
        async with session_scope() as session:
            prize = await session.get(Prize, prize_id)
            check(label, prize is not None and prize.stock_left == expected,
                  f"осталось {prize.stock_left if prize else 'нет приза'}")

    async def check_balance(pid: int, expected: int, label: str) -> None:
        bal = await get_balance_async(pid)
        check(label, bal == expected, f"баланс {bal}")

    async def check_redemption_reverted(redemption_id: int, label: str) -> None:
        async with session_scope() as session:
            from app.models import Redemption
            red = await session.get(Redemption, redemption_id)
            check(label, red is not None and red.status == RedemptionStatus.REVERTED,
                  f"статус {red.status if red else 'нет выдачи'}")

    with TestClient(app) as client:
        print("\n1. Доступ без входа")
        r = client.get("/", follow_redirects=False)
        check("дашборд требует вход", r.status_code == 303 and r.headers["location"] == "/login")
        r = client.get("/health")
        check("health открыт без входа", r.status_code == 200 and r.json()["status"] == "ok")

        print("\n2. Вход")
        r = client.post("/login", data={"password": "неверный"})
        check("чужой пароль отклонён", r.status_code == 401)
        r = client.post("/login", data={"password": settings.admin_password},
                        follow_redirects=False)
        check("правильный пароль пускает", r.status_code == 303)

        print("\n3. Страницы")
        for path, needle in [
            ("/", "Сводка"),
            ("/settings", "Настройки"),
            ("/activities?kind=zone", "Веб-зона"),
            (f"/activities/{ids['zone_id']}", "Веб-зона"),
            ("/activities/new?kind=workshop", "мастер-класс"),
            ("/prizes", "Веб-приз"),
            (f"/prizes/{ids['prize_id']}", "Веб-приз"),
            ("/participants", "Тестов"),
            (f"/participants/{ids['participant_id']}", "WEB-1"),
            ("/staff", "Веб-орг"),
        ]:
            r = client.get(path)
            check(f"GET {path}", r.status_code == 200 and needle in r.text)

        print("\n4. Картинки и выгрузки")
        r = client.get(f"/activities/{ids['zone_id']}/qr.png")
        check("QR зоны отдаётся", r.status_code == 200 and r.headers["content-type"] == "image/png")
        r = client.get(f"/activities/{ids['zone_id']}/poster.png")
        check("плакат зоны отдаётся", r.status_code == 200 and len(r.content) > 10_000)
        r = client.get("/activities/posters.zip?kind=zone")
        check("ZIP плакатов собирается", r.status_code == 200 and r.content[:2] == b"PK")
        r = client.get("/participants/export.csv")
        check("CSV участников отдаётся", r.status_code == 200 and "Фамилия" in r.text)
        r = client.get(f"/staff/{ids['staff_id']}/qr.png")
        check("QR приглашения отдаётся", r.status_code == 200)

        print("\n5. Настройки и факультеты")
        r = client.post("/settings/save", data={
            "event_title": "Веб-фест", "welcome_text": "Привет из веба",
            "registration_bonus": "7", "all_zones_bonus": "9",
            "is_registration_open": "on", "is_scanning_open": "on",
        })
        check("настройки сохраняются", r.status_code == 200 and "Веб-фест" in r.text)
        r = client.post("/settings/toggle", data={"field": "is_scanning_open"},
                        headers={"referer": "/settings"})
        check("рубильник переключается", r.status_code == 200)
        r = client.post("/settings/toggle", data={"field": "show_leaderboard"},
                        headers={"referer": "/"})
        check("переключатель из формы — только из белого списка", r.status_code in (200, 303))
        r = client.post("/settings/faculties/add", data={"title": "ВебФак"})
        check("факультет добавлен", "ВебФак" in r.text)
        fid = _faculty_id(client)
        r = client.post(f"/settings/faculties/{fid}/delete")
        check("факультет удалён", "ВебФак" not in r.text)

        print("\n6. Зоны через веб")
        r = client.post("/activities/save", data={
            "kind": "zone", "title": "Зона из веба", "points": "4", "sort_order": "10",
            "is_active": "on",
        }, follow_redirects=False)
        check("зона создана", r.status_code == 303)
        zid = _zone_id(client)
        r = client.post("/activities/save", data={
            "activity_id": str(zid), "kind": "zone", "title": "Зона из веба v2",
            "points": "5", "sort_order": "10", "is_active": "on",
        })
        check("зона правится", "Зона из веба v2" in r.text)
        r = client.post(f"/activities/{zid}/delete", follow_redirects=False)
        check("зона удалена", r.status_code == 303 and "Зона из веба v2" not in client.get("/activities?kind=zone").text)

        print("\n7. Призы и склад")
        r = client.post("/prizes/save", data={
            "title": "Приз из веба", "cost_points": "6", "stock_total": "5",
            "per_user_limit": "1", "is_active": "on",
        }, follow_redirects=False)
        check("приз создан", r.status_code == 303)
        pid = _prize_id(client, "Приз из веба")
        client.post("/prizes/save", data={
            "prize_id": str(pid), "title": "Приз из веба", "cost_points": "6",
            "stock_total": "8", "stock_left": "", "per_user_limit": "1", "is_active": "on",
        })
        await check_prize_stock(pid, 8, "довоз: остаток вырос на прибавку")
        client.post(f"/prizes/{pid}/restock", data={"amount": "2"})
        await check_prize_stock(pid, 10, "кнопка «довезли» работает")
        client.post(f"/prizes/{pid}/delete")
        check("приз удалён", "Приз из веба" not in client.get("/prizes").text)

        print("\n8. Участники: баллы, блок, откат")
        pid_part = ids["participant_id"]
        client.post(f"/participants/{pid_part}/points",
                    data={"delta": "5", "comment": "веб-начисление"})
        await check_balance(pid_part, 12, "ручные баллы начислены")
        r = client.post(f"/participants/{pid_part}/block")
        check("участник заблокирован", "заблокирован" in r.text.lower())
        r = client.post(f"/participants/{pid_part}/block")
        check("разблокировка возвращает", "заблокирован" not in r.text.lower())
        r = client.post(f"/participants/redemptions/{ids['redemption_id']}/revert",
                        data={"comment": "веб-откат"})
        await check_redemption_reverted(ids["redemption_id"], "выдача откачена через веб")
        await check_balance(pid_part, 15, "баллы вернулись после отката")

        print("\n9. Организаторы")
        client.post("/staff/add", data={"name": "Веб-новичок", "role": StaffRole.ADMIN})
        sid = _staff_id(client, "Веб-новичок")
        r = client.post(f"/staff/{sid}/role", data={"role": StaffRole.SUPERADMIN})
        check("роль меняется", "Суперадмин" in r.text)
        r = client.post(f"/staff/{sid}/toggle")
        check("организатор отключается", "отключён" in r.text)
        r = client.post(f"/staff/{sid}/reset")
        check("ссылка переиздаётся, Telegram отвязан", "Веб-новичок" in r.text and r.status_code == 200)
        client.post(f"/staff/{sid}/delete")
        check("организатор удалён", "Веб-новичок" not in client.get("/staff").text)

        print("\n10. Выход")
        r = client.get("/logout", follow_redirects=False)
        check("выход сбрасывает сессию", r.status_code == 303)
        r = client.get("/", follow_redirects=False)
        check("после выхода снова логин", r.status_code == 303)

    await engine.dispose()
    TEST_DB.unlink(missing_ok=True)

    print()
    if failures:
        print(f"ПРОВАЛЕНО: {len(failures)}")
        for name in failures:
            print(f"  - {name}")
        sys.exit(1)
    print("Все проверки пройдены.")


def get_balance_sync(participant_id: int) -> int:
    return asyncio.run(get_balance_async(participant_id))


async def get_balance_async(participant_id: int) -> int:
    async with session_scope() as session:
        return await get_balance(session, participant_id)


def _faculty_id(client: TestClient) -> int:
    html = client.get("/settings").text
    match = re.search(r'/settings/faculties/(\d+)/delete', html)
    if match is None:
        raise AssertionError("кнопка удаления факультета не найдена")
    return int(match.group(1))


def _zone_id(client: TestClient) -> int:
    html = client.get("/activities?kind=zone").text
    match = re.search(r'/activities/(\d+)"><strong>', html)
    return int(match.group(1))


def _prize_id(client: TestClient, title: str) -> int:
    html = client.get("/prizes").text
    match = re.search(rf'/prizes/(\d+)"><strong>{title}', html)
    return int(match.group(1))


def _staff_id(client: TestClient, name: str) -> int:
    """Id карточки организатора: имя в карточке стоит раньше её форм."""
    html = client.get("/staff").text
    pos = html.find(f">{name}<")
    if pos == -1:
        raise AssertionError(f"организатор {name} не найден на странице")
    match = re.search(r'/staff/(\d+)/role"', html[pos:])
    return int(match.group(1))


if __name__ == "__main__":
    asyncio.run(main())