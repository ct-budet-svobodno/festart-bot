"""Карта площадки с отметками пройденных зон.

Дизайнеры отдают одну чистую картинку, координаты зон задаются в админке
в процентах от размера изображения. Персональная карта собирается на лету.

Логика отметок обратная «зачёркиванию»: пройденное загорается цветом,
непройденное остаётся видимым и заметным — карта должна отвечать на вопрос
«куда идти дальше», а не только «где я был».
"""

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import MEDIA_DIR
from app.models import Activity, ActivityKind
from app.services.event import get_event_settings
from app.services.fonts import get_font
from app.services.points import visited_activity_ids

VISITED_FILL = (46, 204, 113)  # зелёный
VISITED_OUTLINE = (255, 255, 255)
PENDING_FILL = (255, 255, 255)
PENDING_OUTLINE = (120, 120, 130)
PENDING_TEXT = (90, 90, 100)

GRID_LINE = (215, 60, 60)  # красноватая сетка заметна на любой подложке
GRID_TEXT = (180, 40, 40)

MAX_SIDE = 1280  # больше Telegram всё равно сожмёт


def map_image_path(filename: str | None) -> Path | None:
    if not filename:
        return None
    path = MEDIA_DIR / filename
    return path if path.exists() else None


def _draw_marker(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    radius: float,
    *,
    visited: bool,
    label: str,
) -> None:
    box = (x - radius, y - radius, x + radius, y + radius)
    if visited:
        draw.ellipse(box, fill=VISITED_FILL, outline=VISITED_OUTLINE, width=max(2, int(radius * 0.16)))
        # Галочка внутри кружка.
        w = max(3, int(radius * 0.28))
        draw.line(
            [
                (x - radius * 0.42, y + radius * 0.02),
                (x - radius * 0.10, y + radius * 0.36),
                (x + radius * 0.46, y - radius * 0.34),
            ],
            fill=(255, 255, 255),
            width=w,
            joint="curve",
        )
    else:
        draw.ellipse(box, fill=PENDING_FILL, outline=PENDING_OUTLINE, width=max(2, int(radius * 0.14)))
        font = get_font(max(12, int(radius * 1.05)), bold=True)
        draw.text((x, y), label, font=font, fill=PENDING_TEXT, anchor="mm")


def render_map(
    base_path: Path,
    markers: list[tuple[float, float, bool, str]],
) -> bytes:
    """markers: список (x%, y%, пройдено, подпись)."""
    with Image.open(base_path) as src:
        image = src.convert("RGB")

    if max(image.size) > MAX_SIDE:
        scale = MAX_SIDE / max(image.size)
        image = image.resize(
            (int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS
        )

    draw = ImageDraw.Draw(image)
    radius = max(16.0, min(image.size) * 0.035)

    for x_pct, y_pct, visited, label in markers:
        x = image.width * (x_pct / 100)
        y = image.height * (y_pct / 100)
        _draw_marker(draw, x, y, radius, visited=visited, label=label)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88, optimize=True)
    return buffer.getvalue()


@lru_cache(maxsize=256)
def _render_cached(base_path: str, mtime: float, state: tuple) -> bytes:
    """Кэш по состоянию прогресса.

    У сотен участников набор пройденных зон совпадает — рисуем один раз.
    mtime в ключе сбрасывает кэш, если админ перезалил карту.
    """
    markers = [(x, y, visited, label) for x, y, visited, label in state]
    return render_map(Path(base_path), markers)


def render_grid_map(base_path: Path) -> bytes:
    """Карта с сеткой каждые 10% — для админа, который ставит X/Y зон на глаз.

    Подписи на осях = те самые проценты из полей «X на карте, %» и «Y на карте, %».
    """
    with Image.open(base_path) as src:
        image = src.convert("RGB")

    if max(image.size) > MAX_SIDE:
        scale = MAX_SIDE / max(image.size)
        image = image.resize(
            (int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS
        )

    draw = ImageDraw.Draw(image)
    font = get_font(max(18, min(image.size) // 45), bold=True)

    for pct in range(10, 100, 10):
        x = image.width * pct / 100
        y = image.height * pct / 100
        draw.line([(x, 0), (x, image.height)], fill=GRID_LINE, width=2)
        draw.line([(0, y), (image.width, y)], fill=GRID_LINE, width=2)
        # Подписи по верхней кромке и по левому краю
        draw.text((x, 16), str(pct), font=font, fill=GRID_TEXT, anchor="mm")
        draw.text((16, y), str(pct), font=font, fill=GRID_TEXT, anchor="mm")

    draw.text(
        (image.width / 2, image.height - 20),
        "сетка = 10% · X от левого края · Y от верха",
        font=font, fill=GRID_TEXT, anchor="mm",
    )

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


async def build_progress_map(
    session: AsyncSession, participant_id: int
) -> tuple[bytes | None, str]:
    """Возвращает (картинка или None, подпись).

    None означает, что карта ещё не загружена — бот покажет текстовый список.
    """
    event = await get_event_settings(session)
    base = map_image_path(event.map_image)

    rows = await session.scalars(
        select(Activity)
        .where(Activity.kind == ActivityKind.ZONE, Activity.is_active.is_(True))
        .order_by(Activity.sort_order, Activity.id)
    )
    zones = list(rows.all())
    visited = await visited_activity_ids(session, participant_id)

    # Сквозная нумерация: один и тот же номер стоит на карте и в подписи,
    # иначе кружок «4» на картинке не с чем сопоставить.
    numbered = list(enumerate(zones, start=1))
    done = [z for _, z in numbered if z.id in visited]
    left = [(i, z) for i, z in numbered if z.id not in visited]

    lines = [f"<b>Пройдено {len(done)} из {len(zones)}</b>"]
    if left:
        lines.append("")
        lines.append("<b>Осталось:</b>")
        for index, zone in left[:10]:
            lines.append(f"{index}. {zone.title} · {zone.points}")
        if len(left) > 10:
            lines.append(f"…и ещё {len(left) - 10}")
    elif zones:
        lines.append("Ты обошёл все зоны 🎉")
    if event.map_caption:
        lines.append("")
        lines.append(event.map_caption)
    caption = "\n".join(lines)

    if base is None:
        return None, caption

    state = tuple(
        (z.map_x, z.map_y, z.id in visited, str(index))
        for index, z in numbered
        if z.has_map_position
    )
    image = _render_cached(str(base), base.stat().st_mtime, state)
    return image, caption
