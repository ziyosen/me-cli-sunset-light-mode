from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

from app.service.bookmark import BookmarkInstance
from webui.deps import render

router = APIRouter()


@router.get("/bookmark")
def bookmark_page(request: Request):
    BookmarkInstance.load_bookmark()
    return render(request, "bookmark.html", bookmarks=BookmarkInstance.get_bookmarks())


@router.post("/bookmark/add")
def bookmark_add(
    request: Request,
    family_code: str = Form(...),
    family_name: str = Form(""),
    is_enterprise: str = Form("False"),
    variant_name: str = Form(""),
    option_name: str = Form(""),
    order: int = Form(0),
    package_option_code: str = Form(""),
):
    is_ent = str(is_enterprise).lower() in ("true", "1", "yes", "on")
    BookmarkInstance.add_bookmark(
        family_code, family_name, is_ent, variant_name, option_name, order,
        package_option_code=package_option_code,
    )
    return RedirectResponse(url="/bookmark", status_code=303)


@router.post("/bookmark/remove")
def bookmark_remove(
    request: Request,
    family_code: str = Form(...),
    is_enterprise: str = Form("False"),
    variant_name: str = Form(""),
    order: int = Form(0),
):
    is_ent = str(is_enterprise).lower() in ("true", "1", "yes", "on")
    BookmarkInstance.remove_bookmark(family_code, is_ent, variant_name, order)
    return RedirectResponse(url="/bookmark", status_code=303)
