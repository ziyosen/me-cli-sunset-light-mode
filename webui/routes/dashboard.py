from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.client.engsel import get_balance, get_tiering_info, send_api_request
from app.service.auth import AuthInstance
from webui.deps import render, get_active_user_safe

router = APIRouter()


@router.get("/")
def index(request: Request):
    active_user = get_active_user_safe()
    if not active_user:
        return RedirectResponse(url="/login", status_code=303)

    balance = None
    balance_err = None
    try:
        balance = get_balance(AuthInstance.api_key, active_user["tokens"]["id_token"])
    except Exception as e:
        balance_err = str(e)

    tier_info = {"tier": 0, "current_point": 0}
    if active_user.get("subscription_type") == "PREPAID":
        try:
            tier_info = get_tiering_info(AuthInstance.api_key, active_user["tokens"]) or tier_info
        except Exception:
            pass

    active_packages_count = 0
    try:
        qd = send_api_request(
            AuthInstance.api_key,
            "api/v8/packages/quota-details",
            {"is_enterprise": False, "lang": "en", "family_member_id": ""},
            active_user["tokens"]["id_token"],
            "POST",
        )
        if isinstance(qd, dict) and qd.get("status") == "SUCCESS":
            active_packages_count = len((qd.get("data") or {}).get("quotas") or [])
    except Exception:
        pass

    return render(
        request, "dashboard.html",
        balance=balance,
        balance_err=balance_err,
        tier_info=tier_info,
        active_packages_count=active_packages_count,
    )
