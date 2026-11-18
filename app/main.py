"""Точка входа FastAPI: REST API учёта звонков и назначения встреч."""
from __future__ import annotations

from datetime import date as date_type

from fastapi import Depends, FastAPI, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from . import crud, schemas
from .database import get_db


app = FastAPI(
    title="CRM: звонки и встречи",
    version="1.0.0",
    description=(
        "REST API для учёта звонков и назначения встреч. "
        "Входные и выходные моменты времени — UTC (ISO 8601), "
        "рабочие часы проверяются по Asia/Almaty (пн–пт, 09:00–18:00)."
    ),
)


@app.exception_handler(crud.ApiError)
async def api_error_handler(_request: Request, exc: crud.ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        message = str(error.get("msg", "Некорректное значение"))
        if message.startswith("Value error, "):
            message = message[len("Value error, ") :]
        details.append({"field": location, "message": message})
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "validation_error",
                "message": "Некорректные данные запроса",
                "details": details,
            }
        },
    )


@app.get("/health", tags=["service"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Компании
# ---------------------------------------------------------------------------

@app.post(
    "/companies",
    response_model=schemas.CompanyOut,
    status_code=status.HTTP_201_CREATED,
    tags=["companies"],
)
def create_company(payload: schemas.CompanyCreate, db: Session = Depends(get_db)):
    return crud.create_company(db, payload)


@app.get("/companies", response_model=schemas.CompanyList, tags=["companies"])
def list_companies(
    status_filter: schemas.CompanyStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = crud.list_companies(
        db, status=status_filter, limit=limit, offset=offset
    )
    return schemas.CompanyList(items=items, total=total, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Менеджеры и расписание
# ---------------------------------------------------------------------------

@app.post(
    "/managers",
    response_model=schemas.ManagerOut,
    status_code=status.HTTP_201_CREATED,
    tags=["managers"],
)
def create_manager(payload: schemas.ManagerCreate, db: Session = Depends(get_db)):
    return crud.create_manager(db, payload.name)


@app.get("/managers", response_model=list[schemas.ManagerOut], tags=["managers"])
def list_managers(db: Session = Depends(get_db)):
    return crud.list_managers(db)


@app.get(
    "/managers/{manager_id}/schedule",
    response_model=schemas.ManagerSchedule,
    tags=["managers"],
)
def manager_schedule(
    manager_id: int,
    day: date_type = Query(alias="date", description="Дата в Almaty, YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    return crud.get_manager_schedule(db, manager_id, day)


# ---------------------------------------------------------------------------
# Звонки
# ---------------------------------------------------------------------------

@app.post(
    "/calls",
    response_model=schemas.CallOut,
    status_code=status.HTTP_201_CREATED,
    tags=["calls"],
)
def create_call(payload: schemas.CallCreate, db: Session = Depends(get_db)):
    return crud.create_call(db, payload)


# ---------------------------------------------------------------------------
# Встречи
# ---------------------------------------------------------------------------

@app.post(
    "/meetings",
    response_model=schemas.MeetingDetail,
    status_code=status.HTTP_201_CREATED,
    tags=["meetings"],
)
def create_meeting(payload: schemas.MeetingCreate, db: Session = Depends(get_db)):
    return crud.create_meeting(db, payload)


@app.get(
    "/meetings/{meeting_id}",
    response_model=schemas.MeetingDetail,
    tags=["meetings"],
)
def get_meeting(meeting_id: int, db: Session = Depends(get_db)):
    return crud.get_meeting(db, meeting_id)


@app.patch(
    "/meetings/{meeting_id}",
    response_model=schemas.MeetingDetail,
    tags=["meetings"],
)
def patch_meeting(
    meeting_id: int,
    payload: schemas.MeetingPatch,
    db: Session = Depends(get_db),
):
    return crud.patch_meeting(db, meeting_id, payload)
