"""Какие поля у каких сущностей редактируются из бота."""

from dataclasses import dataclass

from app.bot.admin.fields import (
    BOOL,
    INT,
    LONGTEXT,
    PERCENT,
    TEXT,
    TIME,
    URL,
    Field,
)
from app.models import Activity, EventSettings, Prize

PRIZE = "p"
ZONE = "z"
WORKSHOP = "w"
SETTINGS = "s"


@dataclass(frozen=True)
class Spec:
    code: str
    model: type
    title: str  # заголовок раздела
    one: str  # как называется одна запись
    fields: tuple[Field, ...]
    toggles: tuple[Field, ...] = ()


PRIZE_SPEC = Spec(
    code=PRIZE,
    model=Prize,
    title="Призы",
    one="приз",
    fields=(
        Field("title", "Название", TEXT, required=True),
        Field("description", "Описание", LONGTEXT, hint="Строчка, которую увидит участник"),
        Field("cost_points", "Цена в баллах", INT, min_value=0, max_value=100000),
        Field("stock_left", "Осталось на складе", INT, min_value=0, max_value=100000),
        Field("stock_total", "Всего закуплено", INT, min_value=0, max_value=100000),
        Field("per_user_limit", "В одни руки", INT, min_value=0, max_value=100,
              hint="0 — без ограничения"),
        Field("sort_order", "Порядок в списке", INT, min_value=0, max_value=10000,
              hint="Меньше число — выше в списке. Можно не трогать"),
    ),
    toggles=(Field("is_active", "Показывать участникам", BOOL),),
)

ZONE_SPEC = Spec(
    code=ZONE,
    model=Activity,
    title="Зоны",
    one="зона",
    fields=(
        Field("title", "Название", TEXT, required=True),
        Field("description", "Описание", LONGTEXT),
        Field("location", "Место", TEXT),
        Field("points", "Баллов за посещение", INT, min_value=0, max_value=10000),
        Field("map_x", "X на карте, %", PERCENT,
              hint="Процентов от левого края. Прикинь по карте с сеткой — она в разделе «Карта»"),
        Field("map_y", "Y на карте, %", PERCENT,
              hint="Процентов от верха. Прикинь по карте с сеткой — она в разделе «Карта»"),
        Field("sort_order", "Порядок в списке", INT, min_value=0, max_value=10000,
              hint="Меньше число — выше в списке. Можно не трогать"),
    ),
    toggles=(Field("is_active", "Активна", BOOL,
                   hint="Выключенная зона пропадёт у участников и не будет начислять баллы"),),
)

WORKSHOP_SPEC = Spec(
    code=WORKSHOP,
    model=Activity,
    title="Мастер-классы",
    one="мастер-класс",
    fields=(
        Field("title", "Название", TEXT, required=True),
        Field("description", "Описание", LONGTEXT,
              hint="Что там происходит — участник прочитает это в боте"),
        Field("location", "Место", TEXT, hint="Например: Аудитория 204"),
        Field("points", "Баллов за участие", INT, min_value=0, max_value=10000),
        Field("starts_at", "Начало", TIME),
        Field("ends_at", "Конец", TIME),
        Field("sort_order", "Порядок в списке", INT, min_value=0, max_value=10000,
              hint="Меньше число — выше в списке. Обычно хватает времени начала"),
    ),
    toggles=(Field("is_active", "Активен", BOOL,
                   hint="Выключенный мастер-класс пропадёт из расписания"),),
)

SETTINGS_SPEC = Spec(
    code=SETTINGS,
    model=EventSettings,
    title="Настройки",
    one="настройки",
    fields=(
        Field("event_title", "Название мероприятия", TEXT, required=True),
        Field("welcome_text", "Приветствие", LONGTEXT,
              hint="Первое, что видит участник"),
        Field("registration_done_text", "После регистрации", LONGTEXT,
              hint="Можно вставить {name} — подставится имя"),
        Field("qr_hint_text", "Подпись под личным QR", LONGTEXT,
              hint="{short_code} — шестизначный код участника"),
        Field("help_text", "Раздел «Помощь»", LONGTEXT),
        Field("final_message_text", "Итоги дня", LONGTEXT,
              hint="Доступны {points} и {visits}"),
        Field("map_caption", "Подпись под картой", TEXT),
        Field("feedback_url", "Форма обратной связи", URL),
        Field("registration_bonus", "Бонус за регистрацию", INT, min_value=0, max_value=10000),
        Field("all_zones_bonus", "Бонус за все зоны", INT, min_value=0, max_value=10000),
    ),
    toggles=(
        Field("is_registration_open", "Регистрация открыта", BOOL),
        Field("is_scanning_open", "Начисление баллов", BOOL),
        Field("is_redemption_open", "Выдача призов", BOOL),
    ),
)

SPECS: dict[str, Spec] = {
    PRIZE: PRIZE_SPEC,
    ZONE: ZONE_SPEC,
    WORKSHOP: WORKSHOP_SPEC,
    SETTINGS: SETTINGS_SPEC,
}


def all_fields(spec: Spec) -> tuple[Field, ...]:
    return spec.fields + spec.toggles


def find_field(spec: Spec, key: str) -> Field | None:
    return next((f for f in all_fields(spec) if f.key == key), None)
