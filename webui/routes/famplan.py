from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

from app.client.famplan import (
    get_family_data, change_member, remove_member, set_quota_limit, validate_msisdn,
)
from app.service.auth import AuthInstance
from webui.deps import render, get_active_user_safe

router = APIRouter()


@router.get("/family-plan")
def famplan_page(request: Request):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    try:
        data = get_family_data(AuthInstance.api_key, user["tokens"])
    except Exception as e:
        return render(request, "error.html", title="Gagal fetch", message=str(e))

    info = {}
    members = []
    additional = []
    has_plan = False
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        m = data["data"].get("member_info") or {}
        plan_type = m.get("plan_type", "")
        if plan_type:
            has_plan = True
            total_q = m.get("total_quota", 0) or 0
            rem_q = m.get("remaining_quota", 0) or 0
            used_q = max(0, total_q - rem_q)
            pct = int((used_q / total_q) * 100) if total_q else 0
            info = {
                "plan_type": plan_type,
                "parent_msisdn": m.get("parent_msisdn", ""),
                "total_quota": total_q,
                "remaining_quota": rem_q,
                "used_quota": used_q,
                "usage_pct": pct,
                "end_date_ts": m.get("end_date", 0) or 0,
                "total_regular_slot": m.get("total_regular_slot", 0) or 0,
                "total_paid_slot": m.get("total_paid_slot", 0) or 0,
            }

            def _build_member(mem, idx, is_additional=False):
                msisdn = mem.get("msisdn", "") or ""
                usage = mem.get("usage", {}) or {}
                alloc = usage.get("quota_allocated", 0) or 0
                used = usage.get("quota_used", 0) or 0
                mem_pct = int((used / alloc) * 100) if alloc else 0
                exp_ts = usage.get("quota_expired_at", 0) or 0
                return {
                    "idx": idx,
                    "msisdn": msisdn,
                    "alias": mem.get("alias", "") or "",
                    "slot_id": mem.get("slot_id", ""),
                    "family_member_id": mem.get("family_member_id", ""),
                    "member_type": mem.get("member_type", ""),
                    "add_chances": mem.get("add_chances", 0),
                    "total_add_chances": mem.get("total_add_chances", 0),
                    "quota_allocated": alloc,
                    "quota_used": used,
                    "quota_pct": mem_pct,
                    "exp_ts": exp_ts,
                    "is_empty": msisdn == "",
                    "is_parent": mem.get("member_type", "") == "PARENT",
                    "is_additional": is_additional,
                }

            for idx, mem in enumerate(m.get("members", []) or [], start=1):
                members.append(_build_member(mem, idx))
            base = len(members)
            for idx, mem in enumerate(m.get("additional_members", []) or [], start=1):
                additional.append(_build_member(mem, base + idx, is_additional=True))

    return render(request, "famplan.html",
                  data=data, info=info,
                  members=members, additional=additional, has_plan=has_plan)


@router.post("/family-plan/change-member")
def famplan_change(
    request: Request,
    parent_alias: str = Form(...),
    alias: str = Form(...),
    slot_id: int = Form(...),
    family_member_id: str = Form(...),
    new_msisdn: str = Form(...),
):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    res = change_member(AuthInstance.api_key, user["tokens"], parent_alias, alias, slot_id, family_member_id, new_msisdn)
    return render(request, "famplan_result.html", title="Ganti Member", res=res)


@router.post("/family-plan/remove-member")
def famplan_remove(request: Request, family_member_id: str = Form(...)):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    res = remove_member(AuthInstance.api_key, user["tokens"], family_member_id)
    return render(request, "famplan_result.html", title="Hapus Member", res=res)


@router.post("/family-plan/set-quota")
def famplan_quota(
    request: Request,
    family_member_id: str = Form(...),
    original_allocation: int = Form(...),
    new_allocation_mb: int = Form(...),
):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    new_alloc = new_allocation_mb * 1024 * 1024
    res = set_quota_limit(AuthInstance.api_key, user["tokens"], original_allocation, new_alloc, family_member_id)
    return render(request, "famplan_result.html", title="Set Quota", res=res)


@router.get("/validate-msisdn")
def validate_form(request: Request):
    return render(request, "validate_msisdn.html", res=None)


@router.post("/validate-msisdn")
def validate_post(request: Request, msisdn: str = Form(...)):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    try:
        res = validate_msisdn(AuthInstance.api_key, user["tokens"], msisdn)
    except Exception as e:
        return render(request, "error.html", title="Gagal validate", message=str(e))
    return render(request, "validate_msisdn.html", res=res, msisdn=msisdn)
