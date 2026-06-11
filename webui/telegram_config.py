"""Global Telegram bot configuration (instance-wide, not per-user).

Stored at webui_data/telegram.json.
"""
import json
import os
from pathlib import Path

from webui.users import WEBUI_DATA

CONFIG_FILE = WEBUI_DATA / "telegram.json"

_DEFAULTS = {
    "bot_token": "",
    "enabled": False,
    "daily_summary_enabled": True,
    "daily_summary_hour": 7,
    "daily_summary_minute": 0,
    "low_quota_threshold_pct": 10,
    "poll_interval_minutes": 5,
}


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        merged = dict(_DEFAULTS)
        merged.update(data)
        return merged
    except Exception:
        return dict(_DEFAULTS)


def save_config(cfg: dict) -> None:
    WEBUI_DATA.mkdir(parents=True, exist_ok=True)
    merged = dict(_DEFAULTS)
    merged.update(cfg)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)
