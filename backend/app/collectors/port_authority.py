from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from dateutil import parser as date_parser
from lzstring import LZString
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import WaitTimeObservation
from app.repositories import finish_collection_run, start_collection_run, store_raw_response
from app.timeutils import UTC, utcnow

logger = logging.getLogger(__name__)

SECURITY_QUERY = """
query AirportSecurityWaits($airportCode: String!, $terminal: String) {
  securityWaitTimes(airportCode: $airportCode, terminal: $terminal) {
    title
    terminal
    gate
    checkPoint
    queueType
    isOpen
    waitTime
    isWaitTimeAvailable
    status
    lastUpdated
  }
}
""".strip()


@dataclass(frozen=True)
class PortAuthorityWait:
    terminal: str
    checkpoint: str
    gate: str | None
    queue_type: str
    is_open: bool
    is_available: bool
    wait_minutes: int | None
    status: str | None
    last_updated: str | None
    observed_at: datetime


class PortAuthorityClient:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self._client = client

    async def fetch_airport(
        self, airport: str
    ) -> tuple[list[PortAuthorityWait], dict[str, Any], int]:
        airport = airport.upper()
        payload = {
            "operationName": "AirportSecurityWaits",
            "variables": {"airportCode": airport, "terminal": None},
            "query": SECURITY_QUERY,
        }
        body = LZString().compressToEncodedURIComponent(__import__("json").dumps(payload))
        headers = {
            "content-type": "text/plain",
            "accept": "application/graphql-response+json,application/json;q=0.9",
            "origin": "https://www.jfkairport.com",
            "referer": "https://www.jfkairport.com/",
            "user-agent": "airport-wait-times/0.1 (personal analytics project)",
        }
        response = await self._request(body, headers)
        data = response.json()
        if data.get("errors"):
            message = data["errors"][0].get("message", "unknown GraphQL error")
            raise RuntimeError(f"Port Authority GraphQL error: {message}")
        fetched_at = utcnow()
        rows = data.get("data", {}).get("securityWaitTimes") or []
        return [self._parse_row(row, fetched_at) for row in rows], data, response.status_code

    async def _request(self, body: str, headers: dict[str, str]) -> httpx.Response:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.settings.collector_timeout_seconds)
        try:
            for attempt in range(self.settings.collector_max_retries):
                try:
                    response = await client.post(
                        self.settings.port_authority_graphql_url,
                        content=body,
                        headers=headers,
                    )
                except (httpx.TimeoutException, httpx.TransportError):
                    if attempt + 1 >= self.settings.collector_max_retries:
                        raise
                    await asyncio.sleep(min(2**attempt, 20))
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 >= self.settings.collector_max_retries:
                        response.raise_for_status()
                    await asyncio.sleep(_retry_delay(response, attempt))
                    continue
                response.raise_for_status()
                return response
            raise RuntimeError("Port Authority retry loop exhausted")
        finally:
            if owns_client:
                await client.aclose()

    def _parse_row(self, row: dict[str, Any], fetched_at: datetime) -> PortAuthorityWait:
        available = bool(row.get("isWaitTimeAvailable", row.get("waitTime") is not None))
        return PortAuthorityWait(
            terminal=str(row.get("terminal") or "").strip(),
            checkpoint=str(row.get("checkPoint") or row.get("title") or "").strip(),
            gate=_optional_string(row.get("gate")),
            queue_type=_normalize_queue(row.get("queueType")),
            is_open=bool(row.get("isOpen", True)),
            is_available=available,
            wait_minutes=_parse_wait(row.get("waitTime")) if available else None,
            status=_optional_string(row.get("status")),
            last_updated=_optional_string(row.get("lastUpdated")),
            observed_at=_parse_observed_at(
                row.get("lastUpdated"),
                fetched_at,
                self.settings.airport_timezone,
                self.settings.port_authority_poll_minutes,
            ),
        )


async def collect_airport_waits(
    session: Session, airport: str, client: PortAuthorityClient | None = None
) -> int:
    collector = client or PortAuthorityClient()
    run = start_collection_run(session, source="port_authority", airport=airport.upper())
    try:
        records, payload, status = await collector.fetch_airport(airport)
        raw = store_raw_response(
            session,
            source="port_authority",
            request_key=f"securityWaitTimes:{airport.upper()}",
            http_status=status,
            payload=payload,
        )
        stored = 0
        for record in records:
            statement = (
                sqlite_insert(WaitTimeObservation)
                .values(
                    observed_at=record.observed_at,
                    fetched_at=utcnow(),
                    airport=airport.upper(),
                    terminal=record.terminal,
                    checkpoint=record.checkpoint,
                    gate=record.gate,
                    queue_type=record.queue_type,
                    is_open=record.is_open,
                    is_wait_time_available=record.is_available,
                    wait_minutes=record.wait_minutes,
                    status=record.status,
                    source_last_updated=record.last_updated,
                    raw_response_id=raw.id,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        "airport",
                        "terminal",
                        "checkpoint",
                        "queue_type",
                        "observed_at",
                    ]
                )
            )
            result = session.execute(statement)
            stored += max(result.rowcount or 0, 0)
        session.commit()
        finish_collection_run(
            session,
            run,
            status="success",
            records_received=len(records),
            records_stored=stored,
        )
        return stored
    except Exception as exc:
        session.rollback()
        finish_collection_run(session, run, status="failed", error=str(exc))
        logger.exception("Port Authority collection failed for %s", airport)
        raise


def _normalize_queue(value: Any) -> str:
    normalized = str(value or "").lower().replace(" ", "")
    if "pre" in normalized:
        return "precheck"
    return "general"


def _parse_wait(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, round(value))
    numbers = [int(match) for match in re.findall(r"\d+", str(value))]
    return max(numbers) if numbers else None


def _parse_observed_at(
    value: Any, fetched_at: datetime, timezone_name: str, interval: int
) -> datetime:
    if value:
        try:
            local_now = fetched_at.astimezone(ZoneInfo(timezone_name))
            parsed = date_parser.parse(
                str(value), default=local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            )
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
            if parsed > local_now + timedelta(minutes=10):
                parsed -= timedelta(days=1)
            return parsed.astimezone(UTC).replace(microsecond=0)
        except (ValueError, TypeError, OverflowError):
            pass
    minute = fetched_at.minute - (fetched_at.minute % interval)
    return fetched_at.replace(minute=minute, second=0, microsecond=0)


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 60.0))
        except ValueError:
            try:
                return max(
                    0.0, min((parsedate_to_datetime(retry_after) - utcnow()).total_seconds(), 60.0)
                )
            except (TypeError, ValueError):
                pass
    return float(min(2**attempt, 30))


def _optional_string(value: Any) -> str | None:
    return str(value).strip() if value not in (None, "") else None
