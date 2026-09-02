"""Сброс данных участников перед новым прогоном или перед боевым днём.

    python -m scripts.reset --participants   # только участники и их баллы
    python -m scripts.reset --all            # ещё и призы вернуть на склад

Контент — зоны, мастер-классы, призы, тексты, организаторы — не трогаем.
Скрипт спрашивает подтверждение: удаление участников необратимо.
"""

import argparse
import asyncio
import sys

from sqlalchemy import delete, func, select, update

from app.db import session_scope
from app.models import (
    Participant,
    PointsLedger,
    Prize,
    Redemption,
    RedemptionStatus,
    Visit,
)


async def counts() -> dict[str, int]:
    async with session_scope() as session:
        result = {}
        for model in (Participant, Visit, PointsLedger, Redemption):
            result[model.__name__] = int(
                await session.scalar(select(func.count()).select_from(model)) or 0
            )
        return result


async def reset(*, restock: bool) -> None:
    async with session_scope() as session:
        if restock:
            # Возвращаем на склад всё, что было зарезервировано или выдано.
            issued = await session.execute(
                select(Redemption.prize_id, func.count(Redemption.id))
                .where(
                    Redemption.prize_id.isnot(None),
                    Redemption.status.in_(
                        [RedemptionStatus.PENDING, RedemptionStatus.CONFIRMED]
                    ),
                )
                .group_by(Redemption.prize_id)
            )
            for prize_id, quantity in issued.all():
                await session.execute(
                    update(Prize)
                    .where(Prize.id == prize_id)
                    .values(stock_left=func.min(Prize.stock_total,
                                                Prize.stock_left + quantity))
                )

        # Порядок важен: сначала то, что ссылается на участника.
        for model in (Redemption, PointsLedger, Visit, Participant):
            await session.execute(delete(model))


def main() -> None:
    parser = argparse.ArgumentParser(description="Сброс данных участников")
    parser.add_argument("--participants", action="store_true",
                        help="удалить участников, посещения, баллы и выдачи")
    parser.add_argument("--all", action="store_true",
                        help="то же самое плюс вернуть призы на склад")
    parser.add_argument("--yes", action="store_true", help="без подтверждения")
    args = parser.parse_args()

    if not (args.participants or args.all):
        parser.print_help()
        sys.exit(1)

    before = asyncio.run(counts())
    print("Сейчас в базе:")
    for name, value in before.items():
        print(f"  {name:14} {value}")
    if args.all:
        print("\nПризы будут возвращены на склад.")
    print("\nЗоны, мастер-классы, призы, тексты и организаторы останутся на месте.")

    if not args.yes:
        answer = input('\nУдалить участников? Напиши "да": ').strip().lower()
        if answer not in {"да", "yes", "y"}:
            print("Отменено.")
            return

    asyncio.run(reset(restock=args.all))
    after = asyncio.run(counts())
    print("\nГотово. Осталось:")
    for name, value in after.items():
        print(f"  {name:14} {value}")


if __name__ == "__main__":
    main()
