"""Hardening from the 2026-10-18 security review: model base URLs need confirming, Secure cookie behind a proxy, CSP."""
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from binanceclaw.api.app import create_app
from binanceclaw.config import ConfirmationRequired, Secrets
from binanceclaw.core.service import Service

from .conftest import FakeFutures, FakeSpot


def _client(tmp_path, static=None):
    sec = Secrets(admin_password_hash=PasswordHasher().hash("correct-horse-1"), session_secret="x" * 64,
                  binance_api_key="k", binance_api_secret="s")
    svc = Service(data_dir=tmp_path, secrets=sec, client=FakeSpot(), fclient=FakeFutures())
    return TestClient(create_app(svc, static)), svc


def test_model_base_url_is_a_dangerous_setting(svc):
    with pytest.raises(ConfirmationRequired) as e:
        svc.update_settings({"provider": {"base_url": "https://evil.example/v1"}}, guard=True)
    assert e.value.changes[0]["field"] == "provider.base_url"
    with pytest.raises(ConfirmationRequired):
        svc.update_settings({"debate": {"bull": {"base_url": "https://evil.example/v1"}}}, guard=True)


def test_secure_cookie_can_be_forced_behind_a_proxy(tmp_path, monkeypatch):
    c, _ = _client(tmp_path)
    r = c.post("/api/auth/login", json={"password": "correct-horse-1"})
    assert "secure" not in r.headers["set-cookie"].lower()
    monkeypatch.setenv("BINANCECLAW_COOKIE_SECURE", "1")
    c2, _ = _client(tmp_path / "b")
    r = c2.post("/api/auth/login", json={"password": "correct-horse-1"})
    assert all("secure" in v.lower() for v in r.headers.get_list("set-cookie"))


def test_csp_allows_only_own_scripts_and_the_inline_theme_script(tmp_path):
    import base64
    import hashlib
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html><head><script>var a=1;</script></head><body></body></html>")
    c, _ = _client(tmp_path, static)
    csp = c.get("/").headers["content-security-policy"]
    digest = base64.b64encode(hashlib.sha256(b"var a=1;").digest()).decode()
    assert f"script-src 'self' 'sha256-{digest}'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-eval" not in csp
