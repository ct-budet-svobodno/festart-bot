"""Настройки мероприятия: тексты, ссылки, бонусы, рубильники, факультеты."""

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.helpers import form_bool, form_int, form_str, render
from app.db import get_session
from app.models import Faculty
from app.services.event import get_event_settings

router = APIRouter(prefix="/settings")


@router.get("")
async def index(request: Request, session: AsyncSession = Depends(get_session)):
    event = await get_event_settings(session)
    faculties = list(
        (await session.scalars(select(Faculty).order_by(Faculty.sort_order, Faculty.id))).all()
    )
    return render(
        request,
        "settings.html",
        active="settings",
        event=event,
        faculties=faculties,
    )


@router.post("/save")
async def save(
    event_title: str = Form(""),
    welcome_text: str = Form(""),
    registration_done_text: str = Form(""),
    help_text: str = Form(""),
    final_message_text: str = Form(""),
    qr_hint_text: str = Form(""),
    feedback_url: str = Form(""),
    privacy_url: str = Form(""),
    registration_bonus: str = Form("0"),
    all_zones_bonus: str = Form("0"),
    require_consent: str = Form(""),
    is_registration_open: str = Form(""),
    is_scanning_open: str = Form(""),
    is_redemption_open: str = Form(""),
    show_leaderboard: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    event = await get_event_settings(session)
    event.event_title = event_title.strip() or event.event_title
    event.welcome_text = welcome_text.strip() or event.welcome_text
    event.registration_done_text = registration_done_text.strip() or event.registration_done_text
    event.help_text = help_text.strip() or event.help_text
    event.final_message_text = final_message_text.strip() or event.final_message_text
    event.qr_hint_text = qr_hint_text.strip() or event.qr_hint_text
    event.feedback_url = form_str(feedback_url)
    event.privacy_url = form_str(privacy_url)
    event.registration_bonus = max(0, form_int(registration_bonus))
    event.all_zones_bonus = max(0, form_int(all_zones_bonus))
    event.require_consent = form_bool(require_consent)
    event.is_registration_open = form_bool(is_registration_open)
    event.is_scanning_open = form_bool(is_scanning_open)
    event.is_redemption_open = form_bool(is_redemption_open)
    event.show_leaderboard = form_bool(show_leaderboard)
    return RedirectResponse("/settings", status_code=303)


TOGGLE_FIELDS = {
    "is_registration_open",
    "is_scanning_open",
    "is_redemption_open",
    "show_leaderboard",
    "require_consent",
}


@router.post("/toggle")
async def toggle_flag(
    request: Request, field: str = Form(...), session: AsyncSession = Depends(get_session)
):
    """Переключатели со сводки. Список полей закрытый — из формы нельзя
    дотянуться до произвольного атрибута модели."""
    if field in TOGGLE_FIELDS:
        event = await get_event_settings(session)
        setattr(event, field, not getattr(event, field))
    return RedirectResponse(_safe_back(request), status_code=303)


def _safe_back(request: Request) -> str:
    """Возврат на страницу, с которой пришли.

    Берём только путь: Referer приходит из браузера, и подставлять его
    в редирект целиком — это открытый редирект на чужой сайт.
    """
    referer = request.headers.get("referer")
    if not referer:
        return "/"
    path = urlparse(referer).path or "/"
    return path if path.startswith("/") and not path.startswith("//") else "/"


@router.post("/faculties/add")
async def add_faculty(
    title: str = Form(...), session: AsyncSession = Depends(get_session)
):
    cleaned = title.strip()
    if cleaned:
        existing = await session.scalar(select(Faculty).where(Faculty.title == cleaned))
        if existing is None:
            count = len(list((await session.scalars(select(Faculty.id))).all()))
            session.add(Faculty(title=cleaned, sort_order=(count + 1) * 10))
    return RedirectResponse("/settings", status_code=303)


@router.post("/faculties/{faculty_id}/delete")
async def delete_faculty(faculty_id: int, session: AsyncSession = Depends(get_session)):
    faculty = await session.get(Faculty, faculty_id)
    if faculty is not None:
        await session.delete(faculty)
    return RedirectResponse("/settings", status_code=303)
