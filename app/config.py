"""Настройки приложения. Читаются из .env, всё имеет разумный дефолт для разработки."""

from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
MEDIA_DIR = BASE_DIR / "media"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: str = ""
    bot_username: str = "Festart_bot"

    # Telegram ID постоянных суперадминов через запятую.
    # Эти люди получают доступ автоматически и их нельзя отключить из бота —
    # аварийный вход на случай, если приглашения протухли или база пересоздана.
    admin_tg_ids: str = ""

    database_url: str = f"sqlite+aiosqlite:///{DATA_DIR / 'festart.db'}"

    admin_password: str = "changeme"
    secret_key: str = "dev-secret-change-me"
    admin_host: str = "0.0.0.0"
    admin_port: int = 8000
    admin_base_url: str = "http://localhost:8000"

    timezone: str = "Europe/Moscow"

    @field_validator("database_url")
    @classmethod
    def _absolute_sqlite_path(cls, value: str) -> str:
        """Относительный путь к SQLite превращаем в абсолютный.

        В .env путь пишется как data/festart.db — коротко и понятно. Но он
        считается от текущей директории процесса, а не от проекта: запустил
        бота или админку не из корня — и SQLite молча пытается открыть базу
        в другом месте, падая с «unable to open database file».
        """
        prefix = "sqlite+aiosqlite:///"
        if not value.startswith(prefix):
            return value
        path = value[len(prefix):]
        if not path or path.startswith("/") or path.startswith(":memory:"):
            return value
        return prefix + str(BASE_DIR / path)

    @property
    def admin_ids(self) -> set[int]:
        ids: set[int] = set()
        for chunk in self.admin_tg_ids.replace(";", ",").split(","):
            chunk = chunk.strip()
            if chunk.lstrip("-").isdigit():
                ids.add(int(chunk))
        return ids

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def bot_link(self) -> str:
        return f"https://t.me/{self.bot_username}"

    def deep_link(self, payload: str) -> str:
        """Ссылка, которую зашиваем в QR-код."""
        return f"{self.bot_link}?start={payload}"


settings = Settings()

DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
