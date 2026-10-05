from aiogram.fsm.state import State, StatesGroup


class RegistrationStates(StatesGroup):
    invite_code = State()
    full_name = State()
    role = State()


class InspectionStates(StatesGroup):
    work_type = State()
    date = State()
    order = State()
    supplier_query = State()
    supplier_manual = State()
    status = State()
    related_order = State()
    vehicle_number = State()
    project = State()
    rate = State()
    # Allows dialogs paused on the removed pre-release GСМ step to resume.
    fuel = State()
    comment = State()
    confirm = State()


class ReportStates(StatesGroup):
    custom_period = State()


class DirectoryEditStates(StatesGroup):
    name = State()
    status_duplicate = State()
    project_group = State()
    rename = State()
    merge_query = State()


class ReferenceUploadStates(StatesGroup):
    file = State()
