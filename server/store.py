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

import hashlib
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
    created     TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'athlete'
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
CREATE TABLE IF NOT EXISTS garmin_meta (
    user_id  INTEGER NOT NULL,
    key      TEXT NOT NULL,
    value    TEXT NOT NULL,
    updated  TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS briefings (
    user_id  INTEGER NOT NULL,
    date     TEXT NOT NULL,
    lang     TEXT NOT NULL DEFAULT 'en',
    text     TEXT NOT NULL,
    created  TEXT NOT NULL,
    PRIMARY KEY (user_id, date, lang)
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

CREATE TABLE IF NOT EXISTS athlete (
    user_id  INTEGER PRIMARY KEY,
    profile  TEXT NOT NULL,
    updated  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS races (
    user_id   INTEGER NOT NULL,
    id        TEXT NOT NULL,
    date      TEXT NOT NULL,
    payload   TEXT NOT NULL,
    PRIMARY KEY (user_id, id)
);
CREATE TABLE IF NOT EXISTS plans (
    user_id   INTEGER NOT NULL,
    id        TEXT NOT NULL,
    race_id   TEXT,
    status    TEXT NOT NULL,
    payload   TEXT NOT NULL,
    created   TEXT NOT NULL,
    PRIMARY KEY (user_id, id)
);
CREATE TABLE IF NOT EXISTS plan_sessions (
    user_id             INTEGER NOT NULL,
    id                  TEXT NOT NULL,
    plan_id             TEXT NOT NULL,
    date                TEXT NOT NULL,
    sport               TEXT,
    kind                TEXT,
    state               TEXT NOT NULL,
    workout             TEXT NOT NULL,
    est_load            REAL,
    garmin_workout_id   INTEGER,
    garmin_schedule_id  INTEGER,
    activity_id         INTEGER,
    revision            INTEGER NOT NULL DEFAULT 1,
    payload             TEXT NOT NULL,
    PRIMARY KEY (user_id, id)
);
CREATE INDEX IF NOT EXISTS sessions_by_date ON plan_sessions(user_id, date);
CREATE INDEX IF NOT EXISTS sessions_by_state ON plan_sessions(user_id, state);
CREATE TABLE IF NOT EXISTS workout_templates (
    user_id  INTEGER NOT NULL,
    id       TEXT NOT NULL,
    name     TEXT NOT NULL,
    workout  TEXT NOT NULL,
    PRIMARY KEY (user_id, id)
);
CREATE TABLE IF NOT EXISTS decisions (
    user_id  INTEGER NOT NULL,
    date     TEXT NOT NULL,
    payload  TEXT NOT NULL,
    PRIMARY KEY (user_id, date)
);
CREATE TABLE IF NOT EXISTS coaching_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id  INTEGER NOT NULL,
    coach_id    INTEGER NOT NULL,
    status      TEXT NOT NULL,
    created     TEXT NOT NULL,
    updated     TEXT NOT NULL,
    UNIQUE(athlete_id, coach_id)
);
CREATE INDEX IF NOT EXISTS coaching_links_coach ON coaching_links(coach_id, status);
CREATE TABLE IF NOT EXISTS coach_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    coach_id    INTEGER NOT NULL,
    athlete_id  INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    text        TEXT NOT NULL,
    session_id  TEXT,
    created     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS coach_notes_athlete ON coach_notes(athlete_id, created);
CREATE TABLE IF NOT EXISTS garmin_accounts (
    user_id       INTEGER PRIMARY KEY,
    garmin_email  TEXT NOT NULL,
    tokens        TEXT NOT NULL,
    created       TEXT NOT NULL,
    last_sync     REAL,
    last_result   TEXT,
    last_error    TEXT,
    needs_login   INTEGER NOT NULL DEFAULT 0,
    backfill      TEXT
);
CREATE TABLE IF NOT EXISTS email_tokens (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    purpose     TEXT NOT NULL,
    new_email   TEXT,
    created     REAL NOT NULL,
    expires     REAL NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS email_tokens_user ON email_tokens(user_id, purpose);
"""

# How long a link in an email stays valid.
EMAIL_TOKEN_TTL = {"verify": 48 * 3600, "reset": 3600, "email_change": 24 * 3600}

VALID_ROLES = frozenset({"athlete", "coach"})
LINK_STATUSES = frozenset({"pending", "accepted", "rejected"})
NOTE_KINDS = frozenset({"comment", "suggestion"})


class EmailTaken(Exception):
    """Signup hit the unique index on email."""


def new_token() -> str:
    return secrets.token_urlsafe(32)


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


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
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Columns and tables added after the first install."""
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(users)")}
        if "role" not in cols:
            self.conn.execute(
                "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'athlete'")
        if "email_verified" not in cols:
            self.conn.execute(
                "ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
        if "session_version" not in cols:
            self.conn.execute(
                "ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")
        brief_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(briefings)")}
        if brief_cols and "lang" not in brief_cols:
            self.conn.execute(
                "CREATE TABLE briefings_v2 ("
                "user_id INTEGER NOT NULL, date TEXT NOT NULL, "
                "lang TEXT NOT NULL DEFAULT 'en', text TEXT NOT NULL, "
                "created TEXT NOT NULL, PRIMARY KEY (user_id, date, lang))")
            self.conn.execute(
                "INSERT INTO briefings_v2 (user_id, date, lang, text, created) "
                "SELECT user_id, date, 'en', text, created FROM briefings")
            self.conn.execute("DROP TABLE briefings")
            self.conn.execute("ALTER TABLE briefings_v2 RENAME TO briefings")

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
    def create_user(self, email: str, password_hash: str,
                    role: str = "athlete") -> dict:
        email = normalise_email(email)
        token = new_token()
        role = role if role in VALID_ROLES else "athlete"
        try:
            cursor = self.conn.execute(
                "INSERT INTO users (email, password, sync_token, created, role) "
                "VALUES (?,?,?,?,?)",
                (email, password_hash, token, date.today().isoformat(), role))
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
        for table in ("activities", "days", "garmin_meta", "briefings",
                      "coach_usage", "pairings", "athlete", "races", "plans",
                      "plan_sessions", "workout_templates", "decisions",
                      "garmin_accounts", "email_tokens"):
            self.conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        self.conn.execute(
            "DELETE FROM coaching_links WHERE athlete_id = ? OR coach_id = ?",
            (user_id, user_id))
        self.conn.execute(
            "DELETE FROM coach_notes WHERE athlete_id = ? OR coach_id = ?",
            (user_id, user_id))
        self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.commit()

    def user_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]

    def list_users(self) -> list[dict]:
        """Every account, newest first, without passwords or tokens."""
        rows = self.conn.execute(
            "SELECT id, email, role, created, email_verified FROM users "
            "ORDER BY id DESC")
        return [dict(r) for r in rows]

    def set_password(self, user_id: int, password_hash: str) -> None:
        self.conn.execute("UPDATE users SET password = ? WHERE id = ?",
                          (password_hash, user_id))
        self.commit()

    def set_email(self, user_id: int, email: str) -> None:
        try:
            self.conn.execute("UPDATE users SET email = ? WHERE id = ?",
                              (normalise_email(email), user_id))
        except sqlite3.IntegrityError as e:
            raise EmailTaken(email) from e
        self.commit()

    def mark_verified(self, user_id: int) -> None:
        self.conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))
        self.commit()

    def bump_session_version(self, user_id: int) -> int:
        self.conn.execute(
            "UPDATE users SET session_version = session_version + 1 WHERE id = ?",
            (user_id,))
        self.commit()
        return self.user_by_id(user_id)["session_version"]

    # ---- email links ----------------------------------------------------------
    def create_email_token(self, user_id: int, purpose: str,
                           new_email: str | None = None) -> str:
        """A fresh single-use link. Only its hash is stored, and issuing one
        cancels any earlier link for the same purpose."""
        token = new_token()
        now = time.time()
        self.conn.execute(
            "DELETE FROM email_tokens WHERE user_id = ? AND purpose = ?",
            (user_id, purpose))
        self.conn.execute(
            "INSERT INTO email_tokens (token_hash, user_id, purpose, new_email, "
            "created, expires) VALUES (?,?,?,?,?,?)",
            (_hash(token), user_id, purpose,
             normalise_email(new_email) if new_email else None,
             now, now + EMAIL_TOKEN_TTL[purpose]))
        self.commit()
        return token

    def last_email_token(self, user_id: int, purpose: str) -> float | None:
        row = self.conn.execute(
            "SELECT MAX(created) c FROM email_tokens WHERE user_id = ? AND purpose = ?",
            (user_id, purpose)).fetchone()
        return row["c"] if row else None

    def consume_email_token(self, token: str, purpose: str) -> dict | None:
        """The row behind a link, marked used, or None if it's unknown,
        already used or expired."""
        if not token:
            return None
        row = self.conn.execute(
            "SELECT * FROM email_tokens WHERE token_hash = ? AND purpose = ?",
            (_hash(token), purpose)).fetchone()
        if not row or row["used"] or row["expires"] < time.time():
            return None
        self.conn.execute("UPDATE email_tokens SET used = 1 WHERE token_hash = ?",
                          (row["token_hash"],))
        self.commit()
        return dict(row)

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
        # Later chunks of a sync may carry only some of a day's fields.
        # Replace would wipe the rest, so merge over the row that is already
        # there and let the new keys win.
        incoming = dict(day or {})
        row = self.conn.execute(
            "SELECT summary FROM days WHERE user_id = ? AND date = ?",
            (user_id, incoming.get("date"))).fetchone()
        if row and row["summary"]:
            try:
                existing = json.loads(row["summary"])
            except json.JSONDecodeError:
                existing = {}
            if isinstance(existing, dict):
                incoming = {**existing, **incoming}
        self.conn.execute(
            "INSERT INTO days (user_id, date, summary) VALUES (?,?,?) "
            "ON CONFLICT(user_id, date) DO UPDATE SET summary=excluded.summary",
            (user_id, incoming.get("date"), json.dumps(incoming)))

    def last_day(self, user_id: int) -> str | None:
        row = self.conn.execute("SELECT MAX(date) d FROM days WHERE user_id = ?",
                                (user_id,)).fetchone()
        return row["d"] if row and row["d"] else None

    def last_activity(self, user_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(substr(start, 1, 10)) d FROM activities "
            "WHERE user_id = ? AND start IS NOT NULL AND start != ''",
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

    # ---- Garmin extras ------------------------------------------------------
    def set_meta(self, user_id: int, key: str, value) -> None:
        """Store one of the per-sync extras (zones, records, predictions).

        Kept as a JSON blob per key rather than columns: Garmin adds and
        renames these metrics regularly, and none of them is worth a
        migration.
        """
        self.conn.execute(
            "INSERT INTO garmin_meta (user_id, key, value, updated) "
            "VALUES (?,?,?,?) ON CONFLICT(user_id, key) DO UPDATE SET "
            "value=excluded.value, updated=excluded.updated",
            (user_id, key, json.dumps(value), date.today().isoformat()))

    def meta(self, user_id: int) -> dict:
        rows = self.conn.execute(
            "SELECT key, value, updated FROM garmin_meta WHERE user_id = ?",
            (user_id,))
        out = {}
        for row in rows:
            try:
                out[row["key"]] = {"value": json.loads(row["value"]),
                                   "updated": row["updated"]}
            except json.JSONDecodeError:
                continue
        return out

    # ---- coach --------------------------------------------------------------
    def briefing(self, user_id: int, day: str, lang: str = "en") -> str | None:
        row = self.conn.execute(
            "SELECT text FROM briefings WHERE user_id = ? AND date = ? AND lang = ?",
            (user_id, day, lang)).fetchone()
        return row["text"] if row else None

    def save_briefing(self, user_id: int, day: str, text: str, created: str,
                      lang: str = "en") -> None:
        self.conn.execute(
            "INSERT INTO briefings (user_id, date, lang, text, created) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(user_id, date, lang) DO UPDATE SET text=excluded.text, "
            "created=excluded.created", (user_id, day, lang, text, created))
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

    # ---- athlete / races / plans -------------------------------------------
    def get_athlete(self, user_id: int) -> dict:
        row = self.conn.execute(
            "SELECT profile FROM athlete WHERE user_id = ?", (user_id,)).fetchone()
        return json.loads(row["profile"]) if row else {}

    def set_athlete(self, user_id: int, profile: dict) -> dict:
        self.conn.execute(
            "INSERT INTO athlete (user_id, profile, updated) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET profile=excluded.profile, "
            "updated=excluded.updated",
            (user_id, json.dumps(profile), date.today().isoformat()))
        self.commit()
        return profile

    def list_races(self, user_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT payload FROM races WHERE user_id = ? ORDER BY date ASC",
            (user_id,))
        return [json.loads(r["payload"]) for r in rows]

    def upsert_race(self, user_id: int, race: dict) -> dict:
        self.conn.execute(
            "INSERT INTO races (user_id, id, date, payload) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, id) DO UPDATE SET date=excluded.date, "
            "payload=excluded.payload",
            (user_id, race["id"], race["date"], json.dumps(race)))
        self.commit()
        return race

    def delete_race(self, user_id: int, race_id: str) -> None:
        self.conn.execute("DELETE FROM races WHERE user_id = ? AND id = ?",
                          (user_id, race_id))
        self.commit()

    def race(self, user_id: int, race_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT payload FROM races WHERE user_id = ? AND id = ?",
            (user_id, race_id)).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_plans(self, user_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT payload FROM plans WHERE user_id = ? ORDER BY created DESC",
            (user_id,))
        return [json.loads(r["payload"]) for r in rows]

    def save_plan(self, user_id: int, plan: dict) -> dict:
        self.conn.execute(
            "INSERT INTO plans (user_id, id, race_id, status, payload, created) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id, id) DO UPDATE SET "
            "race_id=excluded.race_id, status=excluded.status, "
            "payload=excluded.payload",
            (user_id, plan["id"], plan.get("race_id"), plan.get("status") or "draft",
             json.dumps(plan), plan.get("created") or date.today().isoformat()))
        for session in plan.get("sessions") or []:
            self.upsert_session(user_id, plan["id"], session)
        self.commit()
        return plan

    def get_plan(self, user_id: int, plan_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT payload FROM plans WHERE user_id = ? AND id = ?",
            (user_id, plan_id)).fetchone()
        if not row:
            return None
        plan = json.loads(row["payload"])
        plan["sessions"] = self.sessions_for_plan(user_id, plan_id)
        return plan

    def active_plan(self, user_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT id FROM plans WHERE user_id = ? AND status = 'active' "
            "ORDER BY created DESC LIMIT 1", (user_id,)).fetchone()
        return self.get_plan(user_id, row["id"]) if row else None

    def upsert_session(self, user_id: int, plan_id: str, session: dict) -> None:
        self.conn.execute(
            "INSERT INTO plan_sessions (user_id, id, plan_id, date, sport, kind, "
            "state, workout, est_load, garmin_workout_id, garmin_schedule_id, "
            "activity_id, revision, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id, id) DO UPDATE SET date=excluded.date, "
            "sport=excluded.sport, kind=excluded.kind, state=excluded.state, "
            "workout=excluded.workout, est_load=excluded.est_load, "
            "garmin_workout_id=excluded.garmin_workout_id, "
            "garmin_schedule_id=excluded.garmin_schedule_id, "
            "activity_id=excluded.activity_id, revision=excluded.revision, "
            "payload=excluded.payload",
            (user_id, session["id"], plan_id, session.get("date"),
             session.get("sport"), session.get("kind"),
             session.get("state") or "planned",
             json.dumps(session.get("workout") or {}),
             session.get("est_load"), session.get("garmin_workout_id"),
             session.get("garmin_schedule_id"), session.get("activity_id"),
             session.get("revision") or 1, json.dumps(session)))

    def sessions_for_plan(self, user_id: int, plan_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT payload FROM plan_sessions WHERE user_id = ? AND plan_id = ? "
            "ORDER BY date ASC", (user_id, plan_id))
        return [json.loads(r["payload"]) for r in rows]

    def session(self, user_id: int, session_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT payload FROM plan_sessions WHERE user_id = ? AND id = ?",
            (user_id, session_id)).fetchone()
        return json.loads(row["payload"]) if row else None

    def outbox(self, user_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT payload FROM plan_sessions WHERE user_id = ? AND state = "
            "'queued' ORDER BY date ASC", (user_id,))
        return [json.loads(r["payload"]) for r in rows]

    def list_templates(self, user_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, name, workout FROM workout_templates WHERE user_id = ? "
            "ORDER BY name", (user_id,))
        return [{"id": r["id"], "name": r["name"],
                 "workout": json.loads(r["workout"])} for r in rows]

    def upsert_template(self, user_id: int, tmpl: dict) -> dict:
        self.conn.execute(
            "INSERT INTO workout_templates (user_id, id, name, workout) "
            "VALUES (?,?,?,?) ON CONFLICT(user_id, id) DO UPDATE SET "
            "name=excluded.name, workout=excluded.workout",
            (user_id, tmpl["id"], tmpl["name"], json.dumps(tmpl["workout"])))
        self.commit()
        return tmpl

    def delete_template(self, user_id: int, tmpl_id: str) -> None:
        self.conn.execute(
            "DELETE FROM workout_templates WHERE user_id = ? AND id = ?",
            (user_id, tmpl_id))
        self.commit()

    def get_decision(self, user_id: int, day: str) -> dict | None:
        row = self.conn.execute(
            "SELECT payload FROM decisions WHERE user_id = ? AND date = ?",
            (user_id, day)).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_decision(self, user_id: int, day: str, payload: dict) -> dict:
        self.conn.execute(
            "INSERT INTO decisions (user_id, date, payload) VALUES (?,?,?) "
            "ON CONFLICT(user_id, date) DO UPDATE SET payload=excluded.payload",
            (user_id, day, json.dumps(payload)))
        self.commit()
        return payload

    # ---- direct Garmin connection ------------------------------------------
    def garmin_account(self, user_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM garmin_accounts WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return None
        account = dict(row)
        for key in ("last_result", "backfill"):
            try:
                account[key] = json.loads(account[key]) if account[key] else None
            except json.JSONDecodeError:
                account[key] = None
        return account

    def save_garmin_account(self, user_id: int, garmin_email: str,
                            tokens: str) -> None:
        """A fresh connection: new tokens, errors and history cursor reset."""
        self.conn.execute(
            "INSERT INTO garmin_accounts (user_id, garmin_email, tokens, created) "
            "VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "garmin_email=excluded.garmin_email, tokens=excluded.tokens, "
            "needs_login=0, last_error=NULL",
            (user_id, garmin_email, tokens, date.today().isoformat()))
        self.commit()

    def update_garmin_account(self, user_id: int, **fields) -> None:
        allowed = {"tokens", "last_sync", "last_result", "last_error",
                   "needs_login", "backfill"}
        sets, params = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"Unknown garmin_accounts field: {key}")
            if key in ("last_result", "backfill") and value is not None:
                value = json.dumps(value)
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return
        self.conn.execute(
            f"UPDATE garmin_accounts SET {', '.join(sets)} WHERE user_id = ?",
            (*params, user_id))
        self.commit()

    def delete_garmin_account(self, user_id: int) -> None:
        self.conn.execute("DELETE FROM garmin_accounts WHERE user_id = ?", (user_id,))
        self.commit()

    def garmin_connected_users(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT user_id, last_sync, needs_login, last_error, last_result "
            "FROM garmin_accounts")
        out = []
        for row in rows:
            account = dict(row)
            try:
                account["last_result"] = (
                    json.loads(account["last_result"]) if account["last_result"]
                    else None)
            except json.JSONDecodeError:
                account["last_result"] = None
            out.append(account)
        return out

    # ---- human coach / athlete links ---------------------------------------
    def _link_from_row(self, row) -> dict | None:
        if not row:
            return None
        return dict(row)

    def coaching_link(self, athlete_id: int, coach_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM coaching_links WHERE athlete_id = ? AND coach_id = ?",
            (athlete_id, coach_id)).fetchone()
        return self._link_from_row(row)

    def coaching_link_by_id(self, link_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM coaching_links WHERE id = ?", (link_id,)).fetchone()
        return self._link_from_row(row)

    def upsert_coaching_request(self, athlete_id: int, coach_id: int) -> dict:
        now = date.today().isoformat()
        existing = self.coaching_link(athlete_id, coach_id)
        if existing and existing["status"] == "accepted":
            return existing
        if existing:
            self.conn.execute(
                "UPDATE coaching_links SET status = 'pending', updated = ? "
                "WHERE id = ?", (now, existing["id"]))
            self.commit()
            return self.coaching_link_by_id(existing["id"])
        cursor = self.conn.execute(
            "INSERT INTO coaching_links (athlete_id, coach_id, status, created, updated) "
            "VALUES (?,?,?,?,?)",
            (athlete_id, coach_id, "pending", now, now))
        self.commit()
        return self.coaching_link_by_id(cursor.lastrowid)

    def set_coaching_status(self, link_id: int, status: str) -> dict | None:
        if status not in LINK_STATUSES:
            raise ValueError("Unknown coaching status.")
        now = date.today().isoformat()
        self.conn.execute(
            "UPDATE coaching_links SET status = ?, updated = ? WHERE id = ?",
            (status, now, link_id))
        self.commit()
        return self.coaching_link_by_id(link_id)

    def links_for_athlete(self, athlete_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM coaching_links WHERE athlete_id = ? "
            "ORDER BY updated DESC", (athlete_id,))
        return [dict(r) for r in rows]

    def links_for_coach(self, coach_id: int, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM coaching_links WHERE coach_id = ?"
        params: list = [coach_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY updated DESC"
        return [dict(r) for r in self.conn.execute(sql, tuple(params))]

    def add_coach_note(self, coach_id: int, athlete_id: int, kind: str,
                       text: str, session_id: str | None = None) -> dict:
        kind = kind if kind in NOTE_KINDS else "comment"
        created = date.today().isoformat()
        cursor = self.conn.execute(
            "INSERT INTO coach_notes (coach_id, athlete_id, kind, text, "
            "session_id, created) VALUES (?,?,?,?,?,?)",
            (coach_id, athlete_id, kind, text, session_id, created))
        self.commit()
        return self.coach_note(cursor.lastrowid)

    def coach_note(self, note_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM coach_notes WHERE id = ?", (note_id,)).fetchone()
        return dict(row) if row else None

    def notes_for_athlete(self, athlete_id: int,
                          coach_id: int | None = None) -> list[dict]:
        sql = "SELECT * FROM coach_notes WHERE athlete_id = ?"
        params: list = [athlete_id]
        if coach_id is not None:
            sql += " AND coach_id = ?"
            params.append(coach_id)
        sql += " ORDER BY id DESC"
        return [dict(r) for r in self.conn.execute(sql, tuple(params))]


@contextmanager
def open_store(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)
    try:
        yield store
    finally:
        store.close()
