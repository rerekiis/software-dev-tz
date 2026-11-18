"""Pydantic-схемы и правила валидации рабочего времени."""
from __future__ import annotations

import re
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

ALMATY_TZ = ZoneInfo("Asia/Almaty")
MEETING_DURATION = timedelta(minutes=60)
WORK_DAY_START = time(9, 0)
WORK_DAY_END = time(18, 0)
BIN_RE = re.compile(r"^\d{12}$")
PHONE_RE = re.compile(r"^\+?[\d\s()\-]{5,32}$")


# ---------------------------------------------------------------------------
# Время
# ---------------------------------------------------------------------------

def _serialize_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


UtcDatetime = Annotated[
    datetime, PlainSerializer(_serialize_utc, return_type=str, when_used="json")
]


def to_utc(value: datetime) -> datetime:
    """Приводит aware-datetime к UTC. Naive-datetime считается ошибкой."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            "Время должно содержать часовой пояс, например 2027-03-15T10:00:00+05:00"
        )
    return value.astimezone(timezone.utc)


def validate_meeting_start(value: datetime) -> datetime:
    """Проверяет, что 60-минутная встреча целиком попадает в пн-пт 09:00-18:00 Almaty."""
    start_utc = to_utc(value)
    local = start_utc.astimezone(ALMATY_TZ)

    if local.weekday() >= 5:
        raise ValueError(
            "Встречи назначаются только в рабочие дни (пн-пт) по времени Asia/Almaty"
        )

    start_minutes = local.hour * 60 + local.minute
    end_minutes = start_minutes + int(MEETING_DURATION.total_seconds() // 60)
    work_start = WORK_DAY_START.hour * 60 + WORK_DAY_START.minute
    work_end = WORK_DAY_END.hour * 60 + WORK_DAY_END.minute

    if start_minutes < work_start or end_minutes > work_end:
        raise ValueError(
            "Встреча должна целиком укладываться в рабочие часы 09:00-18:00 "
            "по времени Asia/Almaty"
        )

    return start_utc


def almaty_day_bounds(day: date_type) -> tuple[datetime, datetime]:
    """Возвращает UTC-границы календарного дня в Almaty [start, end)."""
    start_local = datetime.combine(day, time.min, tzinfo=ALMATY_TZ)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Перечисления
# ---------------------------------------------------------------------------

class CompanyStatus(str, Enum):
    NEW = "new"
    NO_ANSWER = "no_answer"
    REFUSED = "refused"
    MEETING_SET = "meeting_set"


class CallResult(str, Enum):
    NO_ANSWER = "no_answer"
    REFUSED = "refused"
    MEETING_SET = "meeting_set"


class MeetingStatus(str, Enum):
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"


class MeetingEventAction(str, Enum):
    CREATED = "created"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Компании и менеджеры
# ---------------------------------------------------------------------------

class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    bin: str
    phone: str

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Название компании не может быть пустым")
        return value

    @field_validator("bin")
    @classmethod
    def _validate_bin(cls, value: str) -> str:
        value = value.strip()
        if not BIN_RE.fullmatch(value):
            raise ValueError("БИН должен состоять ровно из 12 цифр")
        return value

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, value: str) -> str:
        value = value.strip()
        if not PHONE_RE.fullmatch(value):
            raise ValueError(
                "Телефон должен содержать 5-32 символа: цифры, пробелы, +, -, скобки"
            )
        return value


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    bin: str
    phone: str
    status: CompanyStatus
    created_at: UtcDatetime


class CompanyList(BaseModel):
    items: list[CompanyOut]
    total: int
    limit: int
    offset: int


class ManagerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Имя менеджера не может быть пустым")
        return value


class ManagerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: UtcDatetime


# ---------------------------------------------------------------------------
# Звонки
# ---------------------------------------------------------------------------

class CallCreate(BaseModel):
    company_id: int = Field(gt=0)
    result: CallResult
    meeting_id: int | None = Field(default=None, gt=0)
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _meeting_link_matches_result(self):
        if self.result == CallResult.MEETING_SET and self.meeting_id is None:
            raise ValueError(
                "Для результата meeting_set необходимо указать meeting_id "
                "существующей активной встречи"
            )
        if self.result != CallResult.MEETING_SET and self.meeting_id is not None:
            raise ValueError(
                "meeting_id можно передавать только при результате meeting_set"
            )
        return self


class CallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    result: CallResult
    meeting_id: int | None
    comment: str | None
    created_at: UtcDatetime


# ---------------------------------------------------------------------------
# Встречи
# ---------------------------------------------------------------------------

class MeetingCreate(BaseModel):
    company_id: int = Field(gt=0)
    manager_id: int = Field(gt=0)
    start_time: datetime

    @field_validator("start_time")
    @classmethod
    def _validate_start_time(cls, value: datetime) -> datetime:
        return validate_meeting_start(value)


class MeetingPatch(BaseModel):
    start_time: datetime | None = None
    status: Literal["cancelled"] | None = None

    @field_validator("start_time")
    @classmethod
    def _validate_start_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return validate_meeting_start(value)

    @model_validator(mode="after")
    def _exactly_one_change(self):
        if (self.start_time is None) == (self.status is None):
            raise ValueError(
                "Передайте ровно одно изменение: новый start_time или status=cancelled"
            )
        return self


class MeetingEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action: MeetingEventAction
    old_start_time: UtcDatetime | None
    new_start_time: UtcDatetime | None
    created_at: UtcDatetime


class MeetingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    manager_id: int
    start_time: UtcDatetime
    end_time: UtcDatetime
    original_start_time: UtcDatetime
    status: MeetingStatus
    created_at: UtcDatetime
    updated_at: UtcDatetime
    cancelled_at: UtcDatetime | None


class MeetingDetail(MeetingOut):
    events: list[MeetingEventOut] = Field(default_factory=list)


class ScheduleMeeting(BaseModel):
    id: int
    company_id: int
    company_name: str
    start_time: UtcDatetime
    end_time: UtcDatetime
    original_start_time: UtcDatetime
    status: MeetingStatus


class ManagerSchedule(BaseModel):
    manager_id: int
    manager_name: str
    date: date_type
    timezone: str = "Asia/Almaty"
    working_hours: str = "09:00-18:00"
    meetings: list[ScheduleMeeting]
