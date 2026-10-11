from tests.http_helpers import csrf_headers
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.engine import OperationError
from backend.service import Service
from backend.store import Store, wait_seconds


def test_legacy_hours_and_minute_deadline(tmp_path):
    store = Store(tmp_path / 'db.sqlite3')
    store.put('settings', 'policy', {'updateMode':'delay', 'waitHours':24, 'auto':['app']})
    assert store.policy()['waitValue'] == 24 and store.policy()['waitUnit'] == 'hours'
    assert wait_seconds(store.policy()) == 86400
    c = SimpleNamespace(id='a'*64, name='app', attrs={'Image':'old', 'State':{'Running':True}})
    service = Service(store, SimpleNamespace(protected=lambda c:False))
    service.visible_containers = lambda:[c]
    submitted = []
    service.submit = lambda *args, **kwargs: submitted.append(args)
    try:
        service.save_policy({**store.policy(), 'waitValue':30, 'waitUnit':'minutes'})
        store.discovery('app', 'old', 'sha256:new', True, now=100)
        service.automatic(1899)
        assert not submitted
        service.automatic(1900)
        assert len(submitted) == 1
        reopened = Store(tmp_path / 'db.sqlite3')
        assert wait_seconds(reopened.policy()) == 1800
    finally:
        service.close()


def test_delay_api_rejects_units_and_excessive_hours(tmp_path, monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    service = Service(Store(tmp_path/'db.sqlite3'))
    service.start = lambda:None
    client = TestClient(create_app(service))
    headers = csrf_headers(client)
    body = {**service.store.policy(), 'updateMode':'delay', 'waitValue':30, 'waitUnit':'minutes'}
    try:
        assert client.put('/api/policy', json=body, headers=headers).json()['waitUnit'] == 'minutes'
        for change in [{'waitUnit':'days'}, {'waitValue':-1}, {'waitValue':721,'waitUnit':'hours'}, {'waitValue':43201}]:
            assert client.put('/api/policy', json={**body, **change}, headers=headers).status_code == 422
    finally:
        service.close()


def test_batch_is_sequential_reserves_names_and_continues_after_failure(tmp_path):
    release, started = threading.Event(), threading.Event()
    def container(name, n):
        return SimpleNamespace(id=str(n)*64, name=name, attrs={'Image':'old', 'State':{'Running':True}, 'Config':{}, 'HostConfig':{}})
    first, second = container('first',1),container('second',2)
    cs = {c.id:c for c in [first,second]}
    seen = []
    def update(c, job):
        seen.append(c.name)
        if c is first:
            started.set()
            release.wait(3)
            raise OperationError('update failed and rolled back')
        return 'updated'
    engine = SimpleNamespace(get=lambda cid:cs[cid], protected=lambda c:False, update=update)
    service = Service(Store(tmp_path/'db.sqlite3'), engine)
    for c in cs.values(): service.store.discovery(c.name,'old','new',True)
    try:
        batch = service.update_all(list(cs))
        assert started.wait(1) and seen == ['first']
        with pytest.raises(OperationError, match='任务'):
            service.submit(second.id, 'restart')
        release.set()
        service.pool.shutdown(wait=True)
        final = service.store.get('jobs',batch['id'])
        assert seen == ['first','second'] and final['status'] == 'partial'
        assert final['errors'][0]['name'] == 'first' and not service.active
    finally:
        release.set()
        service.close()


@pytest.mark.parametrize('state,protected,status', [({'Running':False},False,'available'), ({'Running':True,'Restarting':True},False,'available'), ({'Running':True,'Health':{'Status':'unhealthy'}},False,'available'), ({'Running':True},True,'available'), ({'Running':True},False,'current')])
def test_batch_rejects_ineligible_targets(tmp_path,state,protected,status):
    c = SimpleNamespace(id='a'*64,name='app',attrs={'Image':'old','State':state})
    service = Service(Store(tmp_path/'db.sqlite3'),SimpleNamespace(get=lambda cid:c,protected=lambda c:protected))
    service.store.put('images','app',{'imageId':'old','status':status})
    try:
        with pytest.raises(OperationError): service.update_all([c.id])
        assert not service.active
    finally:
        service.close()


def test_icon_and_manifest_assets_are_public_but_api_requires_login(tmp_path,monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD','test-only-password')
    service = Service(Store(tmp_path/'db.sqlite3'))
    service.start = lambda:None
    client = TestClient(create_app(service))
    try:
        for path in ['/favicon.ico','/icons/icon.svg','/icons/icon-180.png','/icons/icon-192.png','/icons/icon-maskable-512.png','/manifest.webmanifest']:
            assert client.get(path).status_code == 200
        manifest = client.get('/manifest.webmanifest').json()
        assert manifest['name'] == 'Docker Monitor'
        assert client.get('/api/policy').status_code == 401
        assert client.post('/api/update-all',json={'ids':['a'*64]},headers=csrf_headers(client)).status_code == 401
    finally:
        service.close()
