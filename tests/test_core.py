from tests.http_helpers import csrf_headers
import copy
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.engine import Engine, OperationError, cpu_percent, image_reference, memory_bytes, preserved_config
from backend.service import Service, next_run
from backend.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state.sqlite3")


def attrs():
    return {"Id": "a" * 64, "Image": "sha256:old", "Config": {"Image": "registry:5000/app:latest", "Env": ["CUSTOM=kept"],
        "Labels": {"com.docker.compose.service": "app"}, "Cmd": ["sleep", "3600"]},
        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}, "NetworkMode": "private",
                       "PortBindings": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "9099"}]}, "Binds": ["/tmp/data:/data:ro"]},
        "Mounts": [{"Type": "bind", "Source": "/tmp/data", "Destination": "/data", "RW": False},
                   {"Type": "volume", "Name": "anonymous-existing-volume", "Destination": "/var/lib/app", "RW": True}],
        "NetworkSettings": {"Networks": {"private": {"Aliases": ["app", "a"*12], "IPAddress": "172.18.0.22", "IPAMConfig": {"IPv4Address": "172.18.0.22"}}}}}


def test_preserves_configuration_and_reuses_volumes():
    original = attrs()
    before = copy.deepcopy(original)
    result = preserved_config(original, "sha256:new", "app")
    assert original == before
    assert result["Image"] == "sha256:new"
    assert result["Env"] == ["CUSTOM=kept"]
    assert result["HostConfig"]["PortBindings"] == original["HostConfig"]["PortBindings"]
    assert result["HostConfig"]["Binds"] == original["HostConfig"]["Binds"]
    assert result["HostConfig"]["Mounts"] == [{"Type": "volume", "Source": "anonymous-existing-volume", "Target": "/var/lib/app", "ReadOnly": False}]
    assert result["Labels"]["com.docker.compose.service"] == "app"
    assert result["Labels"]["docker-monitor.source-image"] == "registry:5000/app:latest"
    assert result["NetworkingConfig"]["EndpointsConfig"]["private"] == {"Aliases": ["app"], "IPAMConfig": {"IPv4Address": "172.18.0.22"}}


@pytest.mark.parametrize("field,value", [("NetworkMode", "container:other"), ("PidMode", "container:other"), ("AutoRemove", True), ("Links", ["other:db"]), ("VolumesFrom", ["other"])])
def test_unsupported_topologies_fail_before_mutation(field, value):
    original = attrs()
    original["HostConfig"][field] = value
    with pytest.raises(OperationError):
        preserved_config(original, "sha256:new", "app")


def test_new_digest_resets_wait_but_recheck_does_not(store):
    store.discovery("app", "old", "sha256:first", True, now=100)
    assert store.discovery("app", "old", "sha256:first", True, now=200)["discoveredAt"] == 100
    assert store.discovery("app", "old", "sha256:second", True, now=300)["discoveredAt"] == 300
    assert store.discovery("app", "recreated", "sha256:second", True, now=400)["discoveredAt"] == 400
    assert store.discovery("app", "recreated", "sha256:second", False, now=500)["discoveredAt"] is None


