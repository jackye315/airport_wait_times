from __future__ import annotations

from typing import Any

import joblib
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ModelRun


def active_model(session: Session) -> ModelRun | None:
    return session.scalar(
        select(ModelRun).where(ModelRun.is_active.is_(True)).order_by(ModelRun.created_at.desc())
    )


def predict_wait(
    session: Session, *, queue_type: str, features: dict[str, Any]
) -> dict[str, Any] | None:
    run = active_model(session)
    if run is None:
        return None
    artifact = joblib.load(run.artifact_path)
    feature_set = artifact.get("selected", {}).get(queue_type)
    models = artifact.get("models", {}).get(queue_type, {}).get(feature_set)
    if not models:
        return None
    frame = pd.DataFrame([features])
    values = sorted(
        max(0.0, float(models[name].predict(frame)[0])) for name in ("median", "p90", "p95")
    )
    return {
        "median": round(values[0], 1),
        "p90": round(values[1], 1),
        "p95": round(values[2], 1),
        "model_run": run.run_key,
        "feature_set": feature_set,
    }
