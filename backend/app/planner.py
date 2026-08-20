from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.demand import demand_windows
from app.analytics.historical import historical_quantiles
from app.analytics.terminal_resolver import resolve_terminal
from app.collectors.flightaware import BudgetExceeded, FlightAwareClient, collect_schedules
from app.config import get_settings
from app.ml.features import build_prediction_features
from app.ml.predict import predict_wait
from app.models import Flight, FlightScheduleSnapshot, ScheduleCacheEntry
from app.timeutils import as_utc, utcnow


async def plan_trip(session: Session, request: Any) -> dict[str, Any]:
    settings = get_settings()
    airport = request.airport.upper()
    flight: Flight | None = None
    snapshot: FlightScheduleSnapshot | None = None
    schedule_status = "not_requested"
    if request.flight_number:
        flight, snapshot = _find_flight(
            session, airport, request.flight_number, request.flight_date
        )
        if (
            flight is None
            and request.flight_date
            >= utcnow().astimezone(ZoneInfo(settings.airport_timezone)).date()
        ):
            cache = session.scalar(
                select(ScheduleCacheEntry).where(
                    ScheduleCacheEntry.airport == airport,
                    ScheduleCacheEntry.schedule_date == request.flight_date,
                    ScheduleCacheEntry.source_kind == "future_schedule",
                )
            )
            if cache is None and settings.flightaware_api_key:
                try:
                    await collect_schedules(
                        session,
                        airport,
                        request.flight_date,
                        source_kind="future_schedule",
                        client=FlightAwareClient(session),
                    )
                    schedule_status = "fetched"
                except BudgetExceeded:
                    schedule_status = "budget_exhausted"
                except Exception:
                    schedule_status = "provider_error"
            elif cache:
                schedule_status = "cached"
            else:
                schedule_status = "api_key_missing"
            flight, snapshot = _find_flight(
                session, airport, request.flight_number, request.flight_date
            )

    departure = (
        snapshot.scheduled_out
        if snapshot
        else _manual_departure(request.flight_date, request.departure_time)
    )
    terminal = request.terminal or (snapshot.terminal if snapshot else None)
    resolution = None
    if not terminal and flight:
        resolution = resolve_terminal(
            session,
            airport=airport,
            operator=flight.operator,
            flight_number=flight.flight_number,
            destination=flight.destination,
        )
        terminal = resolution.terminal
    if not departure:
        return {
            "status": "missing_departure",
            "message": "A departure time or resolvable flight number is required.",
        }
    if not terminal:
        return {
            "status": "unknown_terminal",
            "message": "There is not enough terminal information for this flight yet.",
            "airport": airport,
            "departure_time": as_utc(departure),
            "schedule_status": schedule_status,
        }

    lead_hours = 3 if request.international else 2
    security_time = as_utc(departure) - timedelta(hours=lead_hours)
    features = build_prediction_features(
        session,
        airport=airport,
        terminal=terminal,
        checkpoint=None,
        target_time=security_time,
    )
    prediction = predict_wait(session, queue_type=request.queue_type, features=features)
    method = "model"
    if prediction is None:
        prediction = historical_quantiles(
            session,
            airport=airport,
            terminal=terminal,
            queue_type=request.queue_type,
            target_time=security_time,
        )
        method = "historical_baseline"
    windows = demand_windows(
        session, airport=airport, terminal=terminal, reference_time=security_time
    )
    if prediction is None:
        return {
            "status": "insufficient_data",
            "message": "The app is collecting real observations; a prediction will appear once enough matching history exists.",
            "airport": airport,
            "terminal": terminal,
            "departure_time": as_utc(departure),
            "assumed_security_time": security_time,
            "schedule_status": schedule_status,
            "terminal_resolution": _resolution_dict(resolution, terminal, snapshot is not None),
            "demand_windows": windows,
        }

    risk_field = {"normal": "median", "conservative": "p90", "very_conservative": "p95"}[
        request.risk_level
    ]
    selected_wait = float(prediction[risk_field])
    non_security_buffer = 45 + 20 + (30 if request.checked_bag else 0)
    recommended_arrival = as_utc(departure) - timedelta(minutes=selected_wait + non_security_buffer)
    return {
        "status": "ready",
        "airport": airport,
        "terminal": terminal,
        "flight_ident": flight.ident if flight else request.flight_number,
        "departure_time": as_utc(departure),
        "assumed_security_time": security_time,
        "queue_type": request.queue_type,
        "risk_level": request.risk_level,
        "prediction_method": method,
        "prediction": prediction,
        "recommended_arrival": recommended_arrival,
        "recommendation_components": {
            "selected_wait_minutes": selected_wait,
            "gate_buffer_minutes": 45,
            "terminal_walk_minutes": 20,
            "bag_check_minutes": 30 if request.checked_bag else 0,
        },
        "terminal_resolution": _resolution_dict(resolution, terminal, snapshot is not None),
        "schedule_status": schedule_status,
        "demand_windows": windows,
    }


def _find_flight(
    session: Session, airport: str, ident: str, flight_date: date
) -> tuple[Flight | None, FlightScheduleSnapshot | None]:
    normalized = ident.upper().replace(" ", "")
    flight = session.scalar(
        select(Flight)
        .where(
            Flight.airport == airport, Flight.flight_date == flight_date, Flight.ident == normalized
        )
        .order_by(Flight.id.desc())
    )
    if flight is None:
        return None, None
    snapshot = session.scalar(
        select(FlightScheduleSnapshot)
        .where(FlightScheduleSnapshot.flight_id == flight.id)
        .order_by(FlightScheduleSnapshot.collected_at.desc())
    )
    return flight, snapshot


def _manual_departure(value: date, departure_time: time | None) -> datetime | None:
    if departure_time is None:
        return None
    return datetime.combine(
        value, departure_time, ZoneInfo(get_settings().airport_timezone)
    ).astimezone(timezone.utc)


def _resolution_dict(resolution: Any, terminal: str, observed: bool) -> dict[str, Any]:
    if observed:
        return {
            "terminal": terminal,
            "confidence": 1.0,
            "method": "schedule",
            "observations": 1,
            "distribution": {terminal: 1.0},
        }
    if resolution:
        return resolution.__dict__
    return {
        "terminal": terminal,
        "confidence": 1.0,
        "method": "user_provided",
        "observations": 0,
        "distribution": {terminal: 1.0},
    }
