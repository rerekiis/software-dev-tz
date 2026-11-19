"""Наполняет локальную БД демонстрационными данными.

Скрипт применяет миграции и вызывает общий модуль `app.seed`, который также
используется при старте приложения. Данные создаются через те же CRUD-функции,
что и API, поэтому соблюдаются все бизнес-правила (рабочие часы, пересечения,
история встреч).

Запуск:
    .venv/bin/python scripts/seed_demo.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.database import run_migrations  # noqa: E402
from app.seed import next_monday, seed_demo_data  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    run_migrations()
    if seed_demo_data():
        monday = next_monday()
        print(f"Расписание менеджера #1 на {monday}: GET /managers/1/schedule?date={monday}")
    else:
        print("Тестовые данные уже существуют — повторный запуск пропущен.")
        print("Удалите crm.db (или выполните alembic downgrade base), чтобы начать заново.")


if __name__ == "__main__":
    main()
