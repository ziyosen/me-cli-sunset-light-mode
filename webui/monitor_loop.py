"""Background monitor loop.

Lifespan task that, every POLL_INTERVAL seconds:
1. Iterate all webui users (from webui_data/users.json)
2. For each user: chdir into their dir → reload AuthInstance → for each MyXL
   account in their refresh-tokens, call quota-details, evaluate enabled rules,
   fire actions when triggers match (respecting cooldown).

Uses cwd_lock for thread-safe CWD switching.
Also writes quota snapshots to quota_cache and sends daily summaries.
"""
import os
import time
import asyncio
import traceback
from pathlib import Path

from webui.users import load_users, user_dir, PROJECT_DIR
from webui.cwd_lock import user_cwd
from webui import monitoring as M
from webui import quota_cache as QC
from webui import telegram_config as TC

POLL_INTERVAL = 5 * 60  # 5 minutes

_last_summary: dict[str, float] = {}  # username -> last summary timestamp


def _format_bytes(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.2f} {units[i]}"


def _quota_metric_value(quota: dict, benefit: dict, metric: str) -> float | None:
    """Compute the metric value for a benefit row inside a quota row."""
    rem = benefit.get("remaining") or 0
    tot = benefit.get("total") or 0
    if metric == "remaining_pct":
        return (rem / tot * 100.0) if tot else 0.0
    if metric == "remaining_bytes":
        return float(rem)
    if metric == "remaining_minutes":
        return float(rem) / 60.0
    if metric == "expiring_in_days":
        exp = quota.get("expired_at") or 0
        if not exp:
            return None
        return (int(exp) - time.time()) / 86400.0
    return None


def _matches_filter(quota: dict, benefit: dict, match: dict) -> bool:
    kind = match.get("kind") or "any"
    val = (match.get("value") or "").strip()
    dt_filter = (match.get("data_type") or "ANY").upper()

    # Filter by data type first
    if dt_filter != "ANY":
        if (benefit.get("data_type") or "").upper() != dt_filter:
            return False

    if kind == "any":
        return True
    if kind == "quota_name":
        return val.lower() in (quota.get("name", "") or "").lower()
    if kind == "quota_code":
        return val == (quota.get("quota_code", "") or "")
    if kind == "group_name":
        return val.lower() in (quota.get("group_name", "") or "").lower()
    return False


def _cmp(a: float, op: str, b: float) -> bool:
    if op == "lt": return a < b
    if op == "lte": return a <= b
    if op == "gt": return a > b
    if op == "gte": return a >= b
    if op == "eq": return abs(a - b) < 1e-9
    return False


