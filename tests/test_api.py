"""Автотесты REST API (pytest)."""
from __future__ import annotations

import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

# Отдельная БД для тестов должна быть выбрана до импорта приложения.
_TMP_DIR = tempfile.mkdtemp(prefix="crm-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR}/test.db"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.database import SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Meeting, utcnow  # noqa: E402

ALMATY = ZoneInfo("Asia/Almaty")
MANAGER_1 = 1
MANAGER_2 = 2
MONDAY = "2027-03-15"
TUESDAY = "2027-03-16"
SATURDAY = "2027-03-20"

_bin_counter = 0


@pytest.fixture()
def client():
    with engine.begin() as connection:
        for table in ("meeting_events", "calls", "meetings", "companies", "managers"):
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
    init_db()
    with TestClient(app) as test_client:
        yield test_client


def almaty_iso(day: str, hour: int, minute: int = 0) -> str:
    """Локальное время Almaty -> ISO 8601 в UTC."""
    local = datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(
        tzinfo=ALMATY
    )
    return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def as_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def create_company(client: TestClient, name: str = "ТОО Ромашка") -> int:
    global _bin_counter
    _bin_counter += 1
    response = client.post(
        "/companies",
        json={
            "name": name,
            "bin": f"{_bin_counter:012d}",
            "phone": "+7 777 123 45 67",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_meeting(
    client: TestClient,
    company_id: int,
    start_time: str,
    manager_id: int = MANAGER_1,
):
    return client.post(
        "/meetings",
        json={
            "company_id": company_id,
            "manager_id": manager_id,
            "start_time": start_time,
        },
    )


# ---------------------------------------------------------------------------
# Обязательные тесты: граница рабочего дня и пересечение встреч
# ---------------------------------------------------------------------------

def test_workday_boundary(client):
    # 09:00-10:00 и 17:00-18:00 по Almaty разрешены.
    first = create_meeting(client, create_company(client), almaty_iso(MONDAY, 9))
    assert first.status_code == 201, first.text

    second = create_meeting(client, create_company(client), almaty_iso(MONDAY, 17))
    assert second.status_code == 201, second.text
    assert as_utc(second.json()["end_time"]) == as_utc(almaty_iso(MONDAY, 18))

    # 17:30-18:30 не помещается в рабочий день.
    late = create_meeting(
        client, create_company(client), almaty_iso(MONDAY, 17, 30), MANAGER_2
    )
    assert late.status_code == 422
    assert "рабочие часы" in late.json()["error"]["details"][0]["message"]

    # Слишком раннее начало.
    early = create_meeting(client, create_company(client), almaty_iso(MONDAY, 8, 30))
    assert early.status_code == 422

    # Выходной день.
    weekend = create_meeting(client, create_company(client), almaty_iso(SATURDAY, 10))
    assert weekend.status_code == 422
    assert "рабочие дни" in weekend.json()["error"]["details"][0]["message"]

    # Naive datetime без часового пояса отклоняется.
    naive = create_meeting(client, create_company(client), "2027-03-15T10:00:00")
    assert naive.status_code == 422
    assert "часовой пояс" in naive.json()["error"]["details"][0]["message"]


def test_manager_overlap_and_adjacent_slots(client):
    manager = MANAGER_1
    first = create_meeting(client, create_company(client), almaty_iso(MONDAY, 10), manager)
    assert first.status_code == 201, first.text

    overlap = create_meeting(
        client, create_company(client), almaty_iso(MONDAY, 10, 30), manager
    )
    assert overlap.status_code == 409
    assert overlap.json()["error"]["code"] == "meeting_conflict"

    # 11:00-12:00 не пересекается с 10:00-11:00.
    touching = create_meeting(
        client, create_company(client), almaty_iso(MONDAY, 11), manager
    )
    assert touching.status_code == 201, touching.text

    # Встреча, которая начинается раньше и заканчивается внутрь чужой, тоже конфликт.
    covering = create_meeting(
        client, create_company(client), almaty_iso(MONDAY, 10, 30), MANAGER_2
    )
    assert covering.status_code == 201
    ends_inside = create_meeting(
        client, create_company(client), almaty_iso(MONDAY, 10), MANAGER_2
    )
    assert ends_inside.status_code == 409


def test_company_cannot_have_two_active_meetings(client):
    company_id = create_company(client)

    first = create_meeting(client, company_id, almaty_iso(MONDAY, 10), MANAGER_1)
    assert first.status_code == 201, first.text

    # Другой менеджер, но та же организация и пересекающееся время.
    conflict = create_meeting(client, company_id, almaty_iso(MONDAY, 10, 30), MANAGER_2)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "company_conflict"

    # Другая организация с тем же менеджером и временем — тоже конфликт.
    other = create_meeting(client, create_company(client), almaty_iso(MONDAY, 10), MANAGER_1)
    assert other.status_code == 409

    # Другая организация, другой менеджер, то же время — можно.
    allowed = create_meeting(
        client, create_company(client), almaty_iso(MONDAY, 10), MANAGER_2
    )
    assert allowed.status_code == 201, allowed.text


# ---------------------------------------------------------------------------
# Звонки
# ---------------------------------------------------------------------------

def test_call_meeting_set_requires_existing_meeting(client):
    company_id = create_company(client)

    without_meeting = client.post(
        "/calls", json={"company_id": company_id, "result": "meeting_set"}
    )
    assert without_meeting.status_code == 422
    assert without_meeting.json()["error"]["code"] == "validation_error"

    unknown_meeting = client.post(
        "/calls",
        json={"company_id": company_id, "result": "meeting_set", "meeting_id": 9999},
    )
    assert unknown_meeting.status_code == 422
    assert unknown_meeting.json()["error"]["code"] == "meeting_set_requires_meeting"

    meeting = create_meeting(client, company_id, almaty_iso(MONDAY, 10))
    assert meeting.status_code == 201
    meeting_id = meeting.json()["id"]

    ok = client.post(
        "/calls",
        json={
            "company_id": company_id,
            "result": "meeting_set",
            "meeting_id": meeting_id,
            "comment": "Клиент подтвердил встречу",
        },
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["meeting_id"] == meeting_id

    company = client.get("/companies", params={"status": "meeting_set"}).json()
    assert company["total"] == 1
    assert company["items"][0]["id"] == company_id

    # meeting_id запрещён для других результатов.
    invalid = client.post(
        "/calls",
        json={
            "company_id": company_id,
            "result": "refused",
            "meeting_id": meeting_id,
        },
    )
    assert invalid.status_code == 422

    other_company = create_company(client)
    refused = client.post(
        "/calls", json={"company_id": other_company, "result": "refused"}
    )
    assert refused.status_code == 201
    assert refused.json()["result"] == "refused"

    refused_list = client.get("/companies", params={"status": "refused"}).json()
    assert [item["id"] for item in refused_list["items"]] == [other_company]

    no_answer = client.post(
        "/calls", json={"company_id": other_company, "result": "no_answer"}
    )
    assert no_answer.status_code == 201
    assert no_answer.json()["result"] == "no_answer"


# ---------------------------------------------------------------------------
# Перенос, отмена, история
# ---------------------------------------------------------------------------

def test_reschedule_keeps_original_time_and_history(client):
    company_id = create_company(client)
    created = create_meeting(client, company_id, almaty_iso(MONDAY, 10))
    assert created.status_code == 201, created.text
    meeting_id = created.json()["id"]
    assert as_utc(created.json()["original_start_time"]) == as_utc(
        almaty_iso(MONDAY, 10)
    )

    moved = client.patch(
        f"/meetings/{meeting_id}", json={"start_time": almaty_iso(MONDAY, 14)}
    )
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert as_utc(body["start_time"]) == as_utc(almaty_iso(MONDAY, 14))
    assert as_utc(body["end_time"]) == as_utc(almaty_iso(MONDAY, 15))
    # Изначальное время назначения не потеряно.
    assert as_utc(body["original_start_time"]) == as_utc(almaty_iso(MONDAY, 10))
    assert [event["action"] for event in body["events"]] == ["created", "rescheduled"]
    assert as_utc(body["events"][1]["old_start_time"]) == as_utc(
        almaty_iso(MONDAY, 10)
    )
    assert as_utc(body["events"][1]["new_start_time"]) == as_utc(
        almaty_iso(MONDAY, 14)
    )

    # После переноса старый слот свободен.
    back = client.patch(
        f"/meetings/{meeting_id}", json={"start_time": almaty_iso(MONDAY, 10)}
    )
    assert back.status_code == 200, back.text

    # Отмена освобождает слот.
    cancelled = client.patch(f"/meetings/{meeting_id}", json={"status": "cancelled"})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancelled_at"] is not None

    again = client.patch(f"/meetings/{meeting_id}", json={"status": "cancelled"})
    assert again.status_code == 409

    moved_cancelled = client.patch(
        f"/meetings/{meeting_id}", json={"start_time": almaty_iso(MONDAY, 15)}
    )
    assert moved_cancelled.status_code == 409

    replacement = create_meeting(client, company_id, almaty_iso(MONDAY, 10))
    assert replacement.status_code == 201, replacement.text

    # История доступна и после всех изменений.
    history = client.get(f"/meetings/{meeting_id}").json()
    assert [event["action"] for event in history["events"]] == [
        "created",
        "rescheduled",
        "rescheduled",
        "cancelled",
    ]
    assert as_utc(history["original_start_time"]) == as_utc(almaty_iso(MONDAY, 10))


def test_reschedule_conflict_returns_409(client):
    first_company = create_company(client)
    second_company = create_company(client)
    first = create_meeting(client, first_company, almaty_iso(MONDAY, 10))
    second = create_meeting(client, second_company, almaty_iso(MONDAY, 12))
    assert first.status_code == 201 and second.status_code == 201

    response = client.patch(
        f"/meetings/{second.json()['id']}",
        json={"start_time": almaty_iso(MONDAY, 10, 30)},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "meeting_conflict"

    # Встреча не изменилась после отказа.
    unchanged = client.get(f"/meetings/{second.json()['id']}").json()
    assert as_utc(unchanged["start_time"]) == as_utc(almaty_iso(MONDAY, 12))


# ---------------------------------------------------------------------------
# Компании, расписание, защита на уровне БД
# ---------------------------------------------------------------------------

def test_companies_filter_and_pagination(client):
    ids = [create_company(client, f"Компания {index}") for index in range(3)]
    client.post("/calls", json={"company_id": ids[0], "result": "refused"})
    client.post("/calls", json={"company_id": ids[1], "result": "no_answer"})

    page = client.get("/companies", params={"limit": 2, "offset": 0})
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert len(page.json()["items"]) == 2
    assert page.json()["limit"] == 2

    second_page = client.get("/companies", params={"limit": 2, "offset": 2}).json()
    assert len(second_page["items"]) == 1

    refused = client.get("/companies", params={"status": "refused"}).json()
    assert refused["total"] == 1
    assert refused["items"][0]["id"] == ids[0]

    invalid = client.get("/companies", params={"status": "unknown"})
    assert invalid.status_code == 422


def test_manager_schedule(client):
    company_a = create_company(client, "A")
    company_b = create_company(client, "B")
    company_c = create_company(client, "C")

    assert create_meeting(client, company_a, almaty_iso(MONDAY, 15)).status_code == 201
    assert create_meeting(client, company_b, almaty_iso(MONDAY, 10)).status_code == 201
    assert create_meeting(client, company_c, almaty_iso(TUESDAY, 10)).status_code == 201

    cancelled = create_meeting(
        client, create_company(client, "D"), almaty_iso(MONDAY, 12), MANAGER_2
    )
    assert cancelled.status_code == 201
    client.patch(f"/meetings/{cancelled.json()['id']}", json={"status": "cancelled"})

    schedule = client.get(
        f"/managers/{MANAGER_1}/schedule", params={"date": MONDAY}
    )
    assert schedule.status_code == 200, schedule.text
    body = schedule.json()
    assert body["timezone"] == "Asia/Almaty"
    assert [as_utc(item["start_time"]) for item in body["meetings"]] == [
        as_utc(almaty_iso(MONDAY, 10)),
        as_utc(almaty_iso(MONDAY, 15)),
    ]
    assert body["meetings"][0]["company_name"] == "B"

    # Отменённые встречи в расписание не попадают.
    second_manager = client.get(
        f"/managers/{MANAGER_2}/schedule", params={"date": MONDAY}
    ).json()
    assert second_manager["meetings"] == []

    assert (
        client.get("/managers/9999/schedule", params={"date": MONDAY}).status_code
        == 404
    )
    assert (
        client.get(f"/managers/{MANAGER_1}/schedule", params={"date": "15.03.2027"})
        .status_code
        == 422
    )


def test_database_trigger_blocks_direct_overlap(client):
    """Даже вставка в обход API не создаёт двойную бронь."""
    company_id = create_company(client)
    start = as_utc(almaty_iso(MONDAY, 10))
    end = as_utc(almaty_iso(MONDAY, 11))
    now = utcnow()

    with SessionLocal() as db:
        db.add(
            Meeting(
                company_id=company_id,
                manager_id=MANAGER_1,
                start_time=start,
                end_time=end,
                original_start_time=start,
                status="scheduled",
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()

    with SessionLocal() as db:
        db.add(
            Meeting(
                company_id=create_company(client),
                manager_id=MANAGER_1,
                start_time=as_utc(almaty_iso(MONDAY, 10, 30)),
                end_time=as_utc(almaty_iso(MONDAY, 11, 30)),
                original_start_time=as_utc(almaty_iso(MONDAY, 10, 30)),
                status="scheduled",
                created_at=now,
                updated_at=now,
            )
        )
        with pytest.raises(IntegrityError) as error:
            db.commit()
        assert "manager_conflict" in str(error.value.orig)


def test_concurrent_booking_same_slot_only_one_wins(client):
    """Параллельные запросы на один слот: ровно один 201, остальные 409."""
    company_ids = [create_company(client, f"Конкурент {i}") for i in range(6)]
    barrier = threading.Barrier(len(company_ids))
    slot = almaty_iso(MONDAY, 10)

    def book(company_id: int):
        barrier.wait(timeout=10)
        return create_meeting(client, company_id, slot)

    with ThreadPoolExecutor(max_workers=len(company_ids)) as executor:
        responses = list(executor.map(book, company_ids))

    codes = sorted(response.status_code for response in responses)
    assert codes == [201] + [409] * (len(company_ids) - 1), [
        (response.status_code, response.text) for response in responses
    ]
