"""Обмен баллов на призы.

Схема двухшаговая: организатор выбирает приз, участник подтверждает списание
у себя в боте. Так закрывается сразу два риска — ошибка организатора
и списание по чужому QR-коду.
"""

from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    LedgerReason,
    Participant,
    Prize,
    Redemption,
    RedemptionStatus,
    utcnow,
)
from app.services.event import get_event_settings
from app.services.points import add_points, get_balance


class RedeemStatus:
    OK = "ok"
    NOT_ENOUGH_POINTS = "not_enough_points"
    OUT_OF_STOCK = "out_of_stock"
    LIMIT_REACHED = "limit_reached"
    INACTIVE = "inactive"
    CLOSED = "closed"
    HAS_PENDING = "has_pending"
    NOT_FOUND = "not_found"
    BLOCKED = "blocked"


@dataclass
class RedeemResult:
    status: str
    redemption: Redemption | None = None
    prize: Prize | None = None
    balance: int = 0
    missing: int = 0

    @property
    def ok(self) -> bool:
        return self.status == RedeemStatus.OK


async def active_prizes(session: AsyncSession) -> list[Prize]:
    rows = await session.scalars(
        select(Prize).where(Prize.is_active.is_(True)).order_by(Prize.cost_points, Prize.sort_order)
    )
    return list(rows.all())


async def user_redemption_count(
    session: AsyncSession, participant_id: int, prize_id: int
) -> int:
    """Сколько раз участник уже получал этот приз (учитываем и ожидающие подтверждения)."""
    total = await session.scalar(
        select(func.count(Redemption.id)).where(
            Redemption.participant_id == participant_id,
            Redemption.prize_id == prize_id,
            Redemption.status.in_([RedemptionStatus.PENDING, RedemptionStatus.CONFIRMED]),
        )
    )
    return int(total or 0)


async def get_pending_redemption(
    session: AsyncSession, participant_id: int
) -> Redemption | None:
    return await session.scalar(
        select(Redemption)
        .where(
            Redemption.participant_id == participant_id,
            Redemption.status == RedemptionStatus.PENDING,
        )
        .order_by(Redemption.created_at.desc())
    )


async def participant_redemptions(
    session: AsyncSession, participant_id: int
) -> list[Redemption]:
    rows = await session.scalars(
        select(Redemption)
        .where(
            Redemption.participant_id == participant_id,
            Redemption.status == RedemptionStatus.CONFIRMED,
        )
        .order_by(Redemption.created_at.desc())
    )
    return list(rows.all())


async def create_redemption(
    session: AsyncSession,
    participant: Participant,
    prize_id: int,
    *,
    staff_id: int | None,
) -> RedeemResult:
    """Организатор выбрал приз. Резервируем товар и ждём подтверждения участника."""
    if participant.is_blocked:
        return RedeemResult(status=RedeemStatus.BLOCKED)

    event = await get_event_settings(session)
    if not event.is_redemption_open:
        return RedeemResult(status=RedeemStatus.CLOSED)

    prize = await session.get(Prize, prize_id)
    if prize is None:
        return RedeemResult(status=RedeemStatus.NOT_FOUND)
    if not prize.is_active:
        return RedeemResult(status=RedeemStatus.INACTIVE, prize=prize)

    if await get_pending_redemption(session, participant.id) is not None:
        return RedeemResult(status=RedeemStatus.HAS_PENDING, prize=prize)

    if prize.stock_left <= 0:
        return RedeemResult(status=RedeemStatus.OUT_OF_STOCK, prize=prize)

    if prize.per_user_limit > 0:
        already = await user_redemption_count(session, participant.id, prize.id)
        if already >= prize.per_user_limit:
            return RedeemResult(status=RedeemStatus.LIMIT_REACHED, prize=prize)

    balance = await get_balance(session, participant.id)
    if balance < prize.cost_points:
        return RedeemResult(
            status=RedeemStatus.NOT_ENOUGH_POINTS,
            prize=prize,
            balance=balance,
            missing=prize.cost_points - balance,
        )

    # Резервируем сразу: иначе две стойки одновременно раздадут последний
    # экземпляр. Декремент атомарным UPDATE, а не через ORM-атрибут:
    # два параллельных запроса не должны суметь списать один и тот же товар.
    prize_id = prize.id  # до expire: доступ к атрибуту после него = sync-загрузка
    reserved = await session.execute(
        update(Prize)
        .where(Prize.id == prize_id, Prize.stock_left > 0)
        .values(stock_left=Prize.stock_left - 1)
    )
    if reserved.rowcount == 0:
        return RedeemResult(status=RedeemStatus.OUT_OF_STOCK, prize=prize)
    # Синхронизируем память с БД асинхронно: expire здесь дал бы ленивую
    # sync-загрузку при следующем обращении к остатку (MissingGreenlet).
    await session.refresh(prize, ["stock_left"])

    redemption = Redemption(
        participant_id=participant.id,
        prize_id=prize_id,
        staff_id=staff_id,
        prize_title=prize.title,
        cost_points=prize.cost_points,
        status=RedemptionStatus.PENDING,
    )
    try:
        # SAVEPOINT: гонка «два организатора сканируют одного участника»
        # ловится индексом uq_redemption_pending_per_participant.
        async with session.begin_nested():
            session.add(redemption)
            await session.flush()
    except IntegrityError:
        await _release_stock(session, redemption)
        return RedeemResult(status=RedeemStatus.HAS_PENDING, prize=prize)

    return RedeemResult(
        status=RedeemStatus.OK, redemption=redemption, prize=prize, balance=balance
    )


