import json
import time

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from app.service.auth import AuthInstance
from webui.deps import render, get_active_user_safe
from webui import monitoring as M
from webui import quota_cache as QC
from webui import telegram_config as TC
from webui.users import get_user, load_users, user_dir

router = APIRouter()


def _user_accounts(request: Request) -> list[dict]:
    webui_user = getattr(request.state, "webui_user", None)
    if not webui_user:
        return []
    udir = user_dir(webui_user["username"])
    rt_file = udir / "refresh-tokens.json"
    if not rt_file.exists():
        return []
    try:
        return json.loads(rt_file.read_text(encoding="utf-8"))
    except Exception:
        return []


@router.get("/monitoring")
def monitoring_dashboard(request: Request):
    webui_user = getattr(request.state, "webui_user", None)
    if not webui_user:
        return RedirectResponse("/u/login", status_code=303)

    uname = webui_user["username"]
    cache = QC.load_cache(uname)
    rules = M.load_rules()
    tg = M.load_telegram()
    tg_global = TC.load_config()
    log_lines = M.tail_log(50)
    accounts = _user_accounts(request)

    return render(
        request, "monitoring.html",
        cache=cache, rules=rules, tg=tg, tg_global=tg_global,
        log_lines=log_lines, myxl_accounts=accounts,
    )


@router.post("/monitoring/refresh")
def monitoring_refresh(request: Request):
    webui_user = getattr(request.state, "webui_user", None)
    if not webui_user:
        return RedirectResponse("/u/login", status_code=303)

    uname = webui_user["username"]
    accounts = _user_accounts(request)

    from webui.cwd_lock import get_user_tokens

    api_key = AuthInstance.api_key
    for acc in accounts:
        msisdn = acc.get("number")
        if not msisdn:
            continue
        tokens = get_user_tokens(uname, int(msisdn))
        if not tokens:
            continue
        try:
            from app.client.engsel import get_balance, send_api_request
            balance = get_balance(api_key, tokens["id_token"])
            res = send_api_request(
                api_key, "api/v8/packages/quota-details",
                {"is_enterprise": False, "lang": "en", "family_member_id": ""},
                tokens["id_token"], "POST",
            )
            quotas = None
            if isinstance(res, dict) and res.get("status") == "SUCCESS":
                quotas = (res.get("data") or {}).get("quotas") or []
            QC.update_account_cache(uname, int(msisdn), balance, quotas)
        except Exception:
            pass

    return RedirectResponse("/monitoring?msg=refreshed", status_code=303)


@router.get("/monitoring/telegram")
def telegram_settings(request: Request):
    webui_user = getattr(request.state, "webui_user", None)
    if not webui_user:
        return RedirectResponse("/u/login", status_code=303)

    tg_global = TC.load_config()
    tg_user = M.load_telegram()
    user = get_user(webui_user["username"])
    chat_id = user.get("telegram_chat_id") if user else None

    return render(
        request, "monitoring_telegram.html",
        tg_global=tg_global, tg_user=tg_user, chat_id=chat_id,
    )


@router.post("/monitoring/telegram")
def telegram_save(
    request: Request,
    bot_token: str = Form(""),
    enabled: str = Form(""),
    daily_summary_enabled: str = Form(""),
    daily_summary_hour: int = Form(7),
    daily_summary_minute: int = Form(0),
    low_quota_threshold_pct: int = Form(10),
    poll_interval_minutes: int = Form(5),
):
    webui_user = getattr(request.state, "webui_user", None)
    if not webui_user:
        return RedirectResponse("/u/login", status_code=303)

    cfg = TC.load_config()
    old_token = cfg.get("bot_token", "")

    cfg["bot_token"] = bot_token.strip()
    cfg["enabled"] = enabled == "on"
    cfg["daily_summary_enabled"] = daily_summary_enabled == "on"
    cfg["daily_summary_hour"] = daily_summary_hour
    cfg["daily_summary_minute"] = daily_summary_minute
    cfg["low_quota_threshold_pct"] = low_quota_threshold_pct
    cfg["poll_interval_minutes"] = max(1, poll_interval_minutes)
    TC.save_config(cfg)

    # Also save per-user telegram.json for compatibility with monitoring.py send_telegram
    M.save_telegram(cfg["bot_token"], str(
        (get_user(webui_user["username"]) or {}).get("telegram_chat_id", "")
    ))

    # Restart bot if token changed or enabled state changed
    from webui.app import restart_bot
    restart_bot()

    return RedirectResponse("/monitoring/telegram?msg=saved", status_code=303)


