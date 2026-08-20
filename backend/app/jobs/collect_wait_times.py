from __future__ import annotations

import asyncio
import logging

from app.collectors.port_authority import collect_airport_waits
from app.db import SessionLocal, init_database

logger = logging.getLogger(__name__)


async def collect_all_waits() -> None:
    init_database()
    for airport in ("JFK", "LGA"):
        with SessionLocal() as session:
            try:
                await collect_airport_waits(session, airport)
            except Exception:
                logger.exception("Wait collection failed for %s; continuing", airport)


if __name__ == "__main__":
    asyncio.run(collect_all_waits())
