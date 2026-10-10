import threading
import time
import uuid
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from docker.errors import NotFound

from .engine import Engine, OperationError, configuration_fingerprint, source_image
from .registry import friendly_error
from .notifications import BarkNotifier
from .store import normalize_policy, wait_seconds

TZ = ZoneInfo("Asia/Shanghai")


def next_run(expression, now=None):
    if len(expression.split()) != 5 or not croniter.is_valid(expression):
        raise ValueError("请输入有效的五位 Cron：分 时 日 月 周")
    return croniter(expression, datetime.fromtimestamp(now or time.time(), TZ),
                    max_years_between_matches=5).get_next(datetime).timestamp()


class Service:
    def __init__(self, store, engine=None):
        self.store, self.engine = store, engine or Engine(store)
        self.notifications = BarkNotifier(store)
        if isinstance(self.engine, Engine):
            self.engine.on_discovery = self.notifications.discovery
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="docker-task")
        self.metrics_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="docker-stats")
        self.metrics_futures, self.metrics_cache = {}, {}
        self.lock, self.snapshot_lock = threading.RLock(), threading.Lock()
        self.active = set()
        self.checking = False
        self.stop_event = threading.Event()
        self.snapshot_cache, self.cached_at = None, 0
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True, name="docker-scheduler")
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        self.pool.shutdown(wait=True)
        self.metrics_pool.shutdown(wait=True)
        self.notifications.close()

    def recover(self):
        for job in self.store.all("jobs"):
            if (job.get("action") in ("update", "repair") and job.get("status") == "success"
                    and job.get("stage") == "created" and not job.get("rolledBack")
                    and all(job.get(key) for key in ("newId", "oldId", "targetImage", "backupName"))):
                # Older releases retained successful backups. Cleanup still checks
                # both exact container IDs, ownership, name, image and running state.
                job.update(stage="verified", cleanupPending=True)
                self.engine.finish_update(job)
            if job["status"] not in ("queued", "running"):
                continue
            try:
                if job["action"] in ("update", "repair") and job.get("stage"):
                    if job["stage"] in ("verified", "cleaned"):
                        job["message"] = self.engine.finish_update(job)
                        job["status"] = "success"
                        if job["action"] == "update":
                            self.notifications.event("success", job["name"], job["id"], job["message"])
                    else:
                        self.engine.rollback(job)
                        job["message"] = "服务重启，未完成的更新已恢复原容器"
                        job["status"] = "interrupted"
                else:
                    job["message"] = "服务重启，任务中断；请检查当前状态"
                    job["status"] = "interrupted"
            except NotFound:
                job.update(status="failed", message="原容器不存在，无法自动恢复")
            except Exception as exc:
                job.update(status="failed", message="恢复失败：" + str(exc))
            job["finishedAt"] = time.time()
            self.store.put("jobs", job["id"], job)

    def retry_cleanup(self, now):
        for job in self.store.all("jobs"):
            if (job.get("action") not in ("update", "repair") or job.get("status") != "success"
                    or not job.get("cleanupPending") or now < job.get("cleanupRetryAt", 0)):
                continue
            with self.lock:
                if job["name"] in self.active:
                    continue
                self.active.add(job["name"])
            try:
                self.engine.finish_update(job)
                self.cached_at = 0
            finally:
                with self.lock:
                    self.active.discard(job["name"])

    def submit(self, cid, action, automatic=False):
        if action not in ("start", "stop", "restart", "update", "check", "repair"):
            raise OperationError("不支持此操作")
        c = self.engine.get(cid)
        with self.lock:
            if c.name in self.active:
                raise OperationError("该容器已有任务正在执行")
            self.active.add(c.name)
            job = self.new_job(c, action, automatic)
            self.pool.submit(self.perform, job)
            return job

    def new_job(self, c, action, automatic=False):
        job = {"id": uuid.uuid4().hex, "containerId": c.id, "oldId": c.id,
               "name": c.name, "action": action, "automatic": automatic,
               "status": "queued", "createdAt": time.time(), "message": "等待执行",
               "oldImage": c.attrs["Image"], "wasRunning": bool(c.attrs["State"].get("Running")),
               "configuration": configuration_fingerprint(c.attrs),
               "restartPolicy": c.attrs["HostConfig"].get("RestartPolicy", {"Name": "no"})}
        self.store.put("jobs", job["id"], job)
        return job

    def eligible_update(self, c):
        state = c.attrs["State"]
        image = self.store.get("images", c.name, {})
        return (not self.engine.protected(c) and state.get("Running")
                and not state.get("Restarting") and state.get("Health", {}).get("Status") != "unhealthy"
                and image.get("status") == "available" and image.get("imageId") == c.attrs["Image"])

    def update_all(self, ids):
        if not ids or len(ids) != len(set(ids)):
            raise OperationError("请选择有效的可更新容器")
        containers = [self.engine.get(cid) for cid in ids]
        with self.lock:
            if any(c.name in self.active or not self.eligible_update(c) for c in containers):
                raise OperationError("容器状态已变化，请刷新后重新确认")
            self.active.update(c.name for c in containers)
            job = {"id": uuid.uuid4().hex, "name": "一键更新", "action": "update-all",
                   "status": "queued", "createdAt": time.time(), "message": f"等待更新 {len(containers)} 个容器"}
            self.store.put("jobs", job["id"], job)
            self.cached_at = 0
            try:
                self.pool.submit(self.perform_update_all, job, containers)
            except Exception:
                self.active.difference_update(c.name for c in containers)
                raise
            return job

    def perform_update_all(self, job, containers):
        errors, successful, skipped = [], 0, 0
        job.update(status="running")
        try:
            for index, original in enumerate(containers):
                job["message"] = f"正在更新 {original.name} · {index + 1}/{len(containers)}"
                self.store.put("jobs", job["id"], job)
                try:
                    c = self.engine.get(original.id)
                    if not self.eligible_update(c):
                        skipped += 1
                        continue
                    child = self.new_job(c, "update")
                    self.perform(child)  # Sequential; uses the same update and rollback path.
                    if child["status"] == "success":
                        successful += 1
                    else:
                        errors.append({"name": c.name, "message": child["message"]})
                except Exception as exc:
                    errors.append({"name": original.name, "message": str(exc)})
            job.update(status="partial" if errors else "success", errors=errors,
                       message=f"更新完成：成功 {successful}，失败 {len(errors)}，跳过 {skipped}")
        finally:
            job["finishedAt"] = time.time()
            self.store.put("jobs", job["id"], job)
            with self.lock:
                self.active.difference_update(c.name for c in containers)
                self.cached_at = 0

    def perform(self, job):
        job.update(status="running", message="执行中")
        self.store.put("jobs", job["id"], job)
        try:
            c = self.engine.get(job["containerId"])
            if job["automatic"]:
                policy = self.store.policy()
                if c.name not in policy["auto"] or not c.attrs["State"].get("Running") or c.attrs["State"].get("Health", {}).get("Status") == "unhealthy":
                    job.update(status="skipped", message="容器状态或更新策略已变化，本次跳过")
                    return
            action = job["action"]
            if action == "update":
                message = self.engine.update(c, job)
            elif action == "repair":
                message = self.engine.repair_reference(c, job)
            elif action == "check":
                if isinstance(self.engine, Engine):
                    self.engine.registry.invalidate(source_image(c.attrs))
                result = self.engine.check(c)
                message = "发现新镜像" if result["status"] == "available" else "已是最新"
            else:
                if action == "start":
                    c.start()
                elif action == "stop":
                    c.stop(timeout=10)
                else:
                    c.restart(timeout=10)
                message = {"start": "启动成功", "stop": "停止成功", "restart": "重启成功"}[action]
            job.update(status="success", message=message)
        except Exception as exc:
            message = friendly_error(exc) if job["action"] == "check" and not isinstance(exc, OperationError) else str(getattr(exc, "explanation", str(exc)))
            job.update(status="failed", message=message)
            if job["action"] == "check":
                self.check_error(job["name"], job["oldImage"], job["message"])
        finally:
            job["finishedAt"] = time.time()
            self.store.put("jobs", job["id"], job)
            if job.get("action") == "update" and job.get("status") in ("success", "failed"):
                changed = job.get("newId") and job["status"] == "success"
                if changed or job["status"] == "failed":
                    self.notifications.event("success" if changed else "failure", job["name"], job["id"],
                        job["message"] if changed else
                        ("更新失败，已恢复原容器。" if job.get("rolledBack") else "更新未完成，请打开 Docker Monitor 查看任务结果。"))
            with self.lock:
                self.active.discard(job["name"])
                self.cached_at = 0

    def check_error(self, name, image_id, message):
        previous = self.store.get("images", name, {})
        value = dict(previous) if previous.get("imageId") == image_id else {}
        value.update(name=name, imageId=image_id, status="error", error=message, checkedAt=time.time())
        self.store.put("images", name, value)

    def check_all(self):
        with self.lock:
            if self.checking:
                raise OperationError("更新检查正在执行")
            self.checking = True
        try:
            job = {"id": uuid.uuid4().hex, "name": "全部容器", "action": "check-all",
                   "status": "queued", "createdAt": time.time(), "message": "等待检查"}
            self.store.put("jobs", job["id"], job)
            self.pool.submit(self.perform_check_all, job)
            return job
        except Exception:
            self.checking = False
            raise

    def perform_check_all(self, job):
        errors, checked = [], 0
        job.update(status="running", message="检查镜像仓库中")
        self.store.put("jobs", job["id"], job)
        try:
            refreshed = set()
            for c in self.visible_containers():
                if self.engine.protected(c) or c.name in self.active:
                    continue
                try:
                    if isinstance(self.engine, Engine):
                        ref = source_image(c.attrs)
                        if ref not in refreshed:
                            self.engine.registry.invalidate(ref)
                            refreshed.add(ref)
                    self.engine.check(c)
                    checked += 1
                except Exception as exc:
                    message = str(exc) if isinstance(exc, OperationError) else friendly_error(exc)
                    errors.append({"name": c.name, "message": message})
                    self.check_error(c.name, c.attrs["Image"], message)
            job.update(status="success" if not errors else "partial", errors=errors,
                       message=f"已检查 {checked} 个容器，{len(errors)} 个未能检查")
            self.store.put("settings", "lastCheck", time.time())
        except Exception as exc:
            job.update(status="failed", message=str(exc))
        finally:
            job["finishedAt"] = time.time()
            self.store.put("jobs", job["id"], job)
            self.checking, self.cached_at = False, 0

    def visible_containers(self, client=None):
        hidden = {j["oldId"] for j in self.store.all("jobs") if j.get("action") == "update"
                  and ((j.get("status") in ("queued", "running") and j.get("stage"))
                       or (j.get("status") == "success" and j.get("newId")))}
        return [c for c in (client or self.engine.connect()).containers.list(all=True) if c.id not in hidden]

    @staticmethod
    def metrics_identity(c):
        state = c.attrs["State"]
        return (bool(state.get("Running")), state.get("StartedAt"), c.attrs.get("Image"))

    def collect_metrics(self, c):
        row = self.engine.row(c, collect_metrics=True, reload=False)
        return self.metrics_identity(c), time.time(), {k: row[k] for k in ("cpu", "memory", "metricsAvailable")}

    def sampled_rows(self, containers):
        now = time.time()
        visible = {c.id for c in containers}
        self.metrics_cache = {cid: v for cid, v in self.metrics_cache.items() if cid in visible}
        for cid, future in list(self.metrics_futures.items()):
            if future.done():
                try:
                    result = future.result()
                    if cid in visible:
                        self.metrics_cache[cid] = result
                except Exception:
                    self.metrics_cache.pop(cid, None)
                del self.metrics_futures[cid]
        rows = []
        for c in containers:
            row = self.engine.row(c, c.name in self.active, collect_metrics=False, reload=False)
            cached = self.metrics_cache.get(c.id)
            identity = self.metrics_identity(c)
            if cached and cached[0] == identity and now - cached[1] < 20:
                row.update(cached[2])
            if c.attrs["State"].get("Running") and c.id not in self.metrics_futures and (not cached or cached[0] != identity or now - cached[1] >= 4):
                self.metrics_futures[c.id] = self.metrics_pool.submit(self.collect_metrics, c)
            rows.append(row)
        return rows

    def snapshot(self):
        with self.snapshot_lock:
            if self.snapshot_cache is not None and time.time() - self.cached_at < 3:
                return self.snapshot_cache
            started = time.monotonic()
            client = self.engine.monitor()
            info = client.info()
            containers = self.visible_containers(client)
            rows = self.sampled_rows(containers)
            jobs = sorted(self.store.all("jobs"), key=lambda j: j["createdAt"], reverse=True)[:50]
            # API excludes recovery-only data and never exposes container env/config.
            public_jobs = [{k: j.get(k) for k in ("id", "name", "action", "status", "message", "createdAt", "finishedAt", "rolledBack", "errors")} for j in jobs]
            policy = self.store.policy()
            self.snapshot_cache = {"containers": rows, "host": {"name": info.get("Name", "Docker"),
                 "cpus": info.get("NCPU", 1), "memory": info.get("MemTotal", 0),
                 "version": info.get("ServerVersion", ""), "connected": True},
                 "policy": policy, "nextCheck": next_run(policy["checkCron"]),
                 "nextUpdate": next_run(policy["updateCron"]) if policy["updateMode"] == "scheduled" else None,
                 "lastCheck": self.store.get("settings", "lastCheck"), "checking": self.checking,
                 "jobs": public_jobs, "sampledAt": time.time()}
            self.cached_at = time.time()
            logging.getLogger("uvicorn.error").info("Snapshot ready: %s containers in %.3fs", len(rows), time.monotonic() - started)
            return self.snapshot_cache

    def save_policy(self, policy):
        policy = normalize_policy(policy)
        if wait_seconds(policy) > 720 * 3600:
            raise ValueError("等待时间最多为 720 小时或 43200 分钟")
        next_run(policy["checkCron"])
        if policy["updateMode"] == "scheduled":
            next_run(policy["updateCron"])
        else:
            policy["updateCron"] = self.store.policy()["updateCron"]
        with self.lock:
            self.store.put("settings", "policy", policy)
            self.store.put("settings", "nextCheck", next_run(policy["checkCron"]))
            self.store.put("settings", "nextUpdate", next_run(policy["updateCron"]))
            self.cached_at = 0
        return policy

    def automatic(self, now, scheduled=False):
        policy = self.store.policy()
        for c in self.visible_containers():
            state = c.attrs["State"]
            if c.name not in policy["auto"] or self.engine.protected(c) or c.name in self.active:
                continue
            if not state.get("Running") or state.get("Health", {}).get("Status") == "unhealthy" or state.get("Restarting"):
                continue
            image = self.store.get("images", c.name, {})
            if image.get("status") != "available" or image.get("imageId") != c.attrs["Image"]:
                continue
            due = scheduled or (policy["updateMode"] == "delay" and image.get("discoveredAt") is not None
                                and now - image["discoveredAt"] >= wait_seconds(policy))
            if due:
                last = max((j.get("finishedAt", 0) for j in self.store.all("jobs")
                            if j.get("automatic") and j["name"] == c.name and j["status"] == "failed"), default=0)
                if last and now - last < 900:  # No repeated crashing update loop.
                    continue
                try:
                    self.submit(c.id, "update", automatic=True)
                except OperationError:
                    pass

    def tick(self, now=None):
        now = now or time.time()
        self.retry_cleanup(now)
        with self.lock:
            policy = self.store.policy()
            for key, expr in [("nextCheck", policy["checkCron"]), ("nextUpdate", policy["updateCron"])]:
                if self.store.get("settings", key) is None:
                    self.store.put("settings", key, next_run(expr, now))
            if now >= self.store.get("settings", "nextCheck") and not self.checking:
                self.check_all()
                self.store.put("settings", "nextCheck", next_run(policy["checkCron"], now))
            scheduled = policy["updateMode"] == "scheduled" and now >= self.store.get("settings", "nextUpdate")
            if scheduled:
                self.store.put("settings", "nextUpdate", next_run(policy["updateCron"], now))
        # Delayed execution is independent from the image-detection Cron.
        if scheduled or policy["updateMode"] == "delay":
            self.automatic(now, scheduled)

    def loop(self):
        recovered = False
        while not self.stop_event.is_set():
            try:
                self.engine.connect().ping()
                if not recovered:
                    self.recover()
                    recovered = True
                self.tick()
                self.snapshot()  # Warm monitoring data without waiting for a browser.
            except Exception:
                # Docker may be starting or temporarily disconnected; retry without stopping the UI.
                pass
            self.stop_event.wait(5)
