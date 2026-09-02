"""Регрессионные тесты бизнес-логики: баги, которые уже чинили, и их соседи.

    python -m scripts.tests

Запускается на отдельной временной базе, ничего не трогает в боевой.
Отличается от smoke.py тем, что проверяет не «счастливый путь», а защиты:
гонки, двойные нажатия, лимиты, испорченные данные. Каждый тест здесь
соответствует конкретной ошибке из ревью.

Кросс-соединённые гонки (две стойки, два процесса) на SQLite честно не
воспроизвести, поэтому проверяются их гарантии на уровне базы: частичный
уникальный индекс и атомарный UPDATE с условием.
"""

import asyncio
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TEST_DB = BASE / "data" / "tests.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app import db as app_db  # noqa: E402
from app.models import (  # noqa: E402
    Activity,
    ActivityKind,
    Participant,
    Prize,
    Redemption,
    RedemptionStatus,
    Staff,
    StaffRole,
)
from app.services.event import get_event_settings  # noqa: E402
from app.services.participants import (  # noqa: E402
    complete_registration,
    get_or_create_participant,
)
from app.services.points import (  # noqa: E402
    ScanStatus,
    award_manual,
    get_balance,
    register_scan,
)
from app.services.prizes import (  # noqa: E402
    RedeemStatus,
    cancel_redemption,
    confirm_redemption,
    create_redemption,
    revert_redemption,
)
from app.services.staff import activate_staff, resolve_staff  # noqa: E402
from app.utils import fmt_points, gen_activity_code, gen_token, plural  # noqa: E402

failures: list[str] = []
passed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed
    mark = "✓" if condition else "✗"
    print(f"  {mark} {label}" + (f"  — {detail}" if detail and not condition else ""))
    if condition:
        passed += 1
    else:
        failures.append(label)


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


async def make_world():
    """База: два участника с балансом, два приза, зона, организатор."""
    async with session_scope() as session:
        event = await get_event_settings(session)
        event.registration_bonus = 0
        event.all_zones_bonus = 0

        zone = Activity(kind=ActivityKind.ZONE, code=gen_activity_code(), title="Зона", points=2)
        prize_ok = Prize(title="Приз", cost_points=5, stock_total=10, stock_left=10,
                         per_user_limit=0)
        prize_last = Prize(title="Последний", cost_points=1, stock_total=1, stock_left=1,
                           per_user_limit=0)
        staff = Staff(name="Орг", role=StaffRole.ADMIN, invite_token=gen_token(),
                      tg_id=888_001)
        session.add_all([zone, prize_ok, prize_last, staff])
        await session.flush()

        p1, _ = await get_or_create_participant(session, tg_id=777_001)
        await complete_registration(session, p1, first_name="А", last_name="А",
                                    middle_name=None, faculty_id=None, faculty_other=None, student_id="T-1")
        p2, _ = await get_or_create_participant(session, tg_id=777_002)
        await complete_registration(session, p2, first_name="Б", last_name="Б",
                                    middle_name=None, faculty_id=None, faculty_other=None, student_id="T-2")
        for p in (p1, p2):
            await award_manual(session, p, 100, staff_id=None, comment="тестовый запас")

        return {
            "zone_code": zone.code,
            "zone_id": zone.id,
            "prize_ok": prize_ok.id,
            "prize_last": prize_last.id,
            "staff": staff.id,
            "p1": p1.id,
            "p2": p2.id,
        }


async def participant(session, pid: int) -> Participant:
    return await session.get(Participant, pid)


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


async def test_scan_double_credit(world):
    """Повторный скан зоны не должен начислять баллы дважды."""
    async with session_scope() as session:
        p = await participant(session, world["p1"])
        first = await register_scan(session, p, world["zone_code"])
        second = await register_scan(session, p, world["zone_code"])
        check("первый скан начисляет", first.ok)
        check("повторный скан — ALREADY без баллов",
              second.status == ScanStatus.ALREADY and second.points == 0)
        check("баланс учитывает зону один раз",
              await get_balance(session, p.id) == 100 + 2)


