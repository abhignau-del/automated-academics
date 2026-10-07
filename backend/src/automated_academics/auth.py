"""Accounts for the shared (server) mode: password hashing, sign-in tokens and login throttling.

Standard library only. Passwords are stored as salted scrypt hashes; a sign-in token is a random string
handed to the browser in an HttpOnly cookie, and only its SHA-256 is stored, so a copy of the database
cannot be used to sign in.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass

MIN_PASSWORD_LEN = 8
SESSION_DAYS = 14.0
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._@+-]{2,63}$")
_N, _R, _P = 2 ** 14, 8, 1  # scrypt cost: about 50 ms and 16 MB per check


@dataclass(frozen=True)
class User:
    id: str
    username: str
    display_name: str
    role: str  # "admin" (manages users, sees everything) or "member"
    disabled: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def public(self) -> dict:
        return {"id": self.id, "username": self.username, "display_name": self.display_name, "role": self.role}


# Used when sign-in is switched off (the single-user desktop app): everyone is this administrator.
LOCAL_USER = User(id="local", username="local", display_name="Local user", role="admin")


def user_from_row(row: dict) -> User:
    return User(id=row["id"], username=row["username"], display_name=row["display_name"],
                role=row["role"], disabled=bool(row["disabled"]))


def normalise_username(raw: str) -> str:
    name = raw.strip().lower()
    if not _USERNAME.match(name):
        raise ValueError("a username is 3-64 letters, digits or . _ @ + - (starting with a letter or digit)")
    return name


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"a password needs at least {MIN_PASSWORD_LEN} characters")
    if len(password) > 256:
        raise ValueError("that password is too long")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# One check that always runs, so "no such user" and "wrong password" take the same time.
_DUMMY = hash_password(secrets.token_urlsafe(8))


def burn_time() -> None:
    verify_password("x", _DUMMY)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginThrottle:
    """Slows down password guessing: after `limit` failures for one name and address, wait `lock_s` seconds."""

    def __init__(self, limit: int = 5, lock_s: float = 60.0, clock=time.monotonic):
        self.limit, self.lock_s, self.clock = limit, lock_s, clock
        self._fails: dict[str, tuple[int, float]] = {}

    def wait_s(self, key: str) -> float:
        count, until = self._fails.get(key, (0, 0.0))
        return max(0.0, until - self.clock()) if count >= self.limit else 0.0

    def failed(self, key: str) -> None:
        count, until = self._fails.get(key, (0, 0.0))
        if count >= self.limit and self.clock() >= until:
            count = 0  # the lock ran out: start counting again
        count += 1
        self._fails[key] = (count, self.clock() + self.lock_s if count >= self.limit else 0.0)
        if len(self._fails) > 10_000:  # never grow without bound
            self._fails.clear()

    def succeeded(self, key: str) -> None:
        self._fails.pop(key, None)
