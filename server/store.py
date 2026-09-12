"""
The site's database: the same summaries, but keyed by who they belong to.

This is deliberately *not* `garmin_sync.store`. That one is single-user by
nature -- it's the file on your own Mac, and nothing about it should learn
about accounts. Here every row carries a `user_id` in its primary key, so one
person's history can never be read into another's charts. The file is called
`site.db` for the same reason: running the server locally must not collide
with the sync's own `garmin.db`.

Connections are per-request. SQLite doesn't share them across threads, FastAPI
runs sync endpoints in a threadpool, and opening a connection to a local file
costs microseconds.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

# How long a Mac has to finish linking after it starts a pairing.
PAIR_TTL_SECONDS = 600

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL UNIQUE,
    password    TEXT NOT NULL,
    sync_token  TEXT NOT NULL UNIQUE,
    created     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activities (
    user_id  INTEGER NOT NULL,
    id       INTEGER NOT NULL,
    start    TEXT,
    type     TEXT,
    summary  TEXT NOT NULL,
    PRIMARY KEY (user_id, id)
);
CREATE TABLE IF NOT EXISTS days (
    user_id  INTEGER NOT NULL,
    date     TEXT NOT NULL,
    summary  TEXT NOT NULL,
    PRIMARY KEY (user_id, date)
);
CREATE TABLE IF NOT EXISTS briefings (
    user_id  INTEGER NOT NULL,
    date     TEXT NOT NULL,
    text     TEXT NOT NULL,
    created  TEXT NOT NULL,
    PRIMARY KEY (user_id, date)
);
CREATE TABLE IF NOT EXISTS coach_usage (
    user_id  INTEGER NOT NULL,
    date     TEXT NOT NULL,
    calls    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, date)
);
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pairings (
    device_code TEXT PRIMARY KEY,
    user_code   TEXT NOT NULL UNIQUE,
    user_id     INTEGER,
    created     REAL NOT NULL,
    expires     REAL NOT NULL,
    consumed    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS activities_by_start ON activities(user_id, start);
CREATE INDEX IF NOT EXISTS pairings_by_user_code ON pairings(user_code);
"""


class EmailTaken(Exception):
    """Signup hit the unique index on email."""