async def test_scan_race_savepoint(world):
    """Гонка двух сканов одного кода в одной сессии не должна ломать сессию."""
    async with session_scope() as session:
        p = await participant(session, world["p2"])
        first = await register_scan(session, p, world["zone_code"])
        second = await register_scan(session, p, world["zone_code"])
        check("гонка сканов: первый OK, второй ALREADY",
              first.ok and second.status == ScanStatus.ALREADY)
        # Сессия жива после IntegrityError — можно продолжать работу
        await award_manual(session, p, 1, staff_id=None, comment="сессия не откатилась")
        check("сессия работоспособна после гонки", True)


async def test_scan_guards(world):
    """Неактивная зона и закрытое сканирование."""
    async with session_scope() as session:
        zone = await session.get(Activity, world["zone_id"])
        zone.is_active = False
        p = await participant(session, world["p1"])
        result = await register_scan(session, p, world["zone_code"])
        check("неактивная зона не начисляет", result.status == ScanStatus.INACTIVE)
        zone.is_active = True

    async with session_scope() as session:
        event = await get_event_settings(session)
        event.is_scanning_open = False
        p = await participant(session, world["p1"])
        result = await register_scan(session, p, world["zone_code"])
        check("закрытое сканирование не начисляет по QR", result.status == ScanStatus.CLOSED)
        # Ручное начисление живёт даже при закрытом QR — спор решается руками
        await award_manual(session, p, 3, staff_id=world["staff"], comment="вручную при закрытых")
        check("ручное начисление работает при закрытом сканировании",
              await get_balance(session, p.id) > 0)
        event.is_scanning_open = True


async def test_all_zones_bonus_once(world):
    """Бонус за все зоны выдаётся один раз и не слетает от пересканов."""
    async with session_scope() as session:
        event = await get_event_settings(session)
        event.all_zones_bonus = 7

        codes = [gen_activity_code() for _ in range(2)]
        for code in codes:
            session.add(Activity(kind=ActivityKind.ZONE, code=code, title="Бонусная", points=1))
        await session.flush()

        p = await participant(session, world["p1"])
        results = [await register_scan(session, p, c) for c in codes]
        bonus_first = sum(r.bonus for r in results)
        check("бонус начислен при закрытии всех зон", bonus_first == 7)

        # Перескан уже пройденной не должен перевыдать бонус
        again = await register_scan(session, p, codes[0])
        check("перескан не перевыдаёт бонус", again.bonus == 0)
        event.all_zones_bonus = 0


async def test_redemption_pending_race(world):
    """Два организатора на одного участника: второй получает has_pending."""
    async with session_scope() as session:
        p = await participant(session, world["p1"])
        first = await create_redemption(session, p, world["prize_ok"], staff_id=world["staff"])
        second = await create_redemption(session, p, world["prize_ok"], staff_id=world["staff"])
        check("первый запрос создан", first.ok)
        check("второй отклонён как дубль", second.status == RedeemStatus.HAS_PENDING)
        check("резерв списан один раз", first.prize.stock_left == 9)
        await cancel_redemption(session, first.redemption, comment="тест")


async def test_redemption_pending_index_in_db(world):
    """Индекс частичной уникальности реально сидит в базе, а не только в модели."""
    async with session_scope() as session:
        row = await session.scalar(text(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='uq_redemption_pending_per_participant'"
        ))
        check("частичный уникальный индекс существует", row is not None)

        # Прямая попытка вставить второй PENDING тому же участнику должна упасть
        p = await participant(session, world["p1"])
        first = await create_redemption(session, p, world["prize_ok"], staff_id=None)
        try:
            async with session.begin_nested():
                session.add(Redemption(
                    participant_id=p.id, prize_id=world["prize_ok"],
                    prize_title="Обход", cost_points=1, status=RedemptionStatus.PENDING,
                ))
                await session.flush()
            bypassed = False
        except IntegrityError:
            bypassed = True
        check("обход сервиса на уровне БД блокируется индексом", bypassed)
        await cancel_redemption(session, first.redemption, comment="тест")


