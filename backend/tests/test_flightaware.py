from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.collectors.flightaware import BudgetExceeded, FlightAwareClient, collect_schedules
from app.config import Settings
from app.models import ApiUsageLedger, Flight, FlightScheduleSnapshot
from app.timeutils import utcnow


def flight(ident: str, fa_id: str, terminal: str = "4"):
    return {
        "ident": ident,
        "fa_flight_id": fa_id,
        "origin": {"code_icao": "KJFK"},
        "destination": {"code_icao": "KLAX"},
        "scheduled_out": "2026-08-19T18:00:00Z",
        "scheduled_off": "2026-08-19T18:15:00Z",
        "terminal_origin": terminal,
        "aircraft_type": "A321",
        "seats_cabin_coach": 180,
        "seats_cabin_business": 20,
        "seats_cabin_first": 0,
        "type": "Airline",
    }


@pytest.mark.asyncio
async def test_paginates_tracks_cost_and_stores_schedule(session):
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        if request.url.path.endswith("/account/usage"):
            return httpx.Response(
                200,
                json={"total_cost": 0, "total_discount_cost": 0, "total_calls": 0},
            )
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "scheduled_departures": [flight("DAL123", "fa-1")],
                    "links": {"next": "/airports/KJFK/flights/scheduled_departures?cursor=two"},
                },
            )
        return httpx.Response(
            200, json={"scheduled_departures": [flight("DAL124", "fa-2")], "links": {}}
        )

    settings = Settings(
        flightaware_api_key="test",
        flightaware_base_url="https://example.test/aeroapi",
        flightaware_collection_budget_usd=1,
        flightaware_min_request_interval_seconds=0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = FlightAwareClient(session, settings, http_client)
        stored = await collect_schedules(session, "JFK", date(2026, 8, 19), client=client)

    assert calls == 2
    assert stored == 2
    assert session.scalar(select(func.count()).select_from(Flight)) == 2
    assert session.scalar(select(func.count()).select_from(FlightScheduleSnapshot)) == 2
    assert session.scalar(select(func.count()).select_from(ApiUsageLedger)) == 2
    assert client.spent_this_month("collection") == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_codeshare_records_are_deduplicated(session):
    operating = flight("AAL1504", "fa-shared")
    codeshare = {**flight("JBU4077", "fa-shared"), "actual_ident": "AAL1504"}

    def handler(request: httpx.Request):
        if request.url.path.endswith("/account/usage"):
            return httpx.Response(
                200,
                json={"total_cost": 0, "total_discount_cost": 0, "total_calls": 0},
            )
        return httpx.Response(
            200, json={"scheduled_departures": [codeshare, operating], "links": {}}
        )

    transport = httpx.MockTransport(handler)
    settings = Settings(
        flightaware_api_key="test", flightaware_base_url="https://example.test/aeroapi"
    )
    async with httpx.AsyncClient(transport=transport) as http_client:
        await collect_schedules(
            session,
            "JFK",
            date(2026, 8, 19),
            client=FlightAwareClient(session, settings, http_client),
        )
    stored = session.scalar(select(Flight))
    assert stored is not None
    assert stored.ident == "AAL1504"


@pytest.mark.asyncio
async def test_provider_usage_enforces_hard_limit_before_paid_call(session):
    paid_calls = 0

    def handler(request: httpx.Request):
        nonlocal paid_calls
        if request.url.path.endswith("/account/usage"):
            return httpx.Response(
                200,
                json={"total_cost": 4.996, "total_discount_cost": 4.996},
            )
        paid_calls += 1
        return httpx.Response(200, json={"scheduled_departures": [], "links": {}})

    settings = Settings(
        flightaware_api_key="test",
        flightaware_base_url="https://example.test/aeroapi",
        flightaware_monthly_hard_limit_usd=5,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = FlightAwareClient(session, settings, http_client)
        await client.refresh_account_usage()
        assert client.provider_spend_this_month() == pytest.approx(4.996)
        assert client.hard_limit_remaining() == pytest.approx(0.004)
        with pytest.raises(BudgetExceeded, match="hard limit"):
            await client.fetch_scheduled_departures("JFK", date(2026, 8, 19))

    assert paid_calls == 0


@pytest.mark.asyncio
async def test_aeroapi_datetime_parameters_use_strict_zulu_format(session):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request):
        requests.append(request)
        if request.url.path.endswith("/account/usage"):
            return httpx.Response(200, json={"total_cost": 0, "total_discount_cost": 0})
        return httpx.Response(200, json={"scheduled_departures": [], "links": {}})

    settings = Settings(
        flightaware_api_key="test",
        flightaware_base_url="https://example.test/aeroapi",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = FlightAwareClient(session, settings, http_client)
        await client.fetch_scheduled_departures("JFK", date(2026, 8, 19))

    usage_request, departures_request = requests
    for key in ("start", "end"):
        assert usage_request.url.params[key].endswith("Z")
        assert "+00:00" not in usage_request.url.params[key]
        assert departures_request.url.params[key].endswith("Z")
        assert "+00:00" not in departures_request.url.params[key]


@pytest.mark.asyncio
async def test_paid_requests_are_paced_from_usage_ledger(session, monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(delay: float):
        sleeps.append(delay)

    def handler(request: httpx.Request):
        if request.url.path.endswith("/account/usage"):
            return httpx.Response(200, json={"total_cost": 0, "total_discount_cost": 0})
        return httpx.Response(200, json={"scheduled_departures": [], "links": {}})

    session.add(
        ApiUsageLedger(
            occurred_at=utcnow(),
            category="collection",
            request_path="/previous-paid-request",
            response_status=200,
            result_sets=1,
            estimated_cost_usd=Decimal("0.005"),
        )
    )
    session.commit()
    monkeypatch.setattr("app.collectors.flightaware.asyncio.sleep", fake_sleep)
    settings = Settings(
        flightaware_api_key="test",
        flightaware_base_url="https://example.test/aeroapi",
        flightaware_min_request_interval_seconds=6.5,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = FlightAwareClient(session, settings, http_client)
        await client.fetch_scheduled_departures("JFK", date(2026, 8, 19))

    assert len(sleeps) == 1
    assert sleeps[0] > 6


@pytest.mark.asyncio
async def test_429_without_retry_after_uses_full_window_cooldown(session, monkeypatch):
    paid_calls = 0
    sleeps: list[float] = []

    async def fake_sleep(delay: float):
        sleeps.append(delay)

    def handler(request: httpx.Request):
        nonlocal paid_calls
        if request.url.path.endswith("/account/usage"):
            return httpx.Response(200, json={"total_cost": 0, "total_discount_cost": 0})
        paid_calls += 1
        if paid_calls == 1:
            return httpx.Response(429, json={"title": "Too Many Requests"})
        return httpx.Response(200, json={"scheduled_departures": [], "links": {}})

    monkeypatch.setattr("app.collectors.flightaware.asyncio.sleep", fake_sleep)
    settings = Settings(
        flightaware_api_key="test",
        flightaware_base_url="https://example.test/aeroapi",
        flightaware_min_request_interval_seconds=0,
        flightaware_rate_limit_cooldown_seconds=61,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = FlightAwareClient(session, settings, http_client)
        await client.fetch_scheduled_departures("JFK", date(2026, 8, 19))

    assert paid_calls == 2
    assert sleeps == [61]
