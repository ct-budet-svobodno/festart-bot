"""Начисление и учёт баллов.

Баланс всегда вычисляется из журнала PointsLedger, отдельного поля «баланс» нет.
Любая операция оставляет след: кто, кому, сколько и почему.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Activity,
    ActivityKind,
    LedgerReason,
    Participant,
    PointsLedger,
    Visit,
    VisitSource,
)
from app.services.event import get_event_settings

REF_ALL_ZONES = "all_zones"


class ScanStatus:
    OK = "ok"  # баллы начислены
    ALREADY = "already"  # эта зона уже пройдена
    NOT_FOUND = "not_found"  # кода нет в базе
    INACTIVE = "inactive"  # зона выключена админом
    CLOSED = "closed"  # сканирование остановлено на уровне мероприятия
    BLOCKED = "blocked"  # участник заблокирован администратором


@dataclass
class ScanResult:
    status: str
    activity: Activity | None = None
    points: int = 0
    bonus: int = 0
    balance: int = 0
    visited_zones: int = 0
    total_zones: int = 0

    @property
    def ok(self) -> bool:
        return self.status == ScanStatus.OK


async def get_balance(session: AsyncSession, participant_id: int) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(PointsLedger.delta), 0)).where(
            PointsLedger.participant_id == participant_id
        )
    )
    return int(total or 0)


async def add_points(
    session: AsyncSession,
    *,
    participant_id: int,
    delta: int,
    reason: str,
    ref_type: str | None = None,
    ref_id: int | None = None,
    staff_id: int | None = None,
    comment: str | None = None,
) -> PointsLedger:
    entry = PointsLedger(
        participant_id=participant_id,
        delta=delta,
        reason=reason,
        ref_type=ref_type,
        ref_id=ref_id,
        staff_id=staff_id,
        comment=comment,
    )
    session.add(entry)
    await session.flush()
    return entry


async def visited_activity_ids(session: AsyncSession, participant_id: int) -> set[int]:
    rows = await session.scalars(
        select(Visit.activity_id).where(Visit.participant_id == participant_id)
    )
    return set(rows.all())


async def count_visits(session: AsyncSession, participant_id: int) -> int:
    total = await session.scalar(
        select(func.count(Visit.id)).where(Visit.participant_id == participant_id)
    )
    return int(total or 0)


async def zone_progress(session: AsyncSession, participant_id: int) -> tuple[int, int]:
    """(пройдено активных зон, всего активных зон). Мастер-классы не считаются."""
    zone_ids = set(
        (
            await session.scalars(
                select(Activity.id).where(
                    Activity.kind == ActivityKind.ZONE, Activity.is_active.is_(True)
                )
            )
        ).all()
    )
    visited = await visited_activity_ids(session, participant_id)
    return len(zone_ids & visited), len(zone_ids)


async def _has_bonus(session: AsyncSession, participant_id: int, ref_type: str) -> bool:
    found = await session.scalar(
        select(PointsLedger.id).where(
            PointsLedger.participant_id == participant_id,
            PointsLedger.reason == LedgerReason.BONUS,
            PointsLedger.ref_type == ref_type,
        )
    )
    return found is not None


async def _maybe_award_all_zones_bonus(
    session: AsyncSession, participant: Participant
) -> tuple[int, int, int]:
    """Бонус за прохождение всех активных зон.

    Возвращает (начислено бонуса, пройдено зон, всего зон).
    """
    visited_zones, total_zones = await zone_progress(session, participant.id)

    if not total_zones or visited_zones < total_zones:
        return 0, visited_zones, total_zones

    event = await get_event_settings(session)
    if event.all_zones_bonus <= 0:
        return 0, visited_zones, total_zones
    if await _has_bonus(session, participant.id, REF_ALL_ZONES):
        return 0, visited_zones, total_zones

    await add_points(
        session,
        participant_id=participant.id,
        delta=event.all_zones_bonus,
        reason=LedgerReason.BONUS,
        ref_type=REF_ALL_ZONES,
        comment="Пройдены все зоны",
    )
    return event.all_zones_bonus, visited_zones, total_zones


async def register_scan(
    session: AsyncSession,
    participant: Participant,
    code: str,
    *,
    source: str = VisitSource.QR,
    staff_id: int | None = None,
) -> ScanResult:
    """Обработка отсканированного QR-кода зоны."""
    if participant.is_blocked:
        return ScanResult(status=ScanStatus.BLOCKED)

    event = await get_event_settings(session)
    if not event.is_scanning_open and source == VisitSource.QR:
        return ScanResult(status=ScanStatus.CLOSED)

    activity = await session.scalar(select(Activity).where(Activity.code == code))
    if activity is None:
        return ScanResult(status=ScanStatus.NOT_FOUND)
    if not activity.is_active:
        return ScanResult(status=ScanStatus.INACTIVE, activity=activity)

    try:
        # SAVEPOINT, а не общий rollback: ловим гонку двух быстрых сканов
        # одного кода, не откатывая остальную работу сессии.
        async with session.begin_nested():
            session.add(
                Visit(
                    participant_id=participant.id,
                    activity_id=activity.id,
                    points_awarded=activity.points,
                    source=source,
                    staff_id=staff_id,
                )
            )
            await session.flush()
    except IntegrityError:
        balance = await get_balance(session, participant.id)
        visited_zones, total_zones = await zone_progress(session, participant.id)
        return ScanResult(
            status=ScanStatus.ALREADY,
            activity=activity,
            balance=balance,
            visited_zones=visited_zones,
            total_zones=total_zones,
        )

    visit = await session.scalar(
        select(Visit).where(
            Visit.participant_id == participant.id, Visit.activity_id == activity.id
        )
    )

    await add_points(
        session,
        participant_id=participant.id,
        delta=activity.points,
        reason=LedgerReason.VISIT,
        ref_type="visit",
        ref_id=visit.id if visit else None,
        staff_id=staff_id,
        comment=activity.title,
    )

    bonus, visited_zones, total_zones = await _maybe_award_all_zones_bonus(session, participant)
    balance = await get_balance(session, participant.id)

    return ScanResult(
        status=ScanStatus.OK,
        activity=activity,
        points=activity.points,
        bonus=bonus,
        balance=balance,
        visited_zones=visited_zones,
        total_zones=total_zones,
    )


async def award_manual(
    session: AsyncSession,
    participant: Participant,
    delta: int,
    *,
    staff_id: int | None,
    comment: str,
) -> int:
    """Ручное начисление организатором: QR не сработал, спорная ситуация и т.п."""
    await add_points(
        session,
        participant_id=participant.id,
        delta=delta,
        reason=LedgerReason.MANUAL,
        staff_id=staff_id,
        comment=comment,
    )
    return await get_balance(session, participant.id)
