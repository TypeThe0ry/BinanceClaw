import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from binanceclaw.api.app import create_app
from binanceclaw.config import Secrets
from binanceclaw.core.service import Service

from .conftest import FakeSpot, plan, use


@pytest.fixture
def client(tmp_path):
    sec = Secrets(admin_password_hash=PasswordHasher().hash("correct-horse-1"), session_secret="x" * 64,
                  binance_api_key="k", binance_api_secret="s")
    svc = Service(data_dir=tmp_path, secrets=sec, client=FakeSpot())
    c = TestClient(create_app(svc))
    c.svc = svc
    return c


def test_auth_required(client):
    assert client.get("/api/overview").status_code == 401
    assert client.post("/api/control/kill").status_code == 401
    assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "correct-horse-1"}).status_code == 200
    assert client.get("/api/overview").status_code == 200


def test_secrets_never_exposed(client):
    client.post("/api/auth/login", json={"password": "correct-horse-1"})
    body = client.get("/api/settings").text
    assert "correct-horse" not in body and '"k"' not in body and "x" * 64 not in body
    assert client.get("/api/settings").json()["secrets"]["binance_api_key"] is True


def test_approve_via_api_and_mode_gate(client):
    client.post("/api/auth/login", json={"password": "correct-horse-1"})
    use(client.svc, plan(("BUY", "BTCUSDT", 600)))
    aid = client.svc.run_cycle("manual").risk[0]["approval_id"]
    r = client.post(f"/api/approvals/{aid}/approve").json()
    assert r["ok"], r
    assert client.put("/api/settings", json={"mode": "live"}).json()["mode"] == "paper"
    assert client.post("/api/mode", json={"mode": "live", "confirm": "no"}).json()["ok"] is False


def test_brute_force_lockout(client):
    for _ in range(5):
        client.post("/api/auth/login", json={"password": "nope"})
    assert client.post("/api/auth/login", json={"password": "correct-horse-1"}).status_code == 429


def test_log_redaction():
    import logging

    from binanceclaw.cli import RedactFilter
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "POST https://api.telegram.org/bot%s:%s/sendMessage key=%s",
                            ("1234567890", "FAKEfakeFAKEfakeFAKEfakeFAKEfake00", "sk-abcdefghijklmnopqrstu"), None)
    RedactFilter().filter(rec)
    out = rec.getMessage()
    assert "FAKEfake" not in out and "sk-abcdef" not in out and "<redacted>" in out


def test_concurrent_login_cannot_bypass_lockout(client):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=20) as ex:
        codes = list(ex.map(lambda _: client.post("/api/auth/login", json={"password": "nope"}).status_code, range(20)))
    assert codes.count(401) <= 5 and codes.count(429) >= 15


def _ip_app(app):
    """Test-only ASGI wrapper: take the client address from an X-Test-IP header."""
    async def asgi(scope, receive, send):
        if scope["type"] == "http":
            ip = dict(scope["headers"]).get(b"x-test-ip")
            if ip:
                scope = dict(scope, client=(ip.decode(), 1))
        await app(scope, receive, send)
    return asgi


def _login(tc, ip, pw, device=None):
    tc.cookies.clear()
    if device:
        tc.cookies.set("bclaw_device", device)
    return tc.post("/api/auth/login", json={"password": pw}, headers={"X-Test-IP": ip})


def test_global_pressure_serialises_and_never_locks_out_owner(client, monkeypatch):
    import time as _t
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    from binanceclaw.api import app as appmod
    monkeypatch.setitem(appmod.LOGIN_LIMITS, "global", 2)
    monkeypatch.setitem(appmod.LOGIN_LIMITS, "slow_lane_wait", 0.5)
    monkeypatch.setitem(appmod.LOGIN_LIMITS, "fail_delay", 0.0)
    with TestClient(_ip_app(client.app)) as tc:           # one event loop, like uvicorn
        for i in range(2):                                 # push the service under global pressure
            tc.post("/api/auth/login", json={"password": "nope"}, headers={"X-Test-IP": f"198.51.100.{i}"})
        t0 = _t.time()
        with ThreadPoolExecutor(max_workers=4) as ex:      # 4 different "attackers" at once
            list(ex.map(lambda i: tc.post("/api/auth/login", json={"password": "nope"},
                                          headers={"X-Test-IP": f"198.51.100.{10 + i}"}), range(4)))
        assert _t.time() - t0 >= 4 * 0.5 - 0.05             # serialised, not parallel sleeps
        assert _login(tc, "198.51.100.99", "correct-horse-1").status_code == 200   # queued, not locked out


def test_queue_timeout_does_not_consume_attempts_and_known_device_skips_lane(client, monkeypatch):
    import time as _t
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    from binanceclaw.api import app as appmod
    with TestClient(_ip_app(client.app)) as tc:
        r = _login(tc, "192.0.2.7", "correct-horse-1")
        assert r.status_code == 200
        device = tc.cookies.get("bclaw_device")
        assert device
        monkeypatch.setitem(appmod.LOGIN_LIMITS, "global", 1)
        monkeypatch.setitem(appmod.LOGIN_LIMITS, "slow_lane_wait", 0.6)
        monkeypatch.setitem(appmod.LOGIN_LIMITS, "queue_timeout", 0.05)
        monkeypatch.setitem(appmod.LOGIN_LIMITS, "fail_delay", 0.0)
        tc.cookies.clear()
        tc.post("/api/auth/login", json={"password": "nope"}, headers={"X-Test-IP": "198.51.100.1"})   # pressure on
        with ThreadPoolExecutor(max_workers=6) as ex:
            codes = list(ex.map(lambda _: tc.post("/api/auth/login", json={"password": "nope"},
                                                  headers={"X-Test-IP": "203.0.113.5"}).status_code, range(6)))
        assert codes.count(429) >= 4                      # most could not get a slot ...
        # ... and those timeouts were not charged, so this source is not locked out
        assert _login(tc, "203.0.113.5", "correct-horse-1").status_code == 200
        # a known device skips the slow lane entirely
        t0 = _t.time()
        assert _login(tc, "192.0.2.7", "correct-horse-1", device=device).status_code == 200
        assert _t.time() - t0 < 0.5
