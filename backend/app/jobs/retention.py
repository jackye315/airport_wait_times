from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select, update

from app.config import get_settings
from app.db import SessionLocal, init_database
from app.models import FlightScheduleSnapshot, RawSourceResponse, WaitTimeObservation
from app.timeutils import utcnow


def purge_expired_raw_responses() -> int:
    """Remove provider payloads after their configured retention periods.

    Normalized observations and flight snapshots remain; only their provenance
    foreign key is cleared before the raw response is deleted.
    """
    settings = get_settings()
    init_database()
    removed = 0
    policies = {
        "flightaware": settings.flightaware_raw_retention_days,
        "flightaware_usage": settings.flightaware_raw_retention_days,
        "port_authority": settings.port_authority_raw_retention_days,
    }
    with SessionLocal() as session:
        for source, days in policies.items():
            expired_ids = select(RawSourceResponse.id).where(
                RawSourceResponse.source == source,
                RawSourceResponse.fetched_at < utcnow() - timedelta(days=days),
            )
            session.execute(
                update(WaitTimeObservation)
                .where(WaitTimeObservation.raw_response_id.in_(expired_ids))
                .values(raw_response_id=None)
            )
            session.execute(
                update(FlightScheduleSnapshot)
                .where(FlightScheduleSnapshot.raw_response_id.in_(expired_ids))
                .values(raw_response_id=None)
            )
            result = session.execute(
                delete(RawSourceResponse).where(RawSourceResponse.id.in_(expired_ids))
            )
            removed += max(result.rowcount or 0, 0)
        session.commit()
    return removed


if __name__ == "__main__":
    print(f"removed {purge_expired_raw_responses()} expired raw responses")
