import os
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
from .auth import Auth, COOKIE, SESSION_SECONDS
from .icons import install_routes


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

    @app.middleware("http")
    async def guard(request: Request, call_next):
        public = request.url.path in {"/healthz", "/favicon.ico", "/manifest.webmanifest", "/login", "/login.js", "/login.css", "/app.css", "/vendor/lucide.js", "/api/auth/login", "/api/auth/session"} or request.url.path.startswith("/icons/")
        rejection = None
        if not public and not auth.authenticated(request.cookies.get(COOKIE)):
            rejection = JSONResponse({"detail": "登录已过期，请重新登录"}, 401) if request.url.path.startswith("/api/") else RedirectResponse("/login", status_code=303)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                rejection = JSONResponse({"detail": "请求来源不匹配"}, 403)
            if request.headers.get("x-monitor-request") != "1":
                rejection = JSONResponse({"detail": "缺少操作请求标识"}, 403)
        response = rejection if rejection is not None else await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/api") or request.url.path in {"/", "/index.html", "/login"}:
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

    @app.post("/api/auth/login")
    def login(credentials: Login, request: Request):
        if not auth.enabled:
            return {"authenticated": True}
        token, status = auth.login(credentials.username, credentials.password, request.client.host if request.client else "local")
        if status != 200:
            return JSONResponse({"detail": "尝试次数过多，请 5 分钟后再试" if status == 429 else "账号或密码不正确"}, status)
        response = JSONResponse({"authenticated": True})
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=request.url.scheme == "https", samesite="strict", path="/")
        return response

    @app.post("/api/auth/logout")
    def logout(request: Request):
        auth.logout(request.cookies.get(COOKIE))
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE, path="/", httponly=True, secure=request.url.scheme == "https", samesite="strict")
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

    install_routes(app)
    app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app


app = create_app()
