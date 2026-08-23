from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EventSettings


async def get_event_settings(session: AsyncSession) -> EventSettings:
    """Настройки мероприятия. Строка одна, создаётся при первом обращении."""
    obj = await session.get(EventSettings, 1)
    if obj is None:
        obj = EventSettings(id=1)
        session.add(obj)
        await session.flush()
    return obj
