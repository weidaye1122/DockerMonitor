"""Anonymous registry checks independent of the host daemon's mirror selection.

Docker credentials remain with the Docker SDK. No public proxy is hard-coded:
only mirrors already configured on the engine, or explicitly configured here,
are considered for Docker Hub images. Every direct manifest is hash-verified.
"""
import hashlib
import json
import logging
import os
import re
import threading
import time
from urllib.parse import urlparse

import requests

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
ACCEPT = ", ".join(("application/vnd.oci.image.index.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.docker.distribution.manifest.v2+json"))
LOG = logging.getLogger("uvicorn.error")


class RegistryError(Exception):
    def __init__(self, message, code="unavailable"):
        super().__init__(message)
        self.code = code


def friendly_error(error):
    if isinstance(error, RegistryError):
        return str(error)
    # Never display a registry's free-form advertising/contact/error payload.
    text = str(getattr(error, "explanation", error)).lower()
    if text.startswith(("镜像", "连接镜像")) and not any(x in text for x in ("qq", "http", "法律")):
        return text
    if text.startswith(("固定摘要", "当前镜像缺少", "当前平台")):
        return text
    if any(x in text for x in ("unauthorized", "authentication required", "denied")):
        return "镜像仓库需要授权，请检查 Docker 登录配置"
    if any(x in text for x in ("429", "too many requests", "toomanyrequests")):
        return "镜像仓库请求受限，请稍后重试"
    if any(x in text for x in ("manifest unknown", "name unknown", "not found")):
        return "镜像源未提供该镜像或标签，无法检查更新"
    if any(x in text for x in ("timeout", "timed out")):
        return "连接镜像仓库超时，请稍后重试"
    return "镜像仓库暂不可用，请检查网络或镜像源配置"


def split_reference(ref):
    name, sep, digest = ref.partition("@")
    tag = "latest"
    if ":" in name.rsplit("/", 1)[-1]:
        name, tag = name.rsplit(":", 1)
    first = name.split("/", 1)[0]
    if "/" not in name or not ("." in first or ":" in first or first == "localhost"):
        host, repo = "docker.io", name if "/" in name else "library/" + name
    else:
        host, repo = name.split("/", 1)
    if host in ("index.docker.io", "registry-1.docker.io"):
        host = "docker.io"
    if not repo or any(x in repo for x in ("..", "?", "#", "%")) or not re.fullmatch(r"[A-Za-z0-9_./-]+", repo):
        raise RegistryError("镜像仓库地址无效", "invalid")
    selector = digest if sep else tag
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", selector):
        raise RegistryError("镜像标签或摘要无效", "invalid")
    return host, repo, selector


