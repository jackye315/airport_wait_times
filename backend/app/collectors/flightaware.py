from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx
from dateutil import parser as date_parser
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    ApiUsageLedger,
    Flight,
    FlightScheduleSnapshot,
    RawSourceResponse,
    ScheduleCacheEntry,
)
from app.repositories import finish_collection_run, start_collection_run, store_raw_response
from app.timeutils import as_utc, local_day_bounds, month_bounds, utcnow

logger = logging.getLogger(__name__)
ICAO_AIRPORTS = {"JFK": "KJFK", "LGA": "KLGA"}
Category = Literal["collection", "planner"]


class BudgetExceeded(RuntimeError):
    pass


class FlightAwareClient:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.session = session
        self.settings = settings or get_settings()
        self._client = client

    def spent_this_month(self, category: Category) -> float:
        start, end = month_bounds(utcnow())
        amount = self.session.scalar(
            select(func.coalesce(func.sum(ApiUsageLedger.estimated_cost_usd), 0)).where(
                ApiUsageLedger.category == category,
                ApiUsageLedger.occurred_at >= start,
                ApiUsageLedger.occurred_at < end,
            )
        )
        return float(amount or 0)

    def remaining_budget(self, category: Category) -> float:
        budget = (
            self.settings.flightaware_collection_budget_usd
            if category == "collection"
            else self.settings.flightaware_planner_budget_usd
        )
        return max(0.0, budget - self.spent_this_month(category))

    def locally_estimated_spend(self) -> float:
        start, end = month_bounds(utcnow())
        amount = self.session.scalar(
            select(func.coalesce(func.sum(ApiUsageLedger.estimated_cost_usd), 0)).where(
                ApiUsageLedger.occurred_at >= start,
                ApiUsageLedger.occurred_at < end,
            )
        )
        return float(amount or 0)

    def latest_provider_usage(self) -> dict[str, Any] | None:
        start, end = month_bounds(utcnow())
        request_key = self._usage_request_key(start, end)
        response = self.session.scalar(
            select(RawSourceResponse)
            .where(
                RawSourceResponse.source == "flightaware_usage",
                RawSourceResponse.request_key == request_key,
            )
            .order_by(RawSourceResponse.fetched_at.desc())
        )
        return json.loads(response.payload_json) if response else None

    def provider_spend_this_month(self) -> float | None:
        usage = self.latest_provider_usage()
        if not usage:
            return None
        return max(
            float(usage.get("total_cost") or 0),
            float(usage.get("total_discount_cost") or 0),
        )

    def effective_total_spend(self) -> float:
        provider = self.provider_spend_this_month()
        return max(self.locally_estimated_spend(), provider or 0)

    def hard_limit_remaining(self) -> float:
        return max(
            0.0,
            self.settings.flightaware_monthly_hard_limit_usd - self.effective_total_spend(),
        )

    async def refresh_account_usage(self, *, force: bool = False) -> dict[str, Any]:
        if not self.settings.flightaware_api_key:
            raise RuntimeError("FLIGHTAWARE_API_KEY is not configured")
        start, end = month_bounds(utcnow())
        request_key = self._usage_request_key(start, end)
        cached = self.session.scalar(
            select(RawSourceResponse)
            .where(
                RawSourceResponse.source == "flightaware_usage",
                RawSourceResponse.request_key == request_key,
                RawSourceResponse.fetched_at >= utcnow() - timedelta(minutes=10),
            )
            .order_by(RawSourceResponse.fetched_at.desc())
        )
        if cached and not force:
            return json.loads(cached.payload_json)
        url = self._absolute_url("/account/usage")
        response = await self._request(
            url,
            {
                "start": _api_datetime(start),
                "end": _api_datetime(min(end, utcnow())),
                "all_keys": "false",
            },
        )
        payload = response.json()
        response.raise_for_status()
        store_raw_response(
            self.session,
            source="flightaware_usage",
            request_key=request_key,
            http_status=response.status_code,
            payload=payload,
        )
        self.session.commit()
        return payload

    async def assert_budget(self, category: Category, next_page_cost: float) -> None:
        if self.remaining_budget(category) + 1e-9 < next_page_cost:
            raise BudgetExceeded(f"FlightAware {category} budget exhausted")
        await self.refresh_account_usage()
        if self.hard_limit_remaining() + 1e-9 < next_page_cost:
            raise BudgetExceeded("FlightAware monthly hard limit exhausted")

    @staticmethod
    def _usage_request_key(start: datetime, end: datetime) -> str:
        return f"account/usage:{start.date().isoformat()}:{end.date().isoformat()}"

    async def fetch_scheduled_departures(
        self, airport: str, schedule_date: date
    ) -> list[dict[str, Any]]:
        start, end = local_day_bounds(schedule_date, self.settings.airport_timezone)
        path = f"/airports/{ICAO_AIRPORTS[airport.upper()]}/flights/scheduled_departures"
        return await self._fetch_pages(
            path,
            params={"start": _api_datetime(start), "end": _api_datetime(end)},
            result_key="scheduled_departures",
            category="collection",
            page_cost=self.settings.flightaware_departures_page_cost_usd,
        )

    async def fetch_future_schedule(
        self, airport: str, schedule_date: date
    ) -> list[dict[str, Any]]:
        end = schedule_date + timedelta(days=1)
        path = f"/schedules/{schedule_date.isoformat()}/{end.isoformat()}"
        return await self._fetch_pages(
            path,
            params={"origin": ICAO_AIRPORTS[airport.upper()]},
            result_key="scheduled",
            category="planner",
            page_cost=self.settings.flightaware_schedules_page_cost_usd,
        )

    async def _fetch_pages(
        self,
        path: str,
        *,
        params: dict[str, Any],
        result_key: str,
        category: Category,
        page_cost: float,
    ) -> list[dict[str, Any]]:
        if not self.settings.flightaware_api_key:
            raise RuntimeError("FLIGHTAWARE_API_KEY is not configured")
        records: list[dict[str, Any]] = []
        next_url: str | None = path
        next_params: dict[str, Any] | None = params
        seen_urls: set[str] = set()
        while next_url:
            await self.assert_budget(category, page_cost)
            absolute_url = self._absolute_url(next_url)
            request_key = (
                str(httpx.URL(absolute_url, params=next_params)) if next_params else absolute_url
            )
            if request_key in seen_urls:
                raise RuntimeError("FlightAware pagination repeated a page URL")
            seen_urls.add(request_key)
            await self._wait_for_rate_limit_slot()
            response = await self._request(absolute_url, next_params)
            payload = response.json()
            result_sets = max(1, int(payload.get("num_pages") or 1))
            billed = response.status_code != 429
            self.session.add(
                ApiUsageLedger(
                    category=category,
                    request_path=request_key,
                    response_status=response.status_code,
                    result_sets=result_sets,
                    estimated_cost_usd=Decimal(str(page_cost * result_sets if billed else 0)),
                )
            )
            store_raw_response(
                self.session,
                source="flightaware",
                request_key=request_key,
                http_status=response.status_code,
                payload=payload,
            )
            self.session.commit()
            response.raise_for_status()
            records.extend(payload.get(result_key) or [])
            next_url = (payload.get("links") or {}).get("next")
            next_params = None
        return _deduplicate_codeshares(records)

    async def _request(self, url: str, params: dict[str, Any] | None) -> httpx.Response:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.settings.collector_timeout_seconds)
        headers = {
            "x-apikey": self.settings.flightaware_api_key or "",
            "accept": "application/json",
        }
        try:
            for attempt in range(self.settings.collector_max_retries):
                try:
                    response = await client.get(url, params=params, headers=headers)
                except (httpx.TimeoutException, httpx.TransportError):
                    if attempt + 1 >= self.settings.collector_max_retries:
                        raise
                    await asyncio.sleep(min(2**attempt, 20))
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 >= self.settings.collector_max_retries:
                        response.raise_for_status()
                    delay = _retry_delay(response, attempt)
                    if response.status_code == 429 and "retry-after" not in response.headers:
                        delay = max(
                            delay,
                            self.settings.flightaware_rate_limit_cooldown_seconds,
                        )
                    await asyncio.sleep(delay)
                    continue
                return response
            raise RuntimeError("FlightAware retry loop exhausted")
        finally:
            if owns_client:
                await client.aclose()

    async def _wait_for_rate_limit_slot(self) -> None:
        """Pace paid result sets for FlightAware's Personal-tier minute quota."""
        minimum_interval = self.settings.flightaware_min_request_interval_seconds
        if minimum_interval <= 0:
            return
        latest = self.session.scalar(select(func.max(ApiUsageLedger.occurred_at)))
        if latest is None:
            return
        elapsed = (utcnow() - as_utc(latest)).total_seconds()
        delay = minimum_interval - elapsed
        if delay > 0:
            logger.info("Waiting %.2fs for a FlightAware rate-limit slot", delay)
            await asyncio.sleep(delay)

    def _absolute_url(self, value: str) -> str:
        if value.startswith("http://") or value.startswith("https://"):
            return value
        base = self.settings.flightaware_base_url.rstrip("/") + "/"
        if value.startswith("/aeroapi/"):
            origin = str(httpx.URL(base).copy_with(path="/"))
            return urljoin(origin, value.lstrip("/"))
        return urljoin(base, value.lstrip("/"))


