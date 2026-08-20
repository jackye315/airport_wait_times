from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.analytics.correlation import demand_correlations
from app.analytics.demand import demand_time_series, demand_windows
from app.analytics.historical import current_waits, hourly_statistics, wait_history
from app.collectors.flightaware import FlightAwareClient
from app.db import get_db
from app.ml.predict import active_model
from app.models import CollectionRun, Flight, FlightScheduleSnapshot, WaitTimeObservation
from app.planner import plan_trip
from app.schemas import PlannerRequest
from app.timeutils import as_utc, utcnow

router = APIRouter(prefix="/api")
Db = Annotated[Session, Depends(get_db)]


@router.get("/airports")
def airports(session: Db) -> dict[str, Any]:
    output = []
    names = {"JFK": "John F. Kennedy International", "LGA": "LaGuardia"}
    for code, name in names.items():
        wait_terminals = session.scalars(
            select(distinct(WaitTimeObservation.terminal)).where(
                WaitTimeObservation.airport == code
            )
        )
        flight_terminals = session.scalars(
            select(distinct(FlightScheduleSnapshot.terminal))
            .join(Flight, Flight.id == FlightScheduleSnapshot.flight_id)
            .where(Flight.airport == code)
        )
        terminals = sorted({value for value in [*wait_terminals, *flight_terminals] if value})
        output.append({"code": code, "name": name, "terminals": terminals})
    return {"airports": output}


@router.get("/dashboard/current")
def dashboard_current(
    session: Db, airport: str = "JFK", terminal: str | None = None
) -> dict[str, Any]:
    return {
        "airport": airport.upper(),
        "terminal": terminal,
        "waits": current_waits(session, airport, terminal),
    }


@router.get("/dashboard/history")
def dashboard_history(
    session: Db,
    airport: str = "JFK",
    terminal: str | None = None,
    queue_type: str | None = None,
    hours: int = Query(default=24, ge=1, le=24 * 90),
) -> dict[str, Any]:
    end = utcnow()
    return {
        "airport": airport.upper(),
        "start": end - timedelta(hours=hours),
        "end": end,
        "observations": wait_history(
            session,
            airport=airport,
            terminal=terminal,
            queue_type=queue_type,
            start=end - timedelta(hours=hours),
            end=end,
        ),
    }


@router.get("/dashboard/demand")
def dashboard_demand(
    session: Db,
    airport: str = "JFK",
    terminal: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    reference = as_utc(at) if at else utcnow()
    return {
        "airport": airport.upper(),
        "terminal": terminal,
        "reference_time": reference,
        "windows": demand_windows(
            session, airport=airport, terminal=terminal, reference_time=reference
        ),
    }


@router.get("/dashboard/demand/history")
def dashboard_demand_history(
    session: Db,
    airport: str = "JFK",
    terminal: str | None = None,
    hours: int = Query(default=24, ge=1, le=24 * 90),
) -> dict[str, Any]:
    end = utcnow()
    start = end - timedelta(hours=hours)
    return {
        "airport": airport.upper(),
        "terminal": terminal,
        "start": start,
        "end": end,
        "offset_minutes": 30,
        "window_minutes": 120,
        "points": demand_time_series(
            session,
            airport=airport,
            terminal=terminal,
            start=start,
            end=end,
        ),
    }


@router.get("/analytics/correlation")
def analytics_correlation(session: Db) -> dict[str, Any]:
    return demand_correlations(session)


@router.get("/analytics/historical")
def analytics_historical(
    session: Db, airport: str = "JFK", terminal: str | None = None, queue_type: str | None = None
) -> dict[str, Any]:
    return {
        "statistics": hourly_statistics(
            session, airport=airport, terminal=terminal, queue_type=queue_type
        )
    }


@router.get("/analytics/model")
def analytics_model(session: Db) -> dict[str, Any]:
    model = active_model(session)
    if model is None:
        return {"status": "collecting_data", "model": None}
    return {
        "status": "ready",
        "model": {
            "run_key": model.run_key,
            "created_at": model.created_at,
            "model_family": model.model_family,
            "feature_set": model.feature_set,
            "training_start": model.training_start,
            "training_end": model.training_end,
            "metrics": model.metrics,
        },
    }


@router.post("/planner/predict")
async def planner_predict(request: PlannerRequest, session: Db) -> dict[str, Any]:
    return await plan_trip(session, request)


@router.get("/system/status")
def system_status(session: Db) -> dict[str, Any]:
    latest_runs = []
    for source, airport in (
        ("port_authority", "JFK"),
        ("port_authority", "LGA"),
        ("flightaware", "JFK"),
        ("flightaware", "LGA"),
    ):
        run = session.scalar(
            select(CollectionRun)
            .where(CollectionRun.source == source, CollectionRun.airport == airport)
            .order_by(CollectionRun.started_at.desc())
        )
        latest_runs.append(
            {
                "source": source,
                "airport": airport,
                "status": run.status if run else "never_run",
                "started_at": run.started_at if run else None,
                "finished_at": run.finished_at if run else None,
                "records_received": run.records_received if run else 0,
                "error": run.error if run else None,
            }
        )
    client = FlightAwareClient(session)
    return {
        "status": "ok",
        "server_time": utcnow(),
        "latest_runs": latest_runs,
        "flightaware": {
            "configured": bool(client.settings.flightaware_api_key),
            "collection_spend": round(client.spent_this_month("collection"), 4),
            "collection_remaining": round(client.remaining_budget("collection"), 4),
            "planner_spend": round(client.spent_this_month("planner"), 4),
            "planner_remaining": round(client.remaining_budget("planner"), 4),
            "provider_reported_spend": client.provider_spend_this_month(),
            "effective_total_spend": round(client.effective_total_spend(), 4),
            "hard_limit_remaining": round(client.hard_limit_remaining(), 4),
        },
        "counts": {
            "wait_observations": session.scalar(
                select(func.count()).select_from(WaitTimeObservation)
            )
            or 0,
            "flight_snapshots": session.scalar(
                select(func.count()).select_from(FlightScheduleSnapshot)
            )
            or 0,
        },
    }


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
