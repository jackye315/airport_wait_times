from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutils import utcnow


class RawSourceResponse(Base):
    __tablename__ = "raw_source_responses"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    request_key: Mapped[str] = mapped_column(String(512))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    http_status: Mapped[int | None] = mapped_column(Integer)
    payload_sha256: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class WaitTimeObservation(Base):
    __tablename__ = "wait_time_observations"
    __table_args__ = (
        UniqueConstraint(
            "airport",
            "terminal",
            "checkpoint",
            "queue_type",
            "observed_at",
            name="uq_wait_observation_identity",
        ),
        Index("idx_wait_airport_terminal_observed", "airport", "terminal", "observed_at"),
        Index("idx_wait_airport_queue_observed", "airport", "queue_type", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    airport: Mapped[str] = mapped_column(String(4))
    terminal: Mapped[str] = mapped_column(String(32), default="")
    checkpoint: Mapped[str] = mapped_column(String(128), default="")
    gate: Mapped[str | None] = mapped_column(String(64))
    queue_type: Mapped[str] = mapped_column(String(16))
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    is_wait_time_available: Mapped[bool] = mapped_column(Boolean, default=True)
    wait_minutes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(64))
    source_last_updated: Mapped[str | None] = mapped_column(String(128))
    raw_response_id: Mapped[int | None] = mapped_column(ForeignKey("raw_source_responses.id"))


class Flight(Base):
    __tablename__ = "flights"
    __table_args__ = (
        UniqueConstraint("canonical_key", name="uq_flight_canonical_key"),
        Index("idx_flight_airport_date", "airport", "flight_date"),
        Index("idx_flight_operator_number", "operator", "flight_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(255))
    fa_flight_id: Mapped[str | None] = mapped_column(String(255), index=True)
    airport: Mapped[str] = mapped_column(String(4))
    flight_date: Mapped[date] = mapped_column(Date)
    ident: Mapped[str] = mapped_column(String(32))
    actual_ident: Mapped[str | None] = mapped_column(String(32))
    operator: Mapped[str | None] = mapped_column(String(8))
    flight_number: Mapped[str | None] = mapped_column(String(16))
    destination: Mapped[str | None] = mapped_column(String(8))
    flight_type: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    snapshots: Mapped[list[FlightScheduleSnapshot]] = relationship(
        back_populates="flight", cascade="all, delete-orphan"
    )


class FlightScheduleSnapshot(Base):
    __tablename__ = "flight_schedule_snapshots"
    __table_args__ = (
        UniqueConstraint("flight_id", "fingerprint", name="uq_flight_snapshot_state"),
        Index("idx_snapshot_scheduled_out", "scheduled_out"),
        Index("idx_snapshot_terminal_out", "terminal", "scheduled_out"),
        Index("idx_snapshot_flight_collected", "flight_id", "collected_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    flight_id: Mapped[int] = mapped_column(ForeignKey("flights.id", ondelete="CASCADE"))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fingerprint: Mapped[str] = mapped_column(String(64))
    scheduled_out: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scheduled_off: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal: Mapped[str | None] = mapped_column(String(32))
    gate: Mapped[str | None] = mapped_column(String(32))
    aircraft_type: Mapped[str | None] = mapped_column(String(16))
    coach_seats: Mapped[int | None] = mapped_column(Integer)
    business_seats: Mapped[int | None] = mapped_column(Integer)
    first_seats: Mapped[int | None] = mapped_column(Integer)
    total_seats: Mapped[int | None] = mapped_column(Integer)
    capacity_source: Mapped[str] = mapped_column(String(32), default="unknown")
    raw_response_id: Mapped[int | None] = mapped_column(ForeignKey("raw_source_responses.id"))

    flight: Mapped[Flight] = relationship(back_populates="snapshots")


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    __table_args__ = (Index("idx_collection_source_started", "source", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(32))
    airport: Mapped[str | None] = mapped_column(String(4))
    requested_date: Mapped[date | None] = mapped_column(Date)
    records_received: Mapped[int] = mapped_column(Integer, default=0)
    records_stored: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="running")
    error: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ApiUsageLedger(Base):
    __tablename__ = "api_usage_ledger"
    __table_args__ = (Index("idx_usage_category_occurred", "category", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[str] = mapped_column(String(32), default="flightaware")
    category: Mapped[str] = mapped_column(String(16))
    request_path: Mapped[str] = mapped_column(String(512))
    response_status: Mapped[int | None] = mapped_column(Integer)
    result_sets: Mapped[int] = mapped_column(Integer, default=1)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=Decimal("0"))


class ScheduleCacheEntry(Base):
    __tablename__ = "schedule_cache_entries"
    __table_args__ = (
        UniqueConstraint("airport", "schedule_date", "source_kind", name="uq_schedule_cache_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    airport: Mapped[str] = mapped_column(String(4))
    schedule_date: Mapped[date] = mapped_column(Date)
    source_kind: Mapped[str] = mapped_column(String(32))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="complete")


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_key: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    training_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    training_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    model_family: Mapped[str] = mapped_column(String(64))
    feature_set: Mapped[str] = mapped_column(String(32))
    feature_schema: Mapped[list[str]] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    artifact_path: Mapped[str] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
