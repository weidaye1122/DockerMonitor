"""Browser origins/cookies across an HTTP backend, reverse proxies and tunnels."""
from types import SimpleNamespace
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth import COOKIE
from backend.proxy import parse_origin

HEADERS = {"X-Monitor-Request": "1"}


class HTTPBackend:
    """The browser uses the public URL; the proxy talks HTTP to the backend."""
    def __init__(self, app, peer):
        self.app, self.peer = app, peer

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "scheme": "http", "client": (self.peer, 12345)}
        await self.app(scope, receive, send)


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "127.0.0.1,::1,192.168.1.10,172.18.0.1,172.18.0.2,10.0.0.10,10.0.0.1,fd00::1")
    monkeypatch.delenv("PUBLIC_URL", raising=False)

    def make(url="https://dm.543221.xyz:6", peer="192.168.1.10", headers=None):
        service = SimpleNamespace(snapshot=lambda: {"containers": []}, save_policy=lambda value: value)
        app = create_app(service)
        return TestClient(HTTPBackend(app, peer), base_url=url, headers=headers or {})
    return make


def login(client, origin, headers=None, password="test"):
    return client.post("/api/auth/login", json={"username": "admin", "password": password},
                       headers={**HEADERS, "Origin": origin, **(headers or {})})


@pytest.mark.parametrize("url,peer,proxy_headers", [
    ("https://dm.543221.xyz:6", "192.168.1.10", {"X-Forwarded-Proto": "https"}),
    ("https://monitor.example.com", "172.18.0.1", {"X-Forwarded-Proto": "https"}),
    ("http://monitor.example.com:8088", "10.0.0.10", {}),
    ("https://monitor.example.com:8443", "127.0.0.1", {
        "Host": "localhost:8080", "X-Forwarded-Host": "monitor.example.com:8443", "X-Forwarded-Proto": "https"}),
    ("https://monitor.example.com:8443", "::1", {
        "Host": "localhost:8080", "X-Forwarded-Host": "monitor.example.com", "X-Forwarded-Port": "8443", "X-Forwarded-Proto": "https"}),
    ("https://monitor.example.com:8443", "172.18.0.2", {
        "Host": "localhost:8080", "X-Forwarded-Host": "monitor.example.com:8443", "X-Forwarded-Port": "8080", "X-Forwarded-Proto": "https"}),
    ("https://monitor.example.com", "10.0.0.1", {
        "X-Forwarded-Host": "evil.example, monitor.example.com", "X-Forwarded-Proto": "http, https"}),
])
def test_proxy_browser_login_assets_session_and_logout(make_client, url, peer, proxy_headers):
    client = make_client(url, peer, proxy_headers)
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    for asset in ("/login", "/login.js", "/login.css", "/app.css", "/vendor/lucide.js", "/manifest.webmanifest", "/icons/icon.svg"):
        assert client.get(asset).status_code == 200
    response = login(client, url)
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"].lower()
    assert ("secure" in cookie) == url.startswith("https:")
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    token = client.cookies.get(COOKIE)
    assert client.get("/api/auth/session").json()["authenticated"]
    assert client.get("/api/snapshot").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.get("/vendor/croner.js").status_code == 200
    policy = {"checkCron": "0 * * * *", "updateMode": "scheduled"}
    assert client.put("/api/policy", json=policy, headers={**HEADERS, "Origin": url}).status_code == 200
    assert client.put("/api/policy", json=policy, headers={**HEADERS, "Origin": "https://evil.example"}).status_code == 403
    # Logout exercises the same origin guard as all other mutating APIs.
    response = client.post("/api/auth/logout", headers={**HEADERS, "Origin": url})
    assert response.status_code == 200
    assert ("secure" in response.headers["set-cookie"].lower()) == url.startswith("https:")
    assert not client.cookies.get(COOKIE)
    client.cookies.set(COOKIE, token)
    assert client.get("/api/snapshot").status_code == 401


def test_ipv6_proxy_origin_and_session(make_client):
    app = make_client(peer="fd00::1").app

    async def check():
        # Starlette's synchronous TestClient cannot parse IPv6 URL netlocs in
        # this pinned version; httpx's ASGI transport exercises the real URL.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                base_url="https://[2001:db8::1]:8443", headers={**HEADERS,
                "Origin": "https://[2001:db8::1]:8443", "Host": "localhost:8080",
                "X-Forwarded-Host": "[2001:db8::1]", "X-Forwarded-Port": "8443",
                "X-Forwarded-Proto": "https"}) as client:
            response = await client.post("/api/auth/login", json={"username": "admin", "password": "test"})
            assert response.status_code == 200
            assert "secure" in response.headers["set-cookie"].lower()
            assert (await client.get("/api/snapshot")).status_code == 200
            assert (await client.post("/api/auth/logout")).status_code == 200
            assert (await client.get("/api/snapshot")).status_code == 401
    asyncio.run(check())


@pytest.mark.parametrize("origin", ["https://evil.example", "http://dm.543221.xyz:6", "https://dm.543221.xyz",
    "https://dm.543221.xyz:7", "null", "https://dm.543221.xyz:6/path", "https://user@dm.543221.xyz:6"])
