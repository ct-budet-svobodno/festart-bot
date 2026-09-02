"""Участники: создание, поиск, завершение регистрации."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Faculty, LedgerReason, Participant, utcnow
from app.services.event import get_event_settings
from app.services.points import add_points
from app.utils import gen_short_code, gen_token


async def _unique_short_code(session: AsyncSession) -> str:
    for _ in range(20):
        code = gen_short_code()
        exists = await session.scalar(
            select(Participant.id).where(Participant.short_code == code)
        )
        if exists is None:
            return code
    raise RuntimeError("Не удалось выдать уникальный короткий код")


async def get_or_create_participant(
    session: AsyncSession,
    *,
    tg_id: int,
    username: str | None = None,
) -> tuple[Participant, bool]:
    """Возвращает (участник, создан_ли_только_что)."""
    participant = await session.scalar(select(Participant).where(Participant.tg_id == tg_id))
    if participant is not None:
        if username and participant.tg_username != username:
            participant.tg_username = username
        participant.last_seen_at = utcnow()
        return participant, False

    participant = Participant(
        tg_id=tg_id,
        tg_username=username,
        qr_token=gen_token(),
        short_code=await _unique_short_code(session),
        last_seen_at=utcnow(),
    )
    session.add(participant)
    await session.flush()
    return participant, True


async def find_by_qr_token(session: AsyncSession, token: str) -> Participant | None:
    return await session.scalar(select(Participant).where(Participant.qr_token == token))


async def find_by_short_code(session: AsyncSession, code: str) -> Participant | None:
    return await session.scalar(select(Participant).where(Participant.short_code == code.strip()))


async def find_by_student_id(session: AsyncSession, student_id: str) -> Participant | None:
    return await session.scalar(
        select(Participant).where(Participant.student_id == student_id.strip())
    )


async def is_student_id_taken(
    session: AsyncSession, student_id: str, *, exclude_participant_id: int | None = None
) -> bool:
    query = select(Participant.id).where(Participant.student_id == student_id.strip())
    if exclude_participant_id is not None:
        query = query.where(Participant.id != exclude_participant_id)
    return await session.scalar(query) is not None


async def get_faculties(session: AsyncSession) -> list[Faculty]:
    rows = await session.scalars(
        select(Faculty).where(Faculty.is_active.is_(True)).order_by(Faculty.sort_order, Faculty.id)
    )
    return list(rows.all())


async def complete_registration(
    session: AsyncSession,
    participant: Participant,
    *,
    first_name: str,
    last_name: str,
    middle_name: str | None,
    faculty_id: int | None,
    faculty_other: str | None,
    student_id: str,
) -> int:
    """Завершает регистрацию и начисляет приветственный бонус.

    Возвращает начисленный бонус (0, если он отключён).
    """
    participant.first_name = first_name.strip()
    participant.last_name = last_name.strip()
    participant.middle_name = middle_name.strip() if middle_name else None
    participant.faculty_id = faculty_id
    participant.faculty_other = faculty_other
    participant.student_id = student_id.strip()
    participant.is_registered = True
    participant.registered_at = utcnow()

    event = await get_event_settings(session)
    bonus = event.registration_bonus
    if bonus > 0:
        await add_points(
            session,
            participant_id=participant.id,
            delta=bonus,
            reason=LedgerReason.REGISTRATION,
            comment="Регистрация в боте",
        )
    await session.flush()
    return bonus
