import os
import asyncio
import secrets
import base64
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from webui.helpers import format_rp, format_ts, format_date, safe_html, humanize_bytes, public_error_message

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

WEBUI_USER = os.getenv("WEBUI_USER", "admin")
WEBUI_PASS = os.getenv("WEBUI_PASS", "")

_bot_instance = None
_monitor_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_instance, _monitor_task
    from webui.monitor_loop import monitor_loop
    from webui.telegram_bot import TelegramBot
    from webui.telegram_config import load_config

    _monitor_task = asyncio.create_task(monitor_loop())

    tg_cfg = load_config()
    if tg_cfg.get("enabled") and tg_cfg.get("bot_token"):
        _bot_instance = TelegramBot(tg_cfg["bot_token"])
        _bot_instance.start()

    yield

    if _bot_instance:
        _bot_instance.stop()
        _bot_instance = None
    if _monitor_task:
        _monitor_task.cancel()
        _monitor_task = None


def get_bot():
    return _bot_instance


def restart_bot():
    global _bot_instance
    from webui.telegram_bot import TelegramBot
    from webui.telegram_config import load_config

    if _bot_instance:
        _bot_instance.stop()
        _bot_instance = None

    tg_cfg = load_config()
    if tg_cfg.get("enabled") and tg_cfg.get("bot_token"):
        _bot_instance = TelegramBot(tg_cfg["bot_token"])
        _bot_instance.start()
        return True
    return False


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, username: str, password: str, realm: str = "me-cli-sunset"):
        super().__init__(app)
        self.username = username
        self.password = password
        self.realm = realm
        self.enabled = bool(username and password)

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        if request.url.path.startswith("/static/"):
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        authed = False
        if auth.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8", "ignore")
                user, _, pwd = decoded.partition(":")
                authed = secrets.compare_digest(user, self.username) and secrets.compare_digest(pwd, self.password)
            except Exception:
                authed = False

        if not authed:
            return Response(
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": f'Basic realm="{self.realm}"'},
                content="Authentication required",
            )

        return await call_next(request)


def create_app() -> FastAPI:
    app = FastAPI(title="me-cli-sunset webui", docs_url=None, redoc_url=None, lifespan=lifespan)

    # New multi-tenant middleware (cookie session + per-user CWD chdir).
    # BasicAuth is now disabled; the webui session is the only auth layer.
    from webui.middleware import WebUIAuthMiddleware
    app.add_middleware(WebUIAuthMiddleware)

    static_dir = BASE_DIR / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    templates.env.filters["rp"] = format_rp
    templates.env.filters["ts"] = format_ts
    templates.env.filters["date"] = format_date
    templates.env.filters["bytes"] = humanize_bytes
    templates.env.filters["safe_html"] = safe_html
    app.state.templates = templates

    # Start at project root. Middleware will chdir into per-user dir per-request.
    os.chdir(PROJECT_DIR)

    from webui.routes import auth as r_auth
    from webui.routes import dashboard as r_dashboard
    from webui.routes import packages as r_packages
    from webui.routes import purchase as r_purchase
    from webui.routes import hot as r_hot
    from webui.routes import bookmark as r_bookmark
    from webui.routes import famplan as r_famplan
    from webui.routes import circle as r_circle
    from webui.routes import store as r_store
    from webui.routes import notification as r_notification
    from webui.routes import transaction as r_transaction
    from webui.routes import registration as r_registration
    from webui.routes import decoy_settings as r_decoy_settings
    from webui.routes import webui_auth as r_webui_auth
    from webui.routes import monitoring as r_monitoring

    app.include_router(r_webui_auth.router)
    app.include_router(r_dashboard.router)
    app.include_router(r_auth.router)
    app.include_router(r_packages.router)
    app.include_router(r_purchase.router)
    app.include_router(r_hot.router)
    app.include_router(r_bookmark.router)
    app.include_router(r_famplan.router)
    app.include_router(r_circle.router)
    app.include_router(r_store.router)
    app.include_router(r_notification.router)
    app.include_router(r_transaction.router)
    app.include_router(r_registration.router)
    app.include_router(r_decoy_settings.router)
    app.include_router(r_monitoring.router)

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return HTMLResponse(
            templates.get_template("error.html").render(
                request=request,
                title="404",
                message="Halaman tidak ditemukan.",
                active_user=None,
                accounts=[],
            ),
            status_code=404,
        )

    @app.exception_handler(Exception)
    async def global_exception(request: Request, exc: Exception):
        message = public_error_message(
            exc, context=f"{request.method} {request.url.path}"
        )
        return HTMLResponse(
            templates.get_template("error.html").render(
                request=request,
                title="Error",
                message=message,
                active_user=None,
                accounts=[],
            ),
            status_code=500,
        )

    return app


app = create_app()
