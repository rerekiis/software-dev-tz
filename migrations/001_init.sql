-- Схема внутренней CRM: компании, менеджеры, звонки, встречи.
-- Все моменты времени хранятся в UTC (naive, формат SQLite DATETIME).
-- Выполняется идемпотентно при старте приложения (app/database.py).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS companies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       VARCHAR(255) NOT NULL,
    bin        VARCHAR(12)  NOT NULL,
    phone      VARCHAR(32)  NOT NULL,
    status     VARCHAR(20)  NOT NULL DEFAULT 'new'
               CHECK (status IN ('new', 'no_answer', 'refused', 'meeting_set')),
    created_at DATETIME     NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_companies_bin ON companies (bin);
CREATE INDEX IF NOT EXISTS ix_companies_status ON companies (status);

CREATE TABLE IF NOT EXISTS managers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       VARCHAR(255) NOT NULL,
    created_at DATETIME     NOT NULL
);

CREATE TABLE IF NOT EXISTS meetings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies (id) ON DELETE RESTRICT,
    manager_id          INTEGER NOT NULL REFERENCES managers (id) ON DELETE RESTRICT,
    start_time          DATETIME NOT NULL,
    end_time            DATETIME NOT NULL,
    original_start_time DATETIME NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'scheduled'
                        CHECK (status IN ('scheduled', 'cancelled')),
    created_at          DATETIME NOT NULL,
    updated_at          DATETIME NOT NULL,
    cancelled_at        DATETIME,
    CHECK (end_time > start_time)
);

CREATE INDEX IF NOT EXISTS ix_meetings_manager_start ON meetings (manager_id, start_time);
CREATE INDEX IF NOT EXISTS ix_meetings_company_start ON meetings (company_id, start_time);
CREATE INDEX IF NOT EXISTS ix_meetings_status ON meetings (status);

CREATE TABLE IF NOT EXISTS calls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies (id) ON DELETE RESTRICT,
    result     VARCHAR(20) NOT NULL
               CHECK (result IN ('no_answer', 'refused', 'meeting_set')),
    meeting_id INTEGER REFERENCES meetings (id) ON DELETE SET NULL,
    comment    TEXT,
    created_at DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_calls_company_id ON calls (company_id);
CREATE INDEX IF NOT EXISTS ix_calls_meeting_id ON calls (meeting_id);

CREATE TABLE IF NOT EXISTS meeting_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id     INTEGER NOT NULL REFERENCES meetings (id) ON DELETE CASCADE,
    action         VARCHAR(20) NOT NULL
                   CHECK (action IN ('created', 'rescheduled', 'cancelled')),
    old_start_time DATETIME,
    new_start_time DATETIME,
    created_at     DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_meeting_events_meeting_id ON meeting_events (meeting_id);

-- ---------------------------------------------------------------------------
-- Страховка на уровне БД: пересечения нельзя создать даже в обход приложения.
-- SQLite сериализует записи, поэтому проверка внутри триггера атомарна.
-- ---------------------------------------------------------------------------

CREATE TRIGGER IF NOT EXISTS trg_meetings_manager_overlap_insert
BEFORE INSERT ON meetings
FOR EACH ROW
WHEN NEW.status = 'scheduled'
BEGIN
    SELECT RAISE(ABORT, 'manager_conflict')
    WHERE EXISTS (
        SELECT 1 FROM meetings m
        WHERE m.manager_id = NEW.manager_id
          AND m.status = 'scheduled'
          AND m.start_time < NEW.end_time
          AND m.end_time > NEW.start_time
    );
    SELECT RAISE(ABORT, 'company_conflict')
    WHERE EXISTS (
        SELECT 1 FROM meetings m
        WHERE m.company_id = NEW.company_id
          AND m.status = 'scheduled'
          AND m.start_time < NEW.end_time
          AND m.end_time > NEW.start_time
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_meetings_overlap_update
BEFORE UPDATE OF start_time, end_time, status, manager_id, company_id ON meetings
FOR EACH ROW
WHEN NEW.status = 'scheduled'
BEGIN
    SELECT RAISE(ABORT, 'manager_conflict')
    WHERE EXISTS (
        SELECT 1 FROM meetings m
        WHERE m.id <> NEW.id
          AND m.manager_id = NEW.manager_id
          AND m.status = 'scheduled'
          AND m.start_time < NEW.end_time
          AND m.end_time > NEW.start_time
    );
    SELECT RAISE(ABORT, 'company_conflict')
    WHERE EXISTS (
        SELECT 1 FROM meetings m
        WHERE m.id <> NEW.id
          AND m.company_id = NEW.company_id
          AND m.status = 'scheduled'
          AND m.start_time < NEW.end_time
          AND m.end_time > NEW.start_time
    );
END;

-- Звонок с результатом meeting_set обязан ссылаться на активную встречу этой компании.
CREATE TRIGGER IF NOT EXISTS trg_calls_meeting_set_requires_meeting
BEFORE INSERT ON calls
FOR EACH ROW
WHEN NEW.result = 'meeting_set'
BEGIN
    SELECT RAISE(ABORT, 'meeting_set_requires_meeting')
    WHERE NEW.meeting_id IS NULL
       OR NOT EXISTS (
            SELECT 1 FROM meetings m
            WHERE m.id = NEW.meeting_id
              AND m.company_id = NEW.company_id
              AND m.status = 'scheduled'
       );
END;

-- Демо-менеджеры, чтобы API можно было использовать сразу после запуска.
INSERT OR IGNORE INTO managers (id, name, created_at)
VALUES (1, 'Айгерим Смагулова', '2026-01-01 00:00:00.000000');

INSERT OR IGNORE INTO managers (id, name, created_at)
VALUES (2, 'Данияр Ахметов', '2026-01-01 00:00:00.000000');
