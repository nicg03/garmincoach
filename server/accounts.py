"""
The emailed side of accounts: verification, password reset and email change.

Every link carries a single-use random token; the database keeps only its
hash (`Store.create_email_token`). Links point at hash routes such as
`/#/reset/<token>`, so nothing happens until the page's JavaScript posts the
token back -- a mail scanner that prefetches links can't burn or use them.

The senders here run as background tasks, after the response has gone out.
That keeps the forgot-password endpoint equally fast whether or not the
address has an account.
"""
from __future__ import annotations

import time

from fastapi import Request

from . import config, db, mailer

# At most one email of a kind per account per minute.
RESEND_COOLDOWN = 60

_warned_origin = False


def base_url(request: Request) -> str:
    global _warned_origin
    if config.PUBLIC_URL:
        return config.PUBLIC_URL
    if not _warned_origin:
        _warned_origin = True
        print("[garmin-sync] PUBLIC_URL is not set: email links use the "
              "request's own address.", flush=True)
    return str(request.base_url).rstrip("/")


def link(base: str, route: str, token: str) -> str:
    return f"{base}/#/{route}/{token}"


def recently_sent(user_id: int, purpose: str) -> bool:
    with db.store() as handle:
        last = handle.last_email_token(user_id, purpose)
    return last is not None and time.time() - last < RESEND_COOLDOWN


def send_verification(base: str, user_id: int) -> None:
    with db.store() as handle:
        user = handle.user_by_id(user_id)
        if not user or user["email_verified"]:
            return
        # The address rides along so a link sent before an email change
        # can't verify the new one.
        token = handle.create_email_token(user_id, "verify", user["email"])
    mailer.verify_email(user["email"], link(base, "verify", token))


def send_reset(base: str, email: str) -> None:
    with db.store() as handle:
        user = handle.user_by_email(email)
    if not user or recently_sent(user["id"], "reset"):
        return
    with db.store() as handle:
        token = handle.create_email_token(user["id"], "reset")
    mailer.reset_password(user["email"], link(base, "reset", token))


def send_email_change(base: str, user_id: int, new_email: str) -> None:
    with db.store() as handle:
        token = handle.create_email_token(user_id, "email_change", new_email)
    mailer.confirm_new_email(new_email, link(base, "email", token))


def notify_change(email: str, what: str) -> None:
    mailer.account_changed(email, what)
