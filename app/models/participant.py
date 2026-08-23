from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Faculty(Base):
    """Справочник факультетов. Редактируется в админке, показывается кнопками при регистрации."""

    __tablename__ = "faculties"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    participants: Mapped[list["Participant"]] = relationship(back_populates="faculty")

    def __repr__(self) -> str:
        return f"<Faculty {self.title}>"


class Participant(Base, TimestampMixin):
    """Участник фестиваля.

    Строка создаётся при первом /start, но регистрация считается завершённой
    только когда заполнены ФИО, факультет и студбилет (is_registered).
    """

    __tablename__ = "participants"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    tg_username: Mapped[str | None] = mapped_column(String(64))

    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    faculty_id: Mapped[int | None] = mapped_column(ForeignKey("faculties.id", ondelete="SET NULL"))
    faculty_other: Mapped[str | None] = mapped_column(String(200))
    student_id: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)

    # Личный QR участника: организатор сканирует его на стойке призов.
    qr_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # Резервный шестизначный код, если QR не считывается (треснутый экран, солнце).
    short_code: Mapped[str] = mapped_column(String(8), unique=True, index=True, nullable=False)

    is_registered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    registered_at: Mapped[datetime | None] = mapped_column(DateTime)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Код зоны, отсканированный до завершения регистрации. Начислим сразу после неё,
    # иначе человек решит, что баллы потерялись.
    pending_activity_code: Mapped[str | None] = mapped_column(String(16))

    faculty: Mapped["Faculty | None"] = relationship(back_populates="participants")

    @property
    def full_name(self) -> str:
        parts = [p for p in (self.last_name, self.first_name) if p]
        return " ".join(parts) or (self.tg_username and f"@{self.tg_username}") or f"id{self.tg_id}"

    @property
    def faculty_title(self) -> str:
        if self.faculty:
            return self.faculty.title
        return self.faculty_other or "—"

    def __repr__(self) -> str:
        return f"<Participant {self.id} {self.full_name}>"
