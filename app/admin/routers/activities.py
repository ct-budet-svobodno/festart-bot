"""Зоны и мастер-классы: список, создание, редактирование, печатные плакаты."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.helpers import (
    dt_input_value,
    form_bool,
    form_float,
    form_int,
    form_str,
    parse_local_dt,
    render,
)
from app.db import get_session
from app.models import Activity, ActivityKind, Visit
from app.services.exports import posters_zip
from app.services.qr import activity_link, make_poster_png, make_qr_png
from app.utils import gen_activity_code

router = APIRouter(prefix="/activities")


@router.get("")
async def list_activities(
    request: Request, kind: str = ActivityKind.ZONE, session: AsyncSession = Depends(get_session)
):
    rows = await session.scalars(
        select(Activity).where(Activity.kind == kind).order_by(Activity.sort_order, Activity.id)
    )
    activities = list(rows.all())

    visit_rows = await session.execute(
        select(Visit.activity_id, func.count(Visit.id))
        .where(Visit.activity_id.in_([a.id for a in activities] or [0]))
        .group_by(Visit.activity_id)
    )
    counts = dict(visit_rows.all())

    return render(
        request,
        "activities.html",
        active="activities",
        kind=kind,
        activities=activities,
        counts=counts,
        links={a.id: activity_link(a.code) for a in activities},
    )


@router.get("/new")
async def new_activity(request: Request, kind: str = ActivityKind.ZONE):
    return render(
        request,
        "activity_form.html",
        active="activities",
        kind=kind,
        activity=None,
        dt_value=dt_input_value,
    )


@router.get("/posters.zip")
async def all_posters(kind: str = ActivityKind.ZONE, session: AsyncSession = Depends(get_session)):
    """Все плакаты одним архивом — чтобы разом отправить в печать.

    Объявлено до /{activity_id}: иначе этот маршрут перехватил бы адрес.
    """
    payload, _count = await posters_zip(session, kind)
    return Response(
        payload,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="festart-posters.zip"'},
    )


@router.get("/{activity_id}")
async def edit_activity(
    request: Request, activity_id: int, session: AsyncSession = Depends(get_session)
):
    activity = await session.get(Activity, activity_id)
    if activity is None:
        return RedirectResponse("/activities", status_code=303)
    return render(
        request,
        "activity_form.html",
        active="activities",
        kind=activity.kind,
        activity=activity,
        link=activity_link(activity.code),
        dt_value=dt_input_value,
    )


@router.post("/save")
async def save_activity(
    activity_id: str = Form(""),
    kind: str = Form(ActivityKind.ZONE),
    title: str = Form(...),
    description: str = Form(""),
    location: str = Form(""),
    points: str = Form("1"),
    starts_at: str = Form(""),
    ends_at: str = Form(""),
    map_x: str = Form(""),
    map_y: str = Form(""),
    sort_order: str = Form("100"),
    is_active: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    activity = None
    if activity_id:
        activity = await session.get(Activity, int(activity_id))

    if activity is None:
        activity = Activity(code=gen_activity_code())
        session.add(activity)

    activity.kind = kind
    activity.title = title.strip()
    activity.description = form_str(description)
    activity.location = form_str(location)
    activity.points = max(0, form_int(points, 1))
    activity.starts_at = parse_local_dt(starts_at)
    activity.ends_at = parse_local_dt(ends_at)
    activity.map_x = form_float(map_x)
    activity.map_y = form_float(map_y)
    activity.sort_order = form_int(sort_order, 100)
    activity.is_active = form_bool(is_active)

    await session.flush()
    return RedirectResponse(f"/activities?kind={kind}", status_code=303)


@router.post("/{activity_id}/delete")
async def delete_activity(activity_id: int, session: AsyncSession = Depends(get_session)):
    activity = await session.get(Activity, activity_id)
    kind = activity.kind if activity else ActivityKind.ZONE
    if activity is not None:
        await session.delete(activity)
    return RedirectResponse(f"/activities?kind={kind}", status_code=303)


@router.get("/{activity_id}/qr.png")
async def activity_qr(activity_id: int, session: AsyncSession = Depends(get_session)):
    activity = await session.get(Activity, activity_id)
    if activity is None:
        return Response(status_code=404)
    png = make_qr_png(activity_link(activity.code), box_size=12, high_quality=True)
    return Response(png, media_type="image/png")


@router.get("/{activity_id}/poster.png")
async def activity_poster(activity_id: int, session: AsyncSession = Depends(get_session)):
    activity = await session.get(Activity, activity_id)
    if activity is None:
        return Response(status_code=404)
    png = make_poster_png(
        activity_link(activity.code),
        activity.title,
        subtitle=f"+{activity.points} за посещение",
    )
    filename = f"poster-{activity.code}.png"
    return Response(
        png,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


