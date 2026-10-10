"""Docker SDK adapter; same-tag updates, immutable targets, and temporary rollback containers.

Design references: dockpeek/update_manager.py (configuration/network preservation),
WUD Docker trigger (health gates, rollback). No source code is copied.
"""
import copy
import hashlib
import json
import os
import time
from datetime import datetime, timezone

from .store import wait_seconds
from .registry import RegistryResolver, RegistryError, friendly_error

import docker
from docker.errors import APIError, NotFound

SOURCE_LABEL = "docker-monitor.source-image"
PROTECT_LABEL = "docker-monitor.protected"


class OperationError(Exception):
    pass


def source_image(attrs):
    return (attrs.get("Config", {}).get("Labels") or {}).get(SOURCE_LABEL) or attrs["Config"]["Image"]


def image_reference(ref):
    """docker.io/library/foo normalization; tags have no effect on repository identity."""
    name = ref.split("@", 1)[0]
    if ":" in name.rsplit("/", 1)[-1]:
        name = name.rsplit(":", 1)[0]
    first = name.split("/", 1)[0]
    if "/" not in name or not ("." in first or ":" in first or first == "localhost"):
        name = "docker.io/" + ("library/" if "/" not in name else "") + name
    return name.replace("index.docker.io/", "docker.io/")


def cpu_percent(stats):
    cur, prev = stats.get("cpu_stats", {}), stats.get("precpu_stats", {})
    delta = cur.get("cpu_usage", {}).get("total_usage", 0) - prev.get("cpu_usage", {}).get("total_usage", 0)
    system = cur.get("system_cpu_usage", 0) - prev.get("system_cpu_usage", 0)
    cores = cur.get("online_cpus") or len(cur.get("cpu_usage", {}).get("percpu_usage", [])) or 1
    return max(0, delta / system * cores * 100) if system > 0 and delta > 0 else 0


def configuration_fingerprint(attrs):
    networks = {name: {"Aliases": endpoint.get("Aliases"), "IPAMConfig": endpoint.get("IPAMConfig")}
                for name, endpoint in attrs.get("NetworkSettings", {}).get("Networks", {}).items()}
    value = {"Config": attrs["Config"], "HostConfig": attrs["HostConfig"], "Networks": networks}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def memory_bytes(stats):
    m = stats.get("memory_stats", {})
    cache = m.get("stats", {}).get("inactive_file", m.get("stats", {}).get("total_inactive_file", 0))
    return max(0, m.get("usage", 0) - cache)


def preserved_config(attrs, image_id, name):
    config = copy.deepcopy(attrs["Config"])
    config["Image"] = image_id
    config.setdefault("Labels", {})
    config["Labels"] = config["Labels"] or {}
    config["Labels"][SOURCE_LABEL] = source_image(attrs)
    host = copy.deepcopy(attrs["HostConfig"])
    if host.get("AutoRemove"):
        raise OperationError("自动删除容器不支持原位更新")
    if host.get("NetworkMode", "").startswith("container:") or host.get("PidMode", "").startswith("container:"):
        raise OperationError("共享其他容器命名空间的容器需要通过原部署项目更新")
    if host.get("Links") or host.get("VolumesFrom"):
        raise OperationError("包含 links 或 volumes_from 的容器需要通过原部署项目更新")
    # Turn anonymous volumes into explicit references to the SAME volume.
    binds = list(host.get("Binds") or [])
    mounts = list(host.get("Mounts") or [])
    mapped = {m.get("Target") for m in mounts}
    for bind in binds:
        parts = bind.split(":")
        if len(parts) >= 2:
            mapped.add(parts[1])
    for mount in attrs.get("Mounts", []):
        target = mount["Destination"]
        if target in mapped or mount.get("Type") == "tmpfs":
            continue
        source = mount.get("Name") if mount["Type"] == "volume" else mount.get("Source")
        if source:
            mounts.append({"Type": mount["Type"], "Source": source, "Target": target,
                           "ReadOnly": not mount.get("RW", True)})
            mapped.add(target)
    if mounts:
        host["Mounts"] = mounts
    config["HostConfig"] = host
    endpoints = {}
    for network, endpoint in attrs.get("NetworkSettings", {}).get("Networks", {}).items():
        if network in ("host", "none", "bridge"):
            continue
        # Preserve requested static addresses and aliases, never generated IP/MAC/IDs.
        aliases = [a for a in (endpoint.get("Aliases") or []) if a not in (attrs["Id"], attrs["Id"][:12])]
        endpoints[network] = {"Aliases": list(dict.fromkeys(aliases + [name])),
                              "IPAMConfig": endpoint.get("IPAMConfig")}
    if endpoints:
        config["NetworkingConfig"] = {"EndpointsConfig": endpoints}
    return config


