from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.demand import normalize_terminal
from app.models import Flight, FlightScheduleSnapshot


@dataclass(frozen=True)
class TerminalResolution:
    terminal: str | None
    confidence: float
    method: str
    observations: int
    distribution: dict[str, float]


def resolve_terminal(
    session: Session,
    *,
    airport: str,
    operator: str | None,
    flight_number: str | None,
    destination: str | None = None,
) -> TerminalResolution:
    strategies = [
        (
            "flight_history",
            operator and flight_number,
            [Flight.operator == operator, Flight.flight_number == flight_number],
        ),
        (
            "route_history",
            operator and destination,
            [Flight.operator == operator, Flight.destination == destination],
        ),
        ("airline_history", operator, [Flight.operator == operator]),
    ]
    for method, enabled, predicates in strategies:
        if not enabled:
            continue
        rows = session.execute(
            select(FlightScheduleSnapshot.terminal)
            .join(Flight, Flight.id == FlightScheduleSnapshot.flight_id)
            .where(
                Flight.airport == airport.upper(),
                *predicates,
                FlightScheduleSnapshot.terminal.is_not(None),
            )
        )
        counts = Counter(normalize_terminal(row[0]) for row in rows if normalize_terminal(row[0]))
        if counts:
            total = sum(counts.values())
            terminal, count = counts.most_common(1)[0]
            return TerminalResolution(
                terminal=terminal,
                confidence=round(count / total, 4),
                method=method,
                observations=total,
                distribution={key: round(value / total, 4) for key, value in counts.items()},
            )
    return TerminalResolution(None, 0.0, "unknown", 0, {})
