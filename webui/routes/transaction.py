from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request
from app.client.engsel import get_transaction_history
from app.service.auth import AuthInstance
from webui.deps import render, get_active_user_safe

router = APIRouter()


STATUS_STYLES = {
    "SUCCESS": ("emerald", "✅"),
    "DONE": ("emerald", "✅"),
    "COMPLETED": ("emerald", "✅"),
    "READY": ("emerald", "✅"),
    "PAID": ("emerald", "✅"),
    "PENDING": ("amber", "⏳"),
    "PROCESSING": ("amber", "⏳"),
    "WAITING": ("amber", "⏳"),
    "FAILED": ("red", "❌"),
    "CANCELLED": ("slate", "🚫"),
    "EXPIRED": ("slate", "⌛"),
}


def _fmt_dt(ts):
    if not ts:
        return ""
    try:
        # API timestamps appear to be in WIB (GMT+7) seconds; display as WIB.
        return datetime.fromtimestamp(int(ts), tz=timezone(timedelta(hours=7))).strftime("%d %b %Y · %H:%M")
    except Exception:
        return str(ts)


@router.get("/transactions")
def transactions(request: Request):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    try:
        data = get_transaction_history(AuthInstance.api_key, user["tokens"])
    except Exception as e:
        return render(request, "error.html", title="Gagal fetch", message=str(e))

    items = []
    if isinstance(data, dict):
        for t in data.get("list", []) or []:
            status = (t.get("status") or "").upper()
            ps = (t.get("payment_status") or "").upper()
            color, emoji = STATUS_STYLES.get(status, ("slate", "•"))
            pcolor, pemoji = STATUS_STYLES.get(ps, ("slate", "•"))
            icon = t.get("icon") or ""
            pm_icon = t.get("payment_method_icon") or ""
            items.append({
                "title": t.get("title") or "—",
                "price": t.get("price") or (f"IDR {t.get('raw_price', 0)}"),
                "validity": t.get("validity") or "",
                "dt": _fmt_dt(t.get("timestamp")) or t.get("formated_date", ""),
                "payment_method": t.get("payment_method_label") or t.get("payment_method") or "—",
                "status": status,
                "status_color": color,
                "status_emoji": emoji,
                "payment_status": ps,
                "payment_status_color": pcolor,
                "payment_status_emoji": pemoji,
                "target": t.get("target_msisdn") or t.get("customer_number") or "",
                "trx_code": t.get("trx_code") or t.get("transaction_id") or "",
                "icon_data_uri": f"data:image/png;base64,{icon}" if icon else "",
                "pm_icon_data_uri": f"data:image/png;base64,{pm_icon}" if pm_icon else "",
            })

    return render(request, "transactions.html", items=items, raw=data)
