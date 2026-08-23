"""Описание редактируемых полей и разбор пользовательского ввода.

Вместо отдельного хендлера на каждое поле каждой сущности — один универсальный
сценарий: показали карточку, нажали кнопку поля, бот спросил значение, разобрал,
сохранил, перерисовал карточку.
"""

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from app.config import settings
from app.utils import UTC

TEXT = "text"
LONGTEXT = "longtext"
INT = "int"
BOOL = "bool"
TIME = "time"
PERCENT = "percent"
URL = "url"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = TEXT
    hint: str = ""
    min_value: int | None = None
    max_value: int | None = None
    required: bool = False


class ParseError(Exception):
    pass


def parse_value(field: Field, raw: str) -> Any:
    """Строка от пользователя -> значение для модели. Бросает ParseError с текстом для бота."""
    text = raw.strip()

    if text == "-" and not field.required:
        return None

    if field.kind in (TEXT, LONGTEXT, URL):
        if not text:
            raise ParseError("Пустое значение. Пришли текст или «-», чтобы очистить.")
        if field.kind == URL and not text.startswith(("http://", "https://")):
            raise ParseError("Ссылка должна начинаться с http:// или https://")
        limit = 4000 if field.kind == LONGTEXT else 250
        if len(text) > limit:
            raise ParseError(f"Слишком длинно: {len(text)} символов, максимум {limit}.")
        return text

    if field.kind == INT:
        try:
            value = int(text)
        except ValueError:
            raise ParseError("Нужно целое число. Например: 10") from None
        if field.min_value is not None and value < field.min_value:
            raise ParseError(f"Не меньше {field.min_value}.")
        if field.max_value is not None and value > field.max_value:
            raise ParseError(f"Не больше {field.max_value}.")
        return value

    if field.kind == PERCENT:
        try:
            value = float(text.replace(",", "."))
        except ValueError:
            raise ParseError("Нужно число от 0 до 100. Например: 42.5") from None
        if not 0 <= value <= 100:
            raise ParseError("Число должно быть от 0 до 100.")
        return value

    if field.kind == TIME:
        return parse_time(text)

    raise ParseError("Неизвестный тип поля.")


def parse_time(text: str) -> datetime | None:
    """«14:30» -> сегодняшняя дата в местном времени, сохранённая как наивный UTC.

    Мероприятие однодневное, поэтому дату не спрашиваем — только время.
    """
    cleaned = text.replace(".", ":").replace("-", ":").strip()
    parts = cleaned.split(":")
    if len(parts) != 2:
        raise ParseError("Формат времени: ЧЧ:ММ. Например: 14:30")
    try:
        hours, minutes = int(parts[0]), int(parts[1])
        moment = time(hours, minutes)
    except ValueError:
        raise ParseError("Формат времени: ЧЧ:ММ. Например: 14:30") from None

    today = datetime.now(settings.tz).date()
    local = datetime.combine(today, moment).replace(tzinfo=settings.tz)
    return local.astimezone(UTC).replace(tzinfo=None)


def display_value(field: Field, value: Any) -> str:
    """Короткое представление значения для кнопки и карточки."""
    if value is None or value == "":
        return "—"
    if field.kind == BOOL:
        return "включено" if value else "выключено"
    if field.kind == TIME:
        return value.replace(tzinfo=UTC).astimezone(settings.tz).strftime("%H:%M")
    if field.kind == LONGTEXT:
        flat = " ".join(str(value).split())
        return flat[:40] + "…" if len(flat) > 40 else flat
    return str(value)


def prompt_for(field: Field) -> str:
    """Текст запроса значения."""
    lines = [f"<b>{field.label}</b>"]
    if field.hint:
        lines.append(field.hint)

    if field.kind == TIME:
        lines.append("Формат: ЧЧ:ММ, например 14:30")
    elif field.kind == INT:
        bounds = []
        if field.min_value is not None:
            bounds.append(f"от {field.min_value}")
        if field.max_value is not None:
            bounds.append(f"до {field.max_value}")
        lines.append("Целое число" + (f" {' '.join(bounds)}" if bounds else ""))
    elif field.kind == PERCENT:
        lines.append("Число от 0 до 100")
    elif field.kind == URL:
        lines.append("Ссылка целиком, начиная с https://")

    if not field.required:
        lines.append("Пришли «-», чтобы очистить поле.")
    lines.append("Отмена — /cancel")
    return "\n".join(lines)
