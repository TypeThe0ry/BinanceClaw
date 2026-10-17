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
