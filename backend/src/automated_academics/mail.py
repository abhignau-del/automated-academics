"""Sending the one kind of e-mail the app sends: the link for choosing a new password.

Standard library only. Switched on by environment variables; with none set, the app simply has no "forgot password" and
an administrator sets passwords by hand, as before:

    AA_SMTP_HOST      the mail server (required to switch it on)
    AA_SMTP_PORT      default 587 (465 with AA_SMTP_SECURITY=ssl)
    AA_SMTP_USER / AA_SMTP_PASSWORD    login for the mail server (optional)
    AA_SMTP_SECURITY  starttls (default), ssl or none
    AA_SMTP_FROM      the sender address (default: AA_SMTP_USER)
    AA_PUBLIC_URL     the address people use to reach the app, e.g. https://timetable.college.edu (required: links
                      are never built from the request, so a forged Host header cannot point them elsewhere)
"""

from __future__ import annotations

import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalise_email(raw: str) -> str:
    """Lower-cased, or ValueError. An empty string is allowed and means "no e-mail address"."""
    email = raw.strip().lower()
    if email and (len(email) > 254 or not _EMAIL.match(email)):
        raise ValueError("that doesn't look like an e-mail address")
    return email


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


@dataclass(frozen=True)
class SmtpMailer:
    host: str
    port: int
    sender: str
    user: str = ""
    password: str = ""
    security: str = "starttls"  # starttls | ssl | none

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        ctx = ssl.create_default_context()
        if self.security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=20, context=ctx)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=20)
        with server:
            if self.security == "starttls":
                server.starttls(context=ctx)
            if self.user:
                server.login(self.user, self.password)
            server.send_message(msg)

    @classmethod
    def from_env(cls) -> SmtpMailer | None:
        host = os.environ.get("AA_SMTP_HOST", "").strip()
        if not host:
            return None
        security = os.environ.get("AA_SMTP_SECURITY", "starttls").strip().lower()
        if security not in ("starttls", "ssl", "none"):
            raise ValueError("AA_SMTP_SECURITY must be starttls, ssl or none")
        user = os.environ.get("AA_SMTP_USER", "").strip()
        sender = os.environ.get("AA_SMTP_FROM", "").strip() or user
        if not sender:
            raise ValueError("set AA_SMTP_FROM (the address the e-mails come from)")
        port = int(os.environ.get("AA_SMTP_PORT", "") or (465 if security == "ssl" else 587))
        return cls(host, port, sender, user, os.environ.get("AA_SMTP_PASSWORD", ""), security)


def reset_message(name: str, link: str, minutes: int) -> tuple[str, str]:
    return ("Choose a new password for Automated Academics",
            f"Hello {name},\n\nSomeone (hopefully you) asked to reset your Automated Academics password.\n"
            f"To choose a new one, open this link within {minutes} minutes:\n\n{link}\n\n"
            "If you didn't ask for this, ignore this e-mail: your password has not changed.\n")
