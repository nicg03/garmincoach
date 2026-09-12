"""
Configuration, entirely from the environment (that's how Railway feeds it).

Almost nothing is required. Attach a volume and the app finds it:
`RAILWAY_VOLUME_MOUNT_PATH` is set for you at runtime. The session-signing
secret creates itself on first boot and lives in the database, so a fresh
deploy needs exactly one variable to be useful -- an API key for the coach
(`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`) -- and even that only switches
the coach on.

Locally nothing needs setting either: the site falls back to the same `data/`
folder the sync writes to, in its own `site.db` so the two never collide.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    for candidate in (os.environ.get("GARMIN_DATA_DIR"),
                      os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")):
        if candidate:
            return Path(candidate)
    return ROOT / "data"


DATA_DIR = _data_dir()

# Accounts and everyone's summaries. Deliberately not `garmin.db`, which is the
# single-user file the local sync owns.
DB_PATH = DATA_DIR / "site.db"

# Optional: overrides the secret stored in the database. Setting it invalidates
# every existing sign-in, which is the emergency lever if a cookie leaks.
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "30"))

# Anyone can sign up. Flip this to close the door without a redeploy.
SIGNUP_OPEN = os.environ.get("SIGNUP_OPEN", "1") != "0"
MIN_PASSWORD = int(os.environ.get("MIN_PASSWORD", "8"))

# Coach: OpenAI wins if both keys are set. Either one turns the coach on.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
COACH_MAX_TOKENS = int(os.environ.get("COACH_MAX_TOKENS", "1500"))

# Coach calls per user per day. Zero means no ceiling; the key is shared, so
# this is the dial to turn if the bill ever gets interesting.
COACH_DAILY_LIMIT = int(os.environ.get("COACH_DAILY_LIMIT", "0"))

# How much history the dashboard loads by default, and how much detail the
# coach gets before falling back to weekly aggregates.
DEFAULT_WINDOW_DAYS = int(os.environ.get("DEFAULT_WINDOW_DAYS", "90"))
COACH_DETAIL_DAYS = int(os.environ.get("COACH_DETAIL_DAYS", "90"))

# Shown to new users so they know where to get the sync tool.
SYNC_REPO_URL = os.environ.get(
    "SYNC_REPO_URL", "https://github.com/nicg03/garmincoach")


def coach_provider() -> str | None:
    if OPENAI_API_KEY:
        return "openai"
    if ANTHROPIC_API_KEY:
        return "anthropic"
    return None


def coach_enabled() -> bool:
    return coach_provider() is not None


def coach_model() -> str:
    if coach_provider() == "openai":
        return OPENAI_MODEL
    return ANTHROPIC_MODEL
