from aiogram import Router

from app.bot.admin import core, extras


def get_admin_router() -> Router:
    router = Router()
    router.include_router(core.router)
    router.include_router(extras.router)
    return router
