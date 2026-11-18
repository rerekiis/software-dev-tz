"""initial schema

Revision ID: c40cb9c90061
Revises:
Create Date: 2026-11-19 01:33:57.087208

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.models import UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "c40cb9c90061"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MANAGER_OVERLAP_INSERT = """
CREATE TRIGGER trg_meetings_manager_overlap_insert
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
END
"""

OVERLAP_UPDATE = """
CREATE TRIGGER trg_meetings_overlap_update
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
END
"""

MEETING_SET_REQUIRES_MEETING = """
CREATE TRIGGER trg_calls_meeting_set_requires_meeting
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
END
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("bin", sa.String(length=12), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('new', 'no_answer', 'refused', 'meeting_set')",
            name="ck_companies_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companies_bin"), "companies", ["bin"], unique=False)
    op.create_index("ix_companies_status", "companies", ["status"], unique=False)

    op.create_table(
        "managers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "meetings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("manager_id", sa.Integer(), nullable=False),
        sa.Column("start_time", UTCDateTime(), nullable=False),
        sa.Column("end_time", UTCDateTime(), nullable=False),
        sa.Column("original_start_time", UTCDateTime(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("cancelled_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('scheduled', 'cancelled')", name="ck_meetings_status"
        ),
        sa.CheckConstraint("end_time > start_time", name="ck_meetings_time_range"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["manager_id"], ["managers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_meetings_company_id"), "meetings", ["company_id"], unique=False
    )
    op.create_index(
        "ix_meetings_company_start",
        "meetings",
        ["company_id", "start_time"],
        unique=False,
    )
    op.create_index(
        op.f("ix_meetings_manager_id"), "meetings", ["manager_id"], unique=False
    )
    op.create_index(
        "ix_meetings_manager_start",
        "meetings",
        ["manager_id", "start_time"],
        unique=False,
    )
    op.create_index(
        op.f("ix_meetings_start_time"), "meetings", ["start_time"], unique=False
    )
    op.create_index("ix_meetings_status", "meetings", ["status"], unique=False)

    op.create_table(
        "calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("meeting_id", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "result IN ('no_answer', 'refused', 'meeting_set')",
            name="ck_calls_result",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_calls_company_id"), "calls", ["company_id"], unique=False)
    op.create_index(op.f("ix_calls_meeting_id"), "calls", ["meeting_id"], unique=False)

    op.create_table(
        "meeting_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("meeting_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("old_start_time", UTCDateTime(), nullable=True),
        sa.Column("new_start_time", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "action IN ('created', 'rescheduled', 'cancelled')",
            name="ck_meeting_events_action",
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_meeting_events_meeting_id"),
        "meeting_events",
        ["meeting_id"],
        unique=False,
    )

    # Защита от двойных броней на уровне БД.
    op.execute(MANAGER_OVERLAP_INSERT)
    op.execute(OVERLAP_UPDATE)
    op.execute(MEETING_SET_REQUIRES_MEETING)

    # Менеджеры, доступные сразу после миграции.
    op.execute(
        "INSERT INTO managers (id, name, created_at) VALUES "
        "(1, 'Айгерим Смагулова', '2026-01-01 00:00:00.000000'), "
        "(2, 'Данияр Ахметов', '2026-01-01 00:00:00.000000')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS trg_calls_meeting_set_requires_meeting")
    op.execute("DROP TRIGGER IF EXISTS trg_meetings_overlap_update")
    op.execute("DROP TRIGGER IF EXISTS trg_meetings_manager_overlap_insert")

    op.drop_index(op.f("ix_meeting_events_meeting_id"), table_name="meeting_events")
    op.drop_table("meeting_events")
    op.drop_index(op.f("ix_calls_meeting_id"), table_name="calls")
    op.drop_index(op.f("ix_calls_company_id"), table_name="calls")
    op.drop_table("calls")
    op.drop_index("ix_meetings_status", table_name="meetings")
    op.drop_index(op.f("ix_meetings_start_time"), table_name="meetings")
    op.drop_index("ix_meetings_manager_start", table_name="meetings")
    op.drop_index(op.f("ix_meetings_manager_id"), table_name="meetings")
    op.drop_index("ix_meetings_company_start", table_name="meetings")
    op.drop_index(op.f("ix_meetings_company_id"), table_name="meetings")
    op.drop_table("meetings")
    op.drop_table("managers")
    op.drop_index("ix_companies_status", table_name="companies")
    op.drop_index(op.f("ix_companies_bin"), table_name="companies")
    op.drop_table("companies")
