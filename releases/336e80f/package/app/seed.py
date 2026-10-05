from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ExportGroup, InspectionScenario, ProjectCode, SupplierStatus
from app.models import InspectionStatus, Project, Supplier
from app.validators import normalize_name

STATUS_SEEDS: tuple[tuple[str, InspectionScenario, bool, bool], ...] = (
    ("далее", InspectionScenario.NEXT, False, False),
    ("отгружались в одной авто", InspectionScenario.SAME_VEHICLE, False, True),
    ("добавить смену", InspectionScenario.SHIFT, False, True),
    ("контроль отгрузки", InspectionScenario.CONTROL_SHIPMENT, False, False),
    ("осмотр / стоп-отгрузка", InspectionScenario.INSPECTION_STOP, False, False),
    ("холостой выезд", InspectionScenario.IDLE_TRIP, False, False),
    ("комиссионная инспекция", InspectionScenario.COMMISSION, True, False),
    ("повторная инспекция", InspectionScenario.REPEAT, True, False),
)

PROJECT_SEEDS: tuple[tuple[str, ProjectCode, ExportGroup], ...] = (
    ("Тандер, АГРОКОНТАРКТ/МАФ", ProjectCode.THUNDER_AGRO_MAF, ExportGroup.AK),
    ("Тандер, арбузы", ProjectCode.THUNDER_WATERMELONS, ExportGroup.AK),
    ("Тандер, САМОВЫВОЗ", ProjectCode.THUNDER_SELF_PICKUP, ExportGroup.SV),
    ("Х5 САМОВЫВОЗ", ProjectCode.X5_SELF_PICKUP, ExportGroup.X5),
    ("Х5 ТЕХМП RVI", ProjectCode.X5_TECHMP_RVI, ExportGroup.X5),
)

SUPPLIER_SEEDS: tuple[str, ...] = (
    "Югай Артур Владиславович ИП (РЦ) (АГРОКОНТРАКТ)",
    "KOROLEVSTVO FRUKTOV ООО",
    "Терновский АК ООО",
    "ЛУГОВСКИЕ ОВОЩИ ООО",
    "Хон С.Д. ГКФХ ИП",
    "Гранада ООО (АГРОКОНТРАКТ) (РЦ)",
    "Дубовский фермер ООО (РЦ) (АГРОКОНТРАКТ)",
    "Хутраева Арзу Абдуллаевна Г(Ф)Х ИП (РЦ) (АГРОКОНТРАКТ)",
    "Артенян Р.Э. ИП (РЦ) (АГРОКОНТРАКТ)",
    "TEMURBEK BUSINESS OOO",
    "Багандов М.А. ИП глава КФХ",
    "Сад ООО (РЦ) (АГРОКОНТРАКТ)",
    "Гранада ООО",
    "Союз Волгоград ООО (РЦ) (АГРОКОНТРАКТ)",
    "Хон К.Д. ИП (РЦ) (Агроконтракт)",
    "Нурмагомедова П.И. КФХ ИП (РЦ) (АГРОКОНТРАКТ)",
    "Артенян Р.Э. ИП",
    "Латипов Д.Х. ИП",
    "Христофорова Анна Германовна ИП",
    "Лайт ООО",
    "Гаффаров Камран Нахиб оглы ИП",
    "Вавилон ООО",
    "Долина ООО",
    "Зеленая грядка ТК ООО",
    "Зеленый мир ООО",
    "ВИТАМИНГО ООО",
    "Мартынова С.Г. Глава КФХ",
    "РПК ООО",
    "Зелень Юга ООО",
    "Гринхаус ООО (СОБСТВЕННОЕ ПРОИЗВОДСТВО)",
    "ПАРТ ООО",
    "Сосногорский ТК ООО",
    "САДЫ ЕЛИЗАВЕТЫ СРПК",
    "ЗЕЛЕНАЯ ПОЛЯНА ООО",
    "ООО «СХП «ДАРЫ ПРЕДГОРЬЯ»",
    "ООО ФИРМА ЛТД ( 7000068285 )",
    'ООО "ЯГОДНЫЕ ПОЛЯ"',
    'ООО "Эльбрус Фрутт"',
    "ИП глава КФХ Багандов",
    "ООО Зеленая Долина",
    'ООО "Брянский Картофель"',
    'ООО "Яблоко-07"',
    'ООО "ТД АГРОКОМПЛЕКС"',
    'ООО "Новозаведенское"',
    'ООО "АГРОФИРМА "ЗОЛОТАЯ НИВА"',
    'ООО "ФРУКТОВЫЙ САД" (7000074583)',
    'ООО "СХП "УРОЖАЙНОЕ" (дочерка ООО "СХП "РАССВЕТ")',
    "ИП Сычев Дмитрий Николаевич",
    'ООО "МСК АГРО"',
    'ОАО АГРОКОМБИНАТ "ГОРЬКОВСКИЙ"',
    'АО АГРОКОМБИНАТ "ЮЖНЫЙ"',
    'АО ФИРМА "АГРОКОМПЛЕКС" ИМ.Ткачева',
    'ООО "ФРУКТОВЫЙ САД"',
    'ООО "АГРОФИРМА "ЛУЧ-15"',
    'ООО "АСТАГРО"',
    'ООО "Вкус Ставрополья"',
    "ООО “Здоровые фрукты”",
    "ООО Владимировский сад",
    'ООО "САДЫ КБР"',
    'ЗАО "АГРОФИРМА ИМЕНИ 15 ЛЕТ ОКТЯБРЯ"',
    "ИП Иванов Александр Алексеевич",
    'ООО "АКВА 7"',
    'ООО "СХП "РАССВЕТ"',
)


