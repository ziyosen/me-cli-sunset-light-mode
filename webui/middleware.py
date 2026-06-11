"""Per-request middleware: validate webui session cookie, chdir into that
user's data directory, then reload AuthInstance/BookmarkInstance from disk
so all downstream code sees their files.

Important: chdir is process-global. For our small/personal-use scale this is
acceptable. If we ever go fully concurrent we should switch to context-vars
or per-request Auth instances.
"""
import os
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse, Response
from starlette.types import ASGIApp

from webui.users import (
    COOKIE_NAME, PROJECT_DIR, USERS_DIR,
    parse_session_token, get_user, user_dir,
)

# Routes accessible without auth:
PUBLIC_PATHS = (
    "/u/login",
    "/u/register",
    "/u/logout",      # logout itself is harmless
    "/static/",
    "/favicon",
    "/u/api/",        # reserved for future public AJAX
)


class WebUIAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request, call_next):
        path = request.url.path
        # Skip auth for public paths
        is_public = any(path == p.rstrip("/") or path.startswith(p) for p in PUBLIC_PATHS)

        # Parse session cookie
        token = request.cookies.get(COOKIE_NAME)
        username = parse_session_token(token) if token else None
        user = get_user(username) if username else None

        if not user:
            if is_public:
                # Return early but leave CWD at project root for assets/login form
                self._chdir_safely(PROJECT_DIR)
                return await call_next(request)
            # Redirect HTML requests to login; return 401 for AJAX/JSON
            accept = request.headers.get("accept", "")
            if "text/html" in accept or accept == "" or accept == "*/*":
                return RedirectResponse(url=f"/u/login?next={path}", status_code=303)
            return Response("Unauthorized", status_code=401)

        # Authenticated → chdir to per-user dir, reload AuthInstance & BookmarkInstance
        udir = user_dir(user["username"])
        udir.mkdir(parents=True, exist_ok=True)
        self._chdir_safely(udir)

        # Make sure required user-local files/dirs exist
        for fn, default in (
            ("refresh-tokens.json", "[]"),
        ):
            p = udir / fn
            if not p.exists():
                p.write_text(default, encoding="utf-8")
        (udir / "decoy_data").mkdir(exist_ok=True)

        # Reload singletons from this user's CWD
        try:
            from app.service.auth import AuthInstance
            AuthInstance.reload_for_current_dir()
        except Exception as e:
            print(f"[middleware] AuthInstance reload err: {e}")
        try:
            from app.service.bookmark import BookmarkInstance
            BookmarkInstance.reload_for_current_dir()
        except Exception:
            pass
        try:
            from app.service.decoy import DecoyInstance
            DecoyInstance.reset_decoys()
        except Exception:
            pass

        # Stash webui user info for templates / handlers
        request.state.webui_user = user
        request.state.webui_user_dir = str(udir)

        response = await call_next(request)
        return response

    @staticmethod
    def _chdir_safely(p):
        try:
            os.chdir(p)
        except Exception as e:
            print(f"[middleware] chdir failed: {e}")
