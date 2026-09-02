"""Сборка текстов сообщений. Держим отдельно от хендлеров, чтобы одни и те же
карточки можно было показывать из разных мест."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, ActivityKind, Faculty, Participant, Visit
from app.services.points import get_balance, zone_progress
from app.services.prizes import participant_redemptions
from app.utils import fmt_points, fmt_time


async def _faculty_title(session: AsyncSession, participant: Participant) -> str:
    if participant.faculty_id:
        faculty = await session.get(Faculty, participant.faculty_id)
        if faculty:
            return faculty.title
    return participant.faculty_other or "—"


async def profile_text(session: AsyncSession, participant: Participant) -> str:
    balance = await get_balance(session, participant.id)
    visited, total = await zone_progress(session, participant.id)
    prizes = await participant_redemptions(session, participant.id)
    faculty_title = await _faculty_title(session, participant)

    lines = [
        f"<b>ФИО:</b> {participant.full_name}",
        f"<b>Факультет:</b> {faculty_title}",
        "",
        f"🏆 Баллы: <b>{fmt_points(balance)}</b>",
        f"✅ Зоны: <b>{visited} из {total}</b>",
    ]
    if prizes:
        titles = ", ".join(r.prize_title for r in prizes[:5])
        lines.append(f"🎁 Получено: {titles}")
    return "\n".join(lines)


async def zones_text(session: AsyncSession, participant: Participant) -> str:
    rows = await session.scalars(
        select(Activity)
        .where(Activity.is_active.is_(True))
        .order_by(Activity.kind, Activity.sort_order, Activity.id)
    )
    activities = list(rows.all())
    if not activities:
        return "Зоны пока не добавлены. Загляни позже."

    visited_rows = await session.scalars(
        select(Visit.activity_id).where(Visit.participant_id == participant.id)
    )
    visited = set(visited_rows.all())

    zones = [a for a in activities if a.kind == ActivityKind.ZONE]
    workshops = [a for a in activities if a.kind == ActivityKind.WORKSHOP]

    lines: list[str] = []
    if zones:
        done = sum(1 for z in zones if z.id in visited)
        lines.append(f"<b>Зоны — {done} из {len(zones)}</b>")
        for zone in zones:
            mark = "✅" if zone.id in visited else "▫️"
            lines.append(f"{mark} {zone.title} · {zone.points}")
        lines.append("")

    if workshops:
        done = sum(1 for w in workshops if w.id in visited)
        lines.append(f"<b>Мастер-классы — {done} из {len(workshops)}</b>")
        for workshop in workshops:
            mark = "✅" if workshop.id in visited else "▫️"
            when = f" · {fmt_time(workshop.starts_at)}" if workshop.starts_at else ""
            lines.append(f"{mark} {workshop.title}{when}")

    return "\n".join(lines)


def workshop_card(workshop: Activity) -> str:
    lines = [f"<b>{workshop.title}</b>"]
    when = ""
    if workshop.starts_at:
        when = fmt_time(workshop.starts_at)
        if workshop.ends_at:
            when += f" – {fmt_time(workshop.ends_at)}"
    meta = " · ".join(x for x in (when, workshop.location) if x)
    if meta:
        lines.append(meta)
    if workshop.description:
        lines.append("")
        lines.append(workshop.description)
    lines.append("")
    lines.append(f"За участие: <b>{fmt_points(workshop.points)}</b>")
    return "\n".join(lines)


async def staff_participant_card(session: AsyncSession, participant: Participant) -> str:
    """Карточка участника глазами организатора на стойке призов."""
    balance = await get_balance(session, participant.id)
    visited, total = await zone_progress(session, participant.id)
    prizes = await participant_redemptions(session, participant.id)
    faculty_title = await _faculty_title(session, participant)

    lines = [
        f"<b>{participant.full_name}</b>",
        faculty_title,
        f"Студ. билет: <code>{participant.student_id or '—'}</code>",
        "",
        f"🏆 Баланс: <b>{fmt_points(balance)}</b>",
        f"✅ Пройдено зон: {visited} из {total}",
    ]
    if prizes:
        lines.append("")
        lines.append("<b>Уже получил:</b>")
        for r in prizes[:10]:
            lines.append(f"• {r.prize_title} ({r.cost_points})")
    return "\n".join(lines)
