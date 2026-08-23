"""Рассылка участникам.

    python -m scripts.broadcast --dry-run          # посмотреть, кому уйдёт
    python -m scripts.broadcast                    # итоги дня + ссылка на анкету
    python -m scripts.broadcast --text "Привет!"   # произвольное сообщение

Telegram ограничивает бота примерно 30 сообщениями в секунду, поэтому шлём
с паузой. Три тысячи человек — около двух минут, десять тысяч — минут семь.
Заблокировавшие бота пропускаются, при 429 ждём столько, сколько просит Telegram.
"""

import argparse
import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramRetryAfter,
    TelegramBadRequest,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from app.config import settings
from app.db import session_scope
from app.models import Participant, PointsLedger, Visit
from app.services.event import get_event_settings
from app.utils import fmt_points

logger = logging.getLogger("broadcast")

# 25 в секунду вместо предельных 30 — запас на случай, если бот
# одновременно отвечает живым людям.
RATE_LIMIT = 25
DELAY = 1 / RATE_LIMIT


async def collect_recipients():
    """Кому шлём и с какими цифрами."""
    async with session_scope() as session:
        event = await get_event_settings(session)
        rows = await session.scalars(
            select(Participant).where(
                Participant.is_registered.is_(True), Participant.is_blocked.is_(False)
            )
        )
        participants = list(rows.all())

        balance_rows = await session.execute(
            select(PointsLedger.participant_id, func.sum(PointsLedger.delta)).group_by(
                PointsLedger.participant_id
            )
        )
        balances = {pid: int(v or 0) for pid, v in balance_rows.all()}

        visit_rows = await session.execute(
            select(Visit.participant_id, func.count(Visit.id)).group_by(Visit.participant_id)
        )
        visits = dict(visit_rows.all())

        return [
            {
                "tg_id": p.tg_id,
                "name": p.first_name or "",
                "points": balances.get(p.id, 0),
                "visits": visits.get(p.id, 0),
            }
            for p in participants
        ], event.final_message_text, event.feedback_url


def build_text(template: str, person: dict) -> str:
    try:
        return template.format(
            name=person["name"],
            points=fmt_points(person["points"]),
            visits=person["visits"],
        )
    except (KeyError, IndexError):
        # Админ мог оставить в тексте фигурную скобку — не роняем всю рассылку.
        return template


async def run(custom_text: str | None, dry_run: bool) -> None:
    recipients, final_template, feedback_url = await collect_recipients()
    template = custom_text or final_template

    if not recipients:
        print("Получателей нет: никто не завершил регистрацию.")
        return

    print(f"Получателей: {len(recipients)}")
    print(f"Ожидаемое время: ~{len(recipients) / RATE_LIMIT / 60:.1f} мин")
    print("-" * 50)
    print(build_text(template, recipients[0]))
    print("-" * 50)

    if dry_run:
        print("Пробный прогон — ничего не отправлено.")
        return

    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан в .env")

    keyboard = None
    if feedback_url and not custom_text:
        builder = InlineKeyboardBuilder()
        builder.button(text="Оставить отзыв", url=feedback_url)
        keyboard = builder.as_markup()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    sent = blocked = failed = 0
    try:
        for index, person in enumerate(recipients, start=1):
            try:
                await bot.send_message(
                    person["tg_id"], build_text(template, person), reply_markup=keyboard
                )
                sent += 1
            except TelegramForbiddenError:
                blocked += 1
            except TelegramRetryAfter as exc:
                logger.warning("Лимит Telegram, ждём %s c", exc.retry_after)
                await asyncio.sleep(exc.retry_after + 1)
                try:
                    await bot.send_message(
                        person["tg_id"], build_text(template, person), reply_markup=keyboard
                    )
                    sent += 1
                except Exception:
                    failed += 1
            except TelegramBadRequest as exc:
                logger.warning("Не доставлено %s: %s", person["tg_id"], exc)
                failed += 1

            if index % 100 == 0:
                print(f"  {index}/{len(recipients)}…")
            await asyncio.sleep(DELAY)
    finally:
        await bot.session.close()

    print(f"\nОтправлено: {sent}")
    print(f"Заблокировали бота: {blocked}")
    print(f"Ошибок: {failed}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Рассылка участникам ФЕСТАРТа")
    parser.add_argument("--text", help="Произвольный текст вместо итогов дня")
    parser.add_argument("--dry-run", action="store_true", help="Показать, но не отправлять")
    args = parser.parse_args()
    asyncio.run(run(args.text, args.dry_run))


if __name__ == "__main__":
    main()
