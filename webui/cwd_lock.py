"""Thread-safe CWD isolation for background tasks.

The Auth singleton reads from CWD, and os.chdir() is process-global.
This module provides a lock so only one thread mutates CWD at a time,
plus helpers that acquire fresh tokens for a given user without holding
the lock longer than necessary.
"""
import os
import json
import threading
from pathlib import Path

from webui.users import user_dir, PROJECT_DIR

_lock = threading.Lock()


class _UserCwd:
    """Context manager: chdir into a user dir, reload AuthInstance, yield, chdir back."""

    def __init__(self, username: str):
        self.username = username
        self.prev = None

    def __enter__(self):
        _lock.acquire()
        self.prev = os.getcwd()
        udir = user_dir(self.username)
        udir.mkdir(parents=True, exist_ok=True)
        os.chdir(udir)
        try:
            from app.service.auth import AuthInstance
            AuthInstance.reload_for_current_dir()
        except Exception:
            pass
        return self

    def __exit__(self, *exc):
        try:
            os.chdir(self.prev or PROJECT_DIR)
        except Exception:
            os.chdir(PROJECT_DIR)
        _lock.release()
        return False


def user_cwd(username: str) -> _UserCwd:
    return _UserCwd(username)


def get_user_tokens(username: str, msisdn: int) -> dict | None:
    """Get fresh tokens for a specific MSISDN under a webui user.

    Acquires the CWD lock briefly to refresh tokens via AuthInstance,
    then returns a *copy* of the tokens dict so callers can use it
    without holding the lock.
    """
    with user_cwd(username):
        from app.service.auth import AuthInstance
        try:
            AuthInstance.set_active_user(msisdn)
        except Exception:
            return None
        user = AuthInstance.get_active_user()
        if not user or user.get("number") != msisdn:
            return None
        return dict(user["tokens"])


def get_all_user_tokens(username: str) -> list[dict]:
    """Get fresh tokens for ALL MSISDN accounts of a webui user.

    Returns list of {number, subscriber_id, subscription_type, tokens: {...}}.
    """
    results = []
    udir = user_dir(username)
    rt_file = udir / "refresh-tokens.json"
    if not rt_file.exists():
        return results

    try:
        entries = json.loads(rt_file.read_text(encoding="utf-8"))
    except Exception:
        return results

    for entry in entries:
        msisdn = entry.get("number")
        if not msisdn:
            continue
        tokens = get_user_tokens(username, int(msisdn))
        if tokens:
            results.append({
                "number": int(msisdn),
                "subscriber_id": entry.get("subscriber_id", ""),
                "subscription_type": entry.get("subscription_type", ""),
                "tokens": tokens,
            })
    return results


def get_api_key() -> str:
    from app.util import ensure_api_key
    return ensure_api_key()
