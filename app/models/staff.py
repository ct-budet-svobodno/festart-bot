from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.models.base import Base, TimestampMixin


class StaffRole:
    SUPERADMIN = "superadmin"  # всё, включая управление организаторами
    ADMIN = "admin"  # контент, статистика, откаты

    CHOICES = [
        (SUPERADMIN, "Суперадмин"),
        (ADMIN, "Администратор"),
    ]
    LABELS = dict(CHOICES)

    # Кто имеет право выдавать призы.
    CAN_REDEEM = {SUPERADMIN, ADMIN}
    # Кто может начислять баллы вручную.
    CAN_AWARD = {SUPERADMIN, ADMIN}
    # Кто может откатывать операции.
    CAN_REVERT = {SUPERADMIN, ADMIN}


class Staff(Base, TimestampMixin):
    """Организатор.

    Заводится в админке, получает персональную ссылку-приглашение.
    Перейдя по ней, привязывает свой Telegram — с этого момента бот узнаёт его
    и показывает служебные сценарии вместо участнических.
    """

    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    tg_username: Mapped[str | None] = mapped_column(String(64))

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default=StaffRole.ADMIN, nullable=False)

    invite_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime)

    @property
    def role_label(self) -> str:
        return StaffRole.LABELS.get(self.role, self.role)

    @property
    def is_activated(self) -> bool:
        return self.tg_id is not None

    @property
    def is_env_admin(self) -> bool:
        """Прописан в ADMIN_TG_IDS. Такого нельзя понизить, отключить или удалить
        из бота — иначе можно случайно закрыть себе вход."""
        return self.tg_id is not None and self.tg_id in settings.admin_ids

    def can(self, permission: set[str]) -> bool:
        return self.is_active and self.role in permission

    def __repr__(self) -> str:
        return f"<Staff {self.name} ({self.role})>"
