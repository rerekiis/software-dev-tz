"""Демонстрационные данные: используются при первом старте приложения и в CLI."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from . import crud, schemas
from .database import SessionLocal
from .models import Company, Manager

logger = logging.getLogger(__name__)

ALMATY = schemas.ALMATY_TZ
MARKER_BIN = "123456789012"
MANAGERS = ("Айгерим Смагулова", "Данияр Ахметов")

COMPANIES = (
    ("ТОО «Ромашка»", "123456789012", "+7 777 123 45 67"),
    ("ТОО «Барс Логистик»", "987654321098", "+7 701 555 12 34"),
    ("ИП «Айбек»", "112233445566", "+7 705 222 33 44"),
    ("ТОО «Нур Агро»", "556677889900", "+7 747 888 99 00"),
    ("ТОО «ТехноСити»", "667788990011", "+7 700 111 22 33"),
    ("ТОО «Астана Строй»", "778899001122", "+7 702 444 55 66"),
)


def almaty_dt(day: date, hour: int, minute: int = 0) -> datetime:
    """Локальное время Almaty -> aware datetime в UTC."""
    local = datetime.combine(day, time(hour, minute), tzinfo=ALMATY)
    return local.astimezone(timezone.utc)


def next_monday() -> date:
    """Ближайший понедельник строго в будущем (чтобы данные не устаревали)."""
    today = datetime.now(ALMATY).date()
    days_ahead = (7 - today.weekday()) % 7
    return today + timedelta(days=days_ahead or 7)


def _ensure_managers(db) -> list[Manager]:
    managers = list(db.scalars(select(Manager).order_by(Manager.id)).all())
    for name in MANAGERS[len(managers) :]:
        managers.append(crud.create_manager(db, name))
    return managers[:2]


def seed_demo_data() -> bool:
    """Создаёт демо-данные, если их ещё нет.

    Возвращает True, если данные были созданы, и False, если БД уже наполнена.
    """
    with SessionLocal() as db:
        if db.scalar(select(Company).where(Company.bin == MARKER_BIN)):
            logger.info("Демо-данные уже существуют — пропускаю наполнение.")
            return False

        managers = _ensure_managers(db)
        manager_1, manager_2 = managers

        companies = [
            crud.create_company(
                db,
                schemas.CompanyCreate(name=name, bin=bin_value, phone=phone),
            )
            for name, bin_value, phone in COMPANIES
        ]
        company_1, company_2, company_3, company_4, company_5, company_6 = companies

        monday = next_monday()
        tuesday = monday + timedelta(days=1)
        wednesday = monday + timedelta(days=2)

        def create_meeting(company: Company, manager: Manager, day: date, hour: int):
            return crud.create_meeting(
                db,
                schemas.MeetingCreate(
                    company_id=company.id,
                    manager_id=manager.id,
                    start_time=almaty_dt(day, hour),
                ),
            )

        meeting_1 = create_meeting(company_1, manager_1, monday, 10)
        meeting_2 = create_meeting(company_2, manager_2, monday, 11)
        meeting_3 = create_meeting(company_3, manager_1, monday, 14)
        meeting_4 = create_meeting(company_1, manager_1, tuesday, 9)
        meeting_5 = create_meeting(company_4, manager_2, tuesday, 14)
        meeting_6 = create_meeting(company_5, manager_1, wednesday, 15)

        def create_call(
            company: Company,
            result: str,
            meeting_id: int | None = None,
            comment: str | None = None,
        ):
            return crud.create_call(
                db,
                schemas.CallCreate(
                    company_id=company.id,
                    result=result,
                    meeting_id=meeting_id,
                    comment=comment,
                ),
            )

        create_call(company_1, "no_answer", comment="Не взяли трубку, перезвонить завтра")
        create_call(company_1, "meeting_set", meeting_1.id, "Клиент подтвердил встречу")
        create_call(company_2, "no_answer", comment="Автоответчик")
        create_call(company_3, "meeting_set", meeting_3.id, "Интересно demo, назначили созвон")
        create_call(company_4, "meeting_set", meeting_5.id, "Обсудить поставку зерна")
        create_call(company_5, "meeting_set", meeting_6.id, "Презентация решения")
        create_call(company_6, "refused", comment="Уже работают с другим подрядчиком")

        # Демонстрация переноса: original_start_time сохранится, появится событие.
        crud.patch_meeting(
            db,
            meeting_5.id,
            schemas.MeetingPatch(start_time=almaty_dt(tuesday, 16)),
        )

        # Демонстрация отмены: статус cancelled и событие в истории.
        crud.patch_meeting(
            db,
            meeting_6.id,
            schemas.MeetingPatch(status="cancelled"),
        )

        logger.info(
            "Созданы демо-данные: компании %s, менеджеры %s, встречи %s; "
            "расписание менеджера #1 на %s: GET /managers/1/schedule?date=%s",
            ", ".join(f"#{c.id} {c.name}" for c in companies),
            ", ".join(f"#{m.id} {m.name}" for m in managers),
            ", ".join(
                f"#{meeting.id} {meeting.status} "
                f"{meeting.start_time.astimezone(ALMATY):%d.%m %H:%M}"
                for meeting in (
                    meeting_1,
                    meeting_2,
                    meeting_3,
                    meeting_4,
                    meeting_5,
                    meeting_6,
                )
            ),
            monday,
            monday,
        )
        return True
