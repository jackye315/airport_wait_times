from __future__ import annotations

from datetime import date, time
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PlannerRequest(BaseModel):
    airport: Literal["JFK", "LGA"]
    flight_date: date
    flight_number: str | None = Field(default=None, max_length=16)
    terminal: str | None = Field(default=None, max_length=32)
    departure_time: time | None = None
    queue_type: Literal["general", "precheck"] = "general"
    risk_level: Literal["normal", "conservative", "very_conservative"] = "conservative"
    checked_bag: bool = False
    international: bool = False

    @model_validator(mode="after")
    def require_flight_or_time(self) -> PlannerRequest:
        if not self.flight_number and self.departure_time is None:
            raise ValueError("Provide a flight number or departure time")
        return self
