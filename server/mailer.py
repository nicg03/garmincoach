"""
Account emails, sent through Resend's HTTP API.

Plain text only: four short messages don't need templates or a dependency.
Without `RESEND_API_KEY` the message is printed to the log, so the links still
work when running locally.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

from . import config

RESEND_URL = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 10


def _tls() -> ssl.SSLContext:
    # python.org builds on macOS ship without system certificates.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _log(message: str) -> None:
    print(f"[garmin-sync] email: {message}", flush=True)


def send(to: str, subject: str, text: str) -> bool:
    """Deliver one message. Failures are logged, never raised: callers run
    this in the background after the response has gone out."""
    if not config.email_enabled():
        _log(f"not configured, would send to {to}: {subject}\n{text}")
        return False
    body = json.dumps({"from": config.EMAIL_FROM, "to": [to],
                       "subject": subject, "text": text}).encode()
    request = urllib.request.Request(RESEND_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {config.RESEND_API_KEY}",
        "Content-Type": "application/json",
        # Resend's edge rejects urllib's default agent with a bare 403.
        "User-Agent": "gepard.fit/1.0",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS,
                                    context=_tls()) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        _log(f"Resend refused the message to {to}: {e.code} {detail}")
    except (urllib.error.URLError, TimeoutError) as e:
        _log(f"could not reach Resend for {to}: {e}")
    return False


SIGNATURE = "\n\n-- gepard.fit"


def verify_email(to: str, link: str) -> bool:
    return send(to, "Confirm your email for gepard.fit",
                "Confirm this is your address so you can reset your password "
                f"if you ever need to:\n\n{link}\n\nThe link works for 48 hours. "
                "If you didn't sign up, ignore this email." + SIGNATURE)


def reset_password(to: str, link: str) -> bool:
    return send(to, "Reset your gepard.fit password",
                f"Choose a new password here:\n\n{link}\n\nThe link works for "
                "one hour and only once. If you didn't ask for this, ignore this "
                "email; your password stays the same." + SIGNATURE)


def confirm_new_email(to: str, link: str) -> bool:
    return send(to, "Confirm your new email for gepard.fit",
                "Confirm that gepard.fit should use this address from now on:"
                f"\n\n{link}\n\nThe link works for 24 hours. Until you confirm, "
                "your old address stays in place." + SIGNATURE)


def feedback_received(to: str, sender: str, text: str, page: str) -> bool:
    return send(to, f"Beta feedback from {sender}",
                f"{sender} wrote{f' on {page}' if page else ''}:\n\n{text}"
                + SIGNATURE)


def account_changed(to: str, what: str) -> bool:
    return send(to, f"Your gepard.fit {what} was changed",
                f"The {what} of your gepard.fit account was just changed, and "
                "other devices were signed out.\n\nIf this wasn't you, reset "
                "your password straight away from the sign-in page." + SIGNATURE)