async def collect_schedules(
    session: Session,
    airport: str,
    schedule_date: date,
    *,
    source_kind: Literal["scheduled_departures", "future_schedule"] = "scheduled_departures",
    client: FlightAwareClient | None = None,
) -> int:
    airport = airport.upper()
    run = start_collection_run(
        session, source="flightaware", airport=airport, requested_date=schedule_date
    )
    collector = client or FlightAwareClient(session)
    try:
        records = (
            await collector.fetch_scheduled_departures(airport, schedule_date)
            if source_kind == "scheduled_departures"
            else await collector.fetch_future_schedule(airport, schedule_date)
        )
        stored = ingest_flights(session, airport, records)
        cache_statement = (
            sqlite_insert(ScheduleCacheEntry)
            .values(
                airport=airport,
                schedule_date=schedule_date,
                source_kind=source_kind,
                fetched_at=utcnow(),
                expires_at=utcnow() + timedelta(hours=24),
                records_count=len(records),
                status="complete",
            )
            .on_conflict_do_update(
                index_elements=["airport", "schedule_date", "source_kind"],
                set_={
                    "fetched_at": utcnow(),
                    "expires_at": utcnow() + timedelta(hours=24),
                    "records_count": len(records),
                    "status": "complete",
                },
            )
        )
        session.execute(cache_statement)
        session.commit()
        finish_collection_run(
            session,
            run,
            status="success",
            records_received=len(records),
            records_stored=stored,
            metadata={
                "source_kind": source_kind,
                "estimated_month_spend": collector.spent_this_month(
                    "collection" if source_kind == "scheduled_departures" else "planner"
                ),
            },
        )
        return stored
    except Exception as exc:
        session.rollback()
        finish_collection_run(
            session,
            run,
            status="budget_stopped" if isinstance(exc, BudgetExceeded) else "failed",
            error=str(exc),
        )
        logger.exception("FlightAware collection failed for %s on %s", airport, schedule_date)
        raise


