"""
Daily copies of the database, next to it on the volume.

`VACUUM INTO` writes a consistent, compacted snapshot while the app keeps
running, so there is no need to stop anything. The copies sit on the same
volume: they cover a bad migration, a buggy write or a deleted account, not
the loss of the volume itself -- for that, turn on Railway's volume backups.
They hold the same secrets as the database, so they never leave the volume.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import date
from pathlib import Path

from . import config

CHECK_SECONDS = 3600


def backup_dir() -> Path:
    return config.DB_PATH.parent / "backups"


def run(today: date | None = None) -> Path | None:
    """Today's snapshot, made if it doesn't exist yet. None when there is no
    database to copy."""
    if not config.DB_PATH.exists():
        return None
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"site-{(today or date.today()).isoformat()}.db"
    if target.exists():
        return target
    partial = target.with_suffix(".partial")
    partial.unlink(missing_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("VACUUM INTO ?", (str(partial),))
    finally:
        conn.close()
    os.replace(partial, target)
    prune()
    return target


def prune() -> list[Path]:
    """Drop all but the newest `BACKUP_KEEP` snapshots."""
    copies = sorted(backup_dir().glob("site-*.db"))
    stale = copies[:-config.BACKUP_KEEP] if config.BACKUP_KEEP > 0 else []
    for path in stale:
        path.unlink(missing_ok=True)
    return stale


async def loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            made = await asyncio.to_thread(run)
            if made:
                print(f"[garmin-sync] backup: {made.name}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[garmin-sync] backup failed: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(CHECK_SECONDS)
