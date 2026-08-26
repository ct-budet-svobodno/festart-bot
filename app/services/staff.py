"""Организаторы: приглашения и привязка Telegram."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Staff, StaffRole, utcnow
from app.utils import gen_token


async def get_staff_by_tg(session: AsyncSession, tg_id: int) -> Staff | None:
    return await session.scalar(
        select(Staff).where(Staff.tg_id == tg_id, Staff.is_active.is_(True))
    )


async def resolve_staff(
    session: AsyncSession, tg_id: int, username: str | None = None
) -> Staff | None:
    """Организатор по Telegram ID.

    Аккаунты из ADMIN_TG_IDS заводятся сами при первом обращении и всегда
    остаются активными суперадминами. Это аварийный вход: даже если все
    приглашения протухли или базу пересоздали, попасть в админку можно.
    """
    staff = await session.scalar(select(Staff).where(Staff.tg_id == tg_id))

    if tg_id in settings.admin_ids:
        if staff is None:
            staff = Staff(
                tg_id=tg_id,
                tg_username=username,
                name=f"@{username}" if username else f"id{tg_id}",
                role=StaffRole.SUPERADMIN,
                invite_token=gen_token(),
                activated_at=utcnow(),
            )
            session.add(staff)
            await session.flush()
        else:
            staff.role = StaffRole.SUPERADMIN
            staff.is_active = True
            if username and staff.tg_username != username:
                staff.tg_username = username
        return staff

    if staff is None or not staff.is_active:
        return None
    return staff


async def create_staff(
    session: AsyncSession, *, name: str, role: str = StaffRole.ADMIN
) -> Staff:
    staff = Staff(name=name.strip(), role=role, invite_token=gen_token())
    session.add(staff)
    await session.flush()
    return staff


async def activate_staff(
    session: AsyncSession, token: str, *, tg_id: int, username: str | None
) -> Staff | None:
    """Привязывает Telegram организатора по ссылке-приглашению.

    Ссылка одноразовая: после активации повторный переход ничего не меняет,
    а чужой Telegram по ней уже не привяжется.
    """
    staff = await session.scalar(
        select(Staff).where(Staff.invite_token == token, Staff.is_active.is_(True))
    )
    if staff is None:
        return None
    if staff.tg_id is not None:
        return staff if staff.tg_id == tg_id else None

    staff.tg_id = tg_id
    staff.tg_username = username
    staff.activated_at = utcnow()
    await session.flush()
    return staff


async def list_staff(session: AsyncSession) -> list[Staff]:
    rows = await session.scalars(select(Staff).order_by(Staff.role, Staff.name))
    return list(rows.all())
