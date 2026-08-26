"""Поиск шрифта с кириллицей.

Нужен и для подписей на печатных плакатах с QR, и для отметок на карте.
Пути перебираем по очереди: macOS и Windows для разработки,
DejaVu внутри Docker.
"""

import os
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

_WIN_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

_REGULAR_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
    str(_WIN_FONTS / "arial.ttf"),
    str(_WIN_FONTS / "segoeui.ttf"),
]

_BOLD_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    str(_WIN_FONTS / "arialbd.ttf"),
    str(_WIN_FONTS / "segoeuib.ttf"),
]


def _first_existing(candidates: list[str]) -> str | None:
    for path in candidates:
        try:
            if Path(path).exists():
                return path
        except OSError:
            continue
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
