from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.ml.features import COUNT_FEATURES, SEAT_FEATURES, build_training_frame


def demand_correlations(session: Session) -> dict[str, Any]:
    frame = build_training_frame(session)
    if frame.empty:
        return {"observations": 0, "features": []}
    baseline_group = ["airport", "terminal", "checkpoint", "queue_type", "weekday"]
    frame = frame.copy()
    frame["half_hour"] = (frame["hour"] * 2).astype(int)
    group = baseline_group + ["half_hour"]
    frame["wait_residual"] = frame["wait_minutes"] - frame.groupby(group)["wait_minutes"].transform(
        "median"
    )
    results: list[dict[str, Any]] = []
    for feature in COUNT_FEATURES + SEAT_FEATURES:
        raw = _safe_correlation(frame[feature], frame["wait_minutes"])
        demand_residual = frame[feature] - frame.groupby(group)[feature].transform("median")
        controlled = _safe_correlation(demand_residual, frame["wait_residual"])
        results.append(
            {"feature": feature, "raw_correlation": raw, "controlled_correlation": controlled}
        )
    return {
        "observations": len(frame),
        "matched_days": frame["observation_date"].nunique(),
        "features": results,
    }


def _safe_correlation(left: pd.Series, right: pd.Series) -> float | None:
    if left.nunique() < 2 or right.nunique() < 2:
        return None
    value = left.corr(right)
    return round(float(value), 4) if pd.notna(value) else None
