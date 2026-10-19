import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from bizviz.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    LoginRateLimiter,
    check_dashboard_password,
    create_session,
    hash_password,
    list_sessions,
    revoke_all,
    revoke_session,
    set_dashboard_password,
    validate_password,
    validate_session,
    verify_password,
)
from bizviz.db.connection import Connection

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)


def test_hash_and_verify_round_trip() -> None:
    hash_hex, salt_hex = hash_password("correct horse battery")
    assert len(bytes.fromhex(salt_hex)) == 16
    assert len(bytes.fromhex(hash_hex)) == 32
    assert verify_password("correct horse battery", hash_hex, salt_hex)
    assert not verify_password("wrong horse battery", hash_hex, salt_hex)
    assert not verify_password("correct horse battery", hash_hex, "zz")
    assert hash_password("correct horse battery")[1] != salt_hex


def test_hash_matches_documented_scrypt_parameters() -> None:
    hash_hex, salt_hex = hash_password("some long password")
    expected = hashlib.scrypt(
        b"some long password",
        salt=bytes.fromhex(salt_hex),
        n=2**15,
        r=8,
        p=1,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    )
    assert hash_hex == expected.hex()


def test_validate_password_length() -> None:
    with pytest.raises(ValueError, match="password_too_short"):
        validate_password("123456789")
    validate_password("1234567890")


def test_dashboard_password_storage(db: Connection) -> None:
    assert not check_dashboard_password(db, "anything at all")
    with pytest.raises(ValueError, match="password_too_short"):
        set_dashboard_password(db, "short")
    set_dashboard_password(db, "a long enough pw")
    assert check_dashboard_password(db, "a long enough pw")
    assert not check_dashboard_password(db, "another long pw")
    set_dashboard_password(db, "replaced password")
    assert not check_dashboard_password(db, "a long enough pw")
    assert check_dashboard_password(db, "replaced password")


def test_cookie_constants() -> None:
    assert SESSION_COOKIE == "bv_session"
    assert SESSION_MAX_AGE == 400 * 24 * 3600