async def confirm_redemption(
    session: AsyncSession, redemption: Redemption
) -> RedeemResult:
    """Участник подтвердил. Списываем баллы."""
    if redemption.status != RedemptionStatus.PENDING:
        return RedeemResult(status=RedeemStatus.NOT_FOUND, redemption=redemption)

    balance = await get_balance(session, redemption.participant_id)
    if balance < redemption.cost_points:
        # Баланс мог измениться между выбором и подтверждением.
        await _release_stock(session, redemption)
        redemption.status = RedemptionStatus.CANCELLED
        redemption.resolved_at = utcnow()
        redemption.comment = "Не хватило баллов на момент подтверждения"
        await session.flush()
        return RedeemResult(
            status=RedeemStatus.NOT_ENOUGH_POINTS,
            redemption=redemption,
            balance=balance,
            missing=redemption.cost_points - balance,
        )

    await add_points(
        session,
        participant_id=redemption.participant_id,
        delta=-redemption.cost_points,
        reason=LedgerReason.REDEMPTION,
        ref_type="redemption",
        ref_id=redemption.id,
        staff_id=redemption.staff_id,
        comment=redemption.prize_title,
    )
    redemption.status = RedemptionStatus.CONFIRMED
    redemption.resolved_at = utcnow()
    await session.flush()

    new_balance = await get_balance(session, redemption.participant_id)
    return RedeemResult(status=RedeemStatus.OK, redemption=redemption, balance=new_balance)


async def cancel_redemption(
    session: AsyncSession, redemption: Redemption, *, comment: str | None = None
) -> None:
    """Отмена до подтверждения: возвращаем товар на склад, баллы не трогали."""
    if redemption.status != RedemptionStatus.PENDING:
        return
    await _release_stock(session, redemption)
    redemption.status = RedemptionStatus.CANCELLED
    redemption.resolved_at = utcnow()
    redemption.comment = comment
    await session.flush()


async def revert_redemption(
    session: AsyncSession,
    redemption: Redemption,
    *,
    staff_id: int | None,
    comment: str,
) -> None:
    """Откат уже выданного приза: возвращаем баллы и товар."""
    if redemption.status != RedemptionStatus.CONFIRMED:
        return
    await add_points(
        session,
        participant_id=redemption.participant_id,
        delta=redemption.cost_points,
        reason=LedgerReason.REVERT,
        ref_type="redemption",
        ref_id=redemption.id,
        staff_id=staff_id,
        comment=comment,
    )
    await _release_stock(session, redemption)
    redemption.status = RedemptionStatus.REVERTED
    redemption.resolved_at = utcnow()
    redemption.comment = comment
    await session.flush()


async def _release_stock(session: AsyncSession, redemption: Redemption) -> None:
    if redemption.prize_id is None:
        return
    # Атомарный инкремент: параллельные возвраты не потеряют единицу товара.
    await session.execute(
        update(Prize)
        .where(Prize.id == redemption.prize_id)
        .values(stock_left=Prize.stock_left + 1)
    )