@router.post("/monitoring/telegram/test")
def telegram_test(request: Request):
    webui_user = getattr(request.state, "webui_user", None)
    if not webui_user:
        return RedirectResponse("/u/login", status_code=303)

    tg_cfg = TC.load_config()
    user = get_user(webui_user["username"])
    chat_id = user.get("telegram_chat_id") if user else None

    if not chat_id:
        return RedirectResponse("/monitoring/telegram?msg=no_chat_id", status_code=303)

    cfg = {"bot_token": tg_cfg.get("bot_token", ""), "chat_id": str(chat_id)}
    ok, info = M.send_telegram("Test message dari me-cli WebUI!", cfg=cfg)

    msg = "test_ok" if ok else f"test_fail"
    return RedirectResponse(f"/monitoring/telegram?msg={msg}", status_code=303)


@router.post("/monitoring/rules")
def add_rule(
    request: Request,
    name: str = Form(""),
    msisdn: str = Form("0"),
    match_kind: str = Form("any"),
    match_value: str = Form(""),
    match_data_type: str = Form("ANY"),
    trigger_metric: str = Form("remaining_pct"),
    trigger_op: str = Form("lt"),
    trigger_value: str = Form("10"),
    cooldown_seconds: int = Form(3600),
    action_type: str = Form("telegram"),
    action_message: str = Form(""),
    action_option_code: str = Form(""),
    action_method: str = Form("balance"),
):
    webui_user = getattr(request.state, "webui_user", None)
    if not webui_user:
        return RedirectResponse("/u/login", status_code=303)

    actions = []
    if action_type == "telegram":
        actions.append({"type": "telegram", "message": action_message or name})
    elif action_type == "buy_option":
        actions.append({"type": "buy_option", "option_code": action_option_code, "method": action_method})
    elif action_type == "unsubscribe":
        actions.append({"type": "unsubscribe"})
    elif action_type == "telegram_and_buy":
        actions.append({"type": "telegram", "message": action_message or name})
        actions.append({"type": "buy_option", "option_code": action_option_code, "method": action_method})

    M.add_rule({
        "name": name or "Untitled Rule",
        "msisdn": msisdn,
        "match": {"kind": match_kind, "value": match_value, "data_type": match_data_type},
        "trigger": {"metric": trigger_metric, "op": trigger_op, "value": float(trigger_value or 10)},
        "actions": actions,
        "cooldown_seconds": cooldown_seconds,
    })

    return RedirectResponse("/monitoring?msg=rule_added", status_code=303)


@router.post("/monitoring/rules/{rule_id}/delete")
def delete_rule(request: Request, rule_id: str):
    M.delete_rule(rule_id)
    return RedirectResponse("/monitoring?msg=rule_deleted", status_code=303)


@router.post("/monitoring/rules/{rule_id}/toggle")
def toggle_rule(request: Request, rule_id: str):
    rule = M.get_rule(rule_id)
    if rule:
        M.update_rule(rule_id, {"enabled": not rule.get("enabled", True)})
    return RedirectResponse("/monitoring", status_code=303)


@router.post("/monitoring/run-once")
def run_once(request: Request):
    webui_user = getattr(request.state, "webui_user", None)
    if not webui_user:
        return RedirectResponse("/u/login", status_code=303)

    from webui.monitor_loop import run_once_sync
    try:
        run_once_sync(webui_user["username"])
    except Exception as e:
        M.log_line(f"[run-once] {webui_user['username']} err: {e}")

    return RedirectResponse("/monitoring?msg=ran", status_code=303)
