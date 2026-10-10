from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth import COOKIE

HEADERS = {"X-Monitor-Request": "1"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "owner")
    monkeypatch.setenv("ADMIN_PASSWORD", "测试-password")
    service = SimpleNamespace(snapshot=lambda: {"containers": []})
    return TestClient(create_app(service))


def sign_in(client, username="owner", password="测试-password", headers=HEADERS):
    return client.post("/api/auth/login", json={"username": username, "password": password}, headers=headers)


def test_page_and_api_require_session_without_browser_prompt(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert client.get("/login").status_code == 200
    for asset in ("login.js", "login.css", "app.css", "vendor/lucide.js"):
        assert client.get("/" + asset).status_code == 200
    assert client.get("/app.js", follow_redirects=False).status_code == 303
    response = client.get("/api/snapshot")
    assert response.status_code == 401
    assert "www-authenticate" not in response.headers
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/auth/session").json() == {"enabled": True, "authenticated": False, "username": None}


def test_configured_account_login_cookie_and_logout_revocation(client):
    assert sign_in(client, username="admin").status_code == 401
    assert sign_in(client, password="wrong").status_code == 401
    response = sign_in(client)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=43200" in cookie
    token = client.cookies.get(COOKIE)
    assert token and "测试-password" not in token
    assert client.get("/api/snapshot").status_code == 200
    assert client.get("/api/auth/session").json()["username"] == "owner"
    assert client.get("/login", follow_redirects=False).status_code == 303
    assert client.post("/api/auth/logout", headers=HEADERS).status_code == 200
    assert not client.cookies.get(COOKIE)
    # Even replaying the old cookie cannot restore a logged-out session.
    client.cookies.set(COOKIE, token)
    assert client.get("/api/snapshot").status_code == 401


def test_expired_and_forged_session_rejected(client):
    sign_in(client)
    client.app.state.auth.sessions = {k: 0 for k in client.app.state.auth.sessions}
    assert client.get("/api/snapshot").status_code == 401
    client.cookies.clear()
    client.cookies.set(COOKIE, "forged")
    assert client.get("/api/snapshot").status_code == 401


def test_login_csrf_guard_security_headers_and_rate_limit(client):
    assert sign_in(client, headers={}).status_code == 403
    assert sign_in(client, headers={**HEADERS, "Origin": "https://other.example"}).status_code == 403
    assert not client.app.state.auth.sessions
    for _ in range(10):
        assert sign_in(client, password="wrong").status_code == 401
    response = sign_in(client)
    assert response.status_code == 429
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"


def test_https_cookie_is_secure(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "test")
    client = TestClient(create_app(SimpleNamespace()), base_url="https://testserver")
    response = sign_in(client, username="admin", password="test")
    assert "Secure" in response.headers["set-cookie"]


def test_no_password_keeps_local_development_access(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    client = TestClient(create_app(SimpleNamespace(snapshot=lambda: {})))
    assert client.get("/").status_code == 200
    assert client.get("/api/snapshot").status_code == 200
    assert client.get("/api/auth/session").json()["enabled"] is False
