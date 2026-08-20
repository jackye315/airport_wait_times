from __future__ import annotations

import argparse
import uuid
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from app.config import get_settings
from app.db import SessionLocal, init_database
from app.ml.evaluate import regression_metrics
from app.ml.features import FEATURE_SETS, build_training_frame
from app.models import ModelRun
from app.timeutils import as_utc, utcnow

QUANTILES = {"median": 0.5, "p90": 0.9, "p95": 0.95}
CATEGORICAL = ["airport", "terminal", "checkpoint"]


def train_models(*, activate: bool = True) -> ModelRun:
    settings = get_settings()
    init_database()
    with SessionLocal() as session:
        frame = build_training_frame(session)
        if len(frame) < 100 or frame["observation_date"].nunique() < 5:
            raise RuntimeError(
                "At least 100 observations across five matched schedule days are required"
            )
        dates = sorted(frame["observation_date"].unique())
        split_index = max(1, int(len(dates) * 0.8))
        validation_dates = set(dates[split_index:]) or {dates[-1]}
        train_frame = frame[~frame["observation_date"].isin(validation_dates)]
        validation_frame = frame[frame["observation_date"].isin(validation_dates)]
        artifact: dict[str, Any] = {
            "models": {},
            "selected": {},
            "created_at": utcnow().isoformat(),
        }
        all_metrics: dict[str, Any] = {}
        for queue_type in sorted(frame["queue_type"].unique()):
            queue_train = train_frame[train_frame["queue_type"] == queue_type]
            queue_validation = validation_frame[validation_frame["queue_type"] == queue_type]
            if len(queue_train) < 50 or len(queue_validation) < 10:
                continue
            artifact["models"][queue_type] = {}
            all_metrics[queue_type] = {}
            best_name: str | None = None
            best_mae = float("inf")
            for feature_name, features in FEATURE_SETS.items():
                models: dict[str, Pipeline] = {}
                predictions: dict[str, np.ndarray] = {}
                for quantile_name, quantile in QUANTILES.items():
                    model = _pipeline(features, quantile)
                    model.fit(queue_train[features], queue_train["wait_minutes"])
                    models[quantile_name] = model
                    predictions[quantile_name] = model.predict(queue_validation[features])
                metrics = regression_metrics(
                    queue_validation["wait_minutes"].to_numpy(),
                    predictions["median"],
                    predictions["p90"],
                    predictions["p95"],
                )
                artifact["models"][queue_type][feature_name] = models
                all_metrics[queue_type][feature_name] = metrics
                if metrics["mae"] < best_mae:
                    best_mae = metrics["mae"]
                    best_name = feature_name
            artifact["selected"][queue_type] = best_name
        if not artifact["selected"]:
            raise RuntimeError("Not enough queue-specific observations to train a model")

        run_key = utcnow().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        output_dir = Path(settings.model_artifact_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = output_dir / f"model-{run_key}.joblib"
        artifact["metrics"] = all_metrics
        artifact["feature_sets"] = FEATURE_SETS
        joblib.dump(artifact, artifact_path)
        if activate:
            for previous in session.query(ModelRun).filter(ModelRun.is_active.is_(True)):
                previous.is_active = False
        run = ModelRun(
            run_key=run_key,
            training_start=as_utc(frame["observed_at"].min().to_pydatetime()),
            training_end=as_utc(frame["observed_at"].max().to_pydatetime()),
            model_family="gradient_boosting_quantile",
            feature_set="ablation-selected",
            feature_schema=sorted(
                {feature for values in FEATURE_SETS.values() for feature in values}
            ),
            metrics=all_metrics,
            artifact_path=str(artifact_path),
            is_active=activate,
        )
        session.add(run)
        session.commit()
        return run


def _pipeline(features: list[str], quantile: float) -> Pipeline:
    categorical = [name for name in CATEGORICAL if name in features]
    numeric = [name for name in features if name not in categorical]
    preprocessing = ColumnTransformer(
        [
            (
                "categorical",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                    ]
                ),
                categorical,
            ),
            ("numeric", Pipeline([("impute", SimpleImputer(strategy="median"))]), numeric),
        ]
    )
    return Pipeline(
        [
            ("preprocess", preprocessing),
            (
                "model",
                GradientBoostingRegressor(
                    loss="quantile",
                    alpha=quantile,
                    n_estimators=200,
                    max_depth=3,
                    learning_rate=0.04,
                    random_state=42,
                ),
            ),
        ]
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-activate", action="store_true")
    arguments = parser.parse_args()
    trained = train_models(activate=not arguments.no_activate)
    print(f"trained {trained.run_key}: {trained.artifact_path}")
