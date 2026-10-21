import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from binanceclaw.api.app import create_app
from binanceclaw.config import Secrets
from binanceclaw.core.service import Service

from .conftest import FakeFutures, FakeSpot, plan, use


@pytest.fixture
def client(tmp_path):
    sec = Secrets(admin_password_hash=PasswordHasher().hash("correct-horse-1"), session_secret="x" * 64,
                  binance_api_key="k", binance_api_secret="s")
    svc = Service(data_dir=tmp_path, secrets=sec, client=FakeSpot(), fclient=FakeFutures())
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


def _dev(tc):
    vals = [c.value for c in tc.cookies.jar if c.name == "bclaw_device" and c.value]
    return vals[-1] if vals else None


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
        device = _dev(tc)
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


def test_device_trust_is_revocable(client, monkeypatch):
    from argon2 import PasswordHasher
    from fastapi.testclient import TestClient

    from binanceclaw.api import app as appmod
    with TestClient(_ip_app(client.app)) as tc:
        assert _login(tc, "192.0.2.8", "correct-horse-1").status_code == 200
        dev = _dev(tc)
        assert len(tc.get("/api/devices").json()) == 1
        # logout revokes the device on the server and clears the cookie
        tc.cookies.clear()
        tc.cookies.set("bclaw_device", dev)
        tc.post("/api/auth/logout")
        assert client.svc.db.kv_get("trusted_devices") == {}
        # a revoked token no longer skips the slow lane
        monkeypatch.setitem(appmod.LOGIN_LIMITS, "global", 0)
        monkeypatch.setitem(appmod.LOGIN_LIMITS, "slow_lane_wait", 0.4)
        import time as _t
        t0 = _t.time()
        assert _login(tc, "192.0.2.8", "correct-horse-1", device=dev).status_code == 200
        assert _t.time() - t0 >= 0.4
        # changing the password invalidates every existing device token
        dev2 = _dev(tc)
        client.svc.secrets.admin_password_hash = PasswordHasher().hash("another-pass-99")
        t0 = _t.time()
        assert _login(tc, "192.0.2.8", "another-pass-99", device=dev2).status_code == 200
        assert _t.time() - t0 >= 0.4


def test_trusted_devices_have_their_own_budget(client, monkeypatch):
    import time as _t

    from fastapi.testclient import TestClient

    from binanceclaw.api import app as appmod
    with TestClient(_ip_app(client.app)) as tc:
        assert _login(tc, "192.0.2.9", "correct-horse-1").status_code == 200
        dev = _dev(tc)
        monkeypatch.setitem(appmod.LOGIN_LIMITS, "global", 0)
        monkeypatch.setitem(appmod.LOGIN_LIMITS, "trusted_global", 2)
        monkeypatch.setitem(appmod.LOGIN_LIMITS, "slow_lane_wait", 0.4)
        monkeypatch.setitem(appmod.LOGIN_LIMITS, "fail_delay", 0.0)
        for i in range(2):   # leaked token used from other addresses
            _login(tc, f"203.0.113.{20 + i}", "wrong", device=dev)
        t0 = _t.time()
        _login(tc, "203.0.113.30", "wrong", device=dev)
        assert _t.time() - t0 >= 0.4          # budget spent: even the device token now waits in line



def test_revoking_devices_ends_their_sessions(client):
    from argon2 import PasswordHasher
    from fastapi.testclient import TestClient
    with TestClient(_ip_app(client.app)) as a, TestClient(_ip_app(client.app)) as b:
        assert _login(a, "192.0.2.21", "correct-horse-1").status_code == 200
        assert _login(b, "192.0.2.22", "correct-horse-1").status_code == 200
        assert a.get("/api/overview").status_code == 200 and b.get("/api/overview").status_code == 200
        assert a.post("/api/devices/revoke-others").json()["revoked"] == 1
        assert a.get("/api/overview").status_code == 200
        assert b.get("/api/overview").status_code == 401          # revoked device: session gone
        a.post("/api/auth/logout")
        assert a.get("/api/overview").status_code == 401
        assert _login(a, "192.0.2.21", "correct-horse-1").status_code == 200
        client.svc.secrets.admin_password_hash = PasswordHasher().hash("rotated-pass-123")
        assert a.get("/api/overview").status_code == 401          # password change: every session ends


