from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, utcnow


class ActivityKind:
    ZONE = "zone"
    WORKSHOP = "workshop"

    CHOICES = [(ZONE, "Интерактивная зона"), (WORKSHOP, "Мастер-класс")]


class VisitSource:
    QR = "qr"
    MANUAL = "manual"


class Activity(Base, TimestampMixin):
    """Зона или мастер-класс. Одна таблица, разделение по kind.

    У зоны и мастер-класса одна и та же механика начисления (скан QR),
    отличаются только набором полей: у МК есть расписание и описание.
    """

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default=ActivityKind.ZONE, nullable=False)

    # То, что зашито в QR-код. Генерируется автоматически, неугадываемое.
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(200))

    points: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Расписание — только для мастер-классов.
    starts_at: Mapped[datetime | None] = mapped_column(DateTime)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Позиция на карте площадки в процентах от ширины/высоты картинки.
    # Проценты, а не пиксели: карту можно перезалить в другом разрешении,
    # и отметки останутся на своих местах.
    map_x: Mapped[float | None] = mapped_column(Float)
    map_y: Mapped[float | None] = mapped_column(Float)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    visits: Mapped[list["Visit"]] = relationship(
        back_populates="activity", cascade="all, delete-orphan"
    )

    @property
    def is_workshop(self) -> bool:
        return self.kind == ActivityKind.WORKSHOP

    @property
    def has_map_position(self) -> bool:
        return self.map_x is not None and self.map_y is not None

    def __repr__(self) -> str:
        return f"<Activity {self.code} {self.title}>"


class Visit(Base):
    """Факт посещения зоны участником. Одна зона засчитывается один раз."""

    __tablename__ = "visits"
    __table_args__ = (
        UniqueConstraint("participant_id", "activity_id", name="uq_visit_participant_activity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), index=True, nullable=False
    )

    points_awarded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default=VisitSource.QR, nullable=False)
    # Кто начислил вручную, если source=manual.
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    activity: Mapped["Activity"] = relationship(back_populates="visits")

    def __repr__(self) -> str:
        return f"<Visit p={self.participant_id} a={self.activity_id}>"