def _execute_actions(rule: dict, user_obj, quota: dict, benefit: dict, *, uname: str = "") -> tuple[str, str]:
    """Run rule.actions[] for a triggered quota+benefit. Returns (status, msg)."""
    from app.service.auth import AuthInstance
    from app.client.engsel import unsubscribe
    from app.client.engsel import get_package, send_api_request
    from app.client.purchase.balance import settlement_balance
    from app.client.purchase.qris import settlement_qris, get_qris_code
    from app.client.purchase.ewallet import settlement_multipayment
    from app.type_dict import PaymentItem

    results: list[str] = []
    tg_cfg = M.resolve_send_config(uname or None)

    for action in rule.get("actions") or []:
        t = action.get("type")
        try:
            if t == "telegram":
                msg = action.get("message") or rule.get("name", "rule")
                # Substitute simple placeholders
                msg = msg.replace("{quota}", quota.get("name", "-"))
                msg = msg.replace("{benefit}", benefit.get("name", "-"))
                rem = benefit.get("remaining", 0) or 0
                tot = benefit.get("total", 0) or 0
                pct = (rem / tot * 100.0) if tot else 0.0
                msg = msg.replace("{pct}", f"{pct:.1f}")
                msg = msg.replace("{remaining}", _format_bytes(rem) if benefit.get("data_type") == "DATA" else str(rem))
                msg = msg.replace("{total}", _format_bytes(tot) if benefit.get("data_type") == "DATA" else str(tot))
                msg = msg.replace("{msisdn}", str(user_obj.get("number", "")))
                # Default header if user didn't include details
                if "{nodefault}" not in msg:
                    header = (
                        f"📡 <b>Quota Alert</b>\n"
                        f"📱 <code>{user_obj.get('number','')}</code> · {user_obj.get('subscription_type','')}\n"
                        f"📦 {quota.get('name','-')}\n"
                        f"🎯 {benefit.get('name','-')}: {pct:.1f}% "
                        f"({_format_bytes(rem) if benefit.get('data_type')=='DATA' else rem}"
                        f" / {_format_bytes(tot) if benefit.get('data_type')=='DATA' else tot})\n\n"
                    )
                    msg = header + msg
                ok, info = M.send_telegram(msg, cfg=tg_cfg, username=uname or None)
                results.append(("✅ tg: " if ok else "⚠️ tg: ") + info)

            elif t == "buy_option":
                code = action.get("option_code", "").strip()
                method = (action.get("method") or "balance").lower()
                if not code:
                    results.append("⚠️ buy: option_code kosong"); continue
                pkg = get_package(AuthInstance.api_key, user_obj["tokens"], code)
                if not pkg:
                    results.append(f"⚠️ buy: paket {code} not found"); continue
                opt = pkg["package_option"]
                item = PaymentItem(
                    item_code=opt["package_option_code"], product_type="",
                    item_price=opt["price"], item_name=opt["name"], tax=0,
                    token_confirmation=pkg["token_confirmation"],
                )
                pf = pkg.get("package_family", {}).get("payment_for", "BUY_PACKAGE")
                if method == "balance":
                    res = settlement_balance(
                        AuthInstance.api_key, user_obj["tokens"], [item],
                        payment_for=pf, ask_overwrite=False,
                        overwrite_amount=item["item_price"],
                    )
                    ok = isinstance(res, dict) and res.get("status") == "SUCCESS"
                    results.append(("✅ buy " if ok else "⚠️ buy ") + opt["name"] + " (pulsa)")
                elif method == "qris":
                    tx = settlement_qris(
                        AuthInstance.api_key, user_obj["tokens"], [item],
                        payment_for=pf, ask_overwrite=False,
                        overwrite_amount=item["item_price"],
                    )
                    if tx and isinstance(tx, str):
                        qris_code = get_qris_code(AuthInstance.api_key, user_obj["tokens"], tx)
                        results.append(f"✅ qris tx={tx[:12]}…")
                        if qris_code and tg_cfg.get("bot_token"):
                            M.send_telegram(
                                f"💸 QRIS untuk auto-buy {opt['name']} ({user_obj.get('number','')}):\n<code>{qris_code}</code>",
                                cfg=tg_cfg)
                    else:
                        results.append("⚠️ qris settlement gagal")
                else:
                    results.append(f"⚠️ buy: method {method} belum di-handle untuk auto-action")

            elif t == "unsubscribe":
                qc = quota.get("quota_code", "")
                pd = quota.get("product_domain", "")
                ps = quota.get("product_subscription_type", "")
                ok = unsubscribe(AuthInstance.api_key, user_obj["tokens"], qc, pd, ps)
                results.append(("✅ unsub " if ok else "⚠️ unsub ") + quota.get("name", ""))

            else:
                results.append(f"⚠️ action type '{t}' unknown")
        except Exception as e:
            results.append(f"❌ action {t} exception: {e}")

    msg = " | ".join(results) if results else "no actions"
    status = "ok" if all(r.startswith("✅") for r in results) else "partial"
    return status, msg


def _check_user_once(uname: str) -> None:
    """Process one user: chdir → reload Auth → for each MyXL account → evaluate rules."""
    udir = user_dir(uname)
    if not udir.exists():
        return

    with user_cwd(uname):
        # Reload singletons from this CWD (already done by user_cwd context manager)
        from app.service.auth import AuthInstance

        rules = [r for r in M.load_rules() if r.get("enabled")]
        if not rules:
            return

        # Group rules by msisdn so we only fetch quota once per MyXL account
        by_msisdn: dict[int, list[dict]] = {}
        for r in rules:
            try:
                n = int(r.get("msisdn") or 0)
            except (TypeError, ValueError):
                continue
            if n:
                by_msisdn.setdefault(n, []).append(r)

        now = int(time.time())

        for msisdn, msisdn_rules in by_msisdn.items():
            # Activate this number
            try:
                ok = AuthInstance.set_active_user(msisdn)
            except Exception as e:
                M.log_line(f"[{uname}] set_active_user({msisdn}) err: {e}")
                continue
            user_obj = AuthInstance.get_active_user()
            if not user_obj or user_obj.get("number") != msisdn:
                M.log_line(f"[{uname}] cannot activate {msisdn}")
                continue

            # Fetch quota-details
            try:
                from app.client.engsel import send_api_request, get_balance
                res = send_api_request(
                    AuthInstance.api_key,
                    "api/v8/packages/quota-details",
                    {"is_enterprise": False, "lang": "en", "family_member_id": ""},
                    user_obj["tokens"]["id_token"],
                    "POST",
                )
            except Exception as e:
                M.log_line(f"[{uname}/{msisdn}] quota fetch err: {e}")
                continue

            if not isinstance(res, dict) or res.get("status") != "SUCCESS":
                M.log_line(f"[{uname}/{msisdn}] quota fetch !SUCCESS: {res}")
                continue
            quotas = (res.get("data") or {}).get("quotas") or []

            # Update quota cache
            try:
                balance = get_balance(AuthInstance.api_key, user_obj["tokens"]["id_token"])
            except Exception:
                balance = None
            QC.update_account_cache(uname, msisdn, balance, quotas)

            for rule in msisdn_rules:
                # Cooldown gate
                last = rule.get("last_fired_at") or 0
                cd = rule.get("cooldown_seconds") or 0
                if last and (now - last) < cd:
                    continue

                trigger = rule.get("trigger") or {}
                metric = trigger.get("metric", "remaining_pct")
                op = trigger.get("op", "lt")
                target = float(trigger.get("value", 0))

                fired = False
                for quota in quotas:
                    for benefit in (quota.get("benefits") or []):
                        if not _matches_filter(quota, benefit, rule.get("match") or {}):
                            continue
                        val = _quota_metric_value(quota, benefit, metric)
                        if val is None:
                            continue
                        if _cmp(val, op, target):
                            fired = True
                            try:
                                status, action_msg = _execute_actions(rule, user_obj, quota, benefit, uname=uname)
                            except Exception as e:
                                status, action_msg = "error", f"{e}"
                            M.mark_fired(rule["id"], status, action_msg)
                            M.log_line(
                                f"[{uname}/{msisdn}] rule '{rule['name']}' FIRED: "
                                f"{quota.get('name','-')} · {benefit.get('name','-')} "
                                f"{metric}={val:.1f} {op} {target} → {action_msg}"
                            )
                            break  # one firing per check
                    if fired:
                        break


