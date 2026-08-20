from __future__ import annotations

import httpx
import pytest
from sqlalchemy import func, select

from app.collectors.port_authority import PortAuthorityClient, collect_airport_waits
from app.config import Settings
from app.models import WaitTimeObservation

PAYLOAD = {
    "data": {
        "securityWaitTimes": [
            {
                "title": "Checkpoint A",
                "terminal": "4",
                "gate": None,
                "checkPoint": "Checkpoint A",
                "queueType": "Reg",
                "isOpen": True,
                "waitTime": 24,
                "isWaitTimeAvailable": True,
                "status": "Open",
                "lastUpdated": "2026-08-19T12:05:00-04:00",
            },
            {
                "title": "Checkpoint A",
                "terminal": "4",
                "gate": None,
                "checkPoint": "Checkpoint A",
                "queueType": "TSAPre",
                "isOpen": True,
                "waitTime": "5-10 minutes",
                "isWaitTimeAvailable": True,
                "status": "Open",
                "lastUpdated": "2026-08-19T12:05:00-04:00",
            },
        ]
    }
}


@pytest.mark.asyncio
async def test_parses_and_idempotently_stores_waits(session):
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=PAYLOAD))
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = PortAuthorityClient(Settings(database_url="sqlite:///:memory:"), http_client)
        assert await collect_airport_waits(session, "JFK", client) == 2
        assert await collect_airport_waits(session, "JFK", client) == 0

    rows = session.scalars(
        select(WaitTimeObservation).order_by(WaitTimeObservation.queue_type)
    ).all()
    assert len(rows) == 2
    assert {row.queue_type for row in rows} == {"general", "precheck"}
    assert max(row.wait_minutes or 0 for row in rows) == 24
    assert session.scalar(select(func.count()).select_from(WaitTimeObservation)) == 2


@pytest.mark.asyncio
async def test_missing_wait_is_not_coerced_to_zero():
    payload = {
        "data": {
            "securityWaitTimes": [
                {
                    **PAYLOAD["data"]["securityWaitTimes"][0],
                    "waitTime": None,
                    "isWaitTimeAvailable": False,
                }
            ]
        }
    }
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as http_client:
        records, _, _ = await PortAuthorityClient(Settings(), http_client).fetch_airport("LGA")
    assert records[0].wait_minutes is None
    assert records[0].is_available is False
