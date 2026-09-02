from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Служебные ключи внутри FSM-данных (не состояния, а значения в data).
# Переживают сброс сценариев: сбросили начисление баллов — режим интерфейса
# и адрес сообщения-хаба должны остаться на месте.
HUB_KEY = "hub_id"  # id сообщения-хаба меню, чтобы уметь его удалить
VIEW_KEY = "view"  # выбранный режим для того, кто и орг, и участник
VIEW_STAFF = "staff"
VIEW_PARTICIPANT = "participant"

PRESERVED_KEYS = (HUB_KEY, VIEW_KEY)


async def reset_state(state: FSMContext, *, keep_registration: bool = False) -> None:
    """Сбросить чужой сценарий (начисление баллов, поиск, админку),
    сохранив служебные ключи и, по флагу, анкету регистрации.

    Без этого организатор, начавший начислять баллы и отвлёкшийся на скан
    зоны или кнопку меню, следующим текстом случайно начислил бы баллы.
    """
    current = await state.get_state()
    if keep_registration and current is not None and current.startswith("Registration"):
        return
    data = await state.get_data()
    preserved = {k: v for k, v in data.items() if k in PRESERVED_KEYS}
    await state.set_data(preserved)
    await state.set_state(None)


class Registration(StatesGroup):
    """Фамилия, имя, отчество, факультет, номер студенческого билета."""

    last_name = State()
    first_name = State()
    middle_name = State()
    faculty = State()
    faculty_other = State()
    student_id = State()


class StaffAward(StatesGroup):
    """Ручное начисление баллов организатором."""

    amount = State()


class StaffLookup(StatesGroup):
    """Поиск участника по шестизначному коду, когда QR не считывается."""

    short_code = State()
