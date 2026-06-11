"""WebUI-level account login / register / logout (multi-tenant).

Separate from MyXL OTP login (which is at /login). End-users register a
webui account first, then inside their session they OTP-login their own
MyXL number(s).
"""
import json
from pathlib import Path

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, Response

from webui.users import (
    COOKIE_NAME, SESSION_MAX_AGE,
    authenticate, create_user, get_user, make_session_token, load_users,
)
from webui.deps import get_templates

router = APIRouter()

LICENSES_FILE = Path("/root/web/licenses.json")


def load_licenses() -> dict:
    try:
        if LICENSES_FILE.exists():
            with open(LICENSES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {}


def save_licenses(data: dict):
    LICENSES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LICENSES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def _render(request: Request, template: str, **ctx):
    templates = get_templates(request)
    base = {"request": request, "active_user": None, "accounts": [], "webui_user": None}
    base.update(ctx)
    return templates.TemplateResponse(request, template, base)


@router.get("/u/login")
def login_page(request: Request, error: str | None = None, info: str | None = None,
               username: str | None = None, next: str | None = None):
    users_count = len(load_users())
    return _render(request, "webui_login.html",
                   mode="login", error=error, info=info,
                   username=username or "", next=(next or "/"),
                   users_count=users_count)


@router.post("/u/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...),
                 next: str = Form("/")):
    u = authenticate(username, password)
    if not u:
        return _render(request, "webui_login.html",
                       mode="login",
                       error="Username atau password salah.",
                       username=username, next=next,
                       users_count=len(load_users()))
    token = make_session_token(u["username"])
    resp = RedirectResponse(url=next or "/", status_code=303)
    resp.set_cookie(
        key=COOKIE_NAME, value=token,
        max_age=SESSION_MAX_AGE, httponly=True,
        samesite="lax", secure=request.url.scheme == "https",
    )
    return resp


@router.get("/u/register")
def register_page(request: Request, error: str | None = None, info: str | None = None,
                  username: str | None = None):
    return _render(request, "webui_login.html",
                   mode="register", error=error, info=info,
                   username=username or "", next="/",
                   users_count=len(load_users()))


@router.post("/u/register")
def register_submit(
    request: Request,
    license_key: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    license_key = license_key.strip()
    licenses = load_licenses()

    if license_key not in licenses:
        return _render(
            request,
            "webui_login.html",
            mode="register",
            error="License key tidak valid.",
            username=username,
            next="/",
            users_count=len(load_users()),
        )

    if password != password_confirm:
        return _render(
            request,
            "webui_login.html",
            mode="register",
            error="Password tidak cocok.",
            username=username,
            next="/",
            users_count=len(load_users()),
        )

    ok, err = create_user(username, password)

    if not ok:
        return _render(
            request,
            "webui_login.html",
            mode="register",
            error=err,
            username=username,
            next="/",
            users_count=len(load_users()),
        )

    # tandai license sudah dipakai
    licenses[license_key] = True
    save_licenses(licenses)

    token = make_session_token(username)

    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )

    return resp


@router.post("/u/logout")
@router.get("/u/logout")
def logout(request: Request):
    resp = RedirectResponse(url="/u/login", status_code=303)
    resp.delete_cookie(key=COOKIE_NAME)
    return resp
