"""SQLAlchemy-модели: компании, менеджеры, звонки, встречи и история встреч."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class UTCDateTime(TypeDecorator):
    """Хранит время в SQLite как naive UTC, наружу отдаёт aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new', 'no_answer', 'refused', 'meeting_set')",
            name="ck_companies_status",
        ),
        Index("ix_companies_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    bin: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    meetings: Mapped[list["Meeting"]] = relationship(back_populates="company")
    calls: Mapped[list["Call"]] = relationship(back_populates="company")


class Manager(Base):
    __tablename__ = "managers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    meetings: Mapped[list["Meeting"]] = relationship(back_populates="manager")


class Call(Base):
    __tablename__ = "calls"
    __table_args__ = (
        CheckConstraint(
            "result IN ('no_answer', 'refused', 'meeting_set')",
            name="ck_calls_result",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    meeting_id: Mapped[int | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL"), nullable=True, index=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    company: Mapped[Company] = relationship(back_populates="calls")
    meeting: Mapped["Meeting | None"] = relationship(back_populates="calls")


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled', 'cancelled')", name="ck_meetings_status"
        ),
        CheckConstraint("end_time > start_time", name="ck_meetings_time_range"),
        Index("ix_meetings_manager_start", "manager_id", "start_time"),
        Index("ix_meetings_company_start", "company_id", "start_time"),
        Index("ix_meetings_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    manager_id: Mapped[int] = mapped_column(
        ForeignKey("managers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    start_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    end_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # Изначальное время назначения не меняется при переносах.
    original_start_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="scheduled"
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    company: Mapped[Company] = relationship(back_populates="meetings")
    manager: Mapped[Manager] = relationship(back_populates="meetings")
    events: Mapped[list["MeetingEvent"]] = relationship(
        back_populates="meeting",
        cascade="all, delete-orphan",
        order_by="MeetingEvent.id",
    )
    calls: Mapped[list[Call]] = relationship(back_populates="meeting")


class MeetingEvent(Base):
    """Аудит изменений встречи: создание, перенос, отмена."""

    __tablename__ = "meeting_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('created', 'rescheduled', 'cancelled')",
            name="ck_meeting_events_action",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    old_start_time: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    new_start_time: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    meeting: Mapped[Meeting] = relationship(back_populates="events")
