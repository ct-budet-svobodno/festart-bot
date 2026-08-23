"""Веб-панель администратора: uvicorn app.admin.main:app

Обычные серверные страницы без SPA. Панелью пользуются нетехнические люди
с телефона в день мероприятия — важнее надёжность и скорость, чем интерактив.
"""

import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from app.admin.helpers import render, templates
from app.admin.routers import activities, participants, prizes, settings as settings_router
from app.admin.routers import staff as staff_router
from app.config import BASE_DIR, MEDIA_DIR, settings
from app.db import get_session, init_db
from app.models import (
    Activity,
    ActivityKind,
    Participant,
    PointsLedger,
    Prize,
    Redemption,
    RedemptionStatus,
    Visit,
)
from app.services.event import get_event_settings

@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="ФЕСТАРТ · админка", docs_url=None, redoc_url=None, lifespan=lifespan
)
STATIC_DIR = BASE_DIR / "app" / "admin" / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# Загруженная карта площадки — показываем её в настройках для проверки.
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")

PUBLIC_PATHS = {"/login", "/logout", "/health"}


@app.middleware("http")
async def require_auth(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/static"):
        return await call_next(request)
    if not request.session.get("admin"):
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


# Добавляется последней и поэтому оказывается снаружи стека — иначе
# require_auth сработал бы раньше и request.session ещё не существовал бы.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="festart_admin",
    max_age=60 * 60 * 12,
    same_site="lax",
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/login")
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    # compare_digest, чтобы по времени ответа нельзя было подобрать пароль посимвольно.
    if secrets.compare_digest(password, settings.admin_password):
        request.session["admin"] = True
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": "Неверный пароль"}, status_code=401
    )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/")
async def dashboard(request: Request, session: AsyncSession = Depends(get_session)):
    event = await get_event_settings(session)

    started = await session.scalar(select(func.count(Participant.id)))
    registered = await session.scalar(
        select(func.count(Participant.id)).where(Participant.is_registered.is_(True))
    )
    visits = await session.scalar(select(func.count(Visit.id)))
    points_issued = await session.scalar(
        select(func.coalesce(func.sum(PointsLedger.delta), 0)).where(PointsLedger.delta > 0)
    )
    points_spent = await session.scalar(
        select(func.coalesce(func.sum(-PointsLedger.delta), 0)).where(PointsLedger.delta < 0)
    )
    prizes_issued = await session.scalar(
        select(func.count(Redemption.id)).where(
            Redemption.status == RedemptionStatus.CONFIRMED
        )
    )
    pending = await session.scalar(
        select(func.count(Redemption.id)).where(Redemption.status == RedemptionStatus.PENDING)
    )

    zone_rows = await session.execute(
        select(Activity.title, func.count(Visit.id).label("total"))
        .join(Visit, Visit.activity_id == Activity.id, isouter=True)
        .where(Activity.kind == ActivityKind.ZONE)
        .group_by(Activity.id)
        .order_by(func.count(Visit.id).desc())
    )
    zone_stats = list(zone_rows.all())

    low_stock_rows = await session.scalars(
        select(Prize)
        .where(Prize.is_active.is_(True), Prize.stock_left <= 5)
        .order_by(Prize.stock_left)
    )

    recent_rows = await session.scalars(
        select(Redemption)
        .where(Redemption.status == RedemptionStatus.CONFIRMED)
        .order_by(Redemption.created_at.desc())
        .limit(10)
    )

    return render(
        request,
        "dashboard.html",
        active="dashboard",
        event=event,
        stats={
            "started": started or 0,
            "registered": registered or 0,
            "visits": visits or 0,
            "points_issued": points_issued or 0,
            "points_spent": points_spent or 0,
            "prizes_issued": prizes_issued or 0,
            "pending": pending or 0,
        },
        zone_stats=zone_stats,
        low_stock=list(low_stock_rows.all()),
        recent=list(recent_rows.all()),
    )


app.include_router(activities.router)
app.include_router(prizes.router)
app.include_router(participants.router)
app.include_router(staff_router.router)
app.include_router(settings_router.router)