async def seed_reference_data(session: AsyncSession) -> None:
    existing_statuses = list((await session.scalars(select(InspectionStatus))).all())
    used_status_ids: set[int] = set()
    for name, code, allows_duplicate, supports_related_orders in STATUS_SEEDS:
        normalized = normalize_name(name)
        existing = next(
            (
                item
                for item in existing_statuses
                if item.code is code or item.normalized_name == normalized
            ),
            None,
        )
        if existing is None:
            session.add(
                InspectionStatus(
                    name=name,
                    normalized_name=normalized,
                    code=code,
                    allows_duplicate=allows_duplicate,
                    supports_related_orders=supports_related_orders,
                    active=True,
                )
            )
        else:
            existing.name = name
            existing.normalized_name = normalized
            existing.code = code
            existing.allows_duplicate = allows_duplicate
            existing.supports_related_orders = supports_related_orders
            existing.active = True
            used_status_ids.add(existing.id)

    for existing in existing_statuses:
        if existing.id not in used_status_ids and existing.code is None:
            existing.active = False

    existing_projects = list((await session.scalars(select(Project))).all())
    used_project_ids: set[int] = set()
    for name, code, export_group in PROJECT_SEEDS:
        normalized = normalize_name(name)
        existing = next(
            (
                item
                for item in existing_projects
                if item.code is code or item.normalized_name == normalized
            ),
            None,
        )
        if existing is None:
            session.add(
                Project(
                    name=name,
                    normalized_name=normalized,
                    code=code,
                    export_group=export_group,
                    active=True,
                )
            )
        else:
            existing.name = name
            existing.normalized_name = normalized
            existing.code = code
            existing.export_group = export_group
            existing.active = True
            used_project_ids.add(existing.id)

    for existing in existing_projects:
        if existing.id not in used_project_ids and existing.code is None:
            existing.active = False

    existing_suppliers = set((await session.scalars(select(Supplier.normalized_name))).all())
    for name in SUPPLIER_SEEDS:
        normalized = normalize_name(name)
        if normalized not in existing_suppliers:
            session.add(
                Supplier(
                    name=name,
                    normalized_name=normalized,
                    status=SupplierStatus.ACTIVE,
                )
            )
            existing_suppliers.add(normalized)

    await session.flush()
