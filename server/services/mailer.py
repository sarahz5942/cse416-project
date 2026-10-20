"""Sending email. In development it just logs messages.

Settings (environment variables):
    EMAIL_MODE      "log" (default) prints emails to the server log;
                    "smtp" sends them for real
    SMTP_HOST       e.g. smtp.gmail.com
    SMTP_PORT       default 587
    SMTP_USERNAME / SMTP_PASSWORD
    SMTP_STARTTLS   "1" (default) or "0"
    EMAIL_FROM      e.g. "CampusReserve <noreply@example.edu>"
    APP_URL         link included in emails, default http://localhost:5173

Sending happens on a background thread so a slow mail server never delays
an API response.
"""

from __future__ import annotations

import logging
import os
import smtplib
import threading
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def app_url() -> str:
    return os.getenv("APP_URL", "http://localhost:5173").rstrip("/")


def _send_smtp(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = os.getenv("EMAIL_FROM", "CampusReserve <noreply@localhost>")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        if os.getenv("SMTP_STARTTLS", "1") == "1":
            smtp.starttls()
        user = os.getenv("SMTP_USERNAME")
        if user:
            smtp.login(user, os.getenv("SMTP_PASSWORD", ""))
        smtp.send_message(msg)


def _deliver(to: str, subject: str, body: str) -> None:
    mode = os.getenv("EMAIL_MODE", "log").lower()
    try:
        if mode == "smtp":
            _send_smtp(to, subject, body)
            logger.info("Emailed %s: %s", to, subject)
        else:
            logger.info("EMAIL (not sent, EMAIL_MODE=%s) to %s: %s\n%s", mode, to, subject, body)
    except Exception:  # never let email problems break the app
        logger.exception("Failed to email %s: %s", to, subject)


def send_email(to: str, subject: str, body: str) -> None:
    """Send in the background. Returns immediately."""
    if not to:
        return
    threading.Thread(target=_deliver, args=(to, subject, body), daemon=True).start()
