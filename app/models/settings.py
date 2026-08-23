from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Тексты по умолчанию — заглушки. Заменяются в админке без правки кода.
DEFAULT_WELCOME = (
    "Привет! 👋\n\n"
    "Это бот фестиваля <b>ФЕСТАРТ</b>.\n\n"
    "Ходи по зонам, сканируй QR-коды, копи баллы и меняй их на призы.\n\n"
    "Для начала давай познакомимся."
)

DEFAULT_REGISTRATION_DONE = (
    "Готово, {name}! 🎉\n\n"
    "Ты в игре. Начинай собирать баллы — ищи QR-коды на зонах и активностях."
)

DEFAULT_HELP = (
    "<b>Как это работает</b>\n\n"
    "1. Находишь зону или мастер-класс\n"
    "2. Сканируешь QR-код камерой телефона\n"
    "3. Получаешь баллы\n"
    "4. Меняешь баллы на призы у стойки выдачи\n\n"
    "Что-то пошло не так — подойди к любому организатору."
)

DEFAULT_FINAL_MESSAGE = (
    "Спасибо, что был с нами на ФЕСТАРТе! ✨\n\n"
    "Твой результат: <b>{points}</b>\n"
    "Пройдено зон: <b>{visits}</b>\n\n"
    "Расскажи, как тебе фестиваль — нам правда важно."
)

DEFAULT_QR_HINT = (
    "Покажи этот код организатору на стойке выдачи призов.\n\n"
    "Если код не считывается — назови номер: <code>{short_code}</code>"
)


class EventSettings(Base, TimestampMixin):
    """Настройки мероприятия. В таблице всегда ровно одна строка (id=1).

    Сюда вынесено всё, что заказчик захочет поменять в последний момент:
    тексты, ссылки, бонусы, переключатели режимов.
    """

    __tablename__ = "event_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)

    event_title: Mapped[str] = mapped_column(String(200), default="ФЕСТАРТ", nullable=False)

    welcome_text: Mapped[str] = mapped_column(Text, default=DEFAULT_WELCOME, nullable=False)
    registration_done_text: Mapped[str] = mapped_column(
        Text, default=DEFAULT_REGISTRATION_DONE, nullable=False
    )
    help_text: Mapped[str] = mapped_column(Text, default=DEFAULT_HELP, nullable=False)
    final_message_text: Mapped[str] = mapped_column(
        Text, default=DEFAULT_FINAL_MESSAGE, nullable=False
    )
    qr_hint_text: Mapped[str] = mapped_column(Text, default=DEFAULT_QR_HINT, nullable=False)

    feedback_url: Mapped[str | None] = mapped_column(String(500))
    privacy_url: Mapped[str | None] = mapped_column(String(500))
    require_consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Имя файла карты в media/. Заливается через админку.
    map_image: Mapped[str | None] = mapped_column(String(255))
    map_caption: Mapped[str | None] = mapped_column(Text)

    registration_bonus: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    all_zones_bonus: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Рубильники на день мероприятия.
    is_registration_open: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_scanning_open: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_redemption_open: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Показывать ли участникам рейтинг.
    show_leaderboard: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __repr__(self) -> str:
        return f"<EventSettings {self.event_title}>"
