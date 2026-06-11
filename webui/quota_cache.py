"""Per-user quota snapshot cache.

Written by monitor_loop after each successful quota fetch.
Read by the monitoring dashboard for instant page loads.
File: webui_data/users/{username}/quota_cache.json
"""
import json
import os
import time
from pathlib import Path

from webui.users import user_dir


def _cache_path(username: str) -> Path:
    return user_dir(username) / "quota_cache.json"


def load_cache(username: str) -> dict:
    p = _cache_path(username)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(username: str, data: dict) -> None:
    p = _cache_path(username)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def update_account_cache(username: str, msisdn: int, balance: dict | None, quotas: list | None) -> None:
    cache = load_cache(username)
    cache[str(msisdn)] = {
        "updated_at": int(time.time()),
        "balance": balance,
        "quotas": quotas,
    }
    save_cache(username, cache)