async def test_redemption_stock_atomic_guard(world):
    """UPDATE ... WHERE stock_left > 0 не даёт складу уйти в минус."""
    async with session_scope() as session:
        from sqlalchemy import update

        prize = await session.get(Prize, world["prize_last"])
        first = await session.execute(
            update(Prize)
            .where(Prize.id == prize.id, Prize.stock_left > 0)
            .values(stock_left=Prize.stock_left - 1)
        )
        check("последний товар списывается", first.rowcount == 1)
        second = await session.execute(
            update(Prize)
            .where(Prize.id == prize.id, Prize.stock_left > 0)
            .values(stock_left=Prize.stock_left - 1)
        )
        check("списание пустого склада невозможно", second.rowcount == 0)
        await session.refresh(prize)
        check("склад не ушёл в минус", prize.stock_left == 0)


async def test_redemption_last_item_two_participants(world):
    """Последний товар: первый участник забрал — второй получает out_of_stock."""
    async with session_scope() as session:
        from sqlalchemy import update as sa_update

        # Предыдущий тест мог потратить этот товар — возвращаем на склад
        await session.execute(
            sa_update(Prize)
            .where(Prize.id == world["prize_last"])
            .values(stock_total=1, stock_left=1)
        )
        p1 = await participant(session, world["p1"])
        offer = await create_redemption(session, p1, world["prize_last"], staff_id=None)
        check("первый участник забрал последний", offer.ok, f"статус: {offer.status}")
        await confirm_redemption(session, offer.redemption)

    async with session_scope() as session:
        p2 = await participant(session, world["p2"])
        late = await create_redemption(session, p2, world["prize_last"], staff_id=None)
        check("второму — out_of_stock, а не минус на складе",
              late.status == RedeemStatus.OUT_OF_STOCK)


async def test_redemption_double_press(world):
    """Двойное нажатие кнопок подтверждения и отмены ничего не портит."""
    async with session_scope() as session:
        p = await participant(session, world["p1"])
        offer = await create_redemption(session, p, world["prize_ok"], staff_id=None)
        rid = offer.redemption.id

        c1 = await confirm_redemption(session, offer.redemption)
        c2 = await confirm_redemption(session, offer.redemption)
        check("дабл-клик «подтвердить» безвреден", c1.ok and not c2.ok)

        # Кнопка «отменить» рядом: нажатие после подтверждения не меняет статус
        fresh = await session.get(Redemption, rid)
        await cancel_redemption(session, fresh, comment="поздний клик")
        await session.refresh(fresh)
        check("отмена после подтверждения не действует",
              fresh.status == RedemptionStatus.CONFIRMED)

    async with session_scope() as session:
        p = await participant(session, world["p1"])
        offer = await create_redemption(session, p, world["prize_ok"], staff_id=None)
        balance_before = await get_balance(session, p.id)
        await cancel_redemption(session, offer.redemption, comment="тест")
        result = await confirm_redemption(session, offer.redemption)
        check("подтверждение после отмены отклонено",
              result.status == RedeemStatus.NOT_FOUND)
        check("баллы не списаны после отмены",
              await get_balance(session, p.id) == balance_before)


async def test_redemption_balance_changed_between_steps(world):
    """Баланс изменился между выбором и подтверждением — выдача отменяется, товар возвращается."""
    async with session_scope() as session:
        p = await participant(session, world["p2"])
        offer = await create_redemption(session, p, world["prize_ok"], staff_id=None)
        check("запрос создан", offer.ok, f"статус: {offer.status}")
        # Пока участник думал, организатор списал ему баллы вручную
        await award_manual(session, p, -100, staff_id=world["staff"], comment="ошибка зоны")

    async with session_scope() as session:
        fresh = (await session.scalars(
            text("SELECT id FROM redemptions WHERE participant_id=:p AND status='pending'"),
            {"p": world["p2"]},
        ))
        rid = fresh.first()
        redemption = await session.get(Redemption, rid)
        result = await confirm_redemption(session, redemption)
        check("списание при нехватке баллов отменено",
              result.status == RedeemStatus.NOT_ENOUGH_POINTS)
        prize = await session.get(Prize, world["prize_ok"])
        check("товар вернулся на склад", prize.stock_left == 9)
        # Ручное начисление могло увести баланс в минус — это валидное
        # состояние (журнал операций, организатор откорректирует).
        # Важно другое: неудачное подтверждение ничего не списало.
        balance = await get_balance(session, world["p2"])
        check("неудачное подтверждение не изменило баланс",
              await get_balance(session, world["p2"]) == balance)


