from datetime import datetime
from fastapi import APIRouter, Request, Query

from app.client.store.segments import get_segments
from app.client.store.search import get_family_list, get_store_packages
from app.client.store.redeemables import get_redeemables
from app.service.auth import AuthInstance
from webui.deps import render, get_active_user_safe

router = APIRouter()


def _need_user(request: Request):
    user = get_active_user_safe()
    if not user:
        return None, render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    return user, None


def _action_href(action_type: str, action_param: str) -> str | None:
    if action_type == "PDP":
        return f"/packages/by-option?code={action_param}"
    if action_type == "PLP":
        return f"/packages/by-family?code={action_param}"
    return None


@router.get("/store/segments")
def store_segments(request: Request, enterprise: bool = Query(False)):
    user, err = _need_user(request)
    if err: return err
    try:
        res = get_segments(AuthInstance.api_key, user["tokens"], is_enterprise=enterprise)
    except Exception as e:
        return render(request, "error.html", title="Gagal fetch", message=str(e))

    segments = []
    if isinstance(res, dict):
        raw_segs = (res.get("data") or {}).get("store_segments") or []
        for s in raw_segs:
            banners = []
            for b in (s.get("banners") or []):
                banners.append({
                    "title": b.get("title", "-"),
                    "family_name": b.get("family_name", ""),
                    "validity": b.get("validity", ""),
                    "price": b.get("discounted_price"),
                    "original_price": b.get("original_price"),
                    "image_url": b.get("image_url") or b.get("background_image_url"),
                    "href": _action_href(b.get("action_type", ""), b.get("action_param", "")),
                    "action_type": b.get("action_type", ""),
                })
            segments.append({"title": s.get("title", "-"), "banners": banners})
    return render(request, "store_segments.html", segments=segments, enterprise=enterprise, raw=res)


@router.get("/store/families")
def store_families(request: Request, enterprise: bool = Query(False)):
    user, err = _need_user(request)
    if err: return err
    subs_type = user.get("subscription_type") or "PREPAID"
    try:
        res = get_family_list(AuthInstance.api_key, user["tokens"], subs_type=subs_type, is_enterprise=enterprise)
    except Exception as e:
        return render(request, "error.html", title="Gagal fetch", message=str(e))

    families = []
    if isinstance(res, dict):
        for f in (res.get("data") or {}).get("results", []):
            families.append({
                "label": f.get("label", "-"),
                "id": f.get("id", ""),
                "icon": f.get("icon_url") or f.get("icon"),
            })
    return render(request, "store_families.html", families=families, enterprise=enterprise, raw=res)


@router.get("/store/packages")
def store_packages(request: Request, enterprise: bool = Query(False), q: str = Query("")):
    user, err = _need_user(request)
    if err: return err
    subs_type = user.get("subscription_type") or "PREPAID"
    try:
        res = get_store_packages(AuthInstance.api_key, user["tokens"], subs_type=subs_type, is_enterprise=enterprise)
    except Exception as e:
        return render(request, "error.html", title="Gagal fetch", message=str(e))

    packages = []
    if isinstance(res, dict):
        for p in (res.get("data") or {}).get("results_price_only", []):
            original = p.get("original_price", 0) or 0
            discounted = p.get("discounted_price", 0) or 0
            packages.append({
                "title": p.get("title", "-"),
                "family_name": p.get("family_name", ""),
                "original_price": original,
                "price": discounted if discounted > 0 else original,
                "has_discount": discounted > 0 and discounted != original,
                "validity": p.get("validity", ""),
                "href": _action_href(p.get("action_type", ""), p.get("action_param", "")),
            })
    if q:
        ql = q.lower()
        packages = [p for p in packages if ql in p["title"].lower() or ql in p["family_name"].lower()]
    return render(request, "store_packages.html", packages=packages, enterprise=enterprise, q=q, raw=res)


@router.get("/store/redemables")
def store_redemables(request: Request, enterprise: bool = Query(False)):
    user, err = _need_user(request)
    if err: return err
    try:
        res = get_redeemables(AuthInstance.api_key, user["tokens"], is_enterprise=enterprise)
    except Exception as e:
        return render(request, "error.html", title="Gagal fetch", message=str(e))

    categories = []
    if isinstance(res, dict):
        data = res.get("data") or {}
        if isinstance(data, dict):
            cats = data.get("categories", []) or []
        else:
            cats = []
        for c in cats:
            items = []
            redeems = c.get("redeemables") or []
            if not isinstance(redeems, (list, tuple)):
                redeems = []
            for r in redeems:
                vu = r.get("valid_until")
                valid_until_str = ""
                if vu:
                    try:
                        valid_until_str = datetime.fromtimestamp(int(vu)).strftime("%Y-%m-%d")
                    except Exception:
                        valid_until_str = str(vu)
                items.append({
                    "name": r.get("name", "-"),
                    "valid_until": valid_until_str,
                    "icon": r.get("icon_url") or r.get("image_url"),
                    "action_type": r.get("action_type", ""),
                    "href": _action_href(r.get("action_type", ""), r.get("action_param", "")),
                })
            categories.append({
                "name": c.get("category_name", "-"),
                "code": c.get("category_code", ""),
                "redeem_items": items,
            })
    return render(request, "store_redemables.html", categories=categories, enterprise=enterprise, raw=res)
