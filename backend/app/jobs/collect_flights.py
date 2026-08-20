from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.analytics.sampling import should_sample_date
from app.collectors.flightaware import BudgetExceeded, collect_schedules
from app.config import get_settings
from app.db import SessionLocal, init_database

logger = logging.getLogger(__name__)


async def collect_sample_day() -> None:
    settings = get_settings()
    if not settings.flightaware_api_key:
        logger.info("Skipping FlightAware sampling because the API key is not configured")
        return
    init_database()
    today = datetime.now(ZoneInfo(settings.airport_timezone)).date()
    with SessionLocal() as session:
        should_sample, reason = should_sample_date(session, today)
    if not should_sample:
        logger.info("Not sampling %s: %s", today, reason)
        return
    for airport in ("JFK", "LGA"):
        with SessionLocal() as session:
            try:
                await collect_schedules(session, airport, today)
            except BudgetExceeded:
                logger.warning("FlightAware budget stopped sampling at %s", airport)
                break
            except Exception:
                logger.exception("Flight schedule collection failed for %s", airport)


if __name__ == "__main__":
    asyncio.run(collect_sample_day())