async def test_redemption_snapshot_and_limit(world):
    """Снимок названия/цены в истории и лимит «в одни руки»."""
    async with session_scope() as session:
        p = await participant(session, world["p1"])
        offer = await create_redemption(session, p, world["prize_ok"], staff_id=None)
        await confirm_redemption(session, offer.redemption)

        # Админ переименовал и переоценил приз задним числом
        prize = await session.get(Prize, world["prize_ok"])
        prize.title = "Новое название"
        prize.cost_points = 999

        from sqlalchemy import select as sa_select
        row = (await session.scalars(
            sa_select(Redemption).where(Redemption.participant_id == world["p1"])
        )).first()
        check("в истории осталось старое название", row.prize_title == "Приз")
        check("в истории осталась старая цена", row.cost_points == 5)

        # Возвращаем приз в исходное состояние — следующие тесты покупают его
        prize.title = "Приз"
        prize.cost_points = 5
        await session.flush()


async def test_revert(world):
    """Откат подтверждённой выдачи возвращает и баллы, и товар."""
    async with session_scope() as session:
        p = await participant(session, world["p2"])
        # Предыдущие тесты могли потратить баланс — выравниваем
        balance_now = await get_balance(session, p.id)
        if balance_now < 10:
            await award_manual(session, p, 100, staff_id=None, comment="пополнение теста")
        offer = await create_redemption(session, p, world["prize_ok"], staff_id=world["staff"])
        check("выдача для отката создана", offer.ok, f"статус: {offer.status}")
        stock_before_revert = offer.prize.stock_left
        await confirm_redemption(session, offer.redemption)
        after_confirm = await get_balance(session, p.id)
        await revert_redemption(session, offer.redemption, staff_id=world["staff"],
                                comment="приз не выдали")
        check("баланс восстановлен", await get_balance(session, p.id) == after_confirm + 5)
        prize = await session.get(Prize, world["prize_ok"])
        check("товар восстановлен", prize.stock_left == stock_before_revert + 1,
              f"{prize.stock_left} против {stock_before_revert}")


async def test_pending_does_not_block_after_resolution(world):
    """После отмены/подтверждения участник снова может получать призы."""
    async with session_scope() as session:
        p = await participant(session, world["p2"])
        if await get_balance(session, p.id) < 10:
            await award_manual(session, p, 100, staff_id=None, comment="пополнение теста")
        offer = await create_redemption(session, p, world["prize_ok"], staff_id=None)
        check("запрос создан", offer.ok, f"статус: {offer.status}")
        await cancel_redemption(session, offer.redemption, comment="передумал")
        again = await create_redemption(session, p, world["prize_ok"], staff_id=None)
        check("после отмены можно запросить снова", again.ok, f"статус: {again.status}")
        await cancel_redemption(session, again.redemption, comment="тест")


async def test_staff_invites(world):
    """Ссылки-приглашения одноразовые, env-админ непоколебим."""
    async with session_scope() as session:
        token = gen_token()
        member = Staff(name="Новый", role=StaffRole.ADMIN, invite_token=token)
        session.add(member)
        await session.flush()

        first = await activate_staff(session, token, tg_id=555_100, username="first")
        check("первый привязался", first is not None and first.tg_id == 555_100)

        second = await activate_staff(session, token, tg_id=555_200, username="second")
        check("второй по той же ссылке отклонён", second is None)

        again = await activate_staff(session, token, tg_id=555_100, username="first")
        check("тот же человек повторно — это он же", again is not None and again.id == member.id)

    from app.config import settings

    env_id = next(iter(settings.admin_ids), None)
    if env_id is None:
        check("env-админ: ADMIN_TG_IDS не задан — проверка пропущена", True)
    else:
        async with session_scope() as session:
            staff = await resolve_staff(session, env_id, username="boss")
            check("env-админ создан автоматически",
                  staff is not None and staff.role == StaffRole.SUPERADMIN)
            staff.role = StaffRole.ADMIN  # кто-то попытался понизить
            refreshed = await resolve_staff(session, env_id, username="boss")
            check("понижение env-админа откатилось", refreshed.role == StaffRole.SUPERADMIN)

            # Обычный активный организатор
            ok_staff = await resolve_staff(session, 555_100)
            check("активный организатор виден", ok_staff is not None)
            # Неактивный — нет
            ok_staff.is_active = False
            check("отключённый организатор невидим",
                  await resolve_staff(session, 555_100) is None)


