import logging
import os
import traceback
from datetime import datetime
from html import escape

logger = logging.getLogger("webui")

def format_rp(value) -> str:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return str(value or "-")
    return "Rp " + f"{n:,}".replace(",", ".")

def format_ts(ts) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)

def format_date(ts) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)

def safe_html(text) -> str:
    if text is None:
        return ""
    return escape(str(text))


def public_error_message(exc: Exception, *, context: str = "") -> str:
    """Log full exception server-side; return a safe message for the browser."""
    if context:
        logger.error("Request error [%s]: %s\n%s", context, exc, traceback.format_exc())
    else:
        logger.error("Unhandled error: %s\n%s", exc, traceback.format_exc())
    debug = os.getenv("WEBUI_DEBUG", "").strip().lower() in ("1", "true", "yes")
    if debug:
        return f"Internal error: {exc}"
    return "Terjadi kesalahan internal. Silakan coba lagi atau hubungi admin jika masalah berlanjut."

def humanize_bytes(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    f = float(n)
    while f >= 1024 and i < len(units) - 1:
        f /= 1024
        i += 1
    return f"{f:.2f} {units[i]}"


def format_data_quota_compact(n) -> str:
    """Compact DATA quota label (e.g. 6 GB) from raw bytes."""
    from app.menus.util import format_quota_byte

    s = format_quota_byte(int(n or 0))
    for unit in (" GB", " MB", " KB"):
        if s.endswith(unit):
            num = float(s[: -len(unit)])
            if unit == " GB":
                return f"{int(num)} GB" if num == int(num) else f"{num:g} GB"
            return s
    return s


def format_benefit_quota_pair(benefit: dict) -> tuple[str, str]:
    """Return (total_display, remaining_display) from quota-details benefit row."""
    if benefit.get("is_unlimited"):
        return "Unlimited", "Unlimited"

    dt = (benefit.get("data_type") or "").upper()
    try:
        rem = int(benefit.get("remaining") or 0)
        tot = int(benefit.get("total") or 0)
    except (TypeError, ValueError):
        rem, tot = 0, 0

    if dt == "DATA":
        return format_data_quota_compact(tot), format_data_quota_compact(rem)
    if dt == "VOICE":
        return f"{tot / 60:.0f} Minutes", f"{rem / 60:.0f} Minutes"
    if dt == "TEXT":
        return str(tot), str(rem)
    return str(tot), str(rem)
