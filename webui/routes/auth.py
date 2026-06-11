import time
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

from app.client.ciam import get_otp, submit_otp
from app.service.auth import AuthInstance
from webui.deps import render

router = APIRouter()

# In-memory subscriber_id store keyed by phone (TTL 5 min)
_otp_state: dict[str, tuple[str, float]] = {}
_OTP_TTL = 300


def _cleanup_otp_state():
    now = time.time()
    expired = [k for k, (_, ts) in _otp_state.items() if now - ts > _OTP_TTL]
    for k in expired:
        _otp_state.pop(k, None)


@router.get("/login")
def login_page(request: Request, error: str | None = None, phone: str | None = None,
               tab: str | None = None, info: str | None = None):
    pending = phone in _otp_state if phone else False
    AuthInstance.load_tokens()
    saved = AuthInstance.refresh_tokens
    active = None
    try:
        active = AuthInstance.active_user
    except Exception:
        pass
    return render(request, "login.html",
                  error=error, info=info, phone=phone, pending_otp=pending,
                  saved_accounts=saved, current_active=active,
                  tab=(tab or ("login" if not pending else "login")))


@router.post("/login/request-otp")
def login_request_otp(request: Request, phone: str = Form(...)):
    phone = phone.strip()
    if not phone.startswith("628") or len(phone) < 10 or len(phone) > 14:
        return render(
            request, "login.html",
            error="Nomor tidak valid. Pastikan diawali 628 dan panjang 10-14 digit.",
            phone=phone, pending_otp=False,
        )

    try:
        subscriber_id = get_otp(phone)
    except Exception as e:
        return render(request, "login.html", error=f"Gagal kirim OTP: {e}", phone=phone, pending_otp=False)

    if not subscriber_id:
        return render(request, "login.html", error="Gagal kirim OTP. Cek nomor & coba lagi.", phone=phone, pending_otp=False)

    _cleanup_otp_state()
    _otp_state[phone] = (subscriber_id, time.time())
    return render(request, "login.html", error=None, phone=phone, pending_otp=True, info="OTP terkirim via SMS.")


@router.post("/login/submit-otp")
def login_submit_otp(request: Request, phone: str = Form(...), otp: str = Form(...)):
    phone = phone.strip()
    otp = otp.strip()
    _cleanup_otp_state()
    if phone not in _otp_state:
        return render(request, "login.html", error="Sesi OTP expired/tidak ditemukan. Kirim ulang.", phone=phone, pending_otp=False)

    if not otp.isdigit() or len(otp) != 6:
        return render(request, "login.html", error="OTP harus 6 digit angka.", phone=phone, pending_otp=True)

    try:
        tokens = submit_otp(AuthInstance.api_key, "SMS", phone, otp)
    except Exception as e:
        return render(request, "login.html", error=f"Submit OTP error: {e}", phone=phone, pending_otp=True)

    if not tokens or "refresh_token" not in tokens:
        return render(request, "login.html", error="OTP salah atau gagal login.", phone=phone, pending_otp=True)

    try:
        AuthInstance.add_refresh_token(int(phone), tokens["refresh_token"])
    except Exception as e:
        return render(request, "login.html", error=f"Simpan akun gagal: {e}", phone=phone, pending_otp=True)

    _otp_state.pop(phone, None)
    return RedirectResponse(url="/", status_code=303)


@router.get("/accounts")
def accounts_page(request: Request):
    return render(request, "accounts.html")


@router.post("/accounts/activate")
def accounts_activate(request: Request, number: int = Form(...)):
    AuthInstance.set_active_user(int(number))
    return RedirectResponse(url="/", status_code=303)


@router.post("/accounts/remove")
def accounts_remove(request: Request, number: int = Form(...)):
    AuthInstance.remove_refresh_token(int(number))
    return RedirectResponse(url="/accounts", status_code=303)


@router.post("/logout")
def logout(request: Request):
    # No session to clear (BasicAuth at HTTP level)
    return RedirectResponse(url="/", status_code=303)
