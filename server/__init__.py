"""
The deployed half of garmincoach: a small FastAPI site that shows your
Garmin history and lets you ask Claude about it.

It never talks to Garmin. Your Mac does that (Playwright needs a real browser
and a Cloudflare clearance tied to your IP) and pushes the summaries here via
`POST /api/ingest`. This side only reads the database, computes metrics and
calls Anthropic.
"""
__all__ = ["main"]
