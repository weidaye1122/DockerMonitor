import json
from types import SimpleNamespace

import pytest

from backend.notifications import BarkNotifier, endpoint_parts
from backend.store import Store
from tests.test_registry import Response

class Session:
    def __init__(self, response=None): self.calls=[]; self.response=response or Response(body=b'{"code":200}')
    def post(self,url,**kwargs): self.calls.append((url,kwargs)); return self.response

@pytest.fixture
def notifier(tmp_path):
    n=BarkNotifier(Store(tmp_path/'state'), Session())
    yield n
    n.close()

CONFIG={'enabled':True,'endpoint':'https://api.day.app/abcdefgh1234','siteUrl':'https://monitor.example.com','iconUrl':'',
        'onAvailable':True,'onSuccess':True,'onFailure':True}


def test_settings_hide_key_and_blank_input_keeps_key(notifier):
    value=notifier.save(CONFIG)
    assert value['configured'] and value['server']=='api.day.app'
    assert 'abcdefgh1234' not in json.dumps(value)
    notifier.save({**CONFIG,'endpoint':''})
    assert notifier.store.get('settings','bark')['endpoint']==CONFIG['endpoint']


def test_bark_json_payload_and_default_own_icon(notifier):
    notifier.test(CONFIG)
    url,options=notifier.session.calls[0]
    assert url=='https://api.day.app/push'
    assert options['json']['device_key']=='abcdefgh1234'
    assert options['json']['icon']=='https://monitor.example.com/icons/icon-192.png?v=a2'
    assert options['json']['group']=='Docker Monitor'
    assert options['allow_redirects'] is False


def test_notification_dedup_persists_and_target_change_notifies(notifier):
    notifier.save(CONFIG)
    notifier.event('available','app','digest1','new')
    notifier.event('available','app','digest1','new')
    notifier.pool.submit(lambda:None).result()
    assert len(notifier.session.calls)==1
    notifier.event('available','app','digest2','new')
    notifier.pool.submit(lambda:None).result()
    assert len(notifier.session.calls)==2


def test_disabled_notifications_do_not_send(notifier):
    notifier.event('available','app','digest','new')
    notifier.save({**CONFIG,'onAvailable':False})
    notifier.event('available','app','digest','new')
    notifier.pool.submit(lambda:None).result()
    assert not notifier.session.calls


def test_failures_redact_provider_payload_and_retry_possible(notifier):
    notifier.session.response=Response(400,b'{"code":400,"message":"secret key invalid"}')
    with pytest.raises(ValueError,match='未接受'):
        notifier.test(CONFIG)
    notifier.save(CONFIG)
    notifier.event('failure','app','job','failed')
    notifier.pool.submit(lambda:None).result()
    assert 'secret' not in json.dumps(notifier.settings())
    notifier.session.response=Response(body=b'{"code":200}')
    notifier.event('failure','app','job','failed')
    notifier.pool.submit(lambda:None).result()
    assert notifier.settings()['lastDelivery']['ok'] is True


def test_endpoint_validation_and_lan_self_host(notifier):
    assert endpoint_parts('http://10.0.0.7:8080/abcdefgh')==('http://10.0.0.7:8080/push','abcdefgh')
    for endpoint in ('https://api.day.app/push','https://user:secret@api.day.app/abcdefgh','http://external.example/abcdefgh','https://api.day.app/abcdefgh?x=secret'):
        with pytest.raises(ValueError): notifier.save({**CONFIG,'endpoint':endpoint})


def test_service_emits_success_and_failure_only_for_actual_update(notifier):
    from backend.service import Service
    from backend.engine import OperationError
    c=SimpleNamespace(name='app',attrs={'State':{'Running':True}})
    def update(container,job):
        job['newId']='new-container'
        return 'updated'
    service=Service(notifier.store, SimpleNamespace(get=lambda _:c,update=update))
    events=[]
    service.notifications.event=lambda *args:events.append(args)
    job={'id':'success-job','name':'app','action':'update','containerId':'old','automatic':False}
    service.perform(job)
    assert events[0][:3]==('success','app','success-job')
    def failure(c,job):
        job['rolledBack']=True
        raise OperationError('health failed')
    service.engine.update=failure
    service.perform({**job,'id':'failed-job','newId':None})
    assert events[1][:3]==('failure','app','failed-job')
    assert '已恢复' in events[1][3]
    service.close()


def test_notification_api_auth_persistence_and_test_with_mock_transport(notifier,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    monkeypatch.setenv('ADMIN_PASSWORD','local-test-only')
    monkeypatch.setenv('ADMIN_USERNAME','owner')
    client=TestClient(create_app(SimpleNamespace(notifications=notifier)))
    assert client.get('/api/notifications').status_code==401
    headers={'X-Monitor-Request':'1'}
    assert client.post('/api/auth/login',json={'username':'owner','password':'local-test-only'},headers=headers).status_code==200
    assert client.put('/api/notifications',json=CONFIG,headers=headers).status_code==200
    assert 'abcdefgh1234' not in client.get('/api/notifications').text
    response=client.post('/api/notifications/test',json={**CONFIG,'endpoint':''},headers=headers)
    assert response.status_code==200
    assert len(notifier.session.calls)==1


def test_malformed_bark_response_never_exposes_provider_body(notifier):
    notifier.session.response=Response(body=b'secret invalid JSON payload')
    with pytest.raises(ValueError,match='返回数据异常'):
        notifier.test(CONFIG)
    assert 'secret' not in json.dumps(notifier.settings())
