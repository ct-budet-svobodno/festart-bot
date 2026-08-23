"""Переключение роли тестового организатора (для проверки прав из одного аккаунта).

    python -m scripts.role superadmin
    python -m scripts.role admin
    python -m scripts.role off      # отвязать аккаунт от организатора

При первом запуске укажи свой Telegram ID вторым аргументом — дальше он запомнится:

    python -m scripts.role admin 123456789

Роль читается из базы на каждое сообщение, поэтому перезапускать бота не нужно.
Внимание: если ID вписан в ADMIN_TG_IDS в .env, роль принудительно вернётся
в суперадмина — для теста остальных ролей держи ADMIN_TG_IDS пустым.
"""

import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.db import session_scope
from app.models import Staff, StaffRole, utcnow
from app.utils import gen_token

BASE = Path(__file__).resolve().parent.parent
MEMO = BASE / "data" / "test-staff.json"

ROLES = {
    "superadmin": StaffRole.SUPERADMIN,
    "admin": StaffRole.ADMIN,
}


def _saved_tg_id() -> int | None:
    if MEMO.exists():
        try:
            return json.loads(MEMO.read_text("utf-8")).get("tg_id")
        except (json.JSONDecodeError, TypeError):
            return None
    return None


async def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] not in {*ROLES, "off"}:
        print(__doc__)
        sys.exit(1)

    tg_id = int(args[1]) if len(args) > 1 else _saved_tg_id()
    if tg_id is None:
        print("Укажи свой Telegram ID вторым аргументом, например:")
        print("  python -m scripts.role admin 123456789")
        print("Свой ID бот показывает командой /id.")
        sys.exit(1)

    async with session_scope() as session:
        # Ищем и неактивных: get_staff_by_tg отфильтровал бы их,
        # и скрипт создал бы дубль, упёршись в unique по tg_id.
        staff = await session.scalar(select(Staff).where(Staff.tg_id == tg_id))

        if args[0] == "off":
            if staff is None:
                print("Этот аккаунт и так не привязан к организатору.")
                return
            staff.tg_id = None
            staff.activated_at = None
            staff.tg_username = None
            print(f"Аккаунт отвязан от «{staff.name}» — ты снова обычный участник.")
            return

        role = ROLES[args[0]]
        if staff is None:
            session.add(
                Staff(
                    name="Тестовый организатор",
                    role=role,
                    invite_token=gen_token(),
                    tg_id=tg_id,
                    activated_at=utcnow(),
                )
            )
            print(f"Организатор создан с ролью «{StaffRole.LABELS[role]}».")
        elif not staff.is_active:
            staff.is_active = True
            staff.role = role
            print(f"Организатор «{staff.name}» включён, роль «{StaffRole.LABELS[role]}».")
        else:
            staff.role = role
            print(f"«{staff.name}»: роль «{staff.role_label}». Перезапуск бота не нужен.")

    MEMO.write_text(json.dumps({"tg_id": tg_id}), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
