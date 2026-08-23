"""Участники: поиск, карточка с историей баллов, ручные операции и выгрузка."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import String, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.admin.helpers import form_int, form_str, render
from app.db import get_session
from app.models import (
    Activity,
    Participant,
    PointsLedger,
    Redemption,
    RedemptionStatus,
    Visit,
)
from app.services.exports import participants_csv
from app.services.points import add_points, get_balance
from app.services.prizes import revert_redemption

router = APIRouter(prefix="/participants")

PAGE_SIZE = 50


@router.get("")
async def list_participants(
    request: Request,
    q: str = "",
    page: int = 1,
    session: AsyncSession = Depends(get_session),
):
    query = select(Participant).options(selectinload(Participant.faculty))
    term = q.strip()
    if term:
        pattern = f"%{term}%"
        query = query.where(
            or_(
                Participant.first_name.ilike(pattern),
                Participant.last_name.ilike(pattern),
                Participant.student_id.ilike(pattern),
                Participant.short_code.ilike(pattern),
                Participant.tg_username.ilike(pattern),
                Participant.tg_id.cast(String).ilike(pattern),
            )
        )

    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    page = max(1, page)
    rows = await session.scalars(
        query.order_by(Participant.created_at.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    participants = list(rows.all())

    balances = {}
    if participants:
        balance_rows = await session.execute(
            select(PointsLedger.participant_id, func.sum(PointsLedger.delta))
            .where(PointsLedger.participant_id.in_([p.id for p in participants]))
            .group_by(PointsLedger.participant_id)
        )
        balances = {pid: int(value or 0) for pid, value in balance_rows.all()}

    return render(
        request,
        "participants.html",
        active="participants",
        participants=participants,
        balances=balances,
        q=term,
        page=page,
        total=total or 0,
        pages=max(1, ((total or 0) + PAGE_SIZE - 1) // PAGE_SIZE),
    )


@router.get("/export.csv")
async def export_csv(session: AsyncSession = Depends(get_session)):
    """Выгрузка для отчёта. Объявлено до /{participant_id}, иначе маршрут перехватится."""
    payload = await participants_csv(session)
    return Response(
        payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="festart-participants.csv"'},
    )


@router.get("/{participant_id}")
async def participant_detail(
    request: Request, participant_id: int, session: AsyncSession = Depends(get_session)
):
    participant = await session.get(
        Participant, participant_id, options=[selectinload(Participant.faculty)]
    )
    if participant is None:
        return RedirectResponse("/participants", status_code=303)

    ledger_rows = await session.scalars(
        select(PointsLedger)
        .where(PointsLedger.participant_id == participant_id)
        .order_by(PointsLedger.created_at.desc())
    )
    visit_rows = await session.execute(
        select(Visit, Activity)
        .join(Activity, Activity.id == Visit.activity_id)
        .where(Visit.participant_id == participant_id)
        .order_by(Visit.created_at.desc())
    )
    redemption_rows = await session.scalars(
        select(Redemption)
        .where(Redemption.participant_id == participant_id)
        .order_by(Redemption.created_at.desc())
    )

    return render(
        request,
        "participant.html",
        active="participants",
        participant=participant,
        balance=await get_balance(session, participant_id),
        ledger=list(ledger_rows.all()),
        visits=list(visit_rows.all()),
        redemptions=list(redemption_rows.all()),
        confirmed_status=RedemptionStatus.CONFIRMED,
    )


@router.post("/{participant_id}/points")
async def manual_points(
    participant_id: int,
    delta: str = Form("0"),
    comment: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    value = form_int(delta)
    if value:
        await add_points(
            session,
            participant_id=participant_id,
            delta=value,
            reason="manual",
            comment=form_str(comment) or "Правка через админку",
        )
    return RedirectResponse(f"/participants/{participant_id}", status_code=303)


@router.post("/{participant_id}/block")
async def toggle_block(participant_id: int, session: AsyncSession = Depends(get_session)):
    participant = await session.get(Participant, participant_id)
    if participant is not None:
        participant.is_blocked = not participant.is_blocked
    return RedirectResponse(f"/participants/{participant_id}", status_code=303)


@router.post("/redemptions/{redemption_id}/revert")
async def revert(
    redemption_id: int,
    comment: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    redemption = await session.get(Redemption, redemption_id)
    if redemption is None:
        return RedirectResponse("/participants", status_code=303)
    await revert_redemption(
        session,
        redemption,
        staff_id=None,
        comment=form_str(comment) or "Откат через админку",
    )
    return RedirectResponse(f"/participants/{redemption.participant_id}", status_code=303)
