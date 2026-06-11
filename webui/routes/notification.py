from fastapi import APIRouter, Request
from app.client.engsel import get_notifications, get_notification_detail
from app.service.auth import AuthInstance
from webui.deps import render, get_active_user_safe

router = APIRouter()


@router.get("/notifications")
def notif_list(request: Request):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    try:
        data = get_notifications(AuthInstance.api_key, user["tokens"])
    except Exception as e:
        return render(request, "error.html", title="Gagal fetch", message=str(e))
    items = []
    if isinstance(data, dict):
        d = data.get("data") or {}
        items = d.get("notifications") if isinstance(d, dict) else d
        items = items or []
    return render(request, "notifications.html", items=items, raw=data)


@router.get("/notifications/{nid}")
def notif_detail(request: Request, nid: str):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    try:
        data = get_notification_detail(AuthInstance.api_key, user["tokens"], nid)
    except Exception as e:
        return render(request, "error.html", title="Gagal fetch", message=str(e))
    return render(request, "notification_detail.html", data=data, nid=nid)
