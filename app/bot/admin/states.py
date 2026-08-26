from aiogram.fsm.state import State, StatesGroup


class AdminEdit(StatesGroup):
    """Ввод нового значения поля. В data лежит spec_code, item_id, field_key."""

    value = State()


class AdminCreate(StatesGroup):
    """Создание записи: спрашиваем только название, остальное правится в карточке."""

    title = State()


class AdminFaculty(StatesGroup):
    title = State()


class AdminStaff(StatesGroup):
    name = State()


class AdminFind(StatesGroup):
    query = State()

