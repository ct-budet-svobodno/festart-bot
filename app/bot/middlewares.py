"""Мидлвари: сессия базы и контекст пользователя для каждого апдейта."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from app.db import session_scope
from app.services.participants import get_or_create_participant
from app.services.staff import resolve_staff


class ContextMiddleware(BaseMiddleware):
    """Открывает сессию на время обработки апдейта и кладёт в data
    участника и, если это организатор, его роль."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with session_scope() as session:
            data["session"] = session

            user: User | None = data.get("event_from_user")
            if user is not None and not user.is_bot:
                participant, created = await get_or_create_participant(
                    session, tg_id=user.id, username=user.username
                )
                data["participant"] = participant
                data["is_new_participant"] = created
                data["staff"] = await resolve_staff(session, user.id, user.username)
            else:
                data["participant"] = None
                data["is_new_participant"] = False
                data["staff"] = None

            return await handler(event, data)