def ingest_flights(session: Session, airport: str, records: list[dict[str, Any]]) -> int:
    stored = 0
    zone = ZoneInfo(get_settings().airport_timezone)
    for record in records:
        if str(record.get("type") or "Airline").lower() not in {"airline", "commercial"}:
            continue
        scheduled_out = _parse_datetime(record.get("scheduled_out"))
        if scheduled_out is None:
            continue
        ident = str(
            record.get("actual_ident") or record.get("ident") or record.get("ident_icao") or ""
        ).upper()
        if not ident:
            continue
        canonical_key = str(
            record.get("fa_flight_id")
            or f"{airport}:{ident}:{scheduled_out.isoformat()}:{_airport_code(record.get('destination'))}"
        )
        flight = session.scalar(select(Flight).where(Flight.canonical_key == canonical_key))
        operator, flight_number = _split_ident(ident)
        if flight is None:
            flight = Flight(
                canonical_key=canonical_key,
                fa_flight_id=_optional_string(record.get("fa_flight_id")),
                airport=airport,
                flight_date=scheduled_out.astimezone(zone).date(),
                ident=ident,
                actual_ident=_optional_string(record.get("actual_ident")),
                operator=operator,
                flight_number=flight_number,
                destination=_airport_code(record.get("destination")),
                flight_type=_optional_string(record.get("type")),
            )
            session.add(flight)
            session.flush()
        snapshot_values = {
            "scheduled_out": scheduled_out.isoformat(),
            "scheduled_off": str(record.get("scheduled_off") or ""),
            "terminal": str(record.get("terminal_origin") or record.get("terminal") or ""),
            "gate": str(record.get("gate_origin") or record.get("gate") or ""),
            "aircraft_type": str(record.get("aircraft_type") or ""),
            "coach": record.get("seats_cabin_coach"),
            "business": record.get("seats_cabin_business"),
            "first": record.get("seats_cabin_first"),
        }
        fingerprint = hashlib.sha256(
            json.dumps(snapshot_values, sort_keys=True, default=str).encode()
        ).hexdigest()
        seats = [
            _as_nonnegative_int(record.get(name))
            for name in ("seats_cabin_coach", "seats_cabin_business", "seats_cabin_first")
        ]
        total_seats = (
            sum(value for value in seats if value is not None)
            if any(value is not None for value in seats)
            else None
        )
        statement = (
            sqlite_insert(FlightScheduleSnapshot)
            .values(
                flight_id=flight.id,
                collected_at=utcnow(),
                fingerprint=fingerprint,
                scheduled_out=scheduled_out,
                scheduled_off=_parse_datetime(record.get("scheduled_off")),
                terminal=_optional_string(record.get("terminal_origin") or record.get("terminal")),
                gate=_optional_string(record.get("gate_origin") or record.get("gate")),
                aircraft_type=_optional_string(record.get("aircraft_type")),
                coach_seats=seats[0],
                business_seats=seats[1],
                first_seats=seats[2],
                total_seats=total_seats,
                capacity_source="flightaware" if total_seats is not None else "unknown",
            )
            .on_conflict_do_nothing(index_elements=["flight_id", "fingerprint"])
        )
        result = session.execute(statement)
        stored += max(result.rowcount or 0, 0)
    session.commit()
    return stored