def new_token() -> str:
    return secrets.token_urlsafe(32)


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        # WAL lets the dashboard read while a push writes; the busy timeout
        # stops two simultaneous pushes from failing outright.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def commit(self):
        self.conn.commit()

    # ---- settings -----------------------------------------------------------
    def setting(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.commit()

    def ensure_setting(self, key: str, factory) -> str:
        """Read a setting, creating it on first use.

        This is how the session-signing secret comes into being: it lives on
        the volume, so sign-ins survive restarts without anyone having to
        configure anything.
        """
        existing = self.setting(key)
        if existing:
            return existing
        value = factory()
        self.set_setting(key, value)
        return value

    # ---- users --------------------------------------------------------------
    def create_user(self, email: str, password_hash: str) -> dict:
        email = normalise_email(email)
        token = new_token()
        try:
            cursor = self.conn.execute(
                "INSERT INTO users (email, password, sync_token, created) "
                "VALUES (?,?,?,?)",
                (email, password_hash, token, date.today().isoformat()))
        except sqlite3.IntegrityError as e:
            raise EmailTaken(email) from e
        self.commit()
        return self.user_by_id(cursor.lastrowid)

    def user_by_email(self, email: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM users WHERE email = ?",
                                (normalise_email(email),)).fetchone()
        return dict(row) if row else None

    def user_by_id(self, user_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?",
                                (user_id,)).fetchone()
        return dict(row) if row else None

    def user_by_token(self, token: str) -> dict | None:
        """Tokens are long random strings, so an index lookup is the whole
        authentication step for a push."""
        if not token:
            return None
        row = self.conn.execute("SELECT * FROM users WHERE sync_token = ?",
                                (token,)).fetchone()
        return dict(row) if row else None

    def rotate_token(self, user_id: int) -> str:
        token = new_token()
        self.conn.execute("UPDATE users SET sync_token = ? WHERE id = ?",
                          (token, user_id))
        self.commit()
        return token

    def delete_user(self, user_id: int) -> None:
        for table in ("activities", "days", "briefings", "coach_usage", "pairings"):
            self.conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.commit()

    def user_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]

    # ---- pairing ------------------------------------------------------------
    def _purge_pairings(self) -> None:
        self.conn.execute("DELETE FROM pairings WHERE expires < ?", (time.time(),))

    def create_pairing(self) -> dict:
        """Start a device link: the Mac polls with device_code; the browser
        shows user_code and, once the signed-in owner approves, the next poll
        returns their sync token once."""
        self._purge_pairings()
        device_code = secrets.token_urlsafe(32)
        # Short, unambiguous characters so a phone can type them if needed.
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        user_code = "".join(secrets.choice(alphabet) for _ in range(8))
        user_code = f"{user_code[:4]}-{user_code[4:]}"
        now = time.time()
        self.conn.execute(
            "INSERT INTO pairings (device_code, user_code, created, expires) "
            "VALUES (?,?,?,?)",
            (device_code, user_code, now, now + PAIR_TTL_SECONDS))
        self.commit()
        return {
            "device_code": device_code,
            "user_code": user_code,
            "expires_in": PAIR_TTL_SECONDS,
            "interval": 2,
        }

    def pairing_by_user_code(self, user_code: str) -> dict | None:
        self._purge_pairings()
        code = (user_code or "").strip().upper()
        row = self.conn.execute(
            "SELECT * FROM pairings WHERE user_code = ?", (code,)).fetchone()
        return dict(row) if row else None

    def approve_pairing(self, user_code: str, user_id: int) -> bool:
        """Mark the pending link as owned by this account. Returns False if
        the code is unknown, expired, or already used."""
        self._purge_pairings()
        code = (user_code or "").strip().upper()
        cursor = self.conn.execute(
            "UPDATE pairings SET user_id = ? "
            "WHERE user_code = ? AND user_id IS NULL AND consumed = 0 "
            "AND expires > ?",
            (user_id, code, time.time()))
        self.commit()
        return cursor.rowcount == 1

    def poll_pairing(self, device_code: str) -> dict | None:
        """None while waiting; a dict with the sync token once approved.
        The token is handed out only once so a stolen device_code can't be
        replayed after the Mac has finished linking."""
        self._purge_pairings()
        row = self.conn.execute(
            "SELECT * FROM pairings WHERE device_code = ?",
            (device_code or "",)).fetchone()
        if not row:
            return None
        pairing = dict(row)
        if pairing["user_id"] is None or pairing["consumed"]:
            return {"pending": True} if pairing["user_id"] is None else None
        user = self.user_by_id(pairing["user_id"])
        if not user:
            return None
        self.conn.execute(
            "UPDATE pairings SET consumed = 1 WHERE device_code = ?",
            (device_code,))
        self.commit()
        return {
            "pending": False,
            "sync_token": user["sync_token"],
            "email": user["email"],
        }

    # ---- data ---------------------------------------------------------------
    def upsert_activity(self, user_id: int, summary: dict) -> None:
        self.conn.execute(
            "INSERT INTO activities (user_id, id, start, type, summary) "
            "VALUES (?,?,?,?,?) ON CONFLICT(user_id, id) DO UPDATE SET "
            "start=excluded.start, type=excluded.type, summary=excluded.summary",
            (user_id, summary.get("id"), summary.get("start"),
             summary.get("type"), json.dumps(summary)))

    def upsert_day(self, user_id: int, day: dict) -> None:
        self.conn.execute(
            "INSERT INTO days (user_id, date, summary) VALUES (?,?,?) "
            "ON CONFLICT(user_id, date) DO UPDATE SET summary=excluded.summary",
            (user_id, day.get("date"), json.dumps(day)))

    def last_day(self, user_id: int) -> str | None:
        row = self.conn.execute("SELECT MAX(date) d FROM days WHERE user_id = ?",
                                (user_id,)).fetchone()
        return row["d"] if row and row["d"] else None

    def first_day(self, user_id: int) -> str | None:
        row = self.conn.execute("SELECT MIN(date) d FROM days WHERE user_id = ?",
                                (user_id,)).fetchone()
        return row["d"] if row and row["d"] else None

    def counts(self, user_id: int) -> dict:
        activities = self.conn.execute(
            "SELECT COUNT(*) c FROM activities WHERE user_id = ?",
            (user_id,)).fetchone()["c"]
        days = self.conn.execute(
            "SELECT COUNT(*) c FROM days WHERE user_id = ?",
            (user_id,)).fetchone()["c"]
        return {"activities": activities, "days": days}

    def days_between(self, user_id: int, start: str | None = None,
                     end: str | None = None) -> list[dict]:
        return self._between("days", "date", user_id, start, end)

    def activities_between(self, user_id: int, start: str | None = None,
                           end: str | None = None) -> list[dict]:
        """`start` is stored as 'YYYY-MM-DD HH:MM:SS', so the bounds compare on
        its date prefix and stay inclusive of the whole end day."""
        return self._between("activities", "substr(start,1,10)", user_id,
                             start, end, order="start")

    def _between(self, table: str, col: str, user_id: int, start: str | None,
                 end: str | None, order: str | None = None) -> list[dict]:
        sql = f"SELECT summary FROM {table} WHERE user_id = ?"
        params: list = [user_id]
        if start:
            sql += f" AND {col} >= ?"
            params.append(start)
        if end:
            sql += f" AND {col} <= ?"
            params.append(end)
        sql += f" ORDER BY {order or col} ASC"
        return [json.loads(r["summary"]) for r in self.conn.execute(sql, tuple(params))]

    # ---- coach --------------------------------------------------------------
    def briefing(self, user_id: int, day: str) -> str | None:
        row = self.conn.execute(
            "SELECT text FROM briefings WHERE user_id = ? AND date = ?",
            (user_id, day)).fetchone()
        return row["text"] if row else None

    def save_briefing(self, user_id: int, day: str, text: str, created: str) -> None:
        self.conn.execute(
            "INSERT INTO briefings (user_id, date, text, created) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, date) DO UPDATE SET text=excluded.text, "
            "created=excluded.created", (user_id, day, text, created))
        self.commit()

    def coach_calls(self, user_id: int, day: str) -> int:
        row = self.conn.execute(
            "SELECT calls FROM coach_usage WHERE user_id = ? AND date = ?",
            (user_id, day)).fetchone()
        return row["calls"] if row else 0

    def record_coach_call(self, user_id: int, day: str) -> int:
        self.conn.execute(
            "INSERT INTO coach_usage (user_id, date, calls) VALUES (?,?,1) "
            "ON CONFLICT(user_id, date) DO UPDATE SET calls = calls + 1",
            (user_id, day))
        self.commit()
        return self.coach_calls(user_id, day)


@contextmanager
def open_store(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)
    try:
        yield store
    finally:
        store.close()
