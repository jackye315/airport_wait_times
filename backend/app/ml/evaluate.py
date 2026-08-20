from __future__ import annotations

import numpy as np
from sklearn.metrics import mean_absolute_error, mean_pinball_loss, mean_squared_error


def regression_metrics(
    truth: np.ndarray,
    median: np.ndarray,
    p90: np.ndarray,
    p95: np.ndarray,
) -> dict[str, float]:
    absolute = np.abs(truth - median)
    return {
        "mae": round(float(mean_absolute_error(truth, median)), 4),
        "rmse": round(float(mean_squared_error(truth, median) ** 0.5), 4),
        "p90_absolute_error": round(float(np.percentile(absolute, 90)), 4),
        "p90_coverage": round(float(np.mean(truth <= p90)), 4),
        "p95_coverage": round(float(np.mean(truth <= p95)), 4),
        "p90_pinball_loss": round(float(mean_pinball_loss(truth, p90, alpha=0.9)), 4),
        "p95_pinball_loss": round(float(mean_pinball_loss(truth, p95, alpha=0.95)), 4),
    }