def _deduplicate_codeshares(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, tuple[int, dict[str, Any]]] = {}
    for record in records:
        key = str(
            record.get("fa_flight_id")
            or f"{record.get('ident')}:{record.get('scheduled_out')}:{_airport_code(record.get('destination'))}"
        )
        ident = str(record.get("ident") or "")
        actual = record.get("actual_ident")
        score = 3 if not actual else (2 if str(actual) == ident else 1)
        if key not in selected or score > selected[key][0]:
            selected[key] = (score, record)
    return [item[1] for item in selected.values()]


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return as_utc(date_parser.isoparse(str(value)))
    except (ValueError, TypeError):
        return None


def _api_datetime(value: datetime) -> str:
    """Format UTC timestamps in the strict Z form required by AeroAPI."""
    return as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _split_ident(value: str) -> tuple[str | None, str | None]:
    match = re.match(r"^([A-Z]{2,3})([0-9].*)$", value)
    return (match.group(1), match.group(2)) if match else (None, None)


def _airport_code(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("code_icao") or value.get("code_iata") or value.get("code")
    return _optional_string(value)


def _optional_string(value: Any) -> str | None:
    return str(value).strip() if value not in (None, "") else None


def _as_nonnegative_int(value: Any) -> int | None:
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 60.0))
        except ValueError:
            try:
                return max(
                    0.0,
                    min((parsedate_to_datetime(retry_after) - utcnow()).total_seconds(), 60.0),
                )
            except (TypeError, ValueError):
                pass
    return float(min(2**attempt, 30))
