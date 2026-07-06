"""
Local store: SQLite for durable, accumulating history, plus a compact JSON
export per run that you drag into a Claude chat or Project.

The SQLite db is also what the future automated phase will read from.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import date as _date
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    id        INTEGER PRIMARY KEY,
    start     TEXT,
    type      TEXT,
    summary   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS days (
    date      TEXT PRIMARY KEY,
    summary   TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def upsert_activity(self, summary: dict):
        self.conn.execute(
            "INSERT INTO activities (id, start, type, summary) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET start=excluded.start, type=excluded.type, summary=excluded.summary",
            (summary.get("id"), summary.get("start"), summary.get("type"), json.dumps(summary)),
        )

    def upsert_day(self, day: dict):
        self.conn.execute(
            "INSERT INTO days (date, summary) VALUES (?,?) "
            "ON CONFLICT(date) DO UPDATE SET summary=excluded.summary",
            (day.get("date"), json.dumps(day)),
        )

    def commit(self):
        self.conn.commit()

    def last_day(self) -> str | None:
        """Most recent date we have a day record for (ISO string), or None."""
        row = self.conn.execute("SELECT MAX(date) FROM days").fetchone()
        return row[0] if row and row[0] else None

    def counts(self) -> dict:
        a = self.conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
        d = self.conn.execute("SELECT COUNT(*) FROM days").fetchone()[0]
        return {"activities": a, "days": d}

    def export(self, out_path: Path, since: str | None = None) -> dict:
        """Write one compact JSON bundle. Returns counts for logging."""
        acts = self._rows("activities", "start", since)
        days = self._rows("days", "date", since)
        bundle = {
            "generated": _date.today().isoformat(),
            "since": since,
            "activities": acts,
            "days": days,
        }
        out_path.write_text(json.dumps(bundle, indent=2))
        return {"activities": len(acts), "days": len(days)}

    def _rows(self, table: str, order_col: str, since: str | None):
        sql = f"SELECT summary FROM {table}"
        params = ()
        if since:
            sql += f" WHERE {order_col} >= ?"
            params = (since,)
        sql += f" ORDER BY {order_col} DESC"
        return [json.loads(r[0]) for r in self.conn.execute(sql, params)]

    def close(self):
        self.conn.close()
