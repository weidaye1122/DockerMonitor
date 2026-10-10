import threading
import time
from types import SimpleNamespace

from backend.engine import Engine
from backend.service import Service
from backend.store import Store


def test_snapshot_does_not_wait_for_metrics_and_never_scans_sizes(tmp_path):
    release = threading.Event()
    c = SimpleNamespace(id="a" * 64, name="app", attrs={"State": {"Running": True, "StartedAt": "first"}, "Image": "old"})
    calls = {"metrics": 0, "sizes": 0}

    def row(container, busy=False, collect_metrics=True, reload=True):
        if collect_metrics:
            calls["metrics"] += 1
            release.wait(3)
        return {"id": c.id, "cpu": 12 if collect_metrics else 0, "memory": 24 if collect_metrics else 0,
                "metricsAvailable": collect_metrics, "state": "running"}

    def sizes(**kwargs):
        calls["sizes"] += 1
        raise AssertionError("size scanning was removed")

    client = SimpleNamespace(info=lambda: {}, containers=SimpleNamespace(list=lambda **kw: [c]), api=SimpleNamespace(containers=sizes))
    store = Store(tmp_path / "db.sqlite3")
    service = Service(store, SimpleNamespace(connect=lambda: client, monitor=lambda: client, row=row))
    try:
        before = time.monotonic()
        snapshot = service.snapshot()
        assert time.monotonic() - before < .5
        assert snapshot["containers"][0]["metricsAvailable"] is False
        assert "size" not in snapshot["containers"][0]
        for _ in range(5):
            service.cached_at = 0
            service.snapshot()
        assert len(service.metrics_futures) == 1
        release.set()
        service.metrics_futures[c.id].result(timeout=2)
        service.cached_at = 0
        row_data = service.snapshot()["containers"][0]
        assert row_data["metricsAvailable"] and row_data["cpu"] == 12
        assert calls == {"metrics": 1, "sizes": 0}
        c.attrs["State"]["StartedAt"] = "restarted"
        service.cached_at = 0
        assert service.snapshot()["containers"][0]["metricsAvailable"] is False
    finally:
        release.set()
        service.close()


def test_lightweight_row_does_not_reload_or_collect_docker_stats(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    c = SimpleNamespace(id="a"*64, name="app", attrs={
        "State": {"Running": True, "StartedAt": "2026-10-09T00:00:00Z"},
        "Image": "old", "Config": {"Image": "nginx:latest", "Labels": {}}})
    c.reload = lambda: (_ for _ in ()).throw(AssertionError("unexpected inspect"))
    engine = Engine(store, SimpleNamespace())
    row = engine.row(c, collect_metrics=False, reload=False)
    assert row["name"] == "app" and row["state"] == "running"
    assert row["metricsAvailable"] is False
