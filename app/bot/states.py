from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    """Порядок как в ТЗ: имя, фамилия, факультет, номер студенческого."""

    first_name = State()
    last_name = State()
    faculty = State()
    faculty_other = State()
    student_id = State()


class StaffAward(StatesGroup):
    """Ручное начисление баллов организатором."""

    amount = State()


class StaffLookup(StatesGroup):
    """Поиск участника по шестизначному коду, когда QR не считывается."""

    short_code = State()