def test_cron_timezone_and_five_fields():
    base = datetime(2026, 10, 9, 10, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    assert datetime.fromtimestamp(next_run("0 3 * * *", base), ZoneInfo("Asia/Shanghai")) == datetime(2026, 10, 10, 3, tzinfo=ZoneInfo("Asia/Shanghai"))
    with pytest.raises(ValueError):
        next_run("0 0 3 * * *", base)
    with pytest.raises(ValueError):
        next_run("0 99 * * *", base)


def test_cpu_multicore_and_memory_cache():
    assert cpu_percent({"cpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 200, "online_cpus": 4},
                        "precpu_stats": {"cpu_usage": {"total_usage": 50}, "system_cpu_usage": 100}}) == 200
    assert memory_bytes({"memory_stats": {"usage": 1000, "stats": {"inactive_file": 200}}}) == 800
    assert cpu_percent({}) == 0


@pytest.mark.parametrize("reference,expected", [("nginx:latest", "docker.io/library/nginx"), ("docker.io/nginx:latest", "docker.io/nginx"), ("index.docker.io/library/nginx@sha256:a", "docker.io/library/nginx"), ("host:5000/app:latest", "host:5000/app")])
def test_reference_normalization(reference, expected):
    assert image_reference(reference) == expected


def test_fixed_and_delay_are_alternatives(store):
    c = SimpleNamespace(name="app", id="a"*64, attrs={"Image": "old", "State": {"Running": True}})
    service = Service(store, SimpleNamespace(protected=lambda c: False))
    service.visible_containers = lambda: [c]
    admitted = []
    service.submit = lambda *a, **k: admitted.append(a)
    store.discovery("app", "old", "sha256:new", True, now=100)
    store.put("settings", "policy", {"updateMode": "delay", "waitHours": 24, "auto": ["app"]})
    service.automatic(100 + 23*3600)
    assert not admitted
    service.automatic(100 + 24*3600)
    assert len(admitted) == 1
    admitted.clear()
    store.put("settings", "policy", {"updateMode": "scheduled", "waitHours": 720, "auto": ["app"]})
    service.automatic(101, scheduled=True)
    assert len(admitted) == 1  # Fixed schedule ignores waiting hours.
    service.close()


def test_protection_and_full_ids(store):
    c = SimpleNamespace(id="a"*64, attrs={"Config": {"Labels": {"docker-monitor.protected": "true"}}})
    engine = Engine(store, SimpleNamespace(containers=SimpleNamespace(get=lambda cid: c)))
    with pytest.raises(OperationError, match="受保护"):
        engine.get("a"*64)
    with pytest.raises(OperationError, match="ID"):
        engine.get("a"*12)


def test_api_validation_csrf_and_persistent_policy(store, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    service = Service(store)
    service.start = lambda: None
    client = TestClient(create_app(service))
    body = {"checkCron": "0 */6 * * *", "updateMode": "delay", "waitHours": 24, "auto": []}
    assert client.put("/api/policy", json=body).status_code == 403
    headers = csrf_headers(client)
    assert client.put("/api/policy", json=body, headers={**headers, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.put("/api/policy", json=body, headers=headers).status_code == 200
    assert Store(store.db.execute("PRAGMA database_list").fetchone()[2]).policy()["updateMode"] == "delay"
    assert client.put("/api/policy", json={**body, "checkCron": "0 99 * * *"}, headers=headers).status_code == 422
    assert client.post("/api/containers/"+"a"*64+"/actions", json={"action": "delete"}, headers=headers).status_code == 422
    service.close()


def test_session_auth_is_applied_to_api(store, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "test-password")
    service = Service(store)
    service.start = lambda: None
    client = TestClient(create_app(service))
    assert client.get("/api/policy").status_code == 401
    assert client.get("/api/policy", auth=("admin", "test-password")).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "test-password"}, headers=csrf_headers(client)).status_code == 200
    assert client.get("/api/policy").status_code == 200
    assert client.get("/healthz").status_code == 200
    service.close()


def test_delay_policy_ignores_inactive_invalid_update_cron(store):
    service = Service(store)
    before = store.policy()["updateCron"]
    result = service.save_policy({**store.policy(), "updateMode": "delay", "updateCron": "invalid"})
    assert result["updateCron"] == before
    assert store.get("settings", "nextUpdate") > 0
    service.close()


def test_containerd_digest_check_when_old_image_metadata_is_gone(store):
    from docker.errors import NotFound
    def missing(_):
        raise NotFound("old image was untagged")
    images = SimpleNamespace(get=missing, get_registry_data=lambda ref: SimpleNamespace(id=("sha256:" + "1" * 64)))
    c = SimpleNamespace(name="app", id="a"*64, attrs={**attrs(),
        "Image": "sha256:" + "1" * 64, "ImageManifestDescriptor": {"digest": "sha256:platform", "platform": {"os": "linux", "architecture": "arm64"}}})
    engine = Engine(store, SimpleNamespace(images=images))
    assert engine.check(c)["status"] == "current"
    images.get_registry_data = lambda ref: SimpleNamespace(id=("sha256:" + "2" * 64))
    engine.registry.invalidate("registry:5000/app:latest")
    assert engine.check(c)["status"] == "available"
    pulled = []
    images.pull = lambda ref, platform: pulled.append((ref, platform)) or SimpleNamespace(id="sha256:immutable")
    assert engine.target(c, ("sha256:" + "2" * 64)) == "sha256:immutable"
    assert pulled == [(("registry:5000/app@sha256:" + "2" * 64), "linux/arm64")]


def test_swarm_and_retained_backups_are_protected(store):
    engine = Engine(store, SimpleNamespace())
    c = SimpleNamespace(id="a"*64, attrs={"Config": {"Labels": {"com.docker.swarm.service.id": "service"}}})
    assert engine.protected(c)
    c.attrs["Config"]["Labels"] = {}
    store.put("jobs", "job", {"id": "job", "action": "update", "oldId": c.id, "status": "running", "stage": "prepared"})
    assert engine.protected(c)
    store.put("jobs", "job", {"id": "job", "action": "update", "oldId": c.id, "status": "success", "newId": "b"*64})
    assert engine.protected(c)


def test_interrupted_updates_recover_durably(store):
    recovered = []
    service = Service(store, SimpleNamespace(rollback=lambda job: recovered.append(job["id"])))
    store.put("jobs", "job", {"id": "job", "action": "update", "status": "running", "stage": "created"})
    service.recover()
    assert recovered == ["job"]
    assert store.get("jobs", "job")["status"] == "interrupted"
    service.recover()
    assert recovered == ["job"]
    service.close()


def test_temporary_check_failure_keeps_first_observation(store):
    service = Service(store)
    store.discovery("app", "old", "sha256:new", True, now=100)
    service.check_error("app", "old", "registry unavailable")
    assert store.discovery("app", "old", "sha256:new", True, now=300)["discoveredAt"] == 100
    service.close()


def test_disabling_auto_while_pulling_cancels_recreation(store):
    from backend.engine import configuration_fingerprint
    original = attrs()
    original["State"] = {"Running": True}
    c = SimpleNamespace(name="app", id="a"*64, attrs=original, reload=lambda: None)
    engine = Engine(store, SimpleNamespace())
    engine.dependencies = lambda _: None
    engine.check = lambda _: {"status": "available", "digest": "sha256:new", "discoveredAt": 100}
    store.put("settings", "policy", {"updateMode": "scheduled", "auto": ["app"]})
    def pull_and_disable(*_):
        store.put("settings", "policy", {"updateMode": "scheduled", "auto": []})
        return "sha256:new"
    engine.target = pull_and_disable
    plan = {"automatic": True, "oldImage": "sha256:old", "name": "app", "wasRunning": True,
            "configuration": configuration_fingerprint(original)}
    assert engine.update(c, plan) == "自动更新已关闭，本次更新取消"
    assert "stage" not in plan