def test_legacy_session_cookie_rejected(client):
    from itsdangerous import TimestampSigner
    legacy = TimestampSigner("x" * 64).sign("owner").decode()
    client.cookies.set("bclaw_session", legacy)
    assert client.get("/api/overview").status_code == 401


def test_answer_question_via_api(client):
    client.post("/api/auth/login", json={"password": "correct-horse-1"})
    p = plan()
    p["questions"] = [{"topic": "TRX", "text": "卖出 TRX？", "options": [{"label": "保留"}, {"label": "卖出"}], "recommended": 0}]
    use(client.svc, p)
    client.svc.run_cycle("manual")
    qs = client.get("/api/questions?status=pending").json()
    assert len(qs) == 1 and qs[0]["options"][1]["label"] == "卖出"
    assert client.get("/api/overview").json()["pending_questions"] == 1
    r = client.post(f"/api/questions/{qs[0]['id']}/answer", json={"option": 1}).json()
    assert r["ok"] and client.get("/api/questions?status=answered").json()[0]["answer"] == 1
    assert client.post(f"/api/questions/{qs[0]['id']}/answer", json={"option": 0}).json()["ok"] is False


def test_track_record_api(client):
    assert client.get("/api/track-record").status_code == 401
    client.post("/api/auth/login", json={"password": "correct-horse-1"})
    r = client.get("/api/track-record")
    assert r.status_code == 200 and r.json()["trades"] == 0 and r.json()["24h"]["win_rate"] is None


def test_analytics_api(client):
    assert client.get("/api/analytics").status_code == 401
    client.post("/api/auth/login", json={"password": "correct-horse-1"})
    r = client.get("/api/analytics?range=90d")
    assert r.status_code == 200 and r.json()["range"] == "90d" and r.json()["trades"]["trades"] == 0
    assert client.get("/api/analytics?range=2w").status_code == 400


def test_cross_site_writes_are_refused(client):
    client.post("/api/auth/login", json={"password": "correct-horse-1"})
    for site in ("cross-site", "same-site"):
        assert client.post("/api/control/kill", headers={"Sec-Fetch-Site": site}).status_code == 403
    assert not client.svc.paused()[0]
    assert client.get("/api/overview", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    assert client.post("/api/control/pause", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_switching_mode_voids_pending_approvals(client):
    svc = client.svc
    use(svc, plan(("BUY", "BTCUSDT", 600)))
    aid = svc.run_cycle("manual").risk[0]["approval_id"]
    svc.live_check = lambda features=None: {"ok": True, "rows": [], "blockers": []}
    from binanceclaw.risk.gate import CONFIRM_PHRASE
    assert svc.set_mode("live", CONFIRM_PHRASE)[0]
    assert svc.resolve_approval(aid, True, "web") == (False, "这笔已处理或不存在")
    assert not svc.db.orders_for_day(svc.today()) or all(o.mode == "paper" for o in svc.db.orders_for_day(svc.today()))


def test_claude_cli_gets_no_other_credentials(monkeypatch, tmp_path):
    import subprocess

    from binanceclaw.brain.providers import ClaudeCodeProvider
    from binanceclaw.config import ProviderSettings
    monkeypatch.setenv("BINANCE_API_SECRET", "s3cret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg")
    monkeypatch.setenv("CLAUDE_BIN", "/bin/true")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw["env"])
        return subprocess.CompletedProcess(cmd, 0, '{"result": "ok"}', "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    p = ClaudeCodeProvider(ProviderSettings(), Secrets(claude_code_oauth_token="oauth"))
    assert p.complete("sys", [{"role": "user", "content": "hi"}]).text == "ok"
    assert "BINANCE_API_SECRET" not in seen and "TELEGRAM_BOT_TOKEN" not in seen
    assert seen["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth"
