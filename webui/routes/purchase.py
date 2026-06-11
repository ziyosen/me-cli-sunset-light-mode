import base64
import io
import json
import time
from pathlib import Path

import qrcode
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import StreamingResponse

from app.client.engsel import get_package, get_family, get_package_details
from app.client.purchase.balance import settlement_balance
from app.client.purchase.qris import settlement_qris, get_qris_code
from app.client.purchase.ewallet import settlement_multipayment
from app.service.auth import AuthInstance
from app.service.decoy import DecoyInstance
from app.type_dict import PaymentItem
from webui.deps import render, get_active_user_safe
from webui.routes.decoy_settings import get_custom_decoy_data, list_custom_decoys

router = APIRouter()
PROJECT_DIR = Path(__file__).resolve().parents[2]


EWALLET_METHODS = {
    "ewallet_dana": "DANA",
    "ewallet_shopeepay": "SHOPEEPAY",
    "ewallet_gopay": "GOPAY",
    "ewallet_ovo": "OVO",
}


def _qris_data_uri(qris_code: str) -> str:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=8,
        border=2,
    )
    qr.add_data(qris_code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _build_item(pkg: dict) -> PaymentItem:
    opt = pkg["package_option"]
    return PaymentItem(
        item_code=opt["package_option_code"],
        product_type="",
        item_price=opt["price"],
        item_name=opt["name"],
        tax=0,
        token_confirmation=pkg["token_confirmation"],
    )


def _make_decoy_item_from_slot(api_key, tokens, slot_key: str):
    """Build decoy PaymentItem from an explicit decoy_data/decoy-{slot_key}.json file."""
    path = Path(f"decoy_data/decoy-{slot_key}.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        return None, (
            f"Decoy '{slot_key}' belum bisa dipakai — file tidak ada atau invalid: {e}. "
            f"Buka /settings/decoy untuk set up."
        )
    if not raw.get("family_code") or not raw.get("variant_code"):
        return None, (
            f"Decoy '{slot_key}' belum di-set (family_code/variant_code kosong). "
            f"Edit di /settings/decoy."
        )
    test = None
    try:
        for ie, mt in [
            (raw.get("is_enterprise", False), raw.get("migration_type", "NONE")),
            (None, None),
        ]:
            test = get_package_details(
                api_key, tokens,
                raw["family_code"], raw["variant_code"], raw.get("order", 1) or 1,
                ie, mt,
            )
            if test:
                break
    except Exception as e:
        return None, f"Decoy '{slot_key}': fetch error → {e}"
    if not test:
        return None, (
            f"Decoy '{slot_key}': server MyXL nolak family_code. "
            f"Ganti di /settings/decoy dengan family/variant yang valid."
        )
    opt = test["package_option"]
    return PaymentItem(
        item_code=opt["package_option_code"], product_type="",
        item_price=opt["price"], item_name=opt["name"], tax=0,
        token_confirmation=test["token_confirmation"],
    ), test


def _make_decoy_item(api_key, tokens, decoy_kind: str, *, slot_key: str | None = None):
    """Returns (PaymentItem, raw decoy package detail) or (None, errstr).
    Builtin variants (balance/qris/qris0) use DecoyInstance prefix logic unless slot_key is set.
    """
    if slot_key:
        return _make_decoy_item_from_slot(api_key, tokens, slot_key)

    decoy = DecoyInstance.get_decoy(decoy_kind)
    if not decoy or not decoy.get("option_code"):
        # Surface the actual problem: try fetching directly to get a meaningful error
        prefix = DecoyInstance.prefix or "default-"
        decoy_name = prefix + decoy_kind
        path = f"decoy_data/decoy-{decoy_name}.json"
        try:
            with open(path) as f:
                raw = json.load(f)
        except Exception as e:
            return None, (
                f"Decoy '{decoy_name}' belum bisa dipakai — file {path} tidak ada atau invalid: {e}. "
                f"Buka /settings/decoy untuk set up."
            )
        if not raw.get("family_code") or not raw.get("variant_code"):
            return None, (
                f"Decoy '{decoy_name}' belum di-set (file ada, tapi family_code/variant_code kosong). "
                f"Edit di /settings/decoy."
            )
        # Try fetch with stored values first, then auto-iterate all combos
        test = None
        try:
            for ie, mt in [
                (raw.get("is_enterprise", False), raw.get("migration_type", "NONE")),
                (None, None),
            ]:
                test = get_package_details(
                    api_key, tokens,
                    raw["family_code"], raw["variant_code"], raw.get("order", 1) or 1,
                    ie, mt,
                )
                if test:
                    break
        except Exception as e:
            return None, f"Decoy '{decoy_name}': fetch error → {e}"
        if not test:
            return None, (
                f"Decoy '{decoy_name}': server MyXL nolak family_code <code>{raw['family_code']}</code>. "
                f"UUID-nya mungkin expired atau nggak available untuk subscription lo. "
                f"Ganti di /settings/decoy dengan family_code/variant_code lain yang valid."
            )
        # If we got here, fetch worked — populate item ourselves
        opt = test["package_option"]
        return PaymentItem(
            item_code=opt["package_option_code"], product_type="",
            item_price=opt["price"], item_name=opt["name"], tax=0,
            token_confirmation=test["token_confirmation"],
        ), test
    pkg = get_package(api_key, tokens, decoy["option_code"])
    if not pkg:
        return None, "Gagal fetch detail paket decoy (option_code mungkin nggak available lagi)."
    opt = pkg["package_option"]
    item = PaymentItem(
        item_code=opt["package_option_code"],
        product_type="",
        item_price=opt["price"],
        item_name=opt["name"],
        tax=0,
        token_confirmation=pkg["token_confirmation"],
    )
    return item, pkg


def _make_custom_decoy_item(api_key, tokens, name: str):
    """Build a PaymentItem from a custom decoy file (decoy_data/custom-{name}.json).
    Returns (PaymentItem, base_method) or (None, errstr).
    """
    data = get_custom_decoy_data(name)
    if not data:
        return None, f"Custom decoy '{name}' tidak ditemukan."
    base = (data.get("base_method") or "balance").lower()
    if base not in ("balance", "qris"):
        base = "balance"
    if not data.get("family_code") or not data.get("variant_code"):
        return None, f"Custom decoy '{name}' belum diisi family_code / variant_code."
    # Retry: stored values first, then auto (None,None) to iterate all combos
    pkg_detail = None
    for ie, mt in [
        (data.get("is_enterprise", False), data.get("migration_type", "NONE")),
        (None, None),
    ]:
        pkg_detail = get_package_details(
            api_key, tokens,
            data["family_code"], data["variant_code"], data.get("order", 1) or 1,
            ie, mt,
        )
        if pkg_detail:
            break
    if not pkg_detail:
        return None, "Gagal fetch detail paket custom decoy (server tolak semua kombo)."
    opt = pkg_detail["package_option"]
    item = PaymentItem(
        item_code=opt["package_option_code"],
        product_type="",
        item_price=opt["price"],
        item_name=opt["name"],
        tax=0,
        token_confirmation=pkg_detail["token_confirmation"],
    )
    return item, base


def run_decoy_settlement(
    api_key: str,
    tokens: dict,
    pkg: dict,
    method: str,
    *,
    slot_key: str | None = None,
    qris_amount: int = -1,
) -> tuple[bool, str, str | None]:
    """Execute decoy purchase. Returns (success, user_message, qris_code_or_none)."""
    item = _build_item(pkg)
    payment_for = pkg.get("package_family", {}).get("payment_for", "BUY_PACKAGE")
    main_name = item["item_name"]

    if method in ("decoy_balance", "decoy_balance_v2"):
        decoy_item, err = _make_decoy_item(api_key, tokens, "balance", slot_key=slot_key)
        if decoy_item is None:
            return False, err or "Decoy gagal", None
        payment_items = [item, decoy_item]
        total = item["item_price"] + decoy_item["item_price"]
        is_v2 = method == "decoy_balance_v2"
        kwargs = dict(
            api_key=api_key, tokens=tokens, items=payment_items,
            payment_for=("🤫" if is_v2 else payment_for),
            ask_overwrite=False, overwrite_amount=total,
            token_confirmation_idx=(1 if is_v2 else 0),
        )
        res = settlement_balance(**kwargs)
        if isinstance(res, dict) and res.get("status") != "SUCCESS":
            err_msg = str(res.get("message", ""))
            if "Bizz-err.Amount.Total" in err_msg and "=" in err_msg:
                try:
                    valid_amount = int(err_msg.split("=")[1].strip())
                    kwargs["overwrite_amount"] = valid_amount
                    if is_v2:
                        kwargs["token_confirmation_idx"] = -1
                    res = settlement_balance(**kwargs)
                except (ValueError, IndexError):
                    pass
        if isinstance(res, dict) and res.get("status") == "SUCCESS":
            title = "Pulsa + Decoy V2" if is_v2 else "Pulsa + Decoy"
            return True, f"Berhasil: {title} · {main_name}", None
        err = res.get("message", "") if isinstance(res, dict) else str(res)
        return False, err or "Pembelian decoy gagal", None

    if method in ("decoy_qris", "decoy_qris0"):
        decoy_kind = "qris0" if method == "decoy_qris0" else "qris"
        decoy_item, err = _make_decoy_item(api_key, tokens, decoy_kind, slot_key=slot_key)
        if decoy_item is None:
            return False, err or "Decoy gagal", None
        payment_items = [item, decoy_item]
        if qris_amount < 0:
            qris_amount = item["item_price"] + decoy_item["item_price"]
        tx = settlement_qris(
            api_key, tokens, payment_items,
            payment_for="SHARE_PACKAGE", ask_overwrite=False,
            overwrite_amount=qris_amount, token_confirmation_idx=1,
        )
        if not tx or not isinstance(tx, str):
            err = tx.get("message", "QRIS decoy gagal") if isinstance(tx, dict) else "QRIS decoy gagal"
            return False, str(err), None
        qris_code = get_qris_code(api_key, tokens, tx)
        if not qris_code:
            return True, f"QRIS decoy dibuat (tx {tx[:16]}…) tapi kode QR tidak ditemukan.", None
        return True, f"QRIS + Decoy ({decoy_kind}) · {main_name}", qris_code

    if method.startswith("decoy_custom_"):
        name = method[len("decoy_custom_"):]
        decoy_item, base_or_err = _make_custom_decoy_item(api_key, tokens, name)
        if decoy_item is None:
            return False, base_or_err or "Custom decoy gagal", None
        base = base_or_err
        payment_items = [item, decoy_item]
        total = item["item_price"] + decoy_item["item_price"]
        if base == "balance":
            res = settlement_balance(
                api_key, tokens, payment_items,
                payment_for=payment_for, ask_overwrite=False,
                overwrite_amount=total, token_confirmation_idx=0,
            )
            if isinstance(res, dict) and res.get("status") != "SUCCESS":
                err_msg = str(res.get("message", ""))
                if "Bizz-err.Amount.Total" in err_msg and "=" in err_msg:
                    try:
                        valid_amount = int(err_msg.split("=")[1].strip())
                        res = settlement_balance(
                            api_key, tokens, payment_items,
                            payment_for=payment_for, ask_overwrite=False,
                            overwrite_amount=valid_amount, token_confirmation_idx=0,
                        )
                    except (ValueError, IndexError):
                        pass
            if isinstance(res, dict) and res.get("status") == "SUCCESS":
                return True, f"Berhasil: Pulsa + Decoy ({name}) · {main_name}", None
            err = res.get("message", "") if isinstance(res, dict) else str(res)
            return False, err or "Pembelian gagal", None
        if qris_amount < 0:
            qris_amount = total
        tx = settlement_qris(
            api_key, tokens, payment_items,
            payment_for="SHARE_PACKAGE", ask_overwrite=False,
            overwrite_amount=qris_amount, token_confirmation_idx=1,
        )
        if not tx or not isinstance(tx, str):
            err = tx.get("message", "QRIS custom decoy gagal") if isinstance(tx, dict) else "QRIS custom decoy gagal"
            return False, str(err), None
        qris_code = get_qris_code(api_key, tokens, tx)
        if not qris_code:
            return True, f"QRIS decoy ({name}) dibuat tanpa kode QR.", None
        return True, f"QRIS + Decoy ({name}) · {main_name}", qris_code

    return False, f"Metode decoy '{method}' tidak dikenal.", None


@router.post("/purchase/{option_code}")
def buy_one(
    request: Request,
    option_code: str,
    method: str = Form(...),
    payment_for: str = Form("BUY_PACKAGE"),
    wallet_number: str = Form(""),
    qris_amount: int = Form(-1),
):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")

    try:
        pkg = get_package(AuthInstance.api_key, user["tokens"], option_code)
    except Exception as e:
        return render(request, "error.html", title="Gagal fetch paket", message=str(e))
    if not pkg:
        return render(request, "error.html", title="Tidak ditemukan", message=f"Option {option_code} tidak ada.")

    item = _build_item(pkg)
    items = [item]

    try:
        if method == "balance":
            res = settlement_balance(
                AuthInstance.api_key, user["tokens"], items,
                payment_for=payment_for, ask_overwrite=False,
                overwrite_amount=item["item_price"],
            )
            return render(request, "purchase_result.html", title="Pembelian Pulsa", result=res, qris_img=None)

        elif method == "qris":
            tx = settlement_qris(
                AuthInstance.api_key, user["tokens"], items,
                payment_for=payment_for, ask_overwrite=False,
                overwrite_amount=item["item_price"],
            )
            if not tx or not isinstance(tx, str):
                return render(request, "purchase_result.html", title="QRIS gagal", result=tx, qris_img=None)
            qris_code = get_qris_code(AuthInstance.api_key, user["tokens"], tx)
            qris_img = _qris_data_uri(qris_code) if qris_code else None
            return render(
                request, "purchase_result.html",
                title="Bayar via QRIS", result={"transaction_id": tx, "qr_code": qris_code},
                qris_img=qris_img,
            )

        elif method in EWALLET_METHODS:
            pm = EWALLET_METHODS[method]
            if pm == "DANA" and (not wallet_number.startswith("08") or not wallet_number.isdigit() or not (10 <= len(wallet_number) <= 13)):
                return render(request, "error.html", title="Nomor DANA invalid", message="Format harus 08xxxxxxxxx")
            res = settlement_multipayment(
                AuthInstance.api_key, user["tokens"], items,
                wallet_number=wallet_number, payment_method=pm,
                payment_for=payment_for, ask_overwrite=False,
                overwrite_amount=item["item_price"],
            )
            return render(request, "purchase_result.html", title=f"Bayar via {pm}", result=res, qris_img=None)

        elif method in ("decoy_balance", "decoy_balance_v2"):
            decoy_item, err = _make_decoy_item(AuthInstance.api_key, user["tokens"], "balance")
            if decoy_item is None:
                return render(request, "error.html", title="Decoy gagal", message=err)
            payment_items = [item, decoy_item]
            total = item["item_price"] + decoy_item["item_price"]
            is_v2 = method == "decoy_balance_v2"
            kwargs = dict(
                api_key=AuthInstance.api_key, tokens=user["tokens"],
                items=payment_items,
                payment_for=("🤫" if is_v2 else payment_for),
                ask_overwrite=False,
                overwrite_amount=total,
                token_confirmation_idx=(1 if is_v2 else 0),
            )
            res = settlement_balance(**kwargs)
            # Auto-adjust if server returns Bizz-err.Amount.Total
            if isinstance(res, dict) and res.get("status") != "SUCCESS":
                err_msg = str(res.get("message", ""))
                if "Bizz-err.Amount.Total" in err_msg and "=" in err_msg:
                    try:
                        valid_amount = int(err_msg.split("=")[1].strip())
                        kwargs["overwrite_amount"] = valid_amount
                        if is_v2:
                            kwargs["token_confirmation_idx"] = -1
                        res = settlement_balance(**kwargs)
                    except (ValueError, IndexError):
                        pass
            return render(
                request, "purchase_result.html",
                title=("Pulsa + Decoy V2" if is_v2 else "Pulsa + Decoy"),
                result=res, qris_img=None,
            )

        elif method in ("decoy_qris", "decoy_qris0"):
            decoy_kind = "qris0" if method == "decoy_qris0" else "qris"
            decoy_item, err = _make_decoy_item(AuthInstance.api_key, user["tokens"], decoy_kind)
            if decoy_item is None:
                return render(request, "error.html", title="Decoy gagal", message=err)
            payment_items = [item, decoy_item]
            # User-overridable amount (trial-error). Default: sum.
            if qris_amount < 0:
                qris_amount = item["item_price"] + decoy_item["item_price"]
            tx = settlement_qris(
                AuthInstance.api_key, user["tokens"], payment_items,
                payment_for="SHARE_PACKAGE", ask_overwrite=False,
                overwrite_amount=qris_amount, token_confirmation_idx=1,
            )
            if not tx or not isinstance(tx, str):
                return render(request, "purchase_result.html",
                              title=f"QRIS + Decoy ({decoy_kind}) gagal", result=tx, qris_img=None)
            qris_code = get_qris_code(AuthInstance.api_key, user["tokens"], tx)
            qris_img = _qris_data_uri(qris_code) if qris_code else None
            return render(
                request, "purchase_result.html",
                title=f"QRIS + Decoy ({decoy_kind})",
                result={"transaction_id": tx, "qr_code": qris_code,
                        "amount_used": qris_amount,
                        "main_price": item["item_price"],
                        "decoy_price": decoy_item["item_price"]},
                qris_img=qris_img,
            )

        elif method.startswith("decoy_custom_"):
            name = method[len("decoy_custom_"):]
            decoy_item, base_or_err = _make_custom_decoy_item(AuthInstance.api_key, user["tokens"], name)
            if decoy_item is None:
                return render(request, "error.html", title="Custom decoy gagal", message=base_or_err)
            base = base_or_err  # "balance" or "qris"
            payment_items = [item, decoy_item]
            total = item["item_price"] + decoy_item["item_price"]
            if base == "balance":
                res = settlement_balance(
                    AuthInstance.api_key, user["tokens"], payment_items,
                    payment_for=payment_for, ask_overwrite=False,
                    overwrite_amount=total, token_confirmation_idx=0,
                )
                # Try amount adjustment if needed
                if isinstance(res, dict) and res.get("status") != "SUCCESS":
                    err_msg = str(res.get("message", ""))
                    if "Bizz-err.Amount.Total" in err_msg and "=" in err_msg:
                        try:
                            valid_amount = int(err_msg.split("=")[1].strip())
                            res = settlement_balance(
                                AuthInstance.api_key, user["tokens"], payment_items,
                                payment_for=payment_for, ask_overwrite=False,
                                overwrite_amount=valid_amount, token_confirmation_idx=0,
                            )
                        except (ValueError, IndexError):
                            pass
                return render(request, "purchase_result.html",
                              title=f"Pulsa + Decoy ({name})", result=res, qris_img=None)
            else:  # qris
                if qris_amount < 0:
                    qris_amount = total
                tx = settlement_qris(
                    AuthInstance.api_key, user["tokens"], payment_items,
                    payment_for="SHARE_PACKAGE", ask_overwrite=False,
                    overwrite_amount=qris_amount, token_confirmation_idx=1,
                )
                if not tx or not isinstance(tx, str):
                    return render(request, "purchase_result.html",
                                  title=f"QRIS + Decoy ({name}) gagal", result=tx, qris_img=None)
                qris_code = get_qris_code(AuthInstance.api_key, user["tokens"], tx)
                qris_img = _qris_data_uri(qris_code) if qris_code else None
                return render(
                    request, "purchase_result.html",
                    title=f"QRIS + Decoy ({name})",
                    result={"transaction_id": tx, "qr_code": qris_code,
                            "amount_used": qris_amount,
                            "main_price": item["item_price"],
                            "decoy_price": decoy_item["item_price"]},
                    qris_img=qris_img,
                )

        else:
            return render(request, "error.html", title="Metode invalid", message=f"Method '{method}' tidak dikenal.")
    except Exception as e:
        return render(request, "error.html", title="Pembelian gagal", message=str(e))


@router.post("/purchase/hot2")
def buy_hot2(request: Request, hot2_idx: int = Form(...), method: str = Form(...), wallet_number: str = Form("")):
    user = get_active_user_safe()
    if not user:
        return render(request, "error.html", title="Login dulu", message="Belum ada akun aktif.")

    try:
        with open(PROJECT_DIR / "hot_data" / "hot2.json", "r", encoding="utf-8") as f:
            hot_packages = json.load(f)
        if not (0 <= hot2_idx < len(hot_packages)):
            return render(request, "error.html", title="Invalid", message="Index hot2 invalid.")
        selected = hot_packages[hot2_idx]
        sub_packages = selected.get("packages", [])
        items: list[PaymentItem] = []
        for p in sub_packages:
            pkg_detail = get_package_details(
                AuthInstance.api_key, user["tokens"],
                p["family_code"], p["variant_code"], p["order"],
                p.get("is_enterprise", False), p.get("migration_type", "NONE"),
            )
            if not pkg_detail:
                return render(request, "error.html", title="Detail gagal", message=f"family {p['family_code']} gagal fetch")
            items.append(PaymentItem(
                item_code=pkg_detail["package_option"]["package_option_code"],
                product_type="",
                item_price=pkg_detail["package_option"]["price"],
                item_name=pkg_detail["package_option"]["name"],
                tax=0,
                token_confirmation=pkg_detail["token_confirmation"],
            ))
        payment_for = selected.get("payment_for", "BUY_PACKAGE")
        overwrite = selected.get("overwrite_amount", -1)
        token_idx = selected.get("token_confirmation_idx", 0)
        amount_idx = selected.get("amount_idx", -1)
        if overwrite == -1:
            overwrite = items[amount_idx]["item_price"] if amount_idx != -1 else items[-1]["item_price"]

        if method == "balance":
            res = settlement_balance(AuthInstance.api_key, user["tokens"], items, payment_for, False, overwrite, token_idx, amount_idx)
            return render(request, "purchase_result.html", title=selected["name"], result=res, qris_img=None)
        elif method == "qris":
            tx = settlement_qris(AuthInstance.api_key, user["tokens"], items, payment_for, False, overwrite, token_idx, amount_idx)
            if not tx or not isinstance(tx, str):
                return render(request, "purchase_result.html", title="QRIS gagal", result=tx, qris_img=None)
            qris_code = get_qris_code(AuthInstance.api_key, user["tokens"], tx)
            return render(request, "purchase_result.html", title=selected["name"],
                          result={"transaction_id": tx, "qr_code": qris_code},
                          qris_img=_qris_data_uri(qris_code) if qris_code else None)
        elif method in EWALLET_METHODS:
            pm = EWALLET_METHODS[method]
            res = settlement_multipayment(AuthInstance.api_key, user["tokens"], items, wallet_number, pm, payment_for, False, overwrite, token_idx, amount_idx)
            return render(request, "purchase_result.html", title=selected["name"], result=res, qris_img=None)
        else:
            return render(request, "error.html", title="Metode invalid", message=method)
    except Exception as e:
        return render(request, "error.html", title="Hot2 gagal", message=str(e))


@router.get("/purchase/family-loop")
def family_loop_form(request: Request, family_code: str | None = Query(None)):
    return render(request, "family_loop.html", family_code=family_code or "")


@router.post("/purchase/family-loop/start")
def family_loop_start(
    request: Request,
    family_code: str = Form(...),
    start_from: int = Form(1),
    delay_seconds: int = Form(0),
    use_decoy: bool = Form(False),
):
    return render(
        request, "family_loop_stream.html",
        family_code=family_code, start_from=start_from,
        delay_seconds=delay_seconds, use_decoy=use_decoy,
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _extract_err(res) -> str:
    """Extract a human-readable error message from API response."""
    if not isinstance(res, dict):
        return str(res)[:200] if res else "Tidak ada response"
    parts = []
    if res.get("status"):
        parts.append(f"status={res['status']}")
    msg = res.get("message") or (res.get("data") or {}).get("message") or res.get("error")
    if msg:
        parts.append(str(msg))
    code = res.get("code") or (res.get("data") or {}).get("code")
    if code:
        parts.append(f"code={code}")
    return " · ".join(parts) if parts else (str(res)[:200])


@router.get("/purchase/family-loop/stream")
def family_loop_stream(
    family_code: str,
    start_from: int = 1,
    delay_seconds: int = 0,
    use_decoy: bool = False,
):
    user = get_active_user_safe()

    def gen():
        if not user:
            yield _sse("error", {"step": "auth", "msg": "Login dulu — belum ada akun aktif."})
            return

        # Phase 1: fetch family
        yield _sse("phase", {"phase": "fetch_family",
                              "msg": f"📡 Mengambil daftar paket untuk family {family_code}…"})
        try:
            family = get_family(AuthInstance.api_key, user["tokens"], family_code)
        except Exception as e:
            yield _sse("error", {"step": "fetch_family", "msg": f"❌ Gagal fetch family: {e}"})
            return

        if not family:
            yield _sse("error", {"step": "fetch_family",
                                 "msg": f"❌ Family code {family_code} tidak ditemukan / tidak valid untuk subscription lo."})
            return

        family_name = family.get("package_family", {}).get("name", family_code)
        variants = family.get("package_variants", []) or []
        total_opts = sum(len(v.get("package_options", []) or []) for v in variants)

        if total_opts == 0:
            yield _sse("error", {"step": "fetch_family",
                                 "msg": f"❌ Family '{family_name}' tidak punya opsi paket apa pun."})
            return

        yield _sse("info", {
            "msg": f"✅ Family '{family_name}': {len(variants)} variant, {total_opts} opsi total.",
            "total": total_opts,
            "start_from": start_from,
            "delay": delay_seconds,
        })
        if start_from > 1:
            yield _sse("info", {"msg": f"⏭️  Skip {start_from - 1} opsi pertama (mulai dari opsi #{start_from})."})

        seq = 0
        ok_count = 0
        fail_count = 0
        err_count = 0
        payment_for = family.get("package_family", {}).get("payment_for", "BUY_PACKAGE")

        for variant in variants:
            variant_name = variant.get("name", "?")
            variant_code = variant.get("package_variant_code", "")
            for opt in variant.get("package_options", []) or []:
                seq += 1
                if seq < start_from:
                    continue

                option_name = opt.get("name", "?")
                price = opt.get("price", 0)
                code = opt.get("package_option_code", "")

                # Step: starting this option
                yield _sse("progress", {
                    "seq": seq, "total": total_opts,
                    "variant": variant_name, "option": option_name,
                    "price": price, "code": code,
                    "step": "start",
                    "msg": f"▶ [#{seq}/{total_opts}] {variant_name} · {option_name} — Rp {price:,}".replace(",", "."),
                })

                # Step 1: fetch package detail
                yield _sse("progress", {
                    "seq": seq, "step": "fetch_detail",
                    "msg": "   ↳ 📦 Fetch package detail…",
                })
                try:
                    pkg = get_package(AuthInstance.api_key, user["tokens"], code, family_code, variant_code)
                except Exception as e:
                    err_count += 1
                    yield _sse("fail", {
                        "seq": seq, "step": "fetch_detail",
                        "msg": f"   ↳ ❌ Gagal fetch detail: {e}",
                        "summary": {"ok": ok_count, "fail": fail_count, "err": err_count},
                    })
                    if delay_seconds > 0: time.sleep(delay_seconds)
                    continue

                if not pkg:
                    err_count += 1
                    yield _sse("fail", {
                        "seq": seq, "step": "fetch_detail",
                        "msg": "   ↳ ❌ Detail paket kosong (mungkin opsi tidak tersedia).",
                        "summary": {"ok": ok_count, "fail": fail_count, "err": err_count},
                    })
                    if delay_seconds > 0: time.sleep(delay_seconds)
                    continue

                item = _build_item(pkg)

                # Step 2: submit settlement
                yield _sse("progress", {
                    "seq": seq, "step": "submit",
                    "msg": f"   ↳ 💸 Submit pembayaran via Pulsa (Rp {item['item_price']:,})…".replace(",", "."),
                })
                try:
                    res = settlement_balance(
                        AuthInstance.api_key, user["tokens"], [item],
                        payment_for=payment_for, ask_overwrite=False,
                        overwrite_amount=item["item_price"],
                    )
                except Exception as e:
                    err_count += 1
                    yield _sse("fail", {
                        "seq": seq, "step": "submit",
                        "msg": f"   ↳ ❌ Exception saat submit: {e}",
                        "summary": {"ok": ok_count, "fail": fail_count, "err": err_count},
                    })
                    if delay_seconds > 0: time.sleep(delay_seconds)
                    continue

                ok = isinstance(res, dict) and res.get("status") == "SUCCESS"
                if ok:
                    ok_count += 1
                    yield _sse("success", {
                        "seq": seq, "step": "done",
                        "msg": f"   ↳ ✅ BERHASIL beli {option_name}",
                        "summary": {"ok": ok_count, "fail": fail_count, "err": err_count},
                    })
                else:
                    fail_count += 1
                    detail = _extract_err(res)
                    hint = ""
                    if "Bizz-err.Amount.Total" in detail:
                        hint = "  💡 Amount tidak match. Server biasanya kasih amount yang benar di pesan error."
                    elif "balance" in detail.lower() or "insufficient" in detail.lower():
                        hint = "  💡 Saldo pulsa nggak cukup."
                    elif "already" in detail.lower() or "duplicate" in detail.lower():
                        hint = "  💡 Paket mungkin sudah aktif/baru saja dibeli."
                    yield _sse("fail", {
                        "seq": seq, "step": "done",
                        "msg": f"   ↳ ⚠️ Ditolak server: {detail}{hint}",
                        "summary": {"ok": ok_count, "fail": fail_count, "err": err_count},
                    })

                if delay_seconds > 0 and seq < total_opts:
                    yield _sse("progress", {
                        "seq": seq, "step": "wait",
                        "msg": f"   ↳ ⏳ Tunggu {delay_seconds}s sebelum opsi berikutnya…",
                    })
                    time.sleep(delay_seconds)

        yield _sse("done", {
            "msg": f"🏁 Selesai — {ok_count} sukses, {fail_count} ditolak, {err_count} error dari {total_opts} opsi.",
            "summary": {"ok": ok_count, "fail": fail_count, "err": err_count, "total": total_opts},
        })

    return StreamingResponse(gen(), media_type="text/event-stream")
