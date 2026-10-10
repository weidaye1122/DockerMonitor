"""Restore public request URLs only from trusted reverse proxies."""
import os
from urllib.parse import urlsplit

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


# Docker/NAS proxies must be explicitly trusted, or use a fixed PUBLIC_URL.
# Private addresses alone cannot distinguish proxies from ordinary LAN clients.
DEFAULT_TRUSTED_PROXIES = "127.0.0.1,::1"


def parse_origin(value):
    """Compare origins by scheme, hostname and effective port, without paths."""
    try:
        if not value or any(c.isspace() or c == "\\" for c in value):
            return None
        url = urlsplit(value)
        if (url.scheme not in ("http", "https") or not url.hostname
                or url.username is not None or url.password is not None
                or url.path not in ("", "/") or url.query or url.fragment):
            return None
        port = url.port
        if port is not None and not 1 <= port <= 65535:
            return None
        return url.scheme, url.hostname.lower(), port or (443 if url.scheme == "https" else 80)
    except ValueError:
        return None


class ReverseProxyMiddleware:
    def __init__(self, app):
        self.app = app
        self.proxy = ProxyHeadersMiddleware(
            app, trusted_hosts=os.environ.get("TRUSTED_PROXY_IPS", DEFAULT_TRUSTED_PROXIES))

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        peer = scope.get("client")
        if not peer or peer[0] not in self.proxy.trusted_hosts:
            return await self.app(scope, receive, send)

        scope = dict(scope)
        headers = dict(scope["headers"])
        # Use the value supplied by the closest trusted proxy when it appends
        # to a chain; never use a client-supplied first value for URL identity.
        def last(name):
            return headers.get(name, b"").decode("latin1").split(",")[-1].strip()

        proto = last(b"x-forwarded-proto")
        if proto not in ("http", "https"):
            proto = scope["scheme"].replace("ws", "http", 1)
            headers.pop(b"x-forwarded-proto", None)
        else:
            headers[b"x-forwarded-proto"] = proto.encode("ascii")
        host = last(b"x-forwarded-host") or headers.get(b"host", b"").decode("latin1")
        parsed = parse_origin(proto + "://" + host)
        if parsed:
            # An explicit port in the forwarded Host is authoritative. Some
            # proxies supply the external port separately instead.
            port = last(b"x-forwarded-port")
            if (urlsplit(proto + "://" + host).port is None and 1 <= len(port) <= 5
                    and port.isascii() and port.isdigit() and 1 <= int(port) <= 65535):
                host += ":" + port
            headers[b"host"] = host.encode("latin1")
        scope["headers"] = list(headers.items())
        # Uvicorn's implementation also resolves client IPs for login limiting.
        # The server must run with --no-proxy-headers to retain the original peer
        # until this trust check; proxy headers are processed once here.
        return await self.proxy(scope, receive, send)
