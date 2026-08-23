from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class LedgerReason:
    REGISTRATION = "registration"
    VISIT = "visit"
    BONUS = "bonus"  # бонус за прохождение всех зон и т.п.
    REDEMPTION = "redemption"
    MANUAL = "manual"  # организатор начислил руками
    REVERT = "revert"  # возврат баллов при откате выдачи

    LABELS = {
        REGISTRATION: "Регистрация",
        VISIT: "Посещение зоны",
        BONUS: "Бонус",
        REDEMPTION: "Обмен на приз",
        MANUAL: "Начислено вручную",
        REVERT: "Возврат",
    }


class PointsLedger(Base):
    """Журнал операций с баллами.

    Баланс участника не хранится отдельным полем, а считается как сумма delta.
    Так любой спор на площадке («мне не начислили», «списали дважды») решается
    просмотром истории, а ошибочную операцию можно откатить обратной записью,
    ничего не затирая.
    """

    __tablename__ = "points_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), index=True, nullable=False
    )

    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)

    # На что ссылается операция: visit / redemption / activity
    ref_type: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[int | None] = mapped_column(Integer)

    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"))
    comment: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)

    @property
    def reason_label(self) -> str:
        return LedgerReason.LABELS.get(self.reason, self.reason)

    def __repr__(self) -> str:
        return f"<Ledger p={self.participant_id} {self.delta:+d} {self.reason}>"
