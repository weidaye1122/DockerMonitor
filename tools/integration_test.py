"""Exercise the running app using only disposable, uniquely labelled Docker fixtures."""
import io
import ipaddress
import json
import os
import time
import uuid
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import docker
import httpx

def main():
    BASE = os.environ.get("MONITOR_TEST_URL", "http://127.0.0.1:9090")
    client = docker.from_env(timeout=120)
    http = httpx.Client(base_url=BASE, timeout=120, headers={"X-Monitor-Request": "1"}, trust_env=False)
    # Acquire the same cookie-bound token as the browser before any operation.
    csrf = http.get('/api/auth/csrf', headers={'X-Monitor-Client-Scheme': httpx.URL(BASE).scheme})
    csrf.raise_for_status()
    http.headers['X-CSRF-Token'] = csrf.json()['token']
    marker = uuid.uuid4().hex[:10]
    labels = {"docker-monitor.test-run": marker}
    name = "docker-monitor-test-" + marker
    image_tag = "localhost:5001/docker-monitor-test:latest"
    out = Path("output/integration")
    out.mkdir(parents=True, exist_ok=True)
    policy = http.get("/api/policy").json()
    report = []
    network = None


    def record(message):
        report.append(message)
        print(message, flush=True)


    def push_version(version, healthy=True):
        dockerfile = f'''FROM busybox:1.37
    RUN echo {version} > /revision {'&& touch /healthy' if healthy else ''}
    VOLUME /data
    EXPOSE 8080
    HEALTHCHECK --interval=1s --timeout=1s --retries=1 CMD test -f /healthy
    CMD ["sh","-c","while true; do sleep 3600; done"]
    '''
        image, _ = client.images.build(fileobj=io.BytesIO(dockerfile.encode()), tag=image_tag, rm=True)
        for event in client.images.push(image_tag, stream=True, decode=True):
            if event.get("error"):
                raise RuntimeError(event["error"])
        return image.id


    def action(container, action_name):
        result = http.post(f"/api/containers/{container.id}/actions", json={"action": action_name})
        result.raise_for_status()
        return wait_job(result.json()["jobId"])


    def wait_job(jid):
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            job = http.get("/api/jobs/" + jid).json()
            if job["status"] not in ("queued", "running"):
                return job
            time.sleep(1)
        raise AssertionError("job timed out")


    def current():
        return client.containers.get(name)


    def wait_image(image_id, timeout=100):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                c = current()
            except docker.errors.NotFound:
                # Updating briefly releases the original name before recreation.
                time.sleep(1)
                continue
            if c.attrs["Image"] == image_id and c.attrs["State"].get("Health", {}).get("Status") == "healthy":
                snapshot = http.get("/api/snapshot").json()
                if not next((row["busy"] for row in snapshot["containers"] if row["name"] == name), True):
                    return c
            time.sleep(1)
        raise AssertionError("automatic update timed out")


    try:
        registry = client.containers.run("registry:2", name="docker-monitor-registry-test-"+marker, detach=True,
            ports={"5000/tcp": ("127.0.0.1", 5001)}, labels={**labels, "docker-monitor.protected": "true"})
        for _ in range(30):
            try:
                if httpx.get("http://127.0.0.1:5001/v2/", trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        network = client.networks.create(name, labels=labels)
        network.reload()
        subnet = ipaddress.ip_network(network.attrs["IPAM"]["Config"][0]["Subnet"])
        ip = str(subnet.network_address + 10)
        original_image = push_version("v1")
        client.images.pull(image_tag)  # Match a real registry-installed container's RepoDigests.
        container = client.containers.run(original_image, name=name, detach=True,
            environment={"CUSTOM": "preserved"}, labels={**labels, "docker-monitor.source-image":image_tag}, restart_policy={"Name": "unless-stopped"},
            ports={"8080/tcp": ("127.0.0.1", 5002)}, network=network.name,
            networking_config={network.name: client.api.create_endpoint_config(ipv4_address=ip, aliases=["stable-alias"])} )
        container.exec_run(["sh", "-c", "echo retained > /data/marker"])
        container.reload()
        endpoint_before = container.attrs["NetworkSettings"]["Networks"][network.name]
        assert endpoint_before["IPAddress"] == ip, endpoint_before
        volume_name = next(m["Name"] for m in container.attrs["Mounts"] if m["Destination"] == "/data")
        record("Disposable fixture created with port, env, anonymous volume, static IP and network alias")
        assert action(container, "stop")["status"] == "success"
        container.reload()
        assert not container.attrs["State"]["Running"]
        assert action(container, "start")["status"] == "success"
        assert action(container, "restart")["status"] == "success"
        record("Real API start / stop / restart passed")
        monitor = client.containers.get("docker-monitor")
        assert http.post(f"/api/containers/{monitor.id}/actions", json={"action": "stop"}).status_code == 409
        record("Monitor self-protection rejected stop")
        from backend.repair import retained
        container.reload()
        before_repair = container.attrs
        repaired = action(container, "repair")
        assert repaired['status'] == 'success', repaired
        container = current()
        assert container.attrs['Config']['Image'] == image_tag
        assert container.attrs['Image'] == before_repair['Image']
        left, right = retained(before_repair), retained(container.attrs)
        for index, (before_part, after_part) in enumerate(zip(left, right)):
            if before_part != after_part:
                if isinstance(before_part, dict):
                    differences = {key:(before_part.get(key),after_part.get(key)) for key in before_part.keys() | after_part.keys()
                                   if before_part.get(key) != after_part.get(key)}
                else:
                    differences = (before_part, after_part)
                raise AssertionError((index, differences))
        assert container.exec_run(['cat','/data/marker']).output.strip() == b'retained'
        record("Legacy SHA image-reference repair preserved the exact image, configuration and volume data")
        new_image = push_version("v2")
        checked = action(container, "check")
        assert checked["status"] == "success", checked
        result = action(container, "update")
        assert result["status"] == "success", result
        updated = current()
        assert updated.id != container.id and updated.attrs["Image"] == new_image
        assert updated.attrs["Config"]["Image"] == image_tag
        assert "CUSTOM=preserved" in updated.attrs["Config"]["Env"]
        assert updated.attrs["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"] == "5002"
        assert updated.attrs["NetworkSettings"]["Networks"][network.name]["IPAddress"] == ip, updated.attrs["NetworkSettings"]["Networks"][network.name]
        assert "stable-alias" in updated.attrs["NetworkSettings"]["Networks"][network.name]["Aliases"]
        assert next(m["Name"] for m in updated.attrs["Mounts"] if m["Destination"] == "/data") == volume_name
        assert updated.exec_run(["cat", "/data/marker"]).output.strip() == b"retained"
        try:
            client.containers.get(container.id)
            raise AssertionError("successful update left the old container behind")
        except docker.errors.NotFound:
            pass
        record("Same-tag digest update preserved env, ports, static IP, network aliases, volume and data")
        record("Successful update removed the old container without deleting its reused volume")
        record("Replacement kept the original repository:tag for compatibility with other Docker managers")
        failed_image = push_version("bad", healthy=False)
        failed = action(updated, "update")
        assert failed["status"] == "failed" and failed["rolledBack"], failed
        rolled_back = current()
        assert rolled_back.id == updated.id and rolled_back.attrs["Image"] == new_image
        assert rolled_back.attrs["State"]["Running"]
        assert rolled_back.attrs["NetworkSettings"]["Networks"][network.name]["IPAddress"] == ip
        assert rolled_back.exec_run(["cat", "/data/marker"]).output.strip() == b"retained"
        record("Unhealthy replacement rolled back to original running container with data and network retained")
        delay_image = push_version("v3")
        response = http.put("/api/policy", json={**policy, "updateMode": "delay", "waitValue": 0, "waitUnit": "minutes", "auto": [name]})
        response.raise_for_status()
        assert action(current(), "check")["status"] == "success"
        wait_image(delay_image)
        record("Real delayed auto-update (wait=0) executed independently of detection Cron")
        schedule_image = push_version("v4")
        response = http.put("/api/policy", json={**policy, "updateMode": "scheduled", "updateCron": "* * * * *", "waitValue": 720, "waitUnit": "hours", "auto": [name]})
        response.raise_for_status()
        assert action(current(), "check")["status"] == "success"
        record("Waiting for next actual Cron minute (waiting-hours setting must be ignored)")
        wait_image(schedule_image)
        record("Real scheduled auto-update passed; fixed schedule ignored waiting hours")
        assert len(client.containers.list(all=True, filters={"label": "docker-monitor.test-run="+marker})) == 2
        record("Repeated manual and automatic updates left only the current fixture and test registry")
    finally:
        response = http.put("/api/policy", json=policy)
        response.raise_for_status()
        volumes = set()
        for fixture in client.containers.list(all=True, filters={"label": "docker-monitor.test-run="+marker}):
            volumes.update(m["Name"] for m in fixture.attrs.get("Mounts", []) if m["Type"] == "volume")
            fixture.remove(force=True, v=False)
        for volume in volumes:
            try:
                client.volumes.get(volume).remove()
            except docker.errors.NotFound:
                pass
        if network:
            network.remove()
        report.append("Test fixtures removed; original auto-update policy restored")
        (out / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(report[-1], flush=True)


if __name__ == "__main__":
    main()