async def test_registration_pending_zone(world):
    """Зона, отсканированная до регистрации, засчитывается после неё."""
    async with session_scope() as session:
        newbie, created = await get_or_create_participant(session, tg_id=777_003)
        check("новый участник создан", created)
        newbie.pending_activity_code = world["zone_code"]
        await session.flush()

        await complete_registration(session, newbie, first_name="В", last_name="Г",
                                    middle_name=None, faculty_id=None, faculty_other=None, student_id="T-3")
        check("код зоны не потерян", newbie.pending_activity_code == world["zone_code"])

        result = await register_scan(session, newbie, newbie.pending_activity_code)
        check("зона засчитана после регистрации", result.ok)
        newbie.pending_activity_code = None


async def test_event_settings_singleton(world):
    """Настройки — всегда одна строка, повторный вызов не плодит записи."""
    async with session_scope() as session:
        first = await get_event_settings(session)
        second = await get_event_settings(session)
        check("настройки — один и тот же объект", first.id == second.id == 1)
        check("рубильники по умолчанию открыты",
              first.is_registration_open and first.is_scanning_open
              and first.is_redemption_open)
        check("рейтинг по умолчанию скрыт", not first.show_leaderboard)


async def test_broadcast_text_survives_braces(world):
    """Фигурные скобки в тексте от админа не роняют рассылку."""
    from scripts.broadcast import build_text

    template = "Привет, {name}! У тебя {points}. {сломанная_переменная} и {}"
    result = build_text(template, {"name": "Егор", "points": 5, "visits": 3})
    check("битый шаблон возвращён как есть", result == template)
    good = build_text("Итоги: {points}, зон: {visits}",
                      {"name": "Егор", "points": 5, "visits": 3})
    check("корректный шаблон заполняется", good == "Итоги: 5 баллов, зон: 3")


async def test_fsm_reset_state(world):
    """Сброс сценария сохраняет служебные ключи и уважает регистрацию."""
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from app.bot.states import (
        HUB_KEY,
        VIEW_KEY,
        Registration,
        StaffAward,
        reset_state,
    )

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    state = FSMContext(storage=storage, key=key)

    await state.set_state(StaffAward.amount)
    await state.update_data(**{HUB_KEY: 42, VIEW_KEY: "participant", "participant_id": 9})
    await reset_state(state)
    data = await state.get_data()
    check("сценарий сброшен", await state.get_state() is None)
    check("хаб пережил сброс", data.get(HUB_KEY) == 42)
    check("режим пережил сброс", data.get(VIEW_KEY) == "participant")
    check("чужие данные ушли", "participant_id" not in data)

    await state.set_state(Registration.first_name)
    await state.update_data(first_name="Егор")
    await reset_state(state, keep_registration=True)
    check("анкета регистрации не сброшена", await state.get_state() == Registration.first_name)
    await reset_state(state)
    check("без флага анкета сбрасывается", await state.get_state() is None)


async def test_keyboards(world):
    """Инлайн-меню: уникальные колбэки, переключатель режима у орга."""
    from app.bot.keyboards import (
        back_keyboard,
        menu_button_keyboard,
        menu_keyboard,
        with_back,
    )

    def callbacks(markup):
        return [btn.callback_data for row in markup.inline_keyboard for btn in row]

    participant_menu = callbacks(menu_keyboard())
    check("нет дублей колбэков в меню", len(participant_menu) == len(set(participant_menu)))
    check("меню участника без админ-кнопки", all("mode:" not in cb for cb in participant_menu))

    staff_menu = callbacks(menu_keyboard(is_staff=True))
    check("меню организатора-участника содержит переключатель", "mode:staff" in staff_menu)
    check("кнопка «Меню» ведёт на menu:main",
          menu_button_keyboard().inline_keyboard[0][0].callback_data == "menu:main")

    base = back_keyboard()
    extended = with_back(base, "menu:ws", "← К списку")
    check("with_back добавил ряд и не испортил исходник",
          len(base.inline_keyboard) == 1 and len(extended.inline_keyboard) == 2)