def test_proxy_still_rejects_other_origins(make_client, origin):
    client = make_client(headers={"X-Forwarded-Proto": "https"})
    response = login(client, origin)
    assert response.status_code == 403 and response.json()["detail"] == "请求来源不匹配"
    assert not client.app.app.state.auth.sessions


def test_public_client_cannot_spoof_proxy_headers(make_client):
    client = make_client("http://monitor.example.com", "203.0.113.10", {
        "X-Forwarded-Proto": "https", "X-Forwarded-Host": "evil.example", "X-Forwarded-Port": "443"})
    assert login(client, "https://evil.example").status_code == 403
    response = login(client, "http://monitor.example.com")
    assert response.status_code == 200
    assert "secure" not in response.headers["set-cookie"].lower()


def test_untrusted_client_cannot_evade_login_limit_with_forwarded_ip(make_client):
    client = make_client("http://monitor.example.com", "203.0.113.10")
    for _ in range(10):
        assert login(client, "http://monitor.example.com", {"X-Forwarded-For": "203.0.113.11"}, "wrong").status_code == 401
    assert login(client, "http://monitor.example.com", {"X-Forwarded-For": "203.0.113.12"}).status_code == 429


def test_proxy_does_not_bypass_operation_marker(make_client):
    client = make_client(headers={"X-Forwarded-Proto": "https"})
    response = client.post("/api/auth/login", json={"username": "admin", "password": "test"},
                           headers={"Origin": "https://dm.543221.xyz:6"})
    assert response.status_code == 403 and response.json()["detail"] == "缺少操作请求标识"


def test_proxy_trust_can_be_restricted(make_client, monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "192.168.1.20")
    client = make_client(headers={"X-Forwarded-Proto": "https"})
    assert login(client, "https://dm.543221.xyz:6").status_code == 403
    client = make_client(peer="192.168.1.20", headers={"X-Forwarded-Proto": "https"})
    assert login(client, "https://dm.543221.xyz:6").status_code == 200


def test_private_peer_is_not_trusted_by_default(make_client, monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
    client = make_client(headers={"X-Forwarded-Proto": "https"})
    assert login(client, "https://dm.543221.xyz:6").status_code == 403


def test_trusted_proxy_client_ips_have_independent_login_limits(make_client):
    client = make_client(headers={"X-Forwarded-Proto": "https"})
    for _ in range(10):
        assert login(client, "https://dm.543221.xyz:6", {"X-Forwarded-For": "203.0.113.11"}, "wrong").status_code == 401
    assert login(client, "https://dm.543221.xyz:6", {"X-Forwarded-For": "203.0.113.11"}).status_code == 429
    assert login(client, "https://dm.543221.xyz:6", {"X-Forwarded-For": "203.0.113.12"}).status_code == 200


def test_tunnel_without_proxy_headers_uses_explicit_public_url(make_client, monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
    monkeypatch.setenv("PUBLIC_URL", "https://dm.543221.xyz:6/")
    client = make_client(peer="203.0.113.10", headers={"Host": "localhost:8080"})
    response = login(client, "https://dm.543221.xyz:6")
    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()
    assert client.get("/api/snapshot").status_code == 200
    assert login(client, "https://evil.example").status_code == 403
    assert login(client, "https://dm.543221.xyz").status_code == 403
    response = client.post("/api/auth/logout", headers={**HEADERS, "Origin": "https://dm.543221.xyz:6"})
    assert response.status_code == 200 and "secure" in response.headers["set-cookie"].lower()
    assert not client.cookies.get(COOKIE)
    # Configuring the public address does not force HTTPS cookies on LAN HTTP.
    local = make_client("http://nas.local:9090")
    response = login(local, "http://nas.local:9090")
    assert response.status_code == 200 and "secure" not in response.headers["set-cookie"].lower()
    assert local.get("/api/snapshot").status_code == 200


def test_default_ports_and_case_are_equivalent(make_client):
    client = make_client("https://monitor.example.com", headers={"X-Forwarded-Proto": "https"})
    assert login(client, "https://MONITOR.EXAMPLE.COM:443").status_code == 200


@pytest.mark.parametrize("header,value", [("X-Forwarded-Host", "user@evil.example"),
    ("X-Forwarded-Host", "monitor.example.com/path"), ("X-Forwarded-Host", "monitor.example.com:bad"),
    ("X-Forwarded-Port", "99999"), ("X-Forwarded-Port", "bad"), ("X-Forwarded-Port", "9" * 5000), ("X-Forwarded-Proto", "javascript")])
def test_malformed_proxy_headers_do_not_crash_or_allow_other_origin(make_client, header, value):
    client = make_client(headers={"Host": "localhost:8080", header: value})
    assert login(client, "https://evil.example").status_code == 403


@pytest.mark.parametrize("value", ["https://monitor.example.com/path", "https://user@monitor.example.com", "https://monitor.example.com:bad"])
def test_invalid_public_url_fails_at_startup(make_client, monkeypatch, value):
    monkeypatch.setenv("PUBLIC_URL", value)
    with pytest.raises(ValueError, match="PUBLIC_URL"):
        make_client()


@pytest.mark.parametrize("value", ["null", "https://x:99999", "https://x:0", "https://x/p", "https://x?y", "https://x#y", "https://x\\evil", "https://x\n"])
def test_invalid_origin(value):
    assert parse_origin(value) is None
