from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import RedirectResponse
from app.client.engsel import get_package, get_family, get_profile, send_api_request, unsubscribe
from app.service.auth import AuthInstance
from app.menus.util import format_quota_byte
from webui.deps import render, get_active_user_safe
from webui.routes.decoy_settings import list_custom_decoys

router = APIRouter()


@router.get("/packages/by-option")
def by_option(request: Request, code: str | None = Query(None), enterprise: bool = False):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    if not code:
        return render(request, "packages_input_code.html", mode="option")

    try:
        pkg = get_package(AuthInstance.api_key, user["tokens"], code)
    except Exception as e:
        return render(request, "error.html", title="Gagal fetch", message=str(e))
    if not pkg:
        return render(request, "error.html", title="Tidak ditemukan", message=f"Option code {code} tidak ditemukan.")

    return render(request, "package_detail.html",
                  pkg=pkg, code=code, is_enterprise=enterprise,
                  custom_decoys=list_custom_decoys())


@router.get("/packages/by-family")
def by_family(request: Request, code: str | None = Query(None)):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    if not code:
        return render(request, "packages_input_code.html", mode="family")

    try:
        family = get_family(AuthInstance.api_key, user["tokens"], code)
    except Exception as e:
        return render(request, "error.html", title="Gagal fetch", message=str(e))
    if not family:
        return render(request, "error.html", title="Tidak ditemukan", message=f"Family code {code} tidak ditemukan.")
    return render(request, "family_detail.html", family=family, code=code, format_quota_byte=format_quota_byte)


@router.get("/packages/my")
def my_packages(request: Request):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")

    try:
        res = send_api_request(
            AuthInstance.api_key,
            "api/v8/packages/quota-details",
            {"is_enterprise": False, "lang": "en", "family_member_id": ""},
            user["tokens"]["id_token"],
            "POST",
        )
    except Exception as e:
        return render(request, "error.html", title="Gagal fetch", message=str(e))

    if not isinstance(res, dict) or res.get("status") != "SUCCESS":
        return render(request, "my_packages.html", quotas=[], raw=res)

    quotas = res.get("data", {}).get("quotas", []) or []
    # Format benefits (data/voice/text)
    formatted = []
    for q in quotas:
        benefits = []
        for b in q.get("benefits", []) or []:
            dt = b.get("data_type", "")
            rem, tot = b.get("remaining", 0), b.get("total", 0)
            if dt == "DATA":
                disp_rem = format_quota_byte(rem)
                disp_tot = format_quota_byte(tot)
                unit = ""
            elif dt == "VOICE":
                disp_rem = f"{rem/60:.0f}"
                disp_tot = f"{tot/60:.0f}"
                unit = "menit"
            elif dt == "TEXT":
                disp_rem = str(rem); disp_tot = str(tot); unit = "SMS"
            else:
                disp_rem = str(rem); disp_tot = str(tot); unit = dt
            pct = int((rem / tot) * 100) if tot else 0
            benefits.append({
                "id": b.get("id"), "name": b.get("name"), "data_type": dt,
                "rem_disp": disp_rem, "tot_disp": disp_tot, "unit": unit, "pct": pct,
                "is_unlimited": b.get("is_unlimited", False),
            })
        formatted.append({
            "name": q.get("name", "-"),
            "quota_code": q.get("quota_code", ""),
            "group_name": q.get("group_name", ""),
            "group_code": q.get("group_code", ""),
            "expired_at": q.get("expired_at"),
            "product_domain": q.get("product_domain", ""),
            "product_subscription_type": q.get("product_subscription_type", ""),
            "benefits": benefits,
        })
    return render(request, "my_packages.html", quotas=formatted, raw=res)


@router.post("/packages/my/unsubscribe")
def my_packages_unsub(
    request: Request,
    quota_code: str = Form(...),
    product_domain: str = Form(""),
    product_subscription_type: str = Form(""),
):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    try:
        ok = unsubscribe(
            AuthInstance.api_key, user["tokens"],
            quota_code, product_domain, product_subscription_type,
        )
    except Exception as e:
        return render(request, "error.html", title="Unsubscribe gagal", message=str(e))
    return RedirectResponse(url=f"/packages/my?msg={'ok' if ok else 'fail'}", status_code=303)
