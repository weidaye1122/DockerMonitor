import copy
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlsplit

import httpx
import pytest
import requests
from fastapi.testclient import TestClient

from backend import repair
from backend.app import create_app
from backend.engine import SOURCE_LABEL
from backend.store import Store


@pytest.fixture
def repair_app(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_USERNAME', 'repair-owner')
    monkeypatch.setenv('ADMIN_PASSWORD', 'repair-test-only')
    before = {
        'Id': 'a' * 64, 'Name': '/repair-fixture', 'Image': 'sha256:unchanged',
        'Config': {'Image': 'sha256:unchanged', 'Env': ['SETTING=kept'], 'Cmd': ['sleep', '3600'],
                   'Labels': {SOURCE_LABEL: 'example.test/app:stable'}},
        'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'},
                       'PortBindings': {'8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '59999'}]}},
        'Mounts': [{'Type': 'volume', 'Name': 'fixture-data', 'Destination': '/data', 'RW': True}],
        'NetworkSettings': {'Networks': {'fixture-net': {'IPAMConfig': {'IPv4Address': '172.30.0.10'},
                                                       'Aliases': ['fixture-alias']}}},
        'State': {'Running': True},
    }
    after = copy.deepcopy(before)
    after['Id'] = 'b' * 64
    after['Config']['Image'] = before['Config']['Labels'][SOURCE_LABEL]
    after['Config']['Labels']['docker-monitor.job'] = 'repair-job'
    store = Store(tmp_path / 'state.sqlite3')
    state = SimpleNamespace(before=before, after=after, store=store, actions=[], calls=[], statuses=[],
                            before_request=None, response_filter=None)

    def submit(cid, action):
        assert cid == before['Id'] and action == 'repair'
        state.actions.append((cid, action))
        job = {'id': 'repair-job', 'status': 'success', 'message': 'repair complete'}
        store.put('jobs', job['id'], job)
        return job

    state.app = create_app(SimpleNamespace(store=store, submit=submit))
    api = TestClient(state.app, base_url='http://127.0.0.1:8080')

    class Session:
        def __init__(self):
            self.headers = {}
            self.trust_env = True

        def request(self, method, url, **kwargs):
            path = urlsplit(url).path
            state.calls.append((method, path))
            if state.before_request:
                state.before_request(method, path)
            kwargs.pop('timeout', None)
            response = api.request(method, url, headers=self.headers, **kwargs)
            if state.response_filter:
                response = state.response_filter(method, path, response)
            state.statuses.append((method, path, response.status_code))
            return response

    state.session = Session()
    monkeypatch.setattr(repair.requests, 'Session', lambda: state.session)

    def get(name):
        assert name == 'repair-fixture'
        attrs = after if state.actions else before
        return SimpleNamespace(id=attrs['Id'], attrs=attrs)

    state.docker_access = Mock(return_value=SimpleNamespace(containers=SimpleNamespace(get=get)))
    monkeypatch.setattr(repair.docker, 'from_env', state.docker_access)
    yield state
    api.close()
    store.db.close()


def action_calls(state):
    return [call for call in state.calls if call[0] == 'POST' and call[1].endswith('/actions')]


@pytest.mark.parametrize('authenticated', [True, False], ids=['login', 'local-no-password'])
def test_cli_logs_in_and_repairs_with_preservation_checks(repair_app, authenticated, capsys):
    if not authenticated:
        repair_app.app.state.auth.password = ''
    repair.main(['repair-fixture'])
    assert repair_app.actions == [(repair_app.before['Id'], 'repair')]
    assert len(action_calls(repair_app)) == 1
    assert all(status < 400 for _, _, status in repair_app.statuses)
    assert not repair_app.session.trust_env
    assert 'Legacy repair completed' in capsys.readouterr().out


def test_cli_refreshes_rejected_csrf_without_duplicate_repair(repair_app):
    def expire_once(method, path):
        if method == 'POST' and path.endswith('/actions'):
            repair_app.app.state.auth.csrf_secret = b'rotated-test-secret'
            repair_app.before_request = None

    repair_app.before_request = expire_once
    repair.main(['repair-fixture'])
    assert [status for _, path, status in repair_app.statuses if path.endswith('/actions')] == [403, 202]
    assert len(repair_app.actions) == 1


def test_cli_stops_after_one_csrf_retry(repair_app):
    def expire_each_time(method, path):
        if method == 'POST' and path.endswith('/actions'):
            repair_app.app.state.auth.csrf_secret += b'x'

    repair_app.before_request = expire_each_time
    with pytest.raises(RuntimeError, match='Repair API refused'):
        repair.main(['repair-fixture'])
    assert len(action_calls(repair_app)) == 2
    assert not repair_app.actions


@pytest.mark.parametrize('failure', ['forbidden', 'non-json-forbidden', 'server-error', 'timeout'])
def test_cli_does_not_replay_ambiguous_or_unrelated_errors(repair_app, failure):
    def fail_after_dispatch(method, path, response):
        if method != 'POST' or not path.endswith('/actions'):
            return response
        if failure == 'timeout':
            raise requests.Timeout('test-only timeout after dispatch')
        if failure == 'non-json-forbidden':
            return httpx.Response(403, text='test-only proxy error')
        return httpx.Response(403 if failure == 'forbidden' else 500, json={'code': 'not_csrf'})

    repair_app.response_filter = fail_after_dispatch
    error = requests.Timeout if failure == 'timeout' else RuntimeError
    with pytest.raises(error):
        repair.main(['repair-fixture'])
    assert len(action_calls(repair_app)) == 1
    assert len(repair_app.actions) == 1


def test_cli_does_not_relogin_or_retry_an_expired_session(repair_app):
    def expire_session(method, path):
        if method == 'POST' and path.endswith('/actions'):
            repair_app.app.state.auth.sessions.clear()

    repair_app.before_request = expire_session
    with pytest.raises(RuntimeError, match='Repair API refused'):
        repair.main(['repair-fixture'])
    assert len(action_calls(repair_app)) == 1
    assert repair_app.calls.count(('POST', '/api/auth/login')) == 1
    assert not repair_app.actions


def test_cli_wrong_password_stops_before_docker_access(repair_app, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'wrong-test-only')
    with pytest.raises(RuntimeError, match='Repair API refused'):
        repair.main(['repair-fixture'])
    repair_app.docker_access.assert_not_called()
    assert not repair_app.actions


def test_cli_retains_automatic_update_pause_requirement(repair_app):
    repair_app.store.put('settings', 'policy', {'auto': ['fixture-auto']})
    with pytest.raises(RuntimeError, match='Pause automatic updates'):
        repair.main(['repair-fixture'])
    repair_app.docker_access.assert_not_called()
    assert not repair_app.actions


@pytest.mark.parametrize('change', ['environment', 'volume'])
def test_cli_still_rejects_post_repair_configuration_changes(repair_app, change):
    if change == 'environment':
        repair_app.after['Config']['Env'] = ['SETTING=changed']
    else:
        repair_app.after['Mounts'][0]['Name'] = 'different-volume'
    with pytest.raises(RuntimeError, match='post-repair configuration verification failed'):
        repair.main(['repair-fixture'])


@pytest.mark.parametrize('failure', ['missing-token', 'unavailable'])
def test_cli_csrf_bootstrap_failure_stops_before_credentials_or_docker(repair_app, failure):
    def reject_bootstrap(method, path, response):
        if path == '/api/auth/csrf':
            return httpx.Response(200 if failure == 'missing-token' else 503, json={})
        return response

    repair_app.response_filter = reject_bootstrap
    with pytest.raises(RuntimeError, match='Repair API refused'):
        repair.main(['repair-fixture'])
    assert ('POST', '/api/auth/login') not in repair_app.calls
    repair_app.docker_access.assert_not_called()
