from __future__ import annotations

from collections import Counter
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ScheduleCacheEntry


def should_sample_date(session: Session, candidate: date) -> tuple[bool, str]:
    settings = get_settings()
    month_entries = list(
        session.scalars(
            select(ScheduleCacheEntry).where(
                ScheduleCacheEntry.source_kind == "scheduled_departures",
                ScheduleCacheEntry.schedule_date >= candidate.replace(day=1),
                ScheduleCacheEntry.schedule_date <= candidate,
                ScheduleCacheEntry.status == "complete",
            )
        )
    )
    completed_airports: dict[date, set[str]] = {}
    for entry in month_entries:
        completed_airports.setdefault(entry.schedule_date, set()).add(entry.airport)
    completed_dates = sorted(
        value for value, airports in completed_airports.items() if {"JFK", "LGA"} <= airports
    )
    if candidate in completed_dates:
        return False, "already_collected"
    if len(completed_dates) >= settings.flightaware_sample_days_per_month:
        return False, "monthly_sample_target_reached"
    weekday_counts = Counter(value.weekday() for value in completed_dates)
    minimum_count = min((weekday_counts[index] for index in range(7)), default=0)
    if weekday_counts[candidate.weekday()] == minimum_count:
        return True, "underrepresented_weekday"
    days_left = (
        __import__("calendar").monthrange(candidate.year, candidate.month)[1] - candidate.day
    ) + 1
    samples_left = settings.flightaware_sample_days_per_month - len(completed_dates)
    if samples_left >= days_left:
        return True, "quota_requires_sampling"
    if not completed_dates or (candidate - completed_dates[-1]).days >= 4:
        return True, "spread_across_month"
    return False, "diversity_wait"