def _send_daily_summary(uname: str, tg_cfg: dict) -> None:
    """Send daily quota summary to user's linked Telegram chat."""
    from webui.users import get_user
    user = get_user(uname)
    if not user:
        return
    chat_id = user.get("telegram_chat_id")
    if not chat_id:
        return

    cache = QC.load_cache(uname)
    if not cache:
        return

    lines = ["<b>📊 Daily Quota Summary</b>\n"]
    for msisdn_str, data in cache.items():
        bal = data.get("balance") or {}
        remaining = bal.get("remaining")
        bal_str = f"Rp {remaining:,.0f}".replace(",", ".") if remaining is not None else "-"
        lines.append(f"📱 <code>{msisdn_str}</code> · Pulsa: {bal_str}")

        quotas = data.get("quotas") or []
        for q in quotas[:8]:
            name = q.get("name", "-")
            benefits = q.get("benefits") or []
            parts = []
            for b in benefits[:3]:
                rem = b.get("remaining", 0) or 0
                tot = b.get("total", 0) or 0
                pct = (rem / tot * 100) if tot else 0
                dt = b.get("data_type", "")
                if dt == "DATA":
                    parts.append(f"{_format_bytes(rem)} ({pct:.0f}%)")
                elif dt == "VOICE":
                    parts.append(f"{rem/60:.0f}m ({pct:.0f}%)")
                else:
                    parts.append(f"{rem} ({pct:.0f}%)")
            lines.append(f"  📦 {name}: {', '.join(parts) if parts else '-'}")
        lines.append("")

    text = "\n".join(lines)
    cfg_for_send = {"bot_token": tg_cfg.get("bot_token", ""), "chat_id": str(chat_id)}
    M.send_telegram(text, cfg=cfg_for_send)
    M.log_line(f"[{uname}] daily summary sent to {chat_id}")


async def monitor_loop():
    """The async lifespan task. Runs forever; sleep POLL_INTERVAL between sweeps."""
    M.log_line("[monitor] started")
    while True:
        try:
            users = load_users()
            tg_cfg = TC.load_config()

            for u in users:
                uname = u["username"]
                try:
                    _check_user_once(uname)
                except Exception as e:
                    M.log_line(f"[monitor] user {uname} err: {e}\n{traceback.format_exc()}")

                # Daily summary check
                if tg_cfg.get("daily_summary_enabled") and tg_cfg.get("bot_token"):
                    now = time.time()
                    last = _last_summary.get(uname, 0)
                    summary_hour = tg_cfg.get("daily_summary_hour", 7)
                    summary_min = tg_cfg.get("daily_summary_minute", 0)

                    import datetime
                    now_dt = datetime.datetime.now()
                    target_today = now_dt.replace(hour=summary_hour, minute=summary_min, second=0, microsecond=0)
                    target_ts = target_today.timestamp()

                    if now >= target_ts and last < target_ts:
                        try:
                            _send_daily_summary(uname, tg_cfg)
                            _last_summary[uname] = now
                        except Exception as e:
                            M.log_line(f"[{uname}] daily summary err: {e}")

        except Exception as e:
            try:
                M.log_line(f"[monitor] outer err: {e}")
            except Exception:
                pass
        await asyncio.sleep(POLL_INTERVAL)


def run_once_sync(uname: str) -> None:
    """Run a single user's rules now (used by 'Run once' button)."""
    _check_user_once(uname)