class RegistryResolver:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.session.trust_env = False  # Do not implicitly send .netrc credentials.
        self.lock = threading.RLock()
        self.cache = {}
        self.mirror_cache = None

    def mirrors(self, client):
        explicit = os.environ.get("REGISTRY_MIRRORS", "")
        if explicit.strip():
            values = [x.strip() for x in explicit.split(",") if x.strip()]
        else:
            with self.lock:
                if self.mirror_cache and time.monotonic() - self.mirror_cache[0] < 300:
                    return self.mirror_cache[1]
            try:
                values = client.info().get("RegistryConfig", {}).get("Mirrors", [])
            except Exception:
                values = []
        result = []
        for value in values:
            parsed = urlparse(value if "://" in value else "https://" + value)
            # Registry proxies must have TLS, no embedded credentials or arbitrary paths.
            if (parsed.scheme == "https" and parsed.hostname and not parsed.username
                    and not parsed.password and parsed.path in ("", "/") and not parsed.query and not parsed.fragment):
                base = "https://" + parsed.netloc
                if base not in result and parsed.hostname not in ("docker.io", "index.docker.io", "registry-1.docker.io"):
                    result.append(base)
        result = result[:4]
        if not explicit.strip():
            with self.lock:
                self.mirror_cache = (time.monotonic(), result)
        return result

    def invalidate(self, ref):
        with self.lock:
            self.cache.pop(ref, None)

    def _request(self, url, headers=None, params=None):
        # Bounded responses, verified certificates, and no token-bearing redirects.
        try:
            response = self.session.get(url, headers=headers or {}, params=params,
                                        timeout=(4, 8), allow_redirects=False, stream=True,
                                        proxies=requests.utils.get_environ_proxies(url),
                                        verify=os.environ.get("REQUESTS_CA_BUNDLE") or True)
            with response:
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 4 * 1024 * 1024:
                        raise RegistryError("镜像仓库返回的数据过大", "invalid")
                    chunks.append(chunk)
                return response.status_code, dict(response.headers), b"".join(chunks)
        except requests.Timeout as exc:
            raise RegistryError("连接镜像仓库超时，请稍后重试", "timeout") from exc
        except requests.RequestException as exc:
            raise RegistryError("镜像仓库连接失败，请检查网络", "network") from exc

    def manifest(self, base, repo, selector):
        url = base + "/v2/" + repo + "/manifests/" + selector
        headers = {"Accept": ACCEPT}
        status, response_headers, body = self._request(url, headers)
        if status == 401:
            challenge = next((v for k, v in response_headers.items() if k.lower() == "www-authenticate"), "")
            if not challenge.lower().startswith("bearer "):
                raise RegistryError("镜像仓库需要授权，请检查 Docker 登录配置", "auth")
            fields = dict(re.findall(r'(\w+)="([^"\r\n]+)"', challenge))
            realm = fields.get("realm", "")
            parsed, origin = urlparse(realm), urlparse(base)
            allowed = {origin.hostname}
            if base == "https://registry-1.docker.io":
                allowed.add("auth.docker.io")
            if (parsed.scheme != "https" or parsed.hostname not in allowed
                    or parsed.username or parsed.password or parsed.fragment):
                raise RegistryError("镜像仓库返回了不受信任的认证地址", "auth")
            token_status, _, token_body = self._request(realm, params={
                "service": fields.get("service", ""), "scope": "repository:" + repo + ":pull"})
            try:
                token_data = json.loads(token_body)
                token = token_data.get("token") or token_data.get("access_token")
            except (ValueError, AttributeError):
                token = None
            if token_status != 200 or not isinstance(token, str) or not token or "\n" in token or "\r" in token:
                raise RegistryError("镜像仓库授权失败，请检查 Docker 登录配置", "auth")
            headers["Authorization"] = "Bearer " + token
            status, response_headers, body = self._request(url, headers)
        if status != 200:
            code, message = {
                401: ("auth", "镜像仓库需要授权，请检查 Docker 登录配置"),
                403: ("auth", "镜像仓库拒绝访问，请检查镜像源或权限"),
                404: ("missing", "镜像源未提供该镜像或标签，无法检查更新"),
                429: ("rate_limit", "镜像仓库请求受限，请稍后重试"),
            }.get(status, ("unavailable", "镜像仓库暂不可用，请稍后重试"))
            raise RegistryError(message, code)
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        declared = next((v for k, v in response_headers.items() if k.lower() == "docker-content-digest"), digest)
        try:
            doc = json.loads(body)
        except ValueError as exc:
            raise RegistryError("镜像仓库返回了无效清单", "invalid") from exc
        if (not isinstance(doc, dict) or doc.get("schemaVersion") != 2
                or not (isinstance(doc.get("manifests"), list) or isinstance(doc.get("layers"), list))
                or declared != digest or (selector.startswith("sha256:") and selector != digest)):
            raise RegistryError("镜像清单校验失败，本次操作已取消", "invalid")
        return digest

    def resolve(self, ref, client):
        with self.lock:
            cached = self.cache.get(ref)
            if cached and time.monotonic() - cached[0] < 45:
                return dict(cached[1])
        host, repo, selector = split_reference(ref)
        errors = []
        # Public registries have a direct anonymous path. Custom/private registries
        # stay on the SDK path, preserving Docker's existing credentials and TLS settings.
        base = {"docker.io": "https://registry-1.docker.io", "ghcr.io": "https://ghcr.io"}.get(host)
        if base:
            try:
                digest = self.manifest(base, repo, selector)
                result = {"digest": digest, "checkSource": base}
                with self.lock:
                    self.cache[ref] = (time.monotonic(), result)
                return dict(result)
            except RegistryError as exc:
                if exc.code == "invalid":
                    raise
                errors.append(exc)
                LOG.info("Direct registry check failed for %s: %s", ref, exc.code)
        try:
            digest = client.images.get_registry_data(ref).id
            if not DIGEST.fullmatch(digest):
                raise RegistryError("镜像仓库未返回有效摘要", "invalid")
            result = {"digest": digest, "checkSource": "docker-engine"}
        except Exception as exc:
            errors.append(exc)
            result = None
        if result is None and host == "docker.io":
            for mirror in self.mirrors(client):
                try:
                    result = {"digest": self.manifest(mirror, repo, selector), "checkSource": mirror}
                    break
                except RegistryError as exc:
                    if exc.code == "invalid":
                        raise
                    errors.append(exc)
                    LOG.info("Configured mirror check failed for %s: %s", ref, exc.code)
        if result is None:
            # Direct source errors are more relevant than a proxy's custom payload.
            raise RegistryError(friendly_error(errors[0]), getattr(errors[0], "code", "unavailable"))
        with self.lock:
            self.cache[ref] = (time.monotonic(), result)
        return dict(result)

    def pull_candidates(self, ref, digest, client):
        host, repo, _ = split_reference(ref)
        repository = ref.split("@", 1)[0]
        if ":" in repository.rsplit("/", 1)[-1]:
            repository = repository.rsplit(":", 1)[0]
        yield repository + "@" + digest, None
        if host == "docker.io":
            # Explicit registry host avoids the daemon's automatic Docker Hub mirror
            # selection. All alternatives must provide the exact discovered digest.
            for base in ["https://registry-1.docker.io", *self.mirrors(client)]:
                yield base.removeprefix("https://") + "/" + repo + "@" + digest, (base, repo)
