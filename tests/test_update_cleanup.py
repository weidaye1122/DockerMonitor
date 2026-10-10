from types import SimpleNamespace

import pytest
from docker.errors import NotFound

from backend.engine import Engine
from backend.service import Service
from backend.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'state.sqlite3')


def fixture(store, remove_error=None, running_old=False, missing_old=False):
    removed = []
    plan = dict(id='job', action='update', status='running', stage='verified',
                name='app', oldId='old', newId='new', targetImage='image-new',
                backupName='app.backup-job', wasRunning=True, cleanupPending=True)
    def remove(**kwargs):
        removed.append(kwargs)
        if remove_error:
            raise remove_error
    old = SimpleNamespace(name=plan['backupName'], attrs={'State': {'Running': running_old}}, remove=remove)
    new = SimpleNamespace(name='app', attrs={'Image': 'image-new', 'Config': {'Labels': {'docker-monitor.job': 'job'}},
                                           'State': {'Running': True, 'Health': {'Status': 'healthy'}}})
    def get(cid):
        if cid == 'old' and missing_old:
            raise NotFound('deleted')
        return old if cid == 'old' else new
    engine = Engine(store, SimpleNamespace(containers=SimpleNamespace(get=get)))
    return engine, plan, removed, new


def test_success_removes_only_stopped_original_and_preserves_volumes(store):
    engine, plan, removed, _ = fixture(store)
    assert engine.finish_update(plan) == '更新成功，旧容器已自动删除'
    assert removed == [{'v': False, 'force': False}]
    assert store.get('jobs', 'job')['stage'] == 'cleaned'
    assert not plan['cleanupPending']


def test_cleanup_failure_keeps_new_container_and_schedules_retry(store):
    engine, plan, _, _ = fixture(store, remove_error=RuntimeError('engine temporarily unavailable'))
    engine.rollback = lambda _: pytest.fail('cleanup must not trigger rollback')
    assert '自动重试' in engine.finish_update(plan)
    assert plan['stage'] == 'verified' and plan['cleanupPending']
    assert plan['cleanupRetryAt'] > 0


def test_external_restart_of_original_prevents_forced_delete(store):
    engine, plan, removed, _ = fixture(store, running_old=True)
    engine.finish_update(plan)
    assert not removed and plan['cleanupPending']


def test_unhealthy_replacement_prevents_original_deletion(store):
    engine, plan, removed, new = fixture(store)
    new.attrs['State']['Health']['Status'] = 'unhealthy'
    engine.finish_update(plan)
    assert not removed and plan['cleanupPending']


@pytest.mark.parametrize('missing_old', [False, True])
def test_interruption_after_verification_completes_cleanup_instead_of_rollback(store, missing_old):
    engine, plan, removed, _ = fixture(store, missing_old=missing_old)
    engine.rollback = lambda _: pytest.fail('verified replacement must not be rolled back')
    store.put('jobs', 'job', plan)
    service = Service(store, engine)
    try:
        service.recover()
        job = store.get('jobs', 'job')
        assert job['status'] == 'success' and job['stage'] == 'cleaned'
        assert len(removed) == (0 if missing_old else 1)
    finally:
        service.close()


def test_pending_cleanup_retries_after_due_time(store):
    engine, plan, removed, _ = fixture(store)
    plan.update(status='success', cleanupRetryAt=100)
    store.put('jobs', 'job', plan)
    service = Service(store, engine)
    try:
        service.retry_cleanup(99)
        assert not removed
        service.retry_cleanup(100)
        assert removed and store.get('jobs', 'job')['stage'] == 'cleaned'
    finally:
        service.close()


@pytest.mark.parametrize('racing_tag', [False, True])
def test_update_preserves_source_tag_but_rejects_wrong_actual_image(store, racing_tag):
    from backend.engine import OperationError, configuration_fingerprint
    old_attrs = {'Id': 'old', 'Image': 'image-old', 'Config': {'Image': 'registry:5000/app:latest', 'Labels': {}},
                 'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}}, 'Mounts': [],
                 'State': {'Running': True}, 'NetworkSettings': {'Networks': {}}}
    removed, started, tagged, created, rolled_back = [], [], [], [], []
    old = SimpleNamespace(id='old', name='app', attrs=old_attrs, reload=lambda: None,
                          update=lambda **_: None, stop=lambda **_: None,
                          remove=lambda **kw: removed.append(kw))
    old.rename = lambda name: setattr(old, 'name', name)
    old.stop = lambda **_: old.attrs['State'].update(Running=False)
    plan = dict(id='job', action='update', status='running', name='app', oldId='old', oldImage='image-old',
                wasRunning=True, configuration=configuration_fingerprint(old_attrs))
    new = SimpleNamespace(id='new', name='app', attrs={'Image': 'wrong-image' if racing_tag else 'image-new',
                          'Config': {'Labels': {'docker-monitor.job': 'job'}}, 'State': {'Running': True}},
                          start=lambda: started.append(True))
    def create(config, name):
        created.append(config)
        return {'Id': 'new'}
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda cid: old if cid == 'old' else new),
                             images=SimpleNamespace(get=lambda _: SimpleNamespace(tag=lambda repo, tag: tagged.append((repo, tag)) or True)),
                             api=SimpleNamespace(create_container_from_config=create))
    engine = Engine(store, client)
    engine.dependencies = lambda _: None
    engine.check = lambda _: {'status': 'available', 'digest': 'digest-new'}
    engine.target = lambda *_: 'image-new'
    engine.wait_healthy = lambda _: None
    engine.rollback = lambda p: rolled_back.append(True)
    if racing_tag:
        with pytest.raises(OperationError, match='镜像标签'):
            engine.update(old, plan)
        assert rolled_back and not started and not removed
    else:
        assert '自动删除' in engine.update(old, plan)
        assert started and removed and not rolled_back
    assert tagged == [('registry:5000/app', 'latest')]
    assert created[0]['Image'] == 'registry:5000/app:latest'


def test_legacy_successful_backup_is_cleaned_only_after_identity_checks(store):
    engine, plan, removed, _ = fixture(store)
    plan.update(status='success', stage='created')
    store.put('jobs', 'job', plan)
    service = Service(store, engine)
    try:
        service.recover()
        assert removed == [{'v': False, 'force': False}]
        assert store.get('jobs', 'job')['stage'] == 'cleaned'
    finally:
        service.close()
