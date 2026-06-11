from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse

from app.service.auth import AuthInstance


def get_templates(request: Request):
    return request.app.state.templates


def list_accounts():
    AuthInstance.load_tokens()
    return AuthInstance.refresh_tokens


def get_active_user_safe():
    """Return active user dict or None without blocking on stdin."""
    try:
        return AuthInstance.get_active_user()
    except Exception:
        return None


def require_active_user(request: Request):
    user = get_active_user_safe()
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def render(request: Request, template: str, **context):
    templates = get_templates(request)
    base_ctx = {
        "request": request,
        "active_user": get_active_user_safe(),
        "accounts": list_accounts(),
        "webui_user": getattr(request.state, "webui_user", None),
    }
    base_ctx.update(context)
    return templates.TemplateResponse(request, template, base_ctx)
