from __future__ import annotations

from datetime import datetime, timezone

from app.analytics.demand import demand_time_series, demand_windows
from app.collectors.flightaware import ingest_flights
from app.models import ScheduleCacheEntry


def test_demand_uses_half_hour_windows_and_terminal_filter(session):
    records = [
        {
            "ident": "DAL1",
            "fa_flight_id": "one",
            "scheduled_out": "2026-08-19T17:20:00Z",
            "terminal_origin": "4",
            "destination": "KLAX",
            "seats_cabin_coach": 100,
            "type": "Airline",
        },
        {
            "ident": "DAL2",
            "fa_flight_id": "two",
            "scheduled_out": "2026-08-19T17:40:00Z",
            "terminal_origin": "1",
            "destination": "KATL",
            "seats_cabin_coach": 80,
            "type": "Airline",
        },
    ]
    ingest_flights(session, "JFK", records)
    windows = demand_windows(
        session,
        airport="JFK",
        terminal="Terminal 4",
        reference_time=datetime(2026, 8, 19, 17, 0, tzinfo=timezone.utc),
    )
    assert windows[0]["flights"] == 1
    assert windows[0]["scheduled_seats"] == 100
    assert windows[1]["flights"] == 0


def test_demand_time_series_counts_forward_window_only_with_schedule_coverage(session):
    records = [
        {
            "ident": "DAL10",
            "fa_flight_id": "series-one",
            "scheduled_out": "2026-08-19T17:20:00Z",
            "terminal_origin": "4",
            "destination": "KLAX",
            "type": "Airline",
        },
        {
            "ident": "DAL11",
            "fa_flight_id": "series-two",
            "scheduled_out": "2026-08-19T18:40:00Z",
            "terminal_origin": "1",
            "destination": "KATL",
            "type": "Airline",
        },
        {
            "ident": "DAL12",
            "fa_flight_id": "series-three",
            "scheduled_out": "2026-08-19T19:05:00Z",
            "terminal_origin": "4",
            "destination": "KBOS",
            "type": "Airline",
        },
    ]
    ingest_flights(session, "JFK", records)
    start = datetime(2026, 8, 19, 17, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)

    assert demand_time_series(
        session, airport="JFK", terminal=None, start=start, end=end, bucket_minutes=30
    ) == []

    session.add(
        ScheduleCacheEntry(
            airport="JFK",
            schedule_date=start.date(),
            source_kind="scheduled_departures",
            records_count=3,
            status="complete",
        )
    )
    session.commit()
    all_terminals = demand_time_series(
        session, airport="JFK", terminal=None, start=start, end=end, bucket_minutes=30
    )
    terminal_four = demand_time_series(
        session, airport="JFK", terminal="Terminal 4", start=start, end=end, bucket_minutes=30
    )

    assert [point["flights"] for point in all_terminals] == [2, 2]
    assert [point["flights"] for point in terminal_four] == [1, 1]
    assert all_terminals[0]["window_start"] == datetime(
        2026, 8, 19, 17, 30, tzinfo=timezone.utc
    )
    assert all_terminals[0]["window_end"] == datetime(
        2026, 8, 19, 19, 30, tzinfo=timezone.utc
    )
