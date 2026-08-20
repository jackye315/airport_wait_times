from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.models import CollectionRun, RawSourceResponse
from app.timeutils import utcnow


def store_raw_response(
    session: Session, *, source: str, request_key: str, http_status: int | None, payload: Any
) -> RawSourceResponse:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    response = RawSourceResponse(
        source=source,
        request_key=request_key,
        http_status=http_status,
        payload_sha256=hashlib.sha256(serialized.encode()).hexdigest(),
        payload_json=serialized,
    )
    session.add(response)
    session.flush()
    return response


def start_collection_run(
    session: Session, *, source: str, airport: str | None = None, requested_date: date | None = None
) -> CollectionRun:
    run = CollectionRun(source=source, airport=airport, requested_date=requested_date)
    session.add(run)
    session.commit()
    return run


def finish_collection_run(
    session: Session,
    run: CollectionRun,
    *,
    status: str,
    records_received: int = 0,
    records_stored: int = 0,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    run.finished_at = utcnow()
    run.status = status
    run.records_received = records_received
    run.records_stored = records_stored
    run.error = error
    run.metadata_json = metadata
    session.add(run)
    session.commit()