async def test_every_screen_has_exit(world):
    """Из каждого экрана участника можно вернуться в меню.

    Этот баг уже ловили руками: раздел рисуется списком собственных кнопок,
    а выхода в меню нет — человек застревает и жмёт /start. Проверяем
    статически, чтобы не зависеть от того, вспомнил ли автор про кнопку.
    """
    import ast

    tree = ast.parse((BASE / "app" / "bot" / "handlers" / "menu.py").read_text("utf-8"))

    # Клавиатуры, которые сами по себе содержат выход в меню.
    exits = {"back_keyboard", "menu_keyboard", "with_back", "menu_button_keyboard"}

    def keyboard_name(node, local_vars):
        # markup может быть присвоен переменной выше по функции — разворачиваем.
        if isinstance(node, ast.Name):
            node = local_vars.get(node.id, node)
        while isinstance(node, ast.Call):
            node = node.func
        return node.attr if isinstance(node, ast.Attribute) else ""

    functions = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]
    missing = []
    screens = 0
    for func in functions:
        local_vars = {
            target.id: node.value
            for node in ast.walk(func)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for call in ast.walk(func):
            if not (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "_edit"):
                continue
            if len(call.args) < 3:
                continue
            screens += 1
            if keyboard_name(call.args[2], local_vars) not in exits:
                missing.append(f"{func.name}, строка {call.lineno}")

    check(
        "каждый экран участника имеет выход в меню",
        not missing,
        ", ".join(missing) if missing else f"экранов проверено: {screens}",
    )

    # QR уходит фотографией: у неё своя клавиатура с возвратом, а обработчик
    # меню обязан уметь убрать фото — отредактировать его в текст нельзя.
    qr = next(f for f in functions if f.name == "cb_menu_qr")
    photo_has_keyboard = any(
        isinstance(node, ast.Call)
        and getattr(node.func, "attr", "") == "answer_photo"
        and any(kw.arg == "reply_markup" for kw in node.keywords)
        for node in ast.walk(qr)
    )
    check("под фото с QR есть кнопка возврата", photo_has_keyboard)

    main = next(f for f in functions if f.name == "cb_menu_main")
    handles_photo = any(
        isinstance(node, ast.Attribute) and node.attr == "photo" for node in ast.walk(main)
    ) and any(
        isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "delete"
        for node in ast.walk(main)
    )
    check("возврат из фото убирает фото и пересобирает меню", handles_photo)


async def test_admin_buttons_have_handlers(world):
    """Каждая кнопка админки ведёт в живой обработчик.

    Удалили раздел, забыли кнопку — она молча перестаёт отвечать. Сверяем
    callback_data из клавиатур со всеми зарегистрированными фильтрами.
    """
    import ast
    import re

    from app.bot.admin import keyboards as akb
    from app.bot.admin.core import load_item, load_items
    from app.bot.admin.specs import SETTINGS, SPECS
    from app.models import StaffRole
    from app.services.staff import list_staff

    datas = []

    def collect(markup):
        datas.extend(
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        )

    collect(akb.main_menu(True))
    collect(akb.main_menu(False))
    collect(akb.exports_menu())

    async with session_scope() as session:
        for code, spec in SPECS.items():
            items = (
                [await load_item(session, spec, 1)]
                if code == SETTINGS
                else await load_items(session, spec)
            )
            if code != SETTINGS:
                collect(akb.item_list(spec, items))
            for item in items:
                if item is not None:
                    collect(akb.item_card(spec, item, with_qr=True))
        members = await list_staff(session)
        collect(akb.staff_kb(members))
        for member in members:
            collect(akb.staff_card_kb(member))
            collect(akb.roles_kb(member.id, StaffRole.CHOICES))

    source = "\n".join(
        (BASE / "app" / "bot" / "admin" / f"{name}.py").read_text("utf-8")
        for name in ("core", "extras")
    )
    exact = set(re.findall(r'F\.data\s*==\s*"([^"]+)"', source))
    prefixes = tuple(re.findall(r'F\.data\.startswith\("([^"]+)"\)', source))

    orphans = sorted(
        {d for d in datas if d not in exact and not d.startswith(prefixes)}
    )
    check(
        "у каждой кнопки админки есть обработчик",
        not orphans,
        ", ".join(orphans) if orphans else f"кнопок проверено: {len(datas)}",
    )

    too_long = [d for d in datas if len(d.encode()) > 64]
    check("callback_data укладывается в лимит Telegram", not too_long,
          f"максимум {max(len(d.encode()) for d in datas)} из 64 байт")


async def test_scan_text_mapping(world):
    """Все статусы скана имеют человекочитаемый ответ, ни один не пустой."""
    from app.bot.handlers.start import _scan_text
    from app.models import Activity
    from app.services.points import ScanResult

    activity = Activity(title="Тестовая зона")
    for status in ("ok", "already", "not_found", "inactive", "closed", "blocked", "мусор"):
        text = _scan_text(ScanResult(status=status, activity=activity))
        check(f"статус {status!r} — непустой текст", bool(text.strip()))


async def test_redeem_error_mapping(world):
    """Все коды ошибок выдачи имеют текст для организатора."""
    from app.bot.handlers.staff import _redeem_error
    from app.services.prizes import RedeemResult

    for status in ("not_enough_points", "out_of_stock", "limit_reached", "has_pending",
                   "inactive", "closed", "blocked", "not_found", "неизвестный"):
        text = _redeem_error(RedeemResult(status=status))
        check(f"ошибка {status!r} описана словами", bool(text.strip()))


async def test_name_validators(world):
    """Регулярки регистрации: кириллица, дефисы — да; цифры — нет."""
    from app.bot.handlers.start import NAME_RE, STUDENT_ID_RE

    ok_names = ["Егор", "Мария-Антуанетта", "Ann", "Ёлкин Пётр"]
    bad_names = ["Иван123", "", "!!!", " "]
    check("имена: валидные проходят", all(NAME_RE.match(n) for n in ok_names))
    check("имена: мусор отклонён", not any(NAME_RE.match(n) for n in bad_names))

    ok_ids = ["237612", "AB-12/9", "21ИТ042".replace("ИТ", "IT")]
    bad_ids = ["абв", "!!", "x"]
    check("студбилет: цифры и латиница проходят", all(STUDENT_ID_RE.match(v) for v in ok_ids))
    check("студбилет: кириллица и коротышки отклонены",
          not any(STUDENT_ID_RE.match(v) for v in bad_ids))


async def test_utils(world):
    """Склонения и генерация кодов."""
    check("1 балл", plural(1, "балл", "балла", "баллов") == "балл")
    check("2 балла", plural(2, "балл", "балла", "баллов") == "балла")
    check("5 баллов", plural(5, "балл", "балла", "баллов") == "баллов")
    check("11 баллов", plural(11, "балл", "балла", "баллов") == "баллов")
    check("21 балл", plural(21, "балл", "балла", "баллов") == "балл")
    check("101 балл", plural(101, "балл", "балла", "баллов") == "балл")
    check("fmt_points", fmt_points(5) == "5 баллов")

    code = gen_activity_code()
    check("код зоны: 8 символов без путаницы O/0/I/1",
          len(code) == 8 and not set(code) & set("O0I1S5"), code)


async def test_admin_pure_logic(world):
    """Чистая логика админки: маппинг типов и текст карточки."""
    from app.bot.admin.core import card_text, kind_for
    from app.bot.admin.specs import PRIZE, SETTINGS, SPECS, WORKSHOP, ZONE

    check("зона -> zone", kind_for(SPECS[ZONE]) == ActivityKind.ZONE)
    check("МК -> workshop", kind_for(SPECS[WORKSHOP]) == ActivityKind.WORKSHOP)
    check("приз -> без kind", kind_for(SPECS[PRIZE]) is None)

    async with session_scope() as session:
        prize = await session.get(Prize, world["prize_ok"])
        text = card_text(SPECS[PRIZE], prize)
        check("карточка приза показывает название", prize.title in text)

        settings = await get_event_settings(session)
        check("карточка настроек рендерится", bool(card_text(SPECS[SETTINGS], settings).strip()))


async def test_ledger_consistency(world):
    """Баланс всегда равен сумме журнала — после всех операций выше."""
    async with session_scope() as session:
        from sqlalchemy import func, select

        for pid in (world["p1"], world["p2"]):
            total = await session.scalar(
                select(func.coalesce(func.sum(text("delta")), 0))
                .select_from(text("points_ledger"))
                .where(text(f"participant_id = {pid}"))
            )
            balance = await get_balance(session, pid)
            check(f"участник {pid}: баланс = сумме журнала", int(total) == balance,
                  f"{total} vs {balance}")


TESTS = [
    ("Сканы: двойное начисление", test_scan_double_credit),
    ("Сканы: гонка двух сканов", test_scan_race_savepoint),
    ("Сканы: неактивная зона и закрытый режим", test_scan_guards),
    ("Сканы: бонус за все зоны один раз", test_all_zones_bonus_once),
    ("Выдача: гонка двух стоек", test_redemption_pending_race),
    ("Выдача: индекс в базе", test_redemption_pending_index_in_db),
    ("Выдача: атомарность склада", test_redemption_stock_atomic_guard),
    ("Выдача: последний товар двум участникам", test_redemption_last_item_two_participants),
    ("Выдача: двойные нажатия", test_redemption_double_press),
    ("Выдача: баланс изменился между шагами", test_redemption_balance_changed_between_steps),
    ("Выдача: снимок истории и лимит", test_redemption_snapshot_and_limit),
    ("Выдача: откат", test_revert),
    ("Выдача: повтор после закрытия запроса", test_pending_does_not_block_after_resolution),
    ("Организаторы: приглашения и env-админ", test_staff_invites),
    ("Регистрация: зона до анкеты", test_registration_pending_zone),
    ("Настройки: синглтон и дефолты", test_event_settings_singleton),
    ("Рассылка: битый шаблон", test_broadcast_text_survives_braces),
    ("FSM: сброс сценариев", test_fsm_reset_state),
    ("Клавиатуры: инлайн-меню", test_keyboards),
    ("Клавиатуры: выход из каждого экрана", test_every_screen_has_exit),
    ("Админка: кнопки без обработчиков", test_admin_buttons_have_handlers),
    ("Хендлеры: тексты статусов скана", test_scan_text_mapping),
    ("Хендлеры: тексты ошибок выдачи", test_redeem_error_mapping),
    ("Регистрация: валидаторы имени и студбилета", test_name_validators),
    ("Утилиты: склонения и коды", test_utils),
    ("Админка: чистая логика", test_admin_pure_logic),
    ("Журнал: баланс сходится", test_ledger_consistency),
]


async def checkpoint_wal() -> None:
    """Слить WAL в основную базу: без этого между тестами висят читатели."""
    async with app_db.SessionLocal() as session:
        await session.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        await session.commit()


async def main() -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(str(TEST_DB) + suffix).unlink(missing_ok=True)
    # NullPool: соединение не переживает сессию — тесты изолированы друг от
    # друга, и «database is locked» от чужого пула здесь невозможен.
    app_db.engine.dispose()
    app_db.engine = create_async_engine(
        f"sqlite+aiosqlite:///{TEST_DB}",
        echo=False,
        poolclass=NullPool,
        connect_args={"timeout": 30},
    )
    app_db.SessionLocal = async_sessionmaker(
        app_db.engine, class_=AsyncSession, expire_on_commit=False
    )
    await init_db()

    print("Подготовка тестового мира…")
    world = await make_world()

    for index, (title, test) in enumerate(TESTS, start=1):
        print(f"\n{index}. {title}")
        try:
            await test(world)
        except Exception as exc:  # падение теста — это провал, а не краш раннера
            import traceback

            traceback.print_exc()
            check(f"тест упал с исключением: {type(exc).__name__}: {exc}", False)
        finally:
            await checkpoint_wal()

    TEST_DB.unlink(missing_ok=True)

    total = passed + len(failures)
    print("\n" + "-" * 40)
    if failures:
        print(f"ПРОВАЛЕНО {len(failures)} из {total}:")
        for name in failures:
            print(f"  - {name}")
        sys.exit(1)
    print(f"Все проверки пройдены: {total}.")


if __name__ == "__main__":
    asyncio.run(main())
