from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

from app.client.circle import (
    get_group_data, get_group_members,
    invite_circle_member, remove_circle_member, accept_circle_invitation, create_circle,
    spending_tracker, get_bonus_data,
)
from app.service.auth import AuthInstance
from webui.deps import render, get_active_user_safe

router = APIRouter()


@router.get("/circle")
def circle_page(request: Request):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    try:
        group = get_group_data(AuthInstance.api_key, user["tokens"])
    except Exception as e:
        return render(request, "error.html", title="Gagal fetch", message=str(e))

    members = None
    spend = None
    bonus = None
    g_id = None
    parent_subs_id = None
    if isinstance(group, dict) and isinstance(group.get("data"), dict):
        g_id = group["data"].get("group_id")
        parent_subs_id = group["data"].get("parent_subs_id")
        if g_id:
            try:
                members = get_group_members(AuthInstance.api_key, user["tokens"], g_id)
            except Exception:
                pass
            if parent_subs_id:
                try:
                    spend = spending_tracker(AuthInstance.api_key, user["tokens"], parent_subs_id, g_id)
                    bonus = get_bonus_data(AuthInstance.api_key, user["tokens"], parent_subs_id, g_id)
                except Exception:
                    pass

    return render(request, "circle.html", group=group, members=members, spend=spend, bonus=bonus, group_id=g_id)


@router.post("/circle/invite")
def circle_invite(
    request: Request,
    msisdn: str = Form(...), name: str = Form(...),
    group_id: str = Form(...), member_id_parent: str = Form(...),
):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    res = invite_circle_member(AuthInstance.api_key, user["tokens"], msisdn, name, group_id, member_id_parent)
    return render(request, "circle_result.html", title="Invite Member", res=res)


@router.post("/circle/remove")
def circle_remove(
    request: Request,
    member_id: str = Form(...), group_id: str = Form(...),
    member_id_parent: str = Form(...), is_last_member: str = Form("false"),
):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    res = remove_circle_member(
        AuthInstance.api_key, user["tokens"],
        member_id, group_id, member_id_parent,
        is_last_member=str(is_last_member).lower() in ("true", "1", "yes", "on"),
    )
    return render(request, "circle_result.html", title="Remove Member", res=res)


@router.post("/circle/accept")
def circle_accept(request: Request, group_id: str = Form(...), member_id: str = Form(...)):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    res = accept_circle_invitation(AuthInstance.api_key, user["tokens"], group_id, member_id)
    return render(request, "circle_result.html", title="Accept Invitation", res=res)


@router.post("/circle/create")
def circle_create(
    request: Request,
    parent_name: str = Form(...), group_name: str = Form(...),
    member_msisdn: str = Form(...), member_name: str = Form(...),
):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")
    res = create_circle(AuthInstance.api_key, user["tokens"], parent_name, group_name, member_msisdn, member_name)
    return render(request, "circle_result.html", title="Create Circle", res=res)
