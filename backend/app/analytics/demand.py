from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Flight, FlightScheduleSnapshot, ScheduleCacheEntry
from app.timeutils import as_utc

WINDOW_MINUTES = 30
WINDOW_COUNT = 6
SERIES_BUCKET_MINUTES = 15
SERIES_OFFSET_MINUTES = 30
SERIES_WINDOW_MINUTES = 120


def normalize_terminal(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("terminal", "").strip()
    return normalized.removeprefix("t").strip()


def latest_schedule_rows(
    session: Session,
    *,
    airport: str,
    start: datetime,
    end: datetime,
) -> list[tuple[Flight, FlightScheduleSnapshot]]:
    latest = (
        select(
            FlightScheduleSnapshot.flight_id,
            func.max(FlightScheduleSnapshot.collected_at).label("latest_collected"),
        )
        .join(Flight, Flight.id == FlightScheduleSnapshot.flight_id)
        .where(
            Flight.airport == airport.upper(),
            FlightScheduleSnapshot.scheduled_out >= as_utc(start),
            FlightScheduleSnapshot.scheduled_out < as_utc(end),
        )
        .group_by(FlightScheduleSnapshot.flight_id)
        .subquery()
    )
    return list(
        session.execute(
            select(Flight, FlightScheduleSnapshot)
            .join(FlightScheduleSnapshot, FlightScheduleSnapshot.flight_id == Flight.id)
            .join(
                latest,
                (latest.c.flight_id == FlightScheduleSnapshot.flight_id)
                & (latest.c.latest_collected == FlightScheduleSnapshot.collected_at),
            )
            .order_by(FlightScheduleSnapshot.scheduled_out)
        ).all()
    )


def demand_windows(
    session: Session,
    *,
    airport: str,
    terminal: str | None,
    reference_time: datetime,
) -> list[dict[str, Any]]:
    reference_time = as_utc(reference_time)
    end = reference_time + timedelta(minutes=WINDOW_MINUTES * WINDOW_COUNT)
    rows = latest_schedule_rows(session, airport=airport, start=reference_time, end=end)
    terminal_key = normalize_terminal(terminal)
    output: list[dict[str, Any]] = []
    for index in range(WINDOW_COUNT):
        window_start = reference_time + timedelta(minutes=index * WINDOW_MINUTES)
        window_end = window_start + timedelta(minutes=WINDOW_MINUTES)
        selected = [
            snapshot
            for _flight, snapshot in rows
            if window_start <= as_utc(snapshot.scheduled_out) < window_end
            and (not terminal_key or normalize_terminal(snapshot.terminal) == terminal_key)
        ]
        known_capacity = [row.total_seats for row in selected if row.total_seats is not None]
        output.append(
            {
                "offset_start_minutes": index * WINDOW_MINUTES,
                "offset_end_minutes": (index + 1) * WINDOW_MINUTES,
                "flights": len(selected),
                "scheduled_seats": sum(known_capacity),
                "flights_with_capacity": len(known_capacity),
                "capacity_coverage": round(len(known_capacity) / len(selected), 3)
                if selected
                else None,
            }
        )
    return output


def demand_time_series(
    session: Session,
    *,
    airport: str,
    terminal: str | None,
    start: datetime,
    end: datetime,
    bucket_minutes: int = SERIES_BUCKET_MINUTES,
    offset_minutes: int = SERIES_OFFSET_MINUTES,
    window_minutes: int = SERIES_WINDOW_MINUTES,
) -> list[dict[str, Any]]:
    """Count flights in a forward-looking window at regular historical timestamps."""
    start = as_utc(start)
    end = as_utc(end)
    if end <= start or bucket_minutes <= 0 or offset_minutes < 0 or window_minutes <= 0:
        return []

    covered_dates = set(
        session.scalars(
            select(ScheduleCacheEntry.schedule_date).where(
                ScheduleCacheEntry.airport == airport.upper(),
                ScheduleCacheEntry.status == "complete",
            )
        )
    )
    if not covered_dates:
        return []

    rows = latest_schedule_rows(
        session,
        airport=airport,
        start=start + timedelta(minutes=offset_minutes),
        end=end + timedelta(minutes=offset_minutes + window_minutes),
    )
    terminal_key = normalize_terminal(terminal)
    departures = [
        as_utc(snapshot.scheduled_out)
        for _flight, snapshot in rows
        if not terminal_key or normalize_terminal(snapshot.terminal) == terminal_key
    ]
    zone = ZoneInfo(get_settings().airport_timezone)
    bucket_seconds = bucket_minutes * 60
    timestamp = datetime.fromtimestamp(
        ((int(start.timestamp()) + bucket_seconds - 1) // bucket_seconds) * bucket_seconds,
        tz=start.tzinfo,
    )
    output: list[dict[str, Any]] = []
    while timestamp < end:
        window_start = timestamp + timedelta(minutes=offset_minutes)
        window_end = window_start + timedelta(minutes=window_minutes)
        if _local_dates_in_window(window_start, window_end, zone).issubset(covered_dates):
            output.append(
                {
                    "timestamp": timestamp,
                    "window_start": window_start,
                    "window_end": window_end,
                    "flights": sum(
                        window_start <= departure < window_end for departure in departures
                    ),
                }
            )
        timestamp += timedelta(minutes=bucket_minutes)
    return output


def _local_dates_in_window(
    start: datetime, end: datetime, zone: ZoneInfo
) -> set[date]:
    first = start.astimezone(zone).date()
    last = (end - timedelta(microseconds=1)).astimezone(zone).date()
    dates: set[date] = set()
    current = first
    while current <= last:
        dates.add(current)
        current += timedelta(days=1)
    return dates
