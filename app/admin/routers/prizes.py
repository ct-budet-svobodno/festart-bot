"""Призы: список, добавление, правка цены и остатков на ходу."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.helpers import form_bool, form_int, form_str, render
from app.db import get_session
from app.models import Prize, Redemption, RedemptionStatus

router = APIRouter(prefix="/prizes")


@router.get("")
async def list_prizes(request: Request, session: AsyncSession = Depends(get_session)):
    rows = await session.scalars(select(Prize).order_by(Prize.sort_order, Prize.cost_points))
    prizes = list(rows.all())

    issued_rows = await session.execute(
        select(Redemption.prize_id, func.count(Redemption.id))
        .where(Redemption.status == RedemptionStatus.CONFIRMED)
        .group_by(Redemption.prize_id)
    )
    issued = dict(issued_rows.all())

    return render(
        request, "prizes.html", active="prizes", prizes=prizes, issued=issued
    )


@router.get("/new")
async def new_prize(request: Request):
    return render(request, "prize_form.html", active="prizes", prize=None)


@router.get("/{prize_id}")
async def edit_prize(
    request: Request, prize_id: int, session: AsyncSession = Depends(get_session)
):
    prize = await session.get(Prize, prize_id)
    if prize is None:
        return RedirectResponse("/prizes", status_code=303)
    return render(request, "prize_form.html", active="prizes", prize=prize)


@router.post("/save")
async def save_prize(
    prize_id: str = Form(""),
    title: str = Form(...),
    description: str = Form(""),
    cost_points: str = Form("1"),
    stock_total: str = Form("0"),
    stock_left: str = Form(""),
    per_user_limit: str = Form("1"),
    sort_order: str = Form("100"),
    is_active: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    prize = None
    if prize_id:
        prize = await session.get(Prize, int(prize_id))

    is_new = prize is None
    if is_new:
        prize = Prize()
        session.add(prize)

    previous_total = 0 if is_new else prize.stock_total
    new_total = max(0, form_int(stock_total))

    prize.title = title.strip()
    prize.description = form_str(description)
    prize.cost_points = max(0, form_int(cost_points, 1))
    prize.per_user_limit = max(0, form_int(per_user_limit, 1))
    prize.sort_order = form_int(sort_order, 100)
    prize.is_active = form_bool(is_active)
    prize.stock_total = new_total

    if is_new:
        prize.stock_left = new_total
    elif stock_left.strip():
        # Админ явно поправил остаток.
        prize.stock_left = max(0, form_int(stock_left))
    else:
        # Довезли товар: увеличиваем остаток ровно на прибавку, не затирая выданное.
        prize.stock_left = max(0, prize.stock_left + (new_total - previous_total))

    await session.flush()
    return RedirectResponse("/prizes", status_code=303)


@router.post("/{prize_id}/restock")
async def restock(
    prize_id: int, amount: str = Form("0"), session: AsyncSession = Depends(get_session)
):
    """Быстрая кнопка «довезли ещё» прямо из списка."""
    prize = await session.get(Prize, prize_id)
    if prize is not None:
        delta = form_int(amount)
        prize.stock_total = max(0, prize.stock_total + delta)
        prize.stock_left = max(0, prize.stock_left + delta)
    return RedirectResponse("/prizes", status_code=303)


@router.post("/{prize_id}/delete")
async def delete_prize(prize_id: int, session: AsyncSession = Depends(get_session)):
    prize = await session.get(Prize, prize_id)
    if prize is not None:
        await session.delete(prize)
    return RedirectResponse("/prizes", status_code=303)
