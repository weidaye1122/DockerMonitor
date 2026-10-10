import hashlib
import json
from types import SimpleNamespace

import pytest
import requests
from docker.errors import APIError

from backend.registry import RegistryResolver, RegistryError, friendly_error, split_reference
from backend.engine import Engine
from backend.store import Store

BODY = b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json","layers":[]}'
DIGEST = 'sha256:' + hashlib.sha256(BODY).hexdigest()

class Response:
    def __init__(self, status=200, body=BODY, headers=None):
        self.status_code, self.body, self.headers = status, body, headers or {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, size): yield self.body

class Session:
    def __init__(self, results): self.results, self.calls = list(results), []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception): raise result
        return result


def client(digest=DIGEST, mirrors=()):
    return SimpleNamespace(images=SimpleNamespace(get_registry_data=lambda _: SimpleNamespace(id=digest)),
                           info=lambda: {'RegistryConfig': {'Mirrors': list(mirrors)}})


def test_direct_check_token_digest_cache_and_no_sdk_request():
    s = Session([Response(401, b'', {'WWW-Authenticate':'Bearer realm="https://auth.docker.io/token",service="registry.docker.io"'}),
                 Response(body=b'{"token":"test-token"}'), Response(headers={'Docker-Content-Digest':DIGEST})])
    resolver = RegistryResolver(s)
    c = client()
    c.images.get_registry_data = lambda _: pytest.fail('direct success should not query daemon')
    assert resolver.resolve('nginx:latest', c) == {'digest': DIGEST, 'checkSource': 'https://registry-1.docker.io'}
    assert resolver.resolve('nginx:latest', c)['digest'] == DIGEST
    assert len(s.calls) == 3
    assert s.calls[1][1]['params']['scope'] == 'repository:library/nginx:pull'
    assert s.calls[2][1]['headers']['Authorization'] == 'Bearer test-token'
    assert all(x[1]['allow_redirects'] is False for x in s.calls)


def test_invalid_manifest_is_not_hidden_by_fallback():
    resolver = RegistryResolver(Session([Response(headers={'Docker-Content-Digest':'sha256:'+'f'*64})]))
    with pytest.raises(RegistryError, match='校验失败'):
        resolver.resolve('mzz2017/v2raya:latest', client())


def test_sdk_failure_tries_only_configured_mirror(monkeypatch):
    monkeypatch.setenv('REGISTRY_MIRRORS', 'https://mirror.example.com')
    resolver = RegistryResolver(Session([requests.Timeout(), Response()]))
    c = client()
    c.images.get_registry_data = lambda _: (_ for _ in ()).throw(APIError('bad', explanation='name unknown: QQ群 123'))
    result = resolver.resolve('mzz2017/v2raya:latest', c)
    assert result['checkSource'] == 'https://mirror.example.com'
    assert len(resolver.session.calls) == 2


def test_custom_private_registry_stays_on_sdk_path():
    resolver = RegistryResolver(Session([]))
    assert resolver.resolve('private.example.com/team/app:stable', client())['checkSource'] == 'docker-engine'
    assert not resolver.session.calls


def test_token_challenge_cannot_send_token_to_unrelated_host():
    resolver = RegistryResolver(Session([Response(401,b'',{'WWW-Authenticate':'Bearer realm="https://evil.example/token"'})]))
    with pytest.raises(RegistryError, match='不受信任'):
        resolver.manifest('https://registry-1.docker.io','library/nginx','latest')
    assert len(resolver.session.calls) == 1


def test_known_registry_errors_and_unknown_payload_are_clean():
    assert '限' in friendly_error('429 too many requests')
    assert '授权' in friendly_error('unauthorized')
    assert 'QQ' not in friendly_error('name unknown: 联系 QQ 群')
    assert '法律' not in friendly_error('name unknown: 可能违反法律')
    assert '超时' in friendly_error('timeout')


def test_mirrors_are_tls_only_and_no_credentials(monkeypatch):
    monkeypatch.setenv('REGISTRY_MIRRORS','http://plain.test,https://user:pass@secret.test,https://ok.test/,https://bad.test/path')
    assert RegistryResolver().mirrors(client()) == ['https://ok.test']
    assert split_reference('nginx:alpine') == ('docker.io','library/nginx','alpine')
    assert split_reference('localhost:5001/app:latest') == ('localhost:5001','app','latest')


def test_pull_fallback_pins_identical_digest_and_platform(tmp_path, monkeypatch):
    monkeypatch.setenv('REGISTRY_MIRRORS','https://mirror.test')
    calls=[]
    def pull(ref, platform):
        calls.append((ref,platform))
        if len(calls)<3: raise APIError('source unavailable')
        return SimpleNamespace(id='immutable-id')
    c = client(); c.images.pull=pull
    engine=Engine(Store(tmp_path/'state'),c)
    validations=[]
    engine.registry.manifest=lambda base,repo,digest: validations.append((base,repo,digest)) or digest
    container=SimpleNamespace(attrs={'Config':{'Image':'mzz2017/v2raya:latest'},'ImageManifestDescriptor':{'platform':{'os':'linux','architecture':'amd64'}}})
    assert engine.target(container,DIGEST)=='immutable-id'
    assert [x[0] for x in calls]==['mzz2017/v2raya@'+DIGEST,'registry-1.docker.io/mzz2017/v2raya@'+DIGEST,'mirror.test/mzz2017/v2raya@'+DIGEST]
    assert all(x[1]=='linux/amd64' for x in calls)
    assert len(validations)==2


def test_manual_check_observes_new_tag_despite_short_cache(tmp_path):
    from backend.service import Service
    old='sha256:'+'1'*64;new='sha256:'+'2'*64
    c=SimpleNamespace(id='a'*64,name='app',attrs={'Id':'a'*64,'Image':old,'Config':{'Image':'private.example.com/app:latest','Labels':{}},'State':{'Running':True}})
    docker_client=client(old)
    docker_client.containers=SimpleNamespace(get=lambda _:c)
    docker_client.images.get=lambda _:SimpleNamespace(attrs={'RepoDigests':['private.example.com/app@'+old]})
    store=Store(tmp_path/'state');engine=Engine(store,docker_client);service=Service(store,engine)
    job={'id':'check1','name':'app','action':'check','containerId':c.id,'oldImage':old,'automatic':False}
    service.perform(job)
    assert store.get('images','app')['status']=='current'
    docker_client.images.get_registry_data=lambda _:SimpleNamespace(id=new)
    service.perform({**job,'id':'check2'})
    assert store.get('images','app')['status']=='available'
    service.close()
