"""Поиск шрифта с кириллицей.

Нужен и для подписей на печатных плакатах с QR, и для отметок на карте.
Пути перебираем по очереди: macOS для разработки, DejaVu внутри Docker.
"""

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

_REGULAR_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
]

_BOLD_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


def _first_existing(candidates: list[str]) -> str | None:
    for path in candidates:
        if Path(path).exists():
            return path
    return None


@lru_cache(maxsize=32)
def get_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = _first_existing(_BOLD_CANDIDATES if bold else _REGULAR_CANDIDATES)
    if path:
        return ImageFont.truetype(path, size)
    # Крайний случай: системных шрифтов нет. Кириллица может отрисоваться
    # квадратами, но приложение не упадёт.
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow старее 10.1
        return ImageFont.load_default()
