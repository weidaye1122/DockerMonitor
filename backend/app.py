import os
import secrets
import re
from contextlib import asynccontextmanager
from pathlib import Path

from docker.errors import DockerException, NotFound
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal, List, Optional

from .engine import OperationError
from .service import Service
from .store import Store
from .auth import Auth, COOKIE, CSRF_COOKIE, SESSION_SECONDS
from .icons import install_routes
from .proxy import ReverseProxyMiddleware


class Policy(BaseModel):
    checkCron: str = Field(max_length=128)
    updateMode: Literal["scheduled", "delay"]
    updateCron: str = Field(default="0 3 * * *", max_length=128)
    waitValue: Optional[int] = Field(default=None, ge=0, le=43200)
    waitUnit: Literal["hours", "minutes"] = "hours"
    waitHours: Optional[int] = Field(default=None, ge=0, le=720)
    auto: List[str] = Field(default_factory=list, max_length=4096)


class Action(BaseModel):
    action: Literal["start", "stop", "restart", "update", "check", "repair"]


class BatchUpdate(BaseModel):
    ids: List[str] = Field(min_length=1, max_length=4096)


class Login(BaseModel):
    username: str = Field(max_length=128)
    password: str = Field(max_length=4096)


class Notifications(BaseModel):
    enabled: bool = False
    endpoint: str = Field(default="", max_length=1024)
    siteUrl: str = Field(default="", max_length=1024)
    iconUrl: str = Field(default="", max_length=1024)
    onAvailable: bool = True
    onSuccess: bool = True
    onFailure: bool = True


def create_app(service=None):
    if service is None:
        os.umask(0o077)
        service = Service(Store(os.environ.get("DATA_PATH", "data/monitor.sqlite3")))

    @asynccontextmanager
    async def lifespan(app):
        service.start()
        yield
        service.close()

    app = FastAPI(title="Docker Monitor", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    auth = Auth(os.environ.get("ADMIN_PASSWORD", ""), os.environ.get("ADMIN_USERNAME", "admin") or "admin")
    app.state.auth = auth
    web = Path(__file__).resolve().parent.parent / "web"
    def secure_cookie(request):
        # The signed CSRF token records the browser's HTTPS transport even if
        # a tunnel discarded all proxy headers. It conveys no identity/trust.
        token = request.headers.get("x-csrf-token", "")
        parts = token.split(":")
        return request.url.scheme == "https" or (len(parts) == 5 and parts[2] == "1")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        public = request.url.path in {"/healthz", "/favicon.ico", "/manifest.webmanifest", "/login", "/login.js", "/request.js", "/login.css", "/app.css", "/vendor/lucide.js", "/api/auth/login", "/api/auth/session", "/api/auth/csrf"} or request.url.path.startswith("/icons/")
        rejection = None
        if not public and not auth.authenticated(request.cookies.get(COOKIE)):
            rejection = JSONResponse({"detail": "登录已过期，请重新登录"}, 401) if request.url.path.startswith("/api/") else RedirectResponse("/login", status_code=303)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("sec-fetch-site") == "cross-site":
                rejection = JSONResponse({"detail": "不允许跨站操作"}, 403)
            elif rejection is None and not auth.csrf_valid(request.headers.get("x-csrf-token"), request.cookies.get(CSRF_COOKIE), request.cookies.get(COOKIE)):
                rejection = JSONResponse({"detail": "页面验证已失效，请重试", "code": "csrf_invalid"}, 403)
            if request.headers.get("x-monitor-request") != "1":
                rejection = JSONResponse({"detail": "缺少操作请求标识"}, 403)
        response = rejection if rejection is not None else await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/api") or request.url.path in {"/", "/index.html", "/login", "/app.js", "/login.js", "/request.js"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(OperationError)
    async def operation_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(NotFound)
    async def missing(request, exc):
        return JSONResponse({"detail": "容器不存在或已被替换，请刷新"}, status_code=404)

    @app.exception_handler(DockerException)
    async def docker_error(request, exc):
        return JSONResponse({"detail": "Docker 请求失败：" + str(getattr(exc, "explanation", str(exc)))}, status_code=503)

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/login")
    def login_page(request: Request):
        if auth.authenticated(request.cookies.get(COOKIE)):
            return RedirectResponse("/", status_code=303)
        return FileResponse(web / "login.html")

    @app.get("/api/auth/session")
    def session(request: Request):
        authenticated = auth.authenticated(request.cookies.get(COOKIE))
        return {"enabled": auth.enabled, "authenticated": authenticated, "username": auth.username if authenticated and auth.enabled else None}

    @app.get("/api/auth/csrf")
    def csrf(request: Request):
        if request.headers.get("x-monitor-request") != "1" or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "不允许跨站获取页面验证"}, 403)
        nonce = request.cookies.get(CSRF_COOKIE, "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", nonce):
            nonce = secrets.token_urlsafe(32)
        secure = request.url.scheme == "https" or request.headers.get("x-monitor-client-scheme") == "https"
        response = JSONResponse({"token": auth.csrf_token(nonce, request.cookies.get(COOKIE), secure)})
        response.set_cookie(CSRF_COOKIE, nonce, max_age=SESSION_SECONDS, httponly=True, secure=secure, samesite="strict", path="/")
        return response

    @app.post("/api/auth/login")
    def login(credentials: Login, request: Request):
        if not auth.enabled:
            return {"authenticated": True}
        token, status = auth.login(credentials.username, credentials.password, request.client.host if request.client else "local")
        if status != 200:
            return JSONResponse({"detail": "尝试次数过多，请 5 分钟后再试" if status == 429 else "账号或密码不正确"}, status)
        response = JSONResponse({"authenticated": True})
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=secure_cookie(request), samesite="strict", path="/")
        return response

    @app.post("/api/auth/logout")
    def logout(request: Request):
        auth.logout(request.cookies.get(COOKIE))
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE, path="/", httponly=True, secure=secure_cookie(request), samesite="strict")
        return response

    @app.get("/api/snapshot")
    def snapshot():
        return service.snapshot()

    @app.get("/api/policy")
    def get_policy():
        return service.store.policy()

    @app.get("/api/notifications")
    def notifications():
        return service.notifications.settings()

    @app.put("/api/notifications")
    def save_notifications(value: Notifications):
        try:
            return service.notifications.save(value.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.post("/api/notifications/test")
    def test_notifications(value: Notifications):
        try:
            return service.notifications.test(value.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.put("/api/policy")
    def save_policy(policy: Policy):
        try:
            return service.save_policy(policy.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.post("/api/containers/{cid}/actions", status_code=202)
    def action(cid: str, action: Action):
        job = service.submit(cid, action.action)
        return {"jobId": job["id"], "status": job["status"]}

    @app.post("/api/check-updates", status_code=202)
    def check_updates():
        job = service.check_all()
        return {"jobId": job["id"], "status": job["status"]}

    @app.post("/api/update-all", status_code=202)
    def update_all(selection: BatchUpdate):
        job = service.update_all(selection.ids)
        return {"jobId": job["id"], "status": job["status"]}

    @app.get("/api/jobs/{jid}")
    def get_job(jid: str):
        job = service.store.get("jobs", jid)
        if not job:
            raise HTTPException(404, "任务不存在")
        return {k: job.get(k) for k in ("id", "name", "action", "status", "message", "rolledBack", "createdAt", "finishedAt", "errors")}

    # Added last so the guard and cookie handling see the public URL.
    app.add_middleware(ReverseProxyMiddleware)
    install_routes(app)
    app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app


app = create_app()
