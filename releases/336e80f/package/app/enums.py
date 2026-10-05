from enum import StrEnum


class UserRole(StrEnum):
    EXPERT = "expert"
    SPECIALIST = "specialist"
    ADMIN = "admin"


class InspectionWorkType(StrEnum):
    EXPERT = "expert"
    SPECIALIST = "specialist"


class InspectionScenario(StrEnum):
    NEXT = "next"
    SAME_VEHICLE = "same_vehicle"
    SHIFT = "shift"
    CONTROL_SHIPMENT = "control_shipment"
    INSPECTION_STOP = "inspection_stop"
    IDLE_TRIP = "idle_trip"
    COMMISSION = "commission"
    REPEAT = "repeat"


class ReconciliationState(StrEnum):
    FOUND = "found"
    MISSING = "missing"
    NO_FILE = "no_file"
    LEGACY = "legacy"


class ProjectCode(StrEnum):
    THUNDER_AGRO_MAF = "thunder_agro_maf"
    THUNDER_WATERMELONS = "thunder_watermelons"
    THUNDER_SELF_PICKUP = "thunder_self_pickup"
    X5_SELF_PICKUP = "x5_self_pickup"
    X5_TECHMP_RVI = "x5_techmp_rvi"


class UserStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    BLOCKED = "blocked"


class SupplierStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    INACTIVE = "inactive"


class ExportGroup(StrEnum):
    AK = "АК"
    SV = "СВ"
    X5 = "Х5"


class ExportMode(StrEnum):
    NEW = "new"
    FULL = "full"


class ExportState(StrEnum):
    PREPARING = "preparing"
    SENT = "sent"
    FAILED = "failed"