class Engine:
    def __init__(self, store, client=None):
        self.store = store
        self.client = client
        self.monitor_client = client
        self.registry = RegistryResolver()
        self.registry_client = client
        self.self_id = os.environ.get("MONITOR_CONTAINER_ID", os.environ.get("HOSTNAME", ""))
        self.on_discovery = None

    def connect(self):
        if self.client is None:
            self.client = docker.from_env(timeout=60)
        return self.client

    def monitor(self):
        if self.monitor_client is None:
            self.monitor_client = docker.from_env(timeout=5)
        return self.monitor_client

    def registry_connect(self):
        if self.registry_client is None:
            self.registry_client = docker.from_env(timeout=8)
        return self.registry_client

    def protected(self, c):
        labels = c.attrs.get("Config", {}).get("Labels") or {}
        archived = any(job.get("action") in ("update", "repair") and job.get("oldId") == c.id
                       and ((job.get("status") == "success" and job.get("newId"))
                            or (job.get("status") in ("queued", "running") and job.get("stage")))
                       for job in self.store.all("jobs"))
        return (archived or bool(labels.get("com.docker.swarm.service.id"))
                or labels.get(PROTECT_LABEL, "").lower() == "true" or bool(self.self_id and c.id.startswith(self.self_id)))

    def get(self, cid):
        # Require a full ID, never resolve a user's substring or image/name ambiguity.
        if len(cid) != 64 or any(ch not in "0123456789abcdef" for ch in cid):
            raise OperationError("容器 ID 无效")
        c = self.connect().containers.get(cid)
        if self.protected(c):
            raise OperationError("该容器受保护")
        return c

    def dependencies(self, c):
        for other in self.connect().containers.list(all=True):
            if other.id == c.id:
                continue
            host = other.attrs.get("HostConfig", {})
            if any(host.get(field) in ("container:" + c.id, "container:" + c.name, "container:" + c.id[:12])
                   for field in ("NetworkMode", "PidMode")):
                raise OperationError("其他容器共享此容器的命名空间，需要通过原部署项目更新")

    def check(self, c):
        ref = source_image(c.attrs)
        if "@sha256:" in ref or ref.startswith("sha256:"):
            raise OperationError("固定摘要或本地镜像没有可跟踪的镜像标签")
        registry = self.registry.resolve(ref, self.registry_connect())
        digest = registry["digest"]
        try:
            image = self.connect().images.get(c.attrs["Image"])
            local_digests = [x.split("@", 1)[1] for x in (image.attrs.get("RepoDigests") or [])
                             if "@" in x and image_reference(x) == image_reference(ref)]
        except NotFound:
            local_digests = []
        # Docker's containerd image store uses manifest/index IDs, rather than the
        # classic store's config IDs. A tag move can remove old RepoDigests and even
        # old image metadata while a container still references its original digest.
        manifest = c.attrs.get("ImageManifestDescriptor")
        if manifest:
            local_digests += [c.attrs["Image"], manifest["digest"]]
        if not local_digests:
            raise OperationError("当前镜像缺少仓库摘要，无法可靠比较更新")
        if not digest.startswith("sha256:"):
            raise OperationError("镜像仓库未返回有效摘要")
        value = self.store.discovery(c.name, c.attrs["Image"], digest, digest not in local_digests)
        value["checkSource"] = registry["checkSource"]
        self.store.put("images", c.name, value)
        if self.on_discovery:
            self.on_discovery(c, value)
        self.store.put("settings", "lastCheck", time.time())
        return value

    def target(self, c, digest):
        ref = source_image(c.attrs)
        if "@" in ref or ref.startswith("sha256:"):
            raise OperationError("固定摘要或本地镜像不支持标签更新")
        descriptor = c.attrs.get("ImageManifestDescriptor", {}).get("platform", {})
        if descriptor:
            platform = descriptor.get("os", "linux") + "/" + descriptor["architecture"]
            if descriptor.get("variant"):
                platform += "/" + descriptor["variant"]
        else:
            image = self.connect().images.get(c.attrs["Image"]).attrs
            platform = image.get("Os", "linux") + "/" + image.get("Architecture", "amd64")
            if image.get("Variant"):
                platform += "/" + image["Variant"]
        for candidate, verification in self.registry.pull_candidates(ref, digest, self.registry_connect()):
            try:
                if verification:
                    base, repo = verification
                    self.registry.manifest(base, repo, digest)
                target = self.connect().images.pull(candidate, platform=platform)
                return target.id
            except Exception as exc:
                # Pulling never touches the current container. A failed source can
                # be retried at another configured source, pinned to the SAME digest.
                if isinstance(exc, RegistryError) and exc.code == "invalid":
                    raise OperationError(str(exc)) from exc
        raise OperationError("目标镜像拉取失败，可用镜像源均未成功；原容器继续运行")

    def wait_healthy(self, c):
        deadline = time.monotonic() + float(os.environ.get("HEALTH_TIMEOUT", "120"))
        grace = float(os.environ.get("START_GRACE", "10"))
        started = time.monotonic()
        while time.monotonic() < deadline:
            c.reload()
            state = c.attrs["State"]
            if not state.get("Running") or state.get("Restarting"):
                raise OperationError("新容器退出或正在反复重启")
            health = state.get("Health", {}).get("Status")
            if health == "unhealthy":
                raise OperationError("新容器健康检查失败")
            if health == "healthy" or (not health and time.monotonic() - started >= grace):
                return
            time.sleep(1)
        raise OperationError("新容器健康检查超时")

    def rollback(self, plan):
        client = self.connect()
        old = client.containers.get(plan["oldId"])
        # After a process interruption, creation may have happened before recording newId.
        try:
            current = client.containers.get(plan.get("newId") or plan["name"])
            if current.id != old.id:
                if (current.attrs.get("Config", {}).get("Labels") or {}).get("docker-monitor.job") != plan["id"]:
                    raise OperationError("容器名称已被外部操作占用，保留原容器等待人工恢复")
                current.remove(force=True, v=False)
        except NotFound:
            pass
        if old.name != plan["name"]:
            old.rename(plan["name"])
        old.update(restart_policy=plan["restartPolicy"])
        self.reconnect_old(plan)
        if plan["wasRunning"]:
            old.start()
        plan["rolledBack"] = True

    def finish_update(self, plan):
        """Remove only this job's stopped original, without deleting any volumes.

        The durable verified stage is written before deletion so a restart cannot
        try to roll back to an original that has already been removed.
        """
        if plan.get("stage") not in ("verified", "cleaned"):
            raise OperationError("新容器尚未通过验证，不能清理原容器")
        try:
            replacement = self.connect().containers.get(plan["newId"])
            if (replacement.name != plan["name"]
                    or replacement.attrs["Image"] != plan["targetImage"]
                    or (replacement.attrs.get("Config", {}).get("Labels") or {}).get("docker-monitor.job") != plan["id"]):
                raise OperationError("新容器已被外部修改，暂停清理")
            if plan["wasRunning"] and (not replacement.attrs["State"].get("Running")
                    or replacement.attrs["State"].get("Restarting")
                    or replacement.attrs["State"].get("Health", {}).get("Status") == "unhealthy"):
                raise OperationError("新容器状态异常，暂停清理")
            try:
                old = self.connect().containers.get(plan["oldId"])
            except NotFound:
                old = None  # Deletion completed before a process interruption.
            if old is not None:
                if old.name != plan["backupName"] or old.attrs["State"].get("Running"):
                    raise OperationError("原容器已被外部修改，暂停清理")
                old.remove(v=False, force=False)
            plan.update(stage="cleaned", cleanupPending=False)
            plan.pop("cleanupRetryAt", None)
            message = ("镜像名称修复成功" if plan.get("action") == "repair" else "更新成功") + "，旧容器已自动删除"
        except Exception:
            # Cleanup failure must never roll back a working replacement.
            plan.update(cleanupPending=True, cleanupRetryAt=time.time() + 60)
            message = ("镜像名称修复成功" if plan.get("action") == "repair" else "更新成功") + "，旧容器清理未完成，将自动重试"
        plan["message"] = message
        self.store.put("jobs", plan["id"], plan)
        return message

    def update(self, c, plan):
        client = self.connect()
        self.dependencies(c)
        config = preserved_config(c.attrs, c.attrs["Image"], c.name)  # Validate before any change.
        self.registry.invalidate(source_image(c.attrs))
        discovered = self.check(c)
        if discovered["status"] != "available":
            return "当前镜像已是最新"
        policy = self.store.policy()
        if plan.get("automatic") and policy["updateMode"] == "delay" and time.time() - discovered["discoveredAt"] < wait_seconds(policy):
            return "目标镜像已变化，重新等待后再更新"
        image_id = self.target(c, discovered["digest"])
        if image_id == plan["oldImage"]:
            self.store.discovery(c.name, image_id, discovered["digest"], False)
            return "当前平台的镜像内容已是最新"
        plan["discoveredAt"] = discovered.get("discoveredAt")
        return self.replace_container(c, plan, image_id, discovered["digest"])

    def repair_reference(self, c, plan):
        ref = source_image(c.attrs)
        if not c.attrs["Config"]["Image"].startswith("sha256:"):
            return "镜像名称已正常，无需修复"
        if ref.startswith("sha256:") or "@" in ref:
            raise OperationError("没有可靠的原镜像标签，不能自动修复")
        if (not c.attrs["State"].get("Running") or c.attrs["State"].get("Restarting")
                or c.attrs["State"].get("Health", {}).get("Status") == "unhealthy"):
            raise OperationError("容器状态异常，请先处理运行问题")
        self.dependencies(c)
        # Recreate the exact current image, without checking or pulling a newer one.
        return self.replace_container(c, plan, c.attrs["Image"], None)

    def replace_container(self, c, plan, image_id, digest):
        client = self.connect()
        c.reload()
        if (c.attrs["Image"] != plan["oldImage"] or c.name != plan["name"]
                or bool(c.attrs["State"].get("Running")) != plan["wasRunning"]
                or configuration_fingerprint(c.attrs) != plan["configuration"]):
            raise OperationError("容器已被外部修改，本次更新取消")
        if plan.get("automatic") and (c.attrs["State"].get("Restarting") or c.attrs["State"].get("Health", {}).get("Status") == "unhealthy"):
            raise OperationError("容器健康状态已变化，本次自动更新取消")
        if plan.get("automatic"):
            policy = self.store.policy()
            if c.name not in policy["auto"]:
                return "自动更新已关闭，本次更新取消"
            if policy["updateMode"] == "delay" and time.time() - plan["discoveredAt"] < wait_seconds(policy):
                return "等待时间已变化，继续等待后再更新"
        # Capture the latest configuration after pulling and pin the immutable image ID.
        config = preserved_config(c.attrs, image_id, c.name)
        # Other Docker managers use Config.Image to track updates. Keep the source
        # tag there and verify the created container's immutable ID before start.
        ref = source_image(c.attrs)
        repository, tag = ref, "latest"
        if ":" in ref.rsplit("/", 1)[-1]:
            repository, tag = ref.rsplit(":", 1)
        if not client.images.get(image_id).tag(repository, tag=tag):
            raise OperationError("目标镜像标签写入失败，原容器继续运行")
        config["Image"] = ref
        config["Labels"]["docker-monitor.job"] = plan["id"]
        plan.update(targetImage=image_id, targetDigest=digest,
                    backupName=c.name + ".backup-" + plan["id"][:8], stage="prepared")
        self.store.put("jobs", plan["id"], plan)
        changed = False
        try:
            c.rename(plan["backupName"])
            changed = True
            c.update(restart_policy={"Name": "no", "MaximumRetryCount": 0})
            if plan["wasRunning"]:
                c.stop(timeout=c.attrs.get("Config", {}).get("StopTimeout") or 10)
            # Release static IPs/aliases held by the rollback container.
            plan["networks"] = config.get("NetworkingConfig", {}).get("EndpointsConfig", {})
            self.store.put("jobs", plan["id"], plan)
            for network in plan["networks"]:
                client.networks.get(network).disconnect(c, force=True)
            created = client.api.create_container_from_config(config, name=plan["name"])
            plan.update(newId=created["Id"], stage="created")
            self.store.put("jobs", plan["id"], plan)
            replacement = client.containers.get(created["Id"])
            if replacement.attrs["Image"] != image_id:
                raise OperationError("镜像标签在更新期间被修改，取消更新并恢复原容器")
            if plan["wasRunning"]:
                replacement.start()
                self.wait_healthy(replacement)
            if plan.get("action") != "repair":
                self.store.discovery(plan["name"], image_id, digest, False)
            plan.update(stage="verified", cleanupPending=True)
            self.store.put("jobs", plan["id"], plan)
        except Exception:
            if changed:
                self.rollback(plan)
            raise
        return self.finish_update(plan)

    def reconnect_old(self, plan):
        old = self.connect().containers.get(plan["oldId"])
        for name, endpoint in plan.get("networks", {}).items():
            old.reload()
            if name not in old.attrs.get("NetworkSettings", {}).get("Networks", {}):
                self.connect().api.connect_container_to_network(old.id, name,
                    aliases=endpoint.get("Aliases"),
                    ipv4_address=(endpoint.get("IPAMConfig") or {}).get("IPv4Address"),
                    ipv6_address=(endpoint.get("IPAMConfig") or {}).get("IPv6Address"))

    def row(self, c, busy=False, collect_metrics=True, reload=True):
        if reload:
            c.reload()
        attrs = c.attrs
        state = attrs["State"]
        running = bool(state.get("Running"))
        status = "warning" if running and (state.get("Health", {}).get("Status") == "unhealthy" or state.get("Restarting")) else "running" if running else "stopped"
        cpu, memory, measured = 0, 0, False
        if running and collect_metrics:
            try:
                stats = self.monitor().api.stats(c.id, stream=False, one_shot=False)
                cpu, memory, measured = cpu_percent(stats), memory_bytes(stats) / 1048576, True
            except APIError:
                pass
        when = state.get("StartedAt" if running else "FinishedAt", "")
        try:
            if when.startswith("0001"):
                raise ValueError("never started")
            elapsed = max(0, time.time() - datetime.fromisoformat(when[:26].replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp())
            duration = f"{int(elapsed // 86400)} 天" if elapsed >= 86400 else f"{int(elapsed // 3600)} 小时" if elapsed >= 3600 else f"{int(elapsed // 60)} 分钟"
        except ValueError:
            duration = "—"
        discovery = self.store.get("images", c.name, {})
        if discovery.get("imageId") != attrs["Image"]:
            discovery = {}
        icon = next((icon for key, icon in [("postgres", "database"), ("redis", "layers"), ("nginx", "network"),
                     ("home-assistant", "house"), ("immich", "images"), ("kuma", "activity"), ("backup", "archive")]
                     if key in c.name), "container")
        return {"id": c.id, "name": c.name, "image": source_image(attrs), "icon": icon, "state": status,
                "cpu": round(cpu, 1), "memory": round(memory, 1),
                "metricsAvailable": measured, "time": duration if running else "已停止 " + duration,
                "protected": self.protected(c), "self": bool(self.self_id and c.id.startswith(self.self_id)),
                "auto": c.name in self.store.policy()["auto"], "busy": busy,
                "update": discovery.get("status", "unchecked"),
                "updateError": friendly_error(discovery["error"]) if discovery.get("error") else None,
                "discoveredAt": discovery.get("discoveredAt"), "lastResult": None}
