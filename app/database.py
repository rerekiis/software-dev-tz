"""Настройка подключения к SQLite и инициализация схемы из SQL-миграции."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent
MIGRATION_PATH = BASE_DIR / "migrations" / "001_init.sql"

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'crm.db'}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 10},
    future=True,
)


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA busy_timeout = 10000")
    cursor.close()


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


def init_db() -> None:
    """Создаёт схему (идемпотентно) и добавляет менеджеров-сидов."""
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    raw_connection = engine.raw_connection()
    try:
        raw_connection.executescript(sql)
        raw_connection.commit()
    finally:
        raw_connection.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
