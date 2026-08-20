from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote

from app.config import get_settings
from app.timeutils import utcnow


def backup_database() -> Path | None:
    settings = get_settings()
    if not settings.database_url.startswith("sqlite:///"):
        return None
    source_path = Path(unquote(settings.database_url.removeprefix("sqlite:///"))).resolve()
    if not source_path.exists():
        return None
    target_dir = Path(settings.backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / f"airports-{utcnow().strftime('%Y%m%dT%H%M%SZ')}.db"
    with sqlite3.connect(source_path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    cutoff = utcnow() - timedelta(days=settings.backup_retention_days)
    for candidate in target_dir.glob("airports-*.db"):
        if candidate.stat().st_mtime < cutoff.timestamp():
            candidate.unlink()
    return destination


if __name__ == "__main__":
    created = backup_database()
    print(created or "No SQLite database was available to back up")
