"""Организаторы: список, приглашения, роли."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.helpers import render
from app.db import get_session
from app.models import Staff, StaffRole
from app.services.qr import make_qr_png, staff_link
from app.services.staff import create_staff, list_staff
from app.utils import gen_token

router = APIRouter(prefix="/staff")


@router.get("")
async def index(request: Request, session: AsyncSession = Depends(get_session)):
    members = await list_staff(session)
    return render(
        request,
        "staff.html",
        active="staff",
        members=members,
        roles=StaffRole.CHOICES,
        links={m.id: staff_link(m.invite_token) for m in members},
    )


@router.post("/add")
async def add(
    name: str = Form(...),
    role: str = Form(StaffRole.ADMIN),
    session: AsyncSession = Depends(get_session),
):
    await create_staff(session, name=name, role=role)
    return RedirectResponse("/staff", status_code=303)


@router.post("/{staff_id}/role")
async def change_role(
    staff_id: int, role: str = Form(...), session: AsyncSession = Depends(get_session)
):
    member = await session.get(Staff, staff_id)
    if member is not None and role in StaffRole.LABELS:
        member.role = role
    return RedirectResponse("/staff", status_code=303)


@router.post("/{staff_id}/toggle")
async def toggle(staff_id: int, session: AsyncSession = Depends(get_session)):
    member = await session.get(Staff, staff_id)
    if member is not None:
        member.is_active = not member.is_active
    return RedirectResponse("/staff", status_code=303)


@router.post("/{staff_id}/reset")
async def reset_link(staff_id: int, session: AsyncSession = Depends(get_session)):
    """Новая ссылка и отвязка Telegram — если человек потерял телефон
    или организатора заменили."""
    member = await session.get(Staff, staff_id)
    if member is not None:
        member.invite_token = gen_token()
        member.tg_id = None
        member.tg_username = None
        member.activated_at = None
    return RedirectResponse("/staff", status_code=303)


@router.post("/{staff_id}/delete")
async def delete(staff_id: int, session: AsyncSession = Depends(get_session)):
    member = await session.get(Staff, staff_id)
    if member is not None:
        await session.delete(member)
    return RedirectResponse("/staff", status_code=303)


@router.get("/{staff_id}/qr.png")
async def invite_qr(staff_id: int, session: AsyncSession = Depends(get_session)):
    member = await session.get(Staff, staff_id)
    if member is None:
        return Response(status_code=404)
    return Response(
        make_qr_png(staff_link(member.invite_token), box_size=10), media_type="image/png"
    )
