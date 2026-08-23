"""Помощники админки: работа с формами и датами."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, settings
from app.utils import fmt_dt, fmt_points, fmt_time

UTC = ZoneInfo("UTC")

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "admin" / "templates"))
templates.env.filters["dt"] = fmt_dt
templates.env.filters["time"] = fmt_time
templates.env.filters["points"] = fmt_points

CSS_PATH = BASE_DIR / "app" / "admin" / "static" / "admin.css"


def asset_v() -> str:
    """Версия стилей = время изменения файла.

    Без этого браузер держит старый CSS после обновления на сервере,
    а объяснять заказчику про Ctrl+Shift+R в день мероприятия не хочется.
    """
    try:
        return str(int(CSS_PATH.stat().st_mtime))
    except OSError:
        return "1"


templates.env.globals["asset_v"] = asset_v


def render(request: Request, template: str, **context):
    """Обёртка, чтобы не таскать request в каждый шаблон руками."""
    context.setdefault("active", "")
    return templates.TemplateResponse(request, template, context)


def parse_local_dt(value: str | None) -> datetime | None:
    """Значение из <input type="datetime-local"> -> наивный UTC для базы."""
    if not value:
        return None
    try:
        local = datetime.fromisoformat(value)
    except ValueError:
        return None
    return local.replace(tzinfo=settings.tz).astimezone(UTC).replace(tzinfo=None)


def dt_input_value(value: datetime | None) -> str:
    """Наивный UTC из базы -> значение для <input type="datetime-local">."""
    if value is None:
        return ""
    return value.replace(tzinfo=UTC).astimezone(settings.tz).strftime("%Y-%m-%dT%H:%M")


def form_int(value: str | None, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def form_float(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).replace(",", ".").strip())
    except ValueError:
        return None


def form_bool(value: str | None) -> bool:
    return str(value).lower() in {"on", "true", "1", "yes"}


def form_str(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
