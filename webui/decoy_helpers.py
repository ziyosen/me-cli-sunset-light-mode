"""Decoy file helpers (no app.* imports — safe under user_cwd)."""
import json
from pathlib import Path


def decoy_dir() -> Path:
    return Path("decoy_data")


def _load_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _configured(data: dict) -> bool:
    return bool(data.get("family_code") and data.get("variant_code"))


def _rp_label(price) -> str:
    try:
        n = int(price or 0)
        return f"Rp {n:,}".replace(",", ".")
    except (TypeError, ValueError):
        return "Rp ?"


def _method_for_builtin_kind(kind: str) -> str | None:
    return {
        "balance": "decoy_balance",
        "qris": "decoy_qris",
        "qris0": "decoy_qris0",
    }.get(kind)


def _pay_icon(kind: str) -> str:
    if kind == "balance":
        return "💳"
    if kind in ("qris", "qris0"):
        return "📱"
    return "🎭"


def list_default_decoy_choices() -> list[dict]:
    """Built-in decoy-*.json slots (V1 / default). One button per configured file."""
    d = decoy_dir()
    if not d.exists():
        return []

    choices: list[dict] = []
    for p in sorted(d.glob("decoy-*.json")):
        slot = p.stem[len("decoy-"):]
        data = _load_json(p)
        if not _configured(data):
            continue
        kind = slot.rsplit("-", 1)[-1] if "-" in slot else slot
        method = _method_for_builtin_kind(kind)
        if not method:
            continue
        opt = (data.get("option_name") or data.get("variant_name") or slot).strip()
        price = _rp_label(data.get("price"))
        prefix = slot.rsplit("-", 1)[0] if "-" in slot else slot
        icon = _pay_icon(kind)
        choices.append({
            "label": f"{icon} {prefix} · {opt} ({price})",
            "method": method,
            "slot": slot,
            "tier": "default",
        })
    return choices


def list_custom_decoy_choices() -> list[dict]:
    """custom-*.json decoys (V2)."""
    d = decoy_dir()
    if not d.exists():
        return []

    choices: list[dict] = []
    for p in sorted(d.glob("custom-*.json")):
        name = p.stem[len("custom-"):]
        data = _load_json(p)
        if not _configured(data):
            continue
        opt = (data.get("option_name") or name).strip()
        price = _rp_label(data.get("price"))
        base_m = (data.get("base_method") or "balance").lower()
        icon = "📱" if base_m == "qris" else "💳"
        choices.append({
            "label": f"{icon} {name} · {opt} ({price})",
            "method": f"decoy_custom_{name}",
            "slot": None,
            "tier": "custom",
        })
    return choices


def list_configured_decoy_choices() -> list[dict]:
    """All configured decoys (default + custom)."""
    return list_default_decoy_choices() + list_custom_decoy_choices()