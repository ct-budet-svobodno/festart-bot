from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, utcnow


class RedemptionStatus:
    PENDING = "pending"  # организатор выбрал приз, ждём подтверждения участника
    CONFIRMED = "confirmed"  # участник подтвердил, баллы списаны, приз выдан
    CANCELLED = "cancelled"  # отменено участником, организатором или по таймауту
    REVERTED = "reverted"  # откат уже подтверждённой выдачи, баллы возвращены

    LABELS = {
        PENDING: "Ждёт подтверждения",
        CONFIRMED: "Выдан",
        CANCELLED: "Отменён",
        REVERTED: "Откачен",
    }


class Prize(Base, TimestampMixin):
    """Приз. Название, цена и количество правятся в админке в любой момент,
    в том числе в день мероприятия."""

    __tablename__ = "prizes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    cost_points: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    stock_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stock_left: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 0 = без ограничения
    per_user_limit: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    redemptions: Mapped[list["Redemption"]] = relationship(back_populates="prize")

    @property
    def in_stock(self) -> bool:
        return self.stock_left > 0

    def __repr__(self) -> str:
        return f"<Prize {self.title} {self.cost_points}б>"


class Redemption(Base):
    """Выдача приза.

    Название и цена копируются в момент операции: админ может переименовать
    или переоценить приз позже, а в истории должно остаться то, что было на самом деле.
    """

    __tablename__ = "redemptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    prize_id: Mapped[int | None] = mapped_column(ForeignKey("prizes.id", ondelete="SET NULL"))
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"))

    prize_title: Mapped[str] = mapped_column(String(200), nullable=False)
    cost_points: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), default=RedemptionStatus.PENDING, index=True, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    comment: Mapped[str | None] = mapped_column(String(500))

    prize: Mapped["Prize | None"] = relationship(back_populates="redemptions")

    @property
    def status_label(self) -> str:
        return RedemptionStatus.LABELS.get(self.status, self.status)

    def __repr__(self) -> str:
        return f"<Redemption {self.id} {self.prize_title} {self.status}>"
