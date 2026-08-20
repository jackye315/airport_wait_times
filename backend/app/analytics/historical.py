from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import WaitTimeObservation
from app.timeutils import as_utc, utcnow


def current_waits(
    session: Session, airport: str, terminal: str | None = None
) -> list[dict[str, Any]]:
    cutoff = utcnow() - timedelta(hours=12)
    statement = (
        select(WaitTimeObservation)
        .where(
            WaitTimeObservation.airport == airport.upper(),
            WaitTimeObservation.observed_at >= cutoff,
        )
        .order_by(WaitTimeObservation.observed_at.desc(), WaitTimeObservation.id.desc())
    )
    if terminal:
        statement = statement.where(WaitTimeObservation.terminal == terminal)
    rows = session.scalars(statement).all()
    latest: dict[tuple[str, str, str], WaitTimeObservation] = {}
    for row in rows:
        key = (row.terminal, row.checkpoint, row.queue_type)
        latest.setdefault(key, row)
    return [_serialize_wait(row) for row in latest.values()]


def wait_history(
    session: Session,
    *,
    airport: str,
    terminal: str | None,
    queue_type: str | None,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    statement = (
        select(WaitTimeObservation)
        .where(
            WaitTimeObservation.airport == airport.upper(),
            WaitTimeObservation.observed_at >= as_utc(start),
            WaitTimeObservation.observed_at < as_utc(end),
        )
        .order_by(WaitTimeObservation.observed_at)
    )
    if terminal:
        statement = statement.where(WaitTimeObservation.terminal == terminal)
    if queue_type:
        statement = statement.where(WaitTimeObservation.queue_type == queue_type)
    return [_serialize_wait(row) for row in session.scalars(statement)]


def hourly_statistics(
    session: Session,
    *,
    airport: str,
    terminal: str | None = None,
    queue_type: str | None = None,
) -> list[dict[str, Any]]:
    statement = select(WaitTimeObservation).where(
        WaitTimeObservation.airport == airport.upper(),
        WaitTimeObservation.wait_minutes.is_not(None),
        WaitTimeObservation.is_open.is_(True),
    )
    if terminal:
        statement = statement.where(WaitTimeObservation.terminal == terminal)
    if queue_type:
        statement = statement.where(WaitTimeObservation.queue_type == queue_type)
    zone = ZoneInfo(get_settings().airport_timezone)
    groups: dict[tuple[int, int, str], list[int]] = defaultdict(list)
    for row in session.scalars(statement):
        local = as_utc(row.observed_at).astimezone(zone)
        groups[(local.weekday(), local.hour, row.queue_type)].append(row.wait_minutes or 0)
    return [
        {
            "weekday": key[0],
            "hour": key[1],
            "queue_type": key[2],
            "observations": len(values),
            "median_minutes": round(float(np.median(values)), 1),
            "p90_minutes": round(float(np.percentile(values, 90)), 1),
        }
        for key, values in sorted(groups.items())
    ]


def historical_quantiles(
    session: Session,
    *,
    airport: str,
    terminal: str,
    queue_type: str,
    target_time: datetime,
) -> dict[str, float | int] | None:
    zone = ZoneInfo(get_settings().airport_timezone)
    target_local = as_utc(target_time).astimezone(zone)
    candidates = session.scalars(
        select(WaitTimeObservation).where(
            WaitTimeObservation.airport == airport.upper(),
            WaitTimeObservation.terminal == terminal,
            WaitTimeObservation.queue_type == queue_type,
            WaitTimeObservation.wait_minutes.is_not(None),
            WaitTimeObservation.is_open.is_(True),
        )
    )
    values = [
        row.wait_minutes
        for row in candidates
        if as_utc(row.observed_at).astimezone(zone).weekday() == target_local.weekday()
        and abs(
            as_utc(row.observed_at).astimezone(zone).hour * 60
            + as_utc(row.observed_at).astimezone(zone).minute
            - (target_local.hour * 60 + target_local.minute)
        )
        <= 30
        and row.wait_minutes is not None
    ]
    if len(values) < get_settings().minimum_baseline_observations:
        return None
    return {
        "observations": len(values),
        "median": round(float(np.percentile(values, 50)), 1),
        "p75": round(float(np.percentile(values, 75)), 1),
        "p90": round(float(np.percentile(values, 90)), 1),
        "p95": round(float(np.percentile(values, 95)), 1),
    }


def _serialize_wait(row: WaitTimeObservation) -> dict[str, Any]:
    return {
        "observed_at": as_utc(row.observed_at),
        "fetched_at": as_utc(row.fetched_at),
        "airport": row.airport,
        "terminal": row.terminal,
        "checkpoint": row.checkpoint,
        "gate": row.gate,
        "queue_type": row.queue_type,
        "is_open": row.is_open,
        "is_wait_time_available": row.is_wait_time_available,
        "wait_minutes": row.wait_minutes,
        "status": row.status,
    }
