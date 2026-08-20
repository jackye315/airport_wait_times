from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.db import init_database
from app.jobs.collect_flights import collect_sample_day
from app.jobs.collect_wait_times import collect_all_waits
from app.jobs.retention import purge_expired_raw_responses
from app.ml.train import train_models
from app.timeutils import utcnow

logger = logging.getLogger(__name__)


def _run_wait_collection() -> None:
    asyncio.run(collect_all_waits())


def _run_flight_collection() -> None:
    asyncio.run(collect_sample_day())


def _train_if_ready() -> None:
    try:
        train_models()
    except RuntimeError as exc:
        logger.info("Model training deferred: %s", exc)
    except Exception:
        logger.exception("Scheduled model training failed")


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    init_database()
    scheduler = BlockingScheduler(
        timezone=settings.airport_timezone, job_defaults={"coalesce": True, "max_instances": 1}
    )
    scheduler.add_job(
        _run_wait_collection,
        IntervalTrigger(minutes=settings.port_authority_poll_minutes),
        id="port_authority_waits",
        next_run_time=utcnow(),
    )
    scheduler.add_job(
        _run_flight_collection, CronTrigger(hour=5, minute=15), id="flightaware_sample"
    )
    scheduler.add_job(
        _train_if_ready, CronTrigger(day_of_week="sun", hour=4, minute=0), id="weekly_training"
    )
    scheduler.add_job(
        purge_expired_raw_responses,
        CronTrigger(hour=3, minute=30),
        id="raw_response_retention",
    )
    logger.info("Scheduler started")
    scheduler.start()


if __name__ == "__main__":
    main()
