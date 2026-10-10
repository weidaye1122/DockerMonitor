"""Optional Bark notifications; credentials never appear in public API responses."""
import hashlib
import json
import ipaddress
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests
from .icons import ICON_URL

DEFAULTS = {"enabled": False, "onAvailable": True, "onSuccess": True, "onFailure": True,
            "siteUrl": "", "iconUrl": ""}


def public_url(value, label):
    if not value:
        return ""
    parsed = urlparse(value)
    local = parsed.hostname == "localhost"
    try:
        local = local or ipaddress.ip_address(parsed.hostname or "").is_private
    except ValueError:
        pass
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme != "https" and not (parsed.scheme == "http" and local))):
        raise ValueError(label + "需要 HTTPS 地址，局域网自建服务可使用 HTTP")
    return value.rstrip("/")


def endpoint_parts(endpoint):
    endpoint = public_url(endpoint, "Bark 推送地址")
    parsed = urlparse(endpoint)
    prefix, _, key = parsed.path.rstrip("/").rpartition("/")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,256}", key or "") or key == "push":
        raise ValueError("请填写 Bark 应用中包含设备 Key 的完整推送地址")
    return parsed.scheme + "://" + parsed.netloc + prefix + "/push", key


class BarkNotifier:
    def __init__(self, store, session=None):
        self.store = store
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bark-notify")
        self.lock = threading.RLock()
        self.pending = 0
        self.run_id = uuid.uuid4().hex

    def settings(self):
        config = {**DEFAULTS, **self.store.get("settings", "bark", {})}
        endpoint = config.pop("endpoint", "")
        config["configured"] = bool(endpoint)
        config["server"] = urlparse(endpoint).netloc if endpoint else ""
        config["lastDelivery"] = self.store.get("settings", "bark-last", None)
        return config

    def validate(self, value):
        config = {**DEFAULTS, **value}
        config["endpoint"] = value.get("endpoint", "").strip() or self.store.get("settings", "bark", {}).get("endpoint", "")
        if config["endpoint"]:
            endpoint_parts(config["endpoint"])
        if config["enabled"] and not config["endpoint"]:
            raise ValueError("请先填写 Bark 推送地址")
        config["siteUrl"] = public_url(config["siteUrl"].strip(), "通知点击地址")
        config["iconUrl"] = public_url(config["iconUrl"].strip(), "通知图标地址")
        return {k: config[k] for k in (*DEFAULTS, "endpoint")}

    def save(self, value):
        config = self.validate(value)
        self.store.put("settings", "bark", config)
        return self.settings()

    def send(self, config, title, body):
        url, key = endpoint_parts(config["endpoint"])
        icon = config.get("iconUrl") or (config.get("siteUrl", "") + ICON_URL if config.get("siteUrl") else "")
        payload = {"device_key": key, "title": title, "body": body, "group": "Docker Monitor", "isArchive": "1"}
        if icon:
            payload["icon"] = icon
        if config.get("siteUrl"):
            payload["url"] = config["siteUrl"]
        try:
            with self.session.post(url, json=payload, timeout=(3, 5), allow_redirects=False, stream=True) as response:
                data = bytearray()
                for chunk in response.iter_content(8192):
                    data.extend(chunk)
                    if len(data) > 65536:
                        raise ValueError("Bark 返回数据异常，请检查推送地址")
                try:
                    result = json.loads(data)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ValueError("Bark 返回数据异常，请检查推送地址") from exc
                if response.status_code != 200 or not isinstance(result, dict) or result.get("code") != 200:
                    raise ValueError("Bark 未接受通知，请检查推送地址和设备 Key")
        except requests.RequestException as exc:
            raise ValueError("Bark 连接失败或超时，请检查推送地址和网络") from exc
        except (TypeError, UnicodeDecodeError) as exc:
            raise ValueError("Bark 返回数据异常，请检查推送地址") from exc

    def test(self, value):
        config = self.validate(value)
        if not config.get("endpoint"):
            raise ValueError("请先填写 Bark 推送地址")
        try:
            self.send(config, "Docker Monitor · 测试通知", "Bark 通知已连接，后续将按你的设置发送容器镜像更新通知。")
        except ValueError as exc:
            self.store.put("settings", "bark-last", {"ok": False, "message": str(exc), "at": time.time()})
            raise
        self.store.put("settings", "bark-last", {"ok": True, "message": "测试通知已发送", "at": time.time()})
        return {"message": "测试通知已发送，请在 Bark 中确认收到"}

    def event(self, kind, name, identity, message):
        config = self.store.get("settings", "bark", {})
        option = {"available": "onAvailable", "success": "onSuccess", "failure": "onFailure"}[kind]
        if not config.get("enabled") or not config.get(option, True) or not config.get("endpoint"):
            return
        # One notice per discovered digest or completed job, persisted across restarts.
        marker = "bark-event-" + hashlib.sha256((config["endpoint"] + kind + name + identity).encode()).hexdigest()
        with self.lock:
            previous = self.store.get("settings", marker, {}) or {}
            if self.pending >= 64 or previous.get("state") == "sent" or previous.get("runId") == self.run_id:
                return
            self.store.put("settings", marker, {"state": "queued", "runId": self.run_id, "at": time.time()})
            self.pending += 1
            self.pool.submit(self._deliver, marker, kind, name, message)

    def _deliver(self, marker, kind, name, message):
        try:
            config = self.store.get("settings", "bark", {})
            option = {"available": "onAvailable", "success": "onSuccess", "failure": "onFailure"}[kind]
            if not config.get("enabled") or not config.get(option, True):
                self.store.put("settings", marker, None)
                return
            title = {"available": "发现镜像更新", "success": "容器更新成功", "failure": "容器更新失败"}[kind]
            self.send(config, "Docker Monitor · " + title, name + "\n" + message)
            self.store.put("settings", marker, {"state": "sent", "at": time.time()})
            self.store.put("settings", "bark-last", {"ok": True, "message": "最近通知已发送", "at": time.time()})
        except Exception:
            # No URL, key, provider response, or raw exception is logged or returned.
            self.store.put("settings", marker, None)
            self.store.put("settings", "bark-last", {"ok": False, "message": "通知发送失败，请检查 Bark 配置", "at": time.time()})
        finally:
            with self.lock:
                self.pending -= 1

    def discovery(self, c, result):
        if result.get("status") == "available":
            self.event("available", c.name, result["digest"], "当前标签已有新镜像，可前往 Docker Monitor 查看。")

    def close(self):
        self.pool.shutdown(wait=True)
