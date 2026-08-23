from aiogram import Router

from app.bot.admin import get_admin_router
from app.bot.handlers import fallback, menu, staff, start


def get_router() -> Router:
    """Порядок важен: команды и deep-link, затем админка и служебные сценарии,
    затем кнопки меню участника, и в самом конце — заглушка на всё остальное."""
    router = Router()
    router.include_router(start.router)
    router.include_router(get_admin_router())
    router.include_router(staff.router)
    router.include_router(menu.router)
    router.include_router(fallback.router)
    return router
