"""Генерация QR-кодов.

Три вида кодов:
- зоны и мастер-классы: печатаются на плакатах, сканируются участниками;
- личный код участника: сканируется организатором на стойке призов;
- приглашение организатора: одноразовая ссылка для привязки Telegram.
"""

import io

import qrcode
from PIL import Image, ImageDraw
from qrcode.constants import ERROR_CORRECT_M, ERROR_CORRECT_Q

from app.config import settings
from app.services.fonts import get_font
from app.utils import PREFIX_ACTIVITY, PREFIX_PARTICIPANT, PREFIX_STAFF


def activity_payload(code: str) -> str:
    return f"{PREFIX_ACTIVITY}_{code}"


def participant_payload(token: str) -> str:
    return f"{PREFIX_PARTICIPANT}_{token}"


def staff_payload(token: str) -> str:
    return f"{PREFIX_STAFF}_{token}"


def activity_link(code: str) -> str:
    return settings.deep_link(activity_payload(code))


def participant_link(token: str) -> str:
    return settings.deep_link(participant_payload(token))


def staff_link(token: str) -> str:
    return settings.deep_link(staff_payload(token))


def make_qr(data: str, *, box_size: int = 10, border: int = 4, high_quality: bool = False) -> Image.Image:
    """Картинка QR-кода.

    high_quality поднимает уровень коррекции ошибок — для печатных плакатов,
    которые могут помяться, запачкаться или выгореть на солнце.
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_Q if high_quality else ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def make_qr_png(data: str, **kwargs) -> bytes:
    buffer = io.BytesIO()
    make_qr(data, **kwargs).save(buffer, format="PNG")
    return buffer.getvalue()


def make_poster_png(
    data: str,
    title: str,
    *,
    subtitle: str | None = None,
    hint: str = "Наведи камеру телефона",
) -> bytes:
    """Готовый к печати лист А4 с QR-кодом и подписью.

    Админ скачивает его из панели и отдаёт в печать — не нужно ничего верстать вручную.
    """
    # A4 при 150 dpi
    width, height = 1240, 1754
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)

    title_font = get_font(76, bold=True)
    subtitle_font = get_font(44)
    hint_font = get_font(52, bold=True)

    margin = 90
    y = 140

    for line in _wrap(draw, title, title_font, width - 2 * margin):
        draw.text((width / 2, y), line, font=title_font, fill="black", anchor="ma")
        y += 92

    if subtitle:
        y += 20
        for line in _wrap(draw, subtitle, subtitle_font, width - 2 * margin):
            draw.text((width / 2, y), line, font=subtitle_font, fill="#555555", anchor="ma")
            y += 56

    qr_size = 760
    qr_img = make_qr(data, box_size=20, border=2, high_quality=True).resize(
        (qr_size, qr_size), Image.Resampling.NEAREST
    )
    qr_y = max(y + 70, (height - qr_size) // 2)
    canvas.paste(qr_img, ((width - qr_size) // 2, qr_y))

    draw.text(
        (width / 2, qr_y + qr_size + 70), hint, font=hint_font, fill="black", anchor="ma"
    )

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines
