from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AirPlanner"
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///../data/airports.db"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    port_authority_graphql_url: str = "https://www.jfkairport.com/api/graphql"
    port_authority_poll_minutes: int = 5
    collector_timeout_seconds: float = 15
    collector_max_retries: int = 4

    flightaware_api_key: str | None = None
    flightaware_base_url: str = "https://aeroapi.flightaware.com/aeroapi"
    flightaware_collection_budget_usd: float = 4.25
    flightaware_planner_budget_usd: float = 0.75
    flightaware_monthly_hard_limit_usd: float = 5.00
    flightaware_departures_page_cost_usd: float = 0.005
    flightaware_schedules_page_cost_usd: float = 0.020
    flightaware_sample_days_per_month: int = 10
    flightaware_min_request_interval_seconds: float = 6.5
    flightaware_rate_limit_cooldown_seconds: float = 61.0
    flightaware_raw_retention_days: int = 29
    port_authority_raw_retention_days: int = 90

    airport_timezone: str = "America/New_York"
    model_artifact_dir: Path = Path("../artifacts")
    minimum_baseline_observations: int = 20
    public_base_url: str = "http://localhost:3000"
    backup_dir: Path = Path("../backups")
    backup_retention_days: int = 30

    @property
    def cors_origin_list(self) -> list[str]:
        return [value.strip() for value in self.cors_origins.split(",") if value.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