def test_create_session_stores_only_hash(db: Connection) -> None:
    token, sid = create_session(db, "iPhone", "UA/1", NOW)
    assert len(token) >= 43
    row = db.execute("SELECT * FROM device_sessions WHERE session_id = ?", (sid,)).fetchone()
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in tuple(row)
    assert row["created_at"] == row["last_seen_at"] == "2026-10-15T15:00:00Z"
    assert row["expires_at"] == (NOW + timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert row["revoked_at"] is None
    other, _ = create_session(db, None, None, NOW)
    assert other != token


def test_validate_session_outcomes(db: Connection) -> None:
    token, sid = create_session(db, "iPhone", "UA", NOW)
    info = validate_session(db, token, NOW + timedelta(minutes=1))
    assert info is not None
    assert (info.session_id, info.device_name) == (sid, "iPhone")
    assert info.created_at == "2026-10-15T15:00:00Z"
    assert validate_session(db, "unknown-token", NOW) is None
    assert validate_session(db, token, NOW + timedelta(days=400)) is None
    revoke_session(db, sid)
    assert validate_session(db, token, NOW + timedelta(minutes=1)) is None


def test_validate_session_touches_at_most_hourly(db: Connection) -> None:
    token, sid = create_session(db, "iPhone", "UA", NOW)

    def row() -> tuple[str, str]:
        r = db.execute(
            "SELECT last_seen_at, expires_at FROM device_sessions WHERE session_id = ?", (sid,)
        ).fetchone()
        return r["last_seen_at"], r["expires_at"]

    initial = row()
    assert validate_session(db, token, NOW + timedelta(minutes=59, seconds=59))
    assert row() == initial
    later = NOW + timedelta(hours=1)
    assert validate_session(db, token, later)
    assert row() == (
        "2026-10-15T16:00:00Z",
        (later + timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    touched = row()
    assert validate_session(db, token, later + timedelta(minutes=30))
    assert row() == touched


def test_sliding_expiry_keeps_session_alive(db: Connection) -> None:
    token, _ = create_session(db, "iPhone", "UA", NOW)
    t = NOW
    for _ in range(3):
        t += timedelta(days=300)
        assert validate_session(db, token, t)
    assert validate_session(db, token, t + timedelta(days=401)) is None


def test_revoke_all_and_list(db: Connection) -> None:
    t1, s1 = create_session(db, "one", "ua1", NOW)
    t2, s2 = create_session(db, "two", "ua2", NOW + timedelta(minutes=1))
    t3, s3 = create_session(db, "three", "ua3", NOW + timedelta(minutes=2))
    listed = list_sessions(db)
    assert [s["session_id"] for s in listed] == [s3, s2, s1]
    assert set(listed[0]) == {
        "session_id",
        "device_name",
        "user_agent",
        "created_at",
        "last_seen_at",
    }

    revoke_session(db, s2)
    assert [s["session_id"] for s in list_sessions(db)] == [s3, s1]

    revoke_all(db, except_session_id=s3)
    assert [s["session_id"] for s in list_sessions(db)] == [s3]
    assert validate_session(db, t3, NOW + timedelta(minutes=3))
    assert validate_session(db, t1, NOW + timedelta(minutes=3)) is None

    revoke_all(db)
    assert list_sessions(db) == []
    assert validate_session(db, t3, NOW + timedelta(minutes=3)) is None
    assert t2


def test_list_sessions_excludes_expired(db: Connection) -> None:
    create_session(db, "old", "ua", datetime(2020, 1, 1, tzinfo=UTC))
    _, live = create_session(db, "new", "ua", datetime.now(UTC))
    assert [s["session_id"] for s in list_sessions(db)] == [live]


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


def _limiter() -> tuple[LoginRateLimiter, Clock]:
    clock = Clock()
    return LoginRateLimiter(now=clock), clock


def test_per_ip_lockout_for_15_minutes() -> None:
    rl, clock = _limiter()
    ip = "192.168.1.20"
    for _ in range(4):
        rl.record_failure(ip)
        clock.t += 1
    assert rl.check(ip) is None
    rl.record_failure(ip)
    wait = rl.check(ip)
    assert wait is not None and 899 <= wait <= 900
    assert rl.check("192.168.1.21") is None
    clock.t += 899
    assert rl.check(ip) is not None
    clock.t += 2
    assert rl.check(ip) is None
    assert rl.global_locked_until() is None


def test_failures_outside_window_do_not_count() -> None:
    rl, clock = _limiter()
    ip = "10.0.0.5"
    for _ in range(4):
        rl.record_failure(ip)
    clock.t += 15 * 60 + 1
    rl.record_failure(ip)
    assert rl.check(ip) is None


@pytest.mark.parametrize("ip", ["127.0.0.1", "::1", "::ffff:127.0.0.1", "127.5.4.3"])
def test_loopback_never_locked(ip: str) -> None:
    rl, _ = _limiter()
    for _ in range(50):
        rl.record_failure(ip)
    assert rl.check(ip) is None
    assert rl.global_locked_until() is None
    assert rl.check("192.168.1.9") is None


def test_success_clears_ip_counter() -> None:
    rl, _ = _limiter()
    ip = "192.168.1.30"
    for _ in range(4):
        rl.record_failure(ip)
    rl.record_success(ip)
    for _ in range(4):
        rl.record_failure(ip)
    assert rl.check(ip) is None
    rl.record_failure(ip)
    assert rl.check(ip) is not None


def test_global_lock_from_rotating_ips() -> None:
    rl, clock = _limiter()
    start = clock.t
    for i in range(19):
        rl.record_failure(f"10.1.0.{i}")
        clock.t += 10
    assert rl.global_locked_until() is None
    assert rl.check("10.9.9.9") is None
    rl.record_failure("10.1.0.200")
    locked_until = rl.global_locked_until()
    assert locked_until == pytest.approx(clock.t + 3600)
    assert start < locked_until
    wait = rl.check("10.9.9.9")
    assert wait is not None and wait == pytest.approx(3600)
    assert rl.check("127.0.0.1") is None
    assert rl.check("::1") is None
    clock.t += 3601
    assert rl.check("10.9.9.9") is None
    assert rl.global_locked_until() is None


def test_global_window_slides() -> None:
    rl, clock = _limiter()
    for i in range(19):
        rl.record_failure(f"10.2.0.{i}")
    clock.t += 3601
    rl.record_failure("10.2.1.1")
    assert rl.global_locked_until() is None
