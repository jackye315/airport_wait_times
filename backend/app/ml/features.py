from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import holidays
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.demand import WINDOW_COUNT, WINDOW_MINUTES, demand_windows, normalize_terminal
from app.config import get_settings
from app.models import Flight, FlightScheduleSnapshot, WaitTimeObservation
from app.timeutils import as_utc

BASELINE_FEATURES = ["airport", "terminal", "checkpoint", "hour", "weekday", "month", "holiday"]
COUNT_FEATURES = [f"flights_{index * 30}_{(index + 1) * 30}" for index in range(WINDOW_COUNT)]
SEAT_FEATURES = [f"seats_{index * 30}_{(index + 1) * 30}" for index in range(WINDOW_COUNT)]
FEATURE_SETS = {
    "baseline": BASELINE_FEATURES,
    "flight_counts": BASELINE_FEATURES + COUNT_FEATURES,
    "scheduled_seats": BASELINE_FEATURES + COUNT_FEATURES + SEAT_FEATURES,
}


def build_training_frame(
    session: Session,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    wait_statement = select(WaitTimeObservation).where(
        WaitTimeObservation.wait_minutes.is_not(None),
        WaitTimeObservation.is_open.is_(True),
        WaitTimeObservation.is_wait_time_available.is_(True),
    )
    if start:
        wait_statement = wait_statement.where(WaitTimeObservation.observed_at >= as_utc(start))
    if end:
        wait_statement = wait_statement.where(WaitTimeObservation.observed_at < as_utc(end))
    waits = list(session.scalars(wait_statement.order_by(WaitTimeObservation.observed_at)))
    if not waits:
        return pd.DataFrame()

    first = min(as_utc(row.observed_at) for row in waits)
    last = max(as_utc(row.observed_at) for row in waits) + timedelta(minutes=180)
    schedule_rows = session.execute(
        select(Flight, FlightScheduleSnapshot)
        .join(FlightScheduleSnapshot, FlightScheduleSnapshot.flight_id == Flight.id)
        .where(
            FlightScheduleSnapshot.scheduled_out >= first,
            FlightScheduleSnapshot.scheduled_out <= last,
        )
        .order_by(FlightScheduleSnapshot.collected_at)
    ).all()
    snapshots: dict[int, list[tuple[Flight, FlightScheduleSnapshot]]] = defaultdict(list)
    for flight, snapshot in schedule_rows:
        snapshots[flight.id].append((flight, snapshot))

    years = {as_utc(row.observed_at).year for row in waits}
    us_holidays = holidays.US(years=years)
    zone = ZoneInfo(get_settings().airport_timezone)
    output: list[dict[str, Any]] = []
    for wait in waits:
        observed_at = as_utc(wait.observed_at)
        local = observed_at.astimezone(zone)
        row: dict[str, Any] = {
            "observed_at": observed_at,
            "observation_date": local.date().isoformat(),
            "airport": wait.airport,
            "terminal": normalize_terminal(wait.terminal),
            "checkpoint": wait.checkpoint or "unknown",
            "queue_type": wait.queue_type,
            "hour": local.hour + local.minute / 60,
            "weekday": local.weekday(),
            "month": local.month,
            "holiday": int(local.date() in us_holidays),
            "wait_minutes": wait.wait_minutes,
        }
        for index in range(WINDOW_COUNT):
            row[COUNT_FEATURES[index]] = 0
            row[SEAT_FEATURES[index]] = 0
        for versions in snapshots.values():
            eligible = [item for item in versions if as_utc(item[1].collected_at) <= observed_at]
            if not eligible:
                continue
            flight, snapshot = eligible[-1]
            if flight.airport != wait.airport or normalize_terminal(
                snapshot.terminal
            ) != normalize_terminal(wait.terminal):
                continue
            minutes = (as_utc(snapshot.scheduled_out) - observed_at).total_seconds() / 60
            if minutes < 0 or minutes >= WINDOW_COUNT * WINDOW_MINUTES:
                continue
            index = int(minutes // WINDOW_MINUTES)
            row[COUNT_FEATURES[index]] += 1
            row[SEAT_FEATURES[index]] += snapshot.total_seats or 0
        output.append(row)
    return pd.DataFrame(output)


def build_prediction_features(
    session: Session,
    *,
    airport: str,
    terminal: str,
    checkpoint: str | None,
    target_time: datetime,
) -> dict[str, Any]:
    target_time = as_utc(target_time)
    local = target_time.astimezone(ZoneInfo(get_settings().airport_timezone))
    us_holidays = holidays.US(years=[local.year])
    row: dict[str, Any] = {
        "airport": airport.upper(),
        "terminal": normalize_terminal(terminal),
        "checkpoint": checkpoint or "unknown",
        "hour": local.hour + local.minute / 60,
        "weekday": local.weekday(),
        "month": local.month,
        "holiday": int(local.date() in us_holidays),
    }
    for index, window in enumerate(
        demand_windows(session, airport=airport, terminal=terminal, reference_time=target_time)
    ):
        row[COUNT_FEATURES[index]] = window["flights"]
        row[SEAT_FEATURES[index]] = window["scheduled_seats"]
    return row
