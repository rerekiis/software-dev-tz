"""Бизнес-логика работы с БД."""
from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .models import Call, Company, Manager, Meeting, MeetingEvent, utcnow
from .schemas import (
    ALMATY_TZ,
    MEETING_DURATION,
    CallCreate,
    CallResult,
    CompanyCreate,
    CompanyStatus,
    ManagerSchedule,
    MeetingCreate,
    MeetingPatch,
    MeetingStatus,
    ScheduleMeeting,
    almaty_day_bounds,
)


class ApiError(Exception):
    """Ожидаемая ошибка API: отдаётся клиенту как JSON с понятным сообщением."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _not_found(entity: str, entity_id: int) -> ApiError:
    return ApiError(404, "not_found", f"{entity} с id={entity_id} не найден")


def _local_time(value: datetime) -> str:
    return value.astimezone(ALMATY_TZ).strftime("%d.%m.%Y %H:%M")


def _lock_for_write(db: Session) -> None:
    """Берёт write-lock SQLite до проверки конфликтов.

    Так проверка пересечений и вставка выполняются атомарно относительно
    параллельных запросов, а не как отдельные read-then-write шаги.
    """
    db.execute(text("BEGIN IMMEDIATE"))


def _raise_integrity_error(exc: IntegrityError) -> None:
    """Превращает сработавшие триггеры БД в корректные HTTP-ошибки."""
    message = str(getattr(exc, "orig", exc))
    if "manager_conflict" in message:
        raise ApiError(
            409,
            "meeting_conflict",
            "У менеджера уже есть встреча, пересекающаяся по времени",
        ) from exc
    if "company_conflict" in message:
        raise ApiError(
            409,
            "company_conflict",
            "У компании уже есть активная встреча, пересекающаяся по времени",
        ) from exc
    if "meeting_set_requires_meeting" in message:
        raise ApiError(
            422,
            "meeting_set_requires_meeting",
            "Для результата meeting_set нужна активная встреча этой компании",
        ) from exc
    raise exc


# ---------------------------------------------------------------------------
# Компании
# ---------------------------------------------------------------------------

def create_company(db: Session, payload: CompanyCreate) -> Company:
    company = Company(
        name=payload.name,
        bin=payload.bin,
        phone=payload.phone,
        status=CompanyStatus.NEW.value,
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def list_companies(
    db: Session,
    *,
    status: CompanyStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[Company], int]:
    filters = []
    if status is not None:
        filters.append(Company.status == status.value)

    total = db.scalar(select(func.count()).select_from(Company).where(*filters)) or 0
    items = list(
        db.scalars(
            select(Company)
            .where(*filters)
            .order_by(Company.id)
            .limit(limit)
            .offset(offset)
        ).all()
    )
    return items, total


# ---------------------------------------------------------------------------
# Менеджеры
# ---------------------------------------------------------------------------

def create_manager(db: Session, name: str) -> Manager:
    manager = Manager(name=name)
    db.add(manager)
    db.commit()
    db.refresh(manager)
    return manager


def list_managers(db: Session) -> list[Manager]:
    return list(db.scalars(select(Manager).order_by(Manager.id)).all())


def get_manager_schedule(
    db: Session, manager_id: int, day: date_type
) -> ManagerSchedule:
    manager = db.get(Manager, manager_id)
    if manager is None:
        raise _not_found("Менеджер", manager_id)

    day_start, day_end = almaty_day_bounds(day)
    rows = db.execute(
        select(Meeting, Company.name)
        .join(Company, Company.id == Meeting.company_id)
        .where(
            Meeting.manager_id == manager_id,
            Meeting.status == MeetingStatus.SCHEDULED.value,
            Meeting.start_time >= day_start,
            Meeting.start_time < day_end,
        )
        .order_by(Meeting.start_time)
    ).all()

    meetings = [
        ScheduleMeeting(
            id=meeting.id,
            company_id=meeting.company_id,
            company_name=company_name,
            start_time=meeting.start_time,
            end_time=meeting.end_time,
            original_start_time=meeting.original_start_time,
            status=meeting.status,
        )
        for meeting, company_name in rows
    ]
    return ManagerSchedule(
        manager_id=manager.id,
        manager_name=manager.name,
        date=day,
        meetings=meetings,
    )


# ---------------------------------------------------------------------------
# Звонки
# ---------------------------------------------------------------------------

def create_call(db: Session, payload: CallCreate) -> Call:
    company = db.get(Company, payload.company_id)
    if company is None:
        raise _not_found("Компания", payload.company_id)

    if payload.result == CallResult.MEETING_SET:
        meeting = db.get(Meeting, payload.meeting_id)
        if (
            meeting is None
            or meeting.company_id != company.id
            or meeting.status != MeetingStatus.SCHEDULED.value
        ):
            raise ApiError(
                422,
                "meeting_set_requires_meeting",
                "Для результата meeting_set нужна активная встреча этой компании",
            )

    call = Call(
        company_id=company.id,
        result=payload.result.value,
        meeting_id=payload.meeting_id,
        comment=payload.comment,
    )
    company.status = payload.result.value
    db.add(call)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _raise_integrity_error(exc)
    db.refresh(call)
    return call


# ---------------------------------------------------------------------------
# Встречи
# ---------------------------------------------------------------------------

def _ensure_no_conflicts(
    db: Session,
    *,
    company_id: int,
    manager_id: int,
    start: datetime,
    end: datetime,
    exclude_meeting_id: int | None = None,
) -> None:
    manager_query = select(Meeting).where(
        Meeting.manager_id == manager_id,
        Meeting.status == MeetingStatus.SCHEDULED.value,
        Meeting.start_time < end,
        Meeting.end_time > start,
    )
    company_query = select(Meeting).where(
        Meeting.company_id == company_id,
        Meeting.status == MeetingStatus.SCHEDULED.value,
        Meeting.start_time < end,
        Meeting.end_time > start,
    )
    if exclude_meeting_id is not None:
        manager_query = manager_query.where(Meeting.id != exclude_meeting_id)
        company_query = company_query.where(Meeting.id != exclude_meeting_id)

    manager_conflict = db.scalar(manager_query.limit(1))
    if manager_conflict is not None:
        raise ApiError(
            409,
            "meeting_conflict",
            "У менеджера уже есть встреча с "
            f"{_local_time(manager_conflict.start_time)} по "
            f"{_local_time(manager_conflict.end_time)} (Asia/Almaty)",
        )

    company_conflict = db.scalar(company_query.limit(1))
    if company_conflict is not None:
        raise ApiError(
            409,
            "company_conflict",
            "У компании уже есть активная встреча с "
            f"{_local_time(company_conflict.start_time)} по "
            f"{_local_time(company_conflict.end_time)} (Asia/Almaty)",
        )


def create_meeting(db: Session, payload: MeetingCreate) -> Meeting:
    _lock_for_write(db)

    company = db.get(Company, payload.company_id)
    if company is None:
        raise _not_found("Компания", payload.company_id)
    manager = db.get(Manager, payload.manager_id)
    if manager is None:
        raise _not_found("Менеджер", payload.manager_id)

    start = payload.start_time
    end = start + MEETING_DURATION
    _ensure_no_conflicts(
        db,
        company_id=company.id,
        manager_id=manager.id,
        start=start,
        end=end,
    )

    now = utcnow()
    meeting = Meeting(
        company_id=company.id,
        manager_id=manager.id,
        start_time=start,
        end_time=end,
        original_start_time=start,
        status=MeetingStatus.SCHEDULED.value,
        created_at=now,
        updated_at=now,
    )
    db.add(meeting)
    try:
        db.flush()
        meeting.events.append(
            MeetingEvent(
                action="created",
                old_start_time=None,
                new_start_time=start,
                created_at=now,
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _raise_integrity_error(exc)

    return meeting


def get_meeting(db: Session, meeting_id: int) -> Meeting:
    meeting = db.scalar(
        select(Meeting)
        .options(selectinload(Meeting.events))
        .where(Meeting.id == meeting_id)
    )
    if meeting is None:
        raise _not_found("Встреча", meeting_id)
    return meeting


def patch_meeting(db: Session, meeting_id: int, payload: MeetingPatch) -> Meeting:
    _lock_for_write(db)

    meeting = db.scalar(
        select(Meeting)
        .options(selectinload(Meeting.events))
        .where(Meeting.id == meeting_id)
    )
    if meeting is None:
        raise _not_found("Встреча", meeting_id)

    now = utcnow()

    if payload.status == MeetingStatus.CANCELLED.value:
        if meeting.status == MeetingStatus.CANCELLED.value:
            raise ApiError(409, "meeting_cancelled", "Встреча уже отменена")
        meeting.status = MeetingStatus.CANCELLED.value
        meeting.cancelled_at = now
        meeting.updated_at = now
        meeting.events.append(
            MeetingEvent(
                action="cancelled",
                old_start_time=meeting.start_time,
                new_start_time=None,
                created_at=now,
            )
        )
    else:
        if meeting.status == MeetingStatus.CANCELLED.value:
            raise ApiError(409, "meeting_cancelled", "Отменённую встречу нельзя перенести")

        new_start = payload.start_time
        old_start = meeting.start_time
        if new_start == old_start:
            raise ApiError(
                422, "validation_error", "Новое время совпадает с текущим временем встречи"
            )

        new_end = new_start + MEETING_DURATION
        _ensure_no_conflicts(
            db,
            company_id=meeting.company_id,
            manager_id=meeting.manager_id,
            start=new_start,
            end=new_end,
            exclude_meeting_id=meeting.id,
        )
        meeting.start_time = new_start
        meeting.end_time = new_end
        meeting.updated_at = now
        meeting.events.append(
            MeetingEvent(
                action="rescheduled",
                old_start_time=old_start,
                new_start_time=new_start,
                created_at=now,
            )
        )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _raise_integrity_error(exc)

    return meeting
