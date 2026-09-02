"""Выгрузки: CSV участников и архив печатных плакатов.

Отдаются участнику и организатору прямо в чат — бот умеет слать файлы.
"""

import csv
import io
import zipfile

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Activity, Participant, PointsLedger, Visit
from app.services.qr import activity_link, make_poster_png
from app.utils import fmt_dt


async def participants_csv(session: AsyncSession) -> bytes:
    rows = await session.scalars(
        select(Participant)
        .options(selectinload(Participant.faculty))
        .order_by(Participant.created_at)
    )
    participants = list(rows.all())

    balance_rows = await session.execute(
        select(PointsLedger.participant_id, func.sum(PointsLedger.delta)).group_by(
            PointsLedger.participant_id
        )
    )
    balances = {pid: int(value or 0) for pid, value in balance_rows.all()}

    visit_rows = await session.execute(
        select(Visit.participant_id, func.count(Visit.id)).group_by(Visit.participant_id)
    )
    visits = dict(visit_rows.all())

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        ["Фамилия", "Имя", "Факультет", "Студбилет", "Баллы", "Зон пройдено",
         "Telegram", "Код", "Зарегистрирован"]
    )
    for p in participants:
        writer.writerow([
            p.last_name or "",
            p.first_name or "",
            p.faculty_title,
            p.student_id or "",
            balances.get(p.id, 0),
            visits.get(p.id, 0),
            f"@{p.tg_username}" if p.tg_username else p.tg_id,
            p.short_code,
            fmt_dt(p.registered_at, "%d.%m.%Y %H:%M"),
        ])

    # BOM, иначе Excel откроет кириллицу кракозябрами.
    return ("﻿" + buffer.getvalue()).encode("utf-8")


async def posters_zip(session: AsyncSession, kind: str) -> tuple[bytes, int]:
    """Архив плакатов A4. Возвращает (архив, сколько внутри)."""
    rows = await session.scalars(
        select(Activity)
        .where(Activity.kind == kind, Activity.is_active.is_(True))
        .order_by(Activity.sort_order, Activity.id)
    )
    activities = list(rows.all())

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for activity in activities:
            png = make_poster_png(
                activity_link(activity.code),
                activity.title,
                subtitle=f"+{activity.points} за посещение",
            )
            safe = "".join(c if c.isalnum() or c in " -_" else "" for c in activity.title)[:40]
            archive.writestr(f"{safe.strip() or activity.code}.png", png)

    return buffer.getvalue(), len(activities)
