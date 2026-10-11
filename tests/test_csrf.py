import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth import COOKIE, CSRF_COOKIE, SESSION_SECONDS
from tests.http_helpers import csrf_headers


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'test')
    return TestClient(create_app(SimpleNamespace(snapshot=lambda: {})))


def login(client, headers):
    return client.post('/api/auth/login', json={'username': 'admin', 'password': 'test'}, headers=headers)


def test_csrf_bootstrap_is_uncached_and_not_exposed_cross_site(client):
    assert client.get('/api/auth/csrf').status_code == 403
    response = client.get('/api/auth/csrf', headers={'X-Monitor-Request': '1', 'Sec-Fetch-Site': 'cross-site'})
    assert response.status_code == 403
    preflight = client.options('/api/auth/csrf', headers={'Origin': 'https://evil.example',
        'Access-Control-Request-Method': 'GET', 'Access-Control-Request-Headers': 'x-monitor-request'})
    assert 'access-control-allow-origin' not in preflight.headers
    headers = csrf_headers(client)
    response = client.get('/api/auth/csrf', headers={'X-Monitor-Request': '1'})
    assert response.headers['cache-control'] == 'no-store'
    assert 'httponly' in response.headers['set-cookie'].lower()
    assert 'samesite=strict' in response.headers['set-cookie'].lower()
    assert 'access-control-allow-origin' not in response.headers
    assert login(client, headers).status_code == 200


@pytest.mark.parametrize('token', ['', 'forged', 'a:b:c:d:e', 'x' * 10000, b'\xff:1:0:x:x'])
def test_missing_or_forged_token_never_creates_session(client, token):
    csrf_headers(client)
    response = login(client, {'X-Monitor-Request': '1', 'X-CSRF-Token': token})
    assert response.status_code == 403 and response.json()['code'] == 'csrf_invalid'
    assert not client.app.state.auth.sessions


def test_signed_token_cannot_be_transferred_between_browsers(client):
    headers = csrf_headers(client)
    other = TestClient(client.app)
    csrf_headers(other)
    assert login(other, headers).status_code == 403
    assert login(client, headers).status_code == 200


def test_token_bound_to_login_session_then_logout_revokes_session(client):
    anonymous = csrf_headers(client)
    assert login(client, anonymous).status_code == 200
    session = client.cookies.get(COOKIE)
    # The login token cannot be reused for authenticated operations.
    assert client.post('/api/auth/logout', headers=anonymous).status_code == 403
    assert client.get('/api/auth/session').json()['authenticated']
    authenticated = csrf_headers(client)
    assert client.post('/api/auth/logout', headers=authenticated).status_code == 200
    assert client.cookies.get(COOKIE) is None
    assert login(client, authenticated).status_code == 403
    client.cookies.set(COOKIE, session)
    assert client.get('/api/snapshot').status_code == 401


def test_token_signature_and_secure_flag_cannot_be_changed(client):
    headers = csrf_headers(client)
    parts = headers['X-CSRF-Token'].split(':')
    parts[2] = '1'
    assert login(client, {**headers, 'X-CSRF-Token': ':'.join(parts)}).status_code == 403
    assert not client.app.state.auth.sessions


def test_expiration_future_timestamp_and_service_restart_recovery(client, monkeypatch):
    headers = csrf_headers(client)
    now = time.time()
    with monkeypatch.context() as patch:
        patch.setattr('backend.auth.time.time', lambda: now + SESSION_SECONDS + 1)
        assert login(client, headers).status_code == 403
    with monkeypatch.context() as patch:
        patch.setattr('backend.auth.time.time', lambda: now - 10)
        assert login(client, headers).status_code == 403
    client.app.state.auth.csrf_secret = b'new-service-secret'
    assert login(client, headers).status_code == 403
    assert login(client, csrf_headers(client)).status_code == 200


def test_multiple_tabs_do_not_rotate_csrf_cookie(client):
    first = csrf_headers(client)
    nonce = client.cookies.get(CSRF_COOKIE)
    second = csrf_headers(client)
    assert client.cookies.get(CSRF_COOKIE) == nonce
    # Both independently fetched tokens remain valid until session changes.
    auth = client.app.state.auth
    assert auth.csrf_valid(first['X-CSRF-Token'], nonce, None)
    assert auth.csrf_valid(second['X-CSRF-Token'], nonce, None)


def test_authenticated_expiry_remains_401_instead_of_csrf_retry(client):
    assert login(client, csrf_headers(client)).status_code == 200
    headers = csrf_headers(client)
    client.app.state.auth.sessions = {}
    assert client.post('/api/auth/logout', headers=headers).status_code == 401


def test_csrf_cookie_blocked_fails_closed_with_recoverable_error(client):
    headers = csrf_headers(client)
    client.cookies.clear()
    response = login(client, headers)
    assert response.status_code == 403 and response.json()['code'] == 'csrf_invalid'
