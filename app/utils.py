"""Мелкие общие помощники: генерация кодов, форматирование дат и склонения."""

import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings

UTC = ZoneInfo("UTC")

# Алфавит без символов, которые путают при чтении и наборе: O/0, I/1, S/5.
UNAMBIGUOUS = "ABCDEFGHJKLMNPQRTUVWXYZ2346789"

# Префиксы payload в deep-link'ах QR-кодов.
PREFIX_ACTIVITY = "z"  # зона или мастер-класс: z_A7F3K2QW
PREFIX_PARTICIPANT = "u"  # личный QR участника: u_<token>
PREFIX_STAFF = "s"  # приглашение организатора: s_<token>


def gen_activity_code(length: int = 8) -> str:
    """Код зоны. Неугадываемый: подобрать перебором чужую зону нельзя."""
    return "".join(secrets.choice(UNAMBIGUOUS) for _ in range(length))


def gen_token(nbytes: int = 16) -> str:
    """URL-безопасный токен. Символы совместимы с deep-link payload Telegram."""
    return secrets.token_urlsafe(nbytes)


def gen_short_code() -> str:
    """Шесть цифр — резервный способ идентификации, если QR не считывается.

    Цифры, а не буквы: организатору на стойке их быстрее набрать на телефоне.
    """
    return "".join(secrets.choice("0123456789") for _ in range(6))


def to_local(dt: datetime | None) -> datetime | None:
    """Наивный UTC из базы -> местное время для показа."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC).astimezone(settings.tz)


def fmt_dt(dt: datetime | None, fmt: str = "%d.%m %H:%M") -> str:
    local = to_local(dt)
    return local.strftime(fmt) if local else "—"


def fmt_time(dt: datetime | None) -> str:
    return fmt_dt(dt, "%H:%M")


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русские склонения: 1 балл, 2 балла, 5 баллов."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def points_word(n: int) -> str:
    return plural(n, "балл", "балла", "баллов")


def fmt_points(n: int) -> str:
    return f"{n} {points_word(n)}"
