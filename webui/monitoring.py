"""Quota monitoring storage + helpers.

Per-user files (resolved CWD-relative; middleware chdir-s into each user dir):
- monitoring.json : list of rules
- telegram.json   : {bot_token, chat_id}
- monitor.log     : append-only log of rule evaluations & actions

Rule schema:
{
  "id": "<uuid hex>",
  "name": "User-given label",
  "msisdn": 6281234567890,            # which MyXL account to monitor (must be in their refresh-tokens)
  "match": {
    "kind": "any" | "quota_name" | "quota_code" | "group_name",
    "value": "Tagihan Bulanan" | None,    # null = any quota
    "data_type": "DATA" | "VOICE" | "TEXT" | "ANY"
  },
  "trigger": {
    "metric": "remaining_pct" | "remaining_bytes" | "remaining_minutes" | "expiring_in_days",
    "op": "lt" | "lte" | "gt" | "gte" | "eq",
    "value": 10              # int / float
  },
  "actions": [
    {"type": "telegram", "message": "..."},
    {"type": "buy_option", "option_code": "U0Nf...", "method": "balance"|"qris"|"ewallet_dana"|... },
    {"type": "unsubscribe"}     # unsubscribe THE matched quota
  ],
  "cooldown_seconds": 3600,
  "enabled": true,
  "last_fired_at": 0,
  "last_status": "ok|fired|error|...",
  "last_msg": ""
}
"""
import os
import json
import time
import uuid
from pathlib import Path
from typing import Optional

import requests


def _read_json(name: str, default):
    p = Path(name)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(name: str, data):
    p = Path(name)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)


# --- Telegram config -------------------------------------------------------

def load_telegram() -> dict:
    return _read_json("telegram.json", {"bot_token": "", "chat_id": ""})


def save_telegram(bot_token: str, chat_id: str) -> None:
    _write_json("telegram.json", {
        "bot_token": (bot_token or "").strip(),
        "chat_id": (chat_id or "").strip(),
    })


def resolve_send_config(username: Optional[str] = None) -> dict:
    """Merge global bot token with chat_id from webui user link or per-user telegram.json."""
    from webui import telegram_config as TC
    from webui.users import get_user

    global_cfg = TC.load_config()
    token = (global_cfg.get("bot_token") or "").strip()
    chat = ""

    if username:
        u = get_user(username)
        if u and u.get("telegram_chat_id"):
            chat = str(u["telegram_chat_id"])

    per_user = load_telegram()
    if not chat:
        chat = (per_user.get("chat_id") or "").strip()
    if not token:
        token = (per_user.get("bot_token") or "").strip()

    return {"bot_token": token, "chat_id": chat}


def send_telegram(text: str, *, cfg: Optional[dict] = None, username: Optional[str] = None) -> tuple[bool, str]:
    """Send a Telegram message. Returns (ok, status_or_error)."""
    cfg = cfg or resolve_send_config(username)
    token = cfg.get("bot_token", "").strip()
    chat = cfg.get("chat_id", "").strip()
    if not token or not chat:
        return False, "Bot token / chat_id belum di-set"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=15,
        )
        if r.status_code == 200 and r.json().get("ok"):
            return True, "Pesan terkirim"
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"Exception: {e}"


# --- Rules ----------------------------------------------------------------

def load_rules() -> list[dict]:
    rules = _read_json("monitoring.json", [])
    return rules if isinstance(rules, list) else []


def save_rules(rules: list[dict]) -> None:
    _write_json("monitoring.json", rules)


def get_rule(rule_id: str) -> Optional[dict]:
    for r in load_rules():
        if r.get("id") == rule_id:
            return r
    return None


def add_rule(payload: dict) -> dict:
    rules = load_rules()
    rule = {
        "id": uuid.uuid4().hex[:12],
        "name": payload.get("name") or "Untitled",
        "msisdn": int(payload.get("msisdn") or 0),
        "match": payload.get("match") or {"kind": "any", "value": None, "data_type": "ANY"},
        "trigger": payload.get("trigger") or {"metric": "remaining_pct", "op": "lt", "value": 10},
        "actions": payload.get("actions") or [],
        "cooldown_seconds": int(payload.get("cooldown_seconds") or 3600),
        "enabled": bool(payload.get("enabled", True)),
        "created_at": int(time.time()),
        "last_fired_at": 0,
        "last_status": "",
        "last_msg": "",
    }
    rules.append(rule)
    save_rules(rules)
    return rule


def update_rule(rule_id: str, patch: dict) -> Optional[dict]:
    rules = load_rules()
    for r in rules:
        if r.get("id") == rule_id:
            for k, v in patch.items():
                if k in ("id", "created_at"):
                    continue
                r[k] = v
            save_rules(rules)
            return r
    return None


def delete_rule(rule_id: str) -> bool:
    rules = load_rules()
    n = len(rules)
    rules = [r for r in rules if r.get("id") != rule_id]
    if len(rules) != n:
        save_rules(rules)
        return True
    return False


def mark_fired(rule_id: str, status: str, msg: str) -> None:
    rules = load_rules()
    for r in rules:
        if r.get("id") == rule_id:
            r["last_fired_at"] = int(time.time())
            r["last_status"] = status
            r["last_msg"] = msg
            save_rules(rules)
            return


# --- Log ------------------------------------------------------------------

LOG_FILE = "monitor.log"
LOG_MAX_BYTES = 256 * 1024  # rotate after 256 KB


def log_line(line: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    full = f"[{ts}] {line}\n"
    try:
        p = Path(LOG_FILE)
        if p.exists() and p.stat().st_size > LOG_MAX_BYTES:
            # Rotate: keep last half
            existing = p.read_text(encoding="utf-8", errors="ignore").splitlines()
            p.write_text("\n".join(existing[-200:]) + "\n", encoding="utf-8")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(full)
    except Exception:
        pass


def tail_log(n: int = 100) -> list[str]:
    p = Path(LOG_FILE)
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        return lines[-n:]
    except Exception:
        return []
