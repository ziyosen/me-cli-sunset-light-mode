from fastapi import APIRouter, Request, Form

from app.client.registration import dukcapil, validate_puk
from app.service.auth import AuthInstance
from webui.deps import render

router = APIRouter()


@router.get("/register")
def register_form(request: Request):
    return render(request, "register.html", res=None)


@router.post("/register")
def register_post(
    request: Request,
    msisdn: str = Form(...),
    nik: str = Form(...),
    kk: str = Form(...),
):
    try:
        res = dukcapil(AuthInstance.api_key, msisdn, kk, nik)
    except Exception as e:
        return render(request, "error.html", title="Register error", message=str(e))
    return render(request, "register.html", res=res, msisdn=msisdn)


@router.get("/register/puk")
def puk_form(request: Request):
    return render(request, "register_puk.html", res=None)


@router.post("/register/puk")
def puk_post(request: Request, msisdn: str = Form(...), puk: str = Form(...)):
    try:
        res = validate_puk(AuthInstance.api_key, msisdn, puk)
    except Exception as e:
        return render(request, "error.html", title="PUK error", message=str(e))
    return render(request, "register_puk.html", res=res, msisdn=msisdn)
