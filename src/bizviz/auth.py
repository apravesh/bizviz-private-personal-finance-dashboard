import hashlib
import hmac
import ipaddress
import os
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bizviz.db.connection import Connection, get_setting, set_setting, write_unit

SESSION_COOKIE = "bv_session"
SESSION_MAX_AGE = 34560000
SESSION_LIFETIME = timedelta(days=400)
SESSION_TOUCH_INTERVAL = timedelta(hours=1)

MIN_PASSWORD_LENGTH = 10
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_SALT_BYTES = 16

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def validate_password(pw: str) -> None:
    if len(pw) < MIN_PASSWORD_LENGTH:
        raise ValueError("password_too_short")


def _scrypt(pw: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        pw.encode(),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )


def hash_password(pw: str) -> tuple[str, str]:
    salt = os.urandom(_SALT_BYTES)
    return _scrypt(pw, salt).hex(), salt.hex()


def verify_password(pw: str, hash_hex: str, salt_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    return hmac.compare_digest(_scrypt(pw, salt), expected)


def set_dashboard_password(conn: Connection, pw: str) -> None:
    validate_password(pw)
    hash_hex, salt_hex = hash_password(pw)
    with write_unit(conn):
        set_setting(conn, "password_hash", hash_hex)
        set_setting(conn, "password_salt", salt_hex)


def check_dashboard_password(conn: Connection, pw: str) -> bool:
    hash_hex = get_setting(conn, "password_hash")
    salt_hex = get_setting(conn, "password_salt")
    if hash_hex is None or salt_hex is None:
        return False
    return verify_password(pw, hash_hex, salt_hex)


@dataclass(frozen=True)
class SessionInfo:
    session_id: int
    device_name: str | None
    created_at: str


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime(_TS_FORMAT)


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, _TS_FORMAT).replace(tzinfo=UTC)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(
    conn: Connection, device_name: str | None, user_agent: str | None, now: datetime
) -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    stamp = _iso(now)
    with write_unit(conn):
        cur = conn.execute(
            "INSERT INTO device_sessions(token_hash, device_name, user_agent, created_at, "
            "last_seen_at, expires_at) VALUES(?, ?, ?, ?, ?, ?)",
            (
                _token_hash(token),
                device_name,
                user_agent,
                stamp,
                stamp,
                _iso(now + SESSION_LIFETIME),
            ),
        )
        session_id = int(cur.lastrowid)
    return token, session_id


def validate_session(conn: Connection, token: str, now: datetime) -> SessionInfo | None:
    row = conn.execute(
        "SELECT session_id, device_name, created_at, last_seen_at, expires_at, revoked_at "
        "FROM device_sessions WHERE token_hash = ?",
        (_token_hash(token),),
    ).fetchone()
    if row is None or row["revoked_at"] is not None:
        return None
    if _parse(row["expires_at"]) <= now:
        return None
    if now - _parse(row["last_seen_at"]) >= SESSION_TOUCH_INTERVAL:
        with write_unit(conn):
            conn.execute(
                "UPDATE device_sessions SET last_seen_at = ?, expires_at = ? "
                "WHERE session_id = ? AND revoked_at IS NULL",
                (_iso(now), _iso(now + SESSION_LIFETIME), row["session_id"]),
            )
    return SessionInfo(
        session_id=int(row["session_id"]),
        device_name=row["device_name"],
        created_at=row["created_at"],
    )


def revoke_session(conn: Connection, session_id: int) -> None:
    with write_unit(conn):
        conn.execute(
            "UPDATE device_sessions SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
            (_iso(datetime.now(UTC)), session_id),
        )


def revoke_all(conn: Connection, except_session_id: int | None = None) -> None:
    with write_unit(conn):
        conn.execute(
            "UPDATE device_sessions SET revoked_at = ? WHERE revoked_at IS NULL "
            "AND (? IS NULL OR session_id != ?)",
            (_iso(datetime.now(UTC)), except_session_id, except_session_id),
        )


def list_sessions(conn: Connection) -> list[dict[str, Any]]:
    now = _iso(datetime.now(UTC))
    rows = conn.execute(
        "SELECT session_id, device_name, user_agent, created_at, last_seen_at "
        "FROM device_sessions WHERE revoked_at IS NULL AND expires_at > ? "
        "ORDER BY created_at DESC, session_id DESC",
        (now,),
    ).fetchall()
    return [
        {
            "session_id": int(r["session_id"]),
            "device_name": r["device_name"],
            "user_agent": r["user_agent"],
            "created_at": r["created_at"],
            "last_seen_at": r["last_seen_at"],
        }
        for r in rows
    ]


_IP_FAILURE_LIMIT = 5
_IP_WINDOW = 15 * 60.0
_IP_LOCK = 15 * 60.0
_GLOBAL_FAILURE_LIMIT = 20
_GLOBAL_WINDOW = 60 * 60.0
_GLOBAL_LOCK = 60 * 60.0


def is_loopback(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_loopback


class LoginRateLimiter:
    def __init__(self, now: Callable[[], float] = time.time) -> None:
        self._now = now
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._global_failures: deque[float] = deque()
        self._global_locked_until: float | None = None

    def _prune(self, now: float) -> None:
        for ip in list(self._locked_until):
            if self._locked_until[ip] <= now:
                del self._locked_until[ip]
        for ip in list(self._failures):
            window = self._failures[ip]
            while window and window[0] <= now - _IP_WINDOW:
                window.popleft()
            if not window:
                del self._failures[ip]
        while self._global_failures and self._global_failures[0] <= now - _GLOBAL_WINDOW:
            self._global_failures.popleft()
        if self._global_locked_until is not None and self._global_locked_until <= now:
            self._global_locked_until = None

    def check(self, ip: str) -> float | None:
        if is_loopback(ip):
            return None
        with self._lock:
            now = self._now()
            self._prune(now)
            waits = [self._locked_until[ip] - now] if ip in self._locked_until else []
            if self._global_locked_until is not None:
                waits.append(self._global_locked_until - now)
            return max(waits) if waits else None

    def record_failure(self, ip: str) -> None:
        if is_loopback(ip):
            return
        with self._lock:
            now = self._now()
            self._prune(now)
            if ip in self._locked_until or self._global_locked_until is not None:
                return
            window = self._failures.setdefault(ip, deque())
            window.append(now)
            if len(window) >= _IP_FAILURE_LIMIT:
                self._locked_until[ip] = now + _IP_LOCK
                del self._failures[ip]
            self._global_failures.append(now)
            if len(self._global_failures) >= _GLOBAL_FAILURE_LIMIT:
                self._global_locked_until = now + _GLOBAL_LOCK
                self._global_failures.clear()

    def record_success(self, ip: str) -> None:
        if is_loopback(ip):
            return
        with self._lock:
            self._failures.pop(ip, None)

    def global_locked_until(self) -> float | None:
        with self._lock:
            self._prune(self._now())
            return self._global_locked_until
