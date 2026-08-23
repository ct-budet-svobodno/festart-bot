"""Ссылка-приглашение для организаторов.

    python -m scripts.invite            # показать всех
    python -m scripts.invite "Имя"      # создать нового суперадмина

Нужен, чтобы попасть в админку в первый раз: в боте она открывается только
тем, кто уже привязан как организатор.
"""

import asyncio
import sys

from app.db import init_db, session_scope
from app.models import StaffRole
from app.services.qr import staff_link
from app.services.staff import create_staff, list_staff


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        if len(sys.argv) > 1:
            member = await create_staff(
                session, name=" ".join(sys.argv[1:]), role=StaffRole.SUPERADMIN
            )
            print(f"Создан суперадмин: {member.name}\n{staff_link(member.invite_token)}")
            return

        members = await list_staff(session)
        if not members:
            print("Организаторов нет. Создать: python -m scripts.invite \"Имя Фамилия\"")
            return

        for member in members:
            status = "подключён" if member.is_activated else "ждёт перехода по ссылке"
            print(f"\n{member.name} — {member.role_label} ({status})")
            if not member.is_activated:
                print(f"  {staff_link(member.invite_token)}")


if __name__ == "__main__":
    asyncio.run(main())
