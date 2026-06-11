"""Telegram Bot — long-polling in a background thread.

Commands:
  /start, /help — info & command list
  /link <username> <password> — link Telegram chat to webui user
  /unlink — remove link
  /nomor — set active MyXL number (used by all menus)
  /kuota — info pelanggan + kuota/paket aktif (nomor aktif)
  /saldo, /paket — alias ke /kuota
  /beli <option_code> — purchase package via balance
  /unsub <quota_code> — unsubscribe a package
  /history — last 10 transactions
"""
import json
import time
import threading
import traceback
from datetime import datetime
from html import escape as html_escape
from pathlib import Path

import requests

from webui.users import (
    authenticate, get_user_by_telegram, link_telegram, unlink_telegram,
    load_users, user_dir,
)
from webui.cwd_lock import get_user_tokens, get_all_user_tokens, get_api_key, user_cwd
from webui.helpers import format_benefit_quota_pair

API_BASE = "https://api.telegram.org/bot{token}"
PROJECT_DIR = Path(__file__).resolve().parents[1]
TG_MSG_MAX = 4096
CALLBACK_DATA_MAX = 64


def _esc(text) -> str:
    return html_escape(str(text or ""), quote=False)


def _tg_err(exc: Exception) -> str:
    """User-facing Telegram error (no stack traces)."""
    if isinstance(exc, (ValueError, KeyError)):
        return f"⚠️ {_esc(exc)}"
    return "⚠️ Terjadi kesalahan. Silakan coba lagi."


def _format_bytes(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.2f} {units[i]}"


def _format_ts_short(ts) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%d %b %Y")
    except (TypeError, ValueError, OSError):
        return "-"


def _format_date_dmY(ts) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%d-%m-%Y")
    except (TypeError, ValueError, OSError):
        return "-"


def _format_date_iso(ts) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return "-"


def _card_age_from_dob(dob_str: str) -> str:
    if not dob_str:
        return "-"
    try:
        start = datetime.strptime(dob_str.strip(), "%d/%m/%Y")
        now = datetime.now()
        months = (now.year - start.year) * 12 + (now.month - start.month)
        if now.day < start.day:
            months -= 1
        if months < 0:
            months = 0
        return f"{months // 12} Year {months % 12} Month"
    except (TypeError, ValueError):
        return "-"


def _format_paket_package_block(q: dict) -> list[str]:
    lines = [
        f"📦Nama Paket : {_esc(q.get('name', '-'))}",
        f"📅Expired : {_format_date_dmY(q.get('expired_at'))}",
        "===========================",
    ]
    for b in q.get("benefits") or []:
        tot_s, rem_s = format_benefit_quota_pair(b)
        lines.append(f"⭐️Benefit : {_esc(b.get('name', '-'))}")
        lines.append(f"💙Quota : {tot_s}")
        lines.append(f"✅Sisa Quota : {rem_s}")
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _build_kuota_saldo_message(msisdn: int, acc: dict, api_key: str, tokens: dict) -> list[str]:
    """Info Pelanggan + pulsa/poin + Info Paket Aktif (gaya myXL)."""
    from app.client.engsel import get_profile, send_api_request, get_tiering_info
    from app.client.famplan import validate_msisdn

    lines = ["<b>Info Pelanggan</b>"]

    profile_data = get_profile(api_key, tokens["access_token"], tokens["id_token"]) or {}
    prof = profile_data.get("profile") or {}

    dob = prof.get("dob", "")
    lines.append(f"Umur Kartu : {_card_age_from_dob(dob)}")

    bal_wrap = send_api_request(
        api_key, "api/v8/packages/balance-and-credit",
        {"is_enterprise": False, "lang": "en"},
        tokens["id_token"], "POST",
    )
    bal_data = (bal_wrap or {}).get("data") or {} if isinstance(bal_wrap, dict) else {}
    balance = bal_data.get("balance") or {}

    grace_end = bal_data.get("grace_end_date")
    if grace_end:
        lines.append(f"Aktif Hingga : {_format_date_iso(grace_end)}")
    else:
        lines.append(f"Aktif Hingga : {_format_date_iso(balance.get('expired_at'))}")

    sub_status = bal_data.get("subscription_status") or bal_data.get("suspended_status") or "ACTIVE"
    lines.append(f"Status Simcard : {_esc(sub_status)}")

    try:
        chk = validate_msisdn(api_key, tokens, str(msisdn))
        registered = (chk or {}).get("data", {}).get("is_registered")
        lines.append(
            f"Status Dukcapil : {'Registered' if registered else 'Unregistered' if registered is False else '-'}"
        )
    except Exception:
        lines.append("Status Dukcapil : -")

    lines.append(f"Masa Aktif Kartu : {_format_date_dmY(balance.get('expired_at'))}")

    rem = balance.get("remaining")
    if rem is not None:
        bal_str = f"Rp {rem:,.0f}".replace(",", ".")
        lines.append(f"Pulsa : {bal_str}")

    if acc.get("subscription_type") == "PREPAID":
        try:
            tier = get_tiering_info(api_key, tokens)
            if tier:
                lines.append(
                    f"Points : {tier.get('current_point', 0)} · "
                    f"Tier : {tier.get('tier', 0)}"
                )
        except Exception:
            pass

    lines.append("")
    lines.append("<b>Info Paket Aktif</b>")

    res = send_api_request(
        api_key, "api/v8/packages/quota-details",
        {"is_enterprise": False, "lang": "en", "family_member_id": ""},
        tokens["id_token"], "POST",
    )
    if not isinstance(res, dict) or res.get("status") != "SUCCESS":
        lines.append("Gagal mengambil daftar paket.")
        return lines

    quotas = (res.get("data") or {}).get("quotas") or []
    if not quotas:
        lines.append("(tidak ada paket aktif)")
        return lines

    for i, q in enumerate(quotas):
        if i > 0:
            lines.append("")
        lines.extend(_format_paket_package_block(q))

    return lines


def _kb_back_menu(extra_rows: list | None = None) -> dict:
    """Inline keyboard with optional extra row(s) + « Menu utama."""
    rows = list(extra_rows or [])
    rows.append([{"text": "« Menu utama", "callback_data": "menu:home"}])
    return {"inline_keyboard": rows}


def _kb_back_only() -> dict:
    return _kb_back_menu()


def _chunk_lines_for_telegram(lines: list[str], max_len: int = 3900) -> list[str]:
    """Split line list into message chunks under Telegram limit."""
    chunks: list[str] = []
    current: list[str] = []
    cur_len = 0
    for line in lines:
        add = len(line) + 1
        if current and cur_len + add > max_len:
            chunks.append("\n".join(current))
            current = []
            cur_len = 0
        current.append(line)
        cur_len += add
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.base = API_BASE.format(token=token)
        self._thread = None
        self._stop = threading.Event()
        self._offset = 0
        self._pending_confirm: dict[int, dict] = {}
        # Conversation state per chat: {"step": str, "data": dict, "account_msisdn": int|None}
        self._states: dict[int, dict] = {}

    def _reply(self, chat_id: int, msg_id: int | None, text: str, reply_markup=None):
        if msg_id:
            self._edit_message(chat_id, msg_id, text, reply_markup=reply_markup)
        else:
            self.send(chat_id, text, reply_markup=reply_markup)

    def _send_chunks(
        self,
        chat_id: int,
        msg_id: int | None,
        chunks: list[str],
        reply_markup=None,
        *,
        back_menu: bool = True,
    ):
        if not chunks:
            return
        back_kb = _kb_back_only() if back_menu else None
        if len(chunks) == 1:
            kb = reply_markup or back_kb
            self._reply(chat_id, msg_id, chunks[0], reply_markup=kb)
            return
        self._reply(chat_id, msg_id, chunks[0], reply_markup=reply_markup)
        for part in chunks[1:-1]:
            self.send(chat_id, part)
        self.send(chat_id, chunks[-1], reply_markup=back_kb)

    def _linked_username(self, chat_id: int) -> str | None:
        user = get_user_by_telegram(chat_id)
        return user["username"] if user else None

    def _resolve_pkg_code(self, state: dict, raw: str) -> str:
        return (state.get("pkg_map") or {}).get(raw, raw)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="tg-bot")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def send(self, chat_id: int, text: str, parse_mode: str = "HTML", reply_markup=None) -> bool:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        try:
            r = requests.post(f"{self.base}/sendMessage", json=payload, timeout=15)
            return r.status_code == 200
        except Exception:
            return False

    def _poll_loop(self):
        backoff = 1
        while not self._stop.is_set():
            try:
                r = requests.get(
                    f"{self.base}/getUpdates",
                    params={"offset": self._offset, "timeout": 30},
                    timeout=35,
                )
                if r.status_code != 200:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 60)
                    continue
                backoff = 1
                data = r.json()
                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1
                    try:
                        self._handle(update)
                    except Exception:
                        traceback.print_exc()
            except requests.exceptions.Timeout:
                continue
            except Exception:
                traceback.print_exc()
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _handle(self, update: dict):
        # Handle callback queries (button clicks)
        cb = update.get("callback_query")
        if cb:
            self._handle_callback(cb)
            return

        msg = update.get("message")
        if not msg:
            return
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()
        if not text:
            return

        # Check for pending confirmation responses
        if chat_id in self._pending_confirm:
            self._handle_confirm(chat_id, text)
            return

        if text.startswith("/"):
            parts = text.split(None, 2)
            cmd = parts[0].lower().split("@")[0]  # strip @botname
            args = parts[1:] if len(parts) > 1 else []
            handler = {
                "/start": self._cmd_start,
                "/help": self._cmd_help,
                "/link": self._cmd_link,
                "/unlink": self._cmd_unlink,
                "/kuota": self._cmd_kuota,
                "/saldo": self._cmd_saldo,
                "/paket": self._cmd_paket,
                "/beli": self._cmd_beli,
                "/unsub": self._cmd_unsub,
                "/history": self._cmd_history,
                "/menu": self._cmd_menu,
                "/nomor": self._cmd_nomor,
            }.get(cmd)
            if handler:
                handler(chat_id, args)
            else:
                self.send(chat_id, "Command tidak dikenal. Ketik /help untuk daftar command.")
        else:
            # Non-command text: check if waiting for input (family code, option code, etc)
            state = self._get_state(chat_id)
            step = state.get("step")

            if step == "await_family_code":
                self._handle_family_code_input(chat_id, text)
            elif step == "await_option_code":
                self._handle_option_code_input(chat_id, text)
            elif get_user_by_telegram(chat_id):
                self._send_main_menu(chat_id)
            else:
                self.send(chat_id, "Akun belum di-link. Gunakan:\n<code>/link username password</code>")

    def _cmd_menu(self, chat_id: int, args: list):
        if get_user_by_telegram(chat_id):
            self._send_main_menu(chat_id)
        else:
            self.send(chat_id, "Akun belum di-link. Gunakan:\n<code>/link username password</code>")

    def _require_linked(self, chat_id: int) -> dict | None:
        user = get_user_by_telegram(chat_id)
        if not user:
            self.send(chat_id,
                      "Akun belum di-link. Gunakan:\n<code>/link username password</code>")
            return None
        return user

    def _get_state(self, chat_id: int) -> dict:
        if chat_id not in self._states:
            active = self._load_active_msisdn(chat_id)
            self._states[chat_id] = {
                "step": None,
                "data": {},
                "active_msisdn": active,
                "account_msisdn": active,
            }
        return self._states[chat_id]

    def _clear_state(self, chat_id: int):
        """Clear purchase flow state; keep active_msisdn."""
        active = self._get_active_msisdn(chat_id)
        self._states.pop(chat_id, None)
        if active is not None:
            st = self._get_state(chat_id)
            st["active_msisdn"] = active
            st["account_msisdn"] = active

    def _load_active_msisdn(self, chat_id: int) -> int | None:
        uname = self._linked_username(chat_id)
        if not uname:
            return None
        path = user_dir(uname) / "active.number"
        if not path.exists():
            return None
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except (TypeError, ValueError, OSError):
            return None

    def _save_active_msisdn(self, chat_id: int, msisdn: int) -> bool:
        uname = self._linked_username(chat_id)
        if not uname:
            return False
        accounts = self._get_user_accounts(chat_id)
        if not any(a["number"] == msisdn for a in accounts):
            return False
        udir = user_dir(uname)
        udir.mkdir(parents=True, exist_ok=True)
        (udir / "active.number").write_text(str(msisdn), encoding="utf-8")
        try:
            with user_cwd(uname):
                from app.service.auth import AuthInstance
                AuthInstance.set_active_user(msisdn)
        except Exception:
            pass
        state = self._get_state(chat_id)
        state["active_msisdn"] = msisdn
        state["account_msisdn"] = msisdn
        return True

    def _get_active_msisdn(self, chat_id: int) -> int | None:
        state = self._get_state(chat_id)
        raw = state.get("active_msisdn") or state.get("account_msisdn")
        if raw:
            return int(raw)
        loaded = self._load_active_msisdn(chat_id)
        if loaded:
            state["active_msisdn"] = loaded
            state["account_msisdn"] = loaded
            return loaded
        accounts = self._get_user_accounts(chat_id)
        if len(accounts) == 1:
            self._save_active_msisdn(chat_id, accounts[0]["number"])
            return accounts[0]["number"]
        return None

    def _get_active_account(self, chat_id: int) -> dict | None:
        msisdn = self._get_active_msisdn(chat_id)
        if not msisdn:
            return None
        return next(
            (a for a in self._get_user_accounts(chat_id) if a["number"] == msisdn),
            None,
        )

    def _require_active_account(self, chat_id: int, msg_id: int | None = None) -> dict | None:
        acc = self._get_active_account(chat_id)
        if acc:
            return acc
        accounts = self._get_user_accounts(chat_id)
        if not accounts:
            self._reply(chat_id, msg_id, "Tidak ada nomor MyXL terdaftar.")
            return None
        self._send_number_menu(chat_id, msg_id, hint="Pilih nomor aktif dulu:")
        return None

    def _main_menu_text(self, chat_id: int) -> str:
        active = self._get_active_msisdn(chat_id)
        if active:
            acc = self._get_active_account(chat_id)
            st = _esc(acc.get("subscription_type", "")) if acc else ""
            return (
                f"<b>Menu utama</b>\n"
                f"📱 Nomor aktif: <code>{active}</code>"
                + (f" ({st})" if st else "")
                + "\n\nPilih menu:"
            )
        accounts = self._get_user_accounts(chat_id)
        if len(accounts) > 1:
            return (
                "<b>Menu utama</b>\n"
                "⚠️ Belum ada nomor aktif — tap <b>📱 Nomor</b> untuk memilih.\n\n"
                "Pilih menu:"
            )
        return "<b>Menu utama</b>\n\nPilih menu:"

    def _get_user_accounts(self, chat_id: int) -> list[dict]:
        user = get_user_by_telegram(chat_id)
        if not user:
            return []
        return get_all_user_tokens(user["username"])

    # ── Main Menu with Inline Keyboard ──

    def _send_main_menu(self, chat_id: int, msg_id: int | None = None):
        kb = {
            "inline_keyboard": [
                [{"text": "📱 Nomor", "callback_data": "menu:nomor"}],
                [{"text": "📊 Kuota & Saldo", "callback_data": "menu:kuota"}],
                [
                    {"text": "🧾 Riwayat", "callback_data": "menu:history"},
                    {"text": "🛒 Beli Paket", "callback_data": "purchase:start"},
                ],
                [
                    {"text": "🗑️ Unsubscribe", "callback_data": "menu:unsub"},
                    {"text": "❓ Help", "callback_data": "menu:help"},
                ],
                [{"text": "🔌 Unlink", "callback_data": "menu:unlink"}],
            ]
        }
        self._reply(chat_id, msg_id, self._main_menu_text(chat_id), reply_markup=kb)

    def _cmd_nomor(self, chat_id: int, args: list):
        if not self._require_linked(chat_id):
            return
        self._send_number_menu(chat_id, None)

    def _send_number_menu(self, chat_id: int, msg_id: int | None, hint: str | None = None):
        accounts = self._get_user_accounts(chat_id)
        if not accounts:
            self._reply(chat_id, msg_id, "Tidak ada nomor MyXL terdaftar.")
            return

        active = self._get_active_msisdn(chat_id)
        lines = [hint or "<b>📱 Pilih nomor aktif</b>", ""]
        if active:
            lines.append(f"Sekarang: <code>{active}</code>\n")

        kb_rows = []
        for acc in accounts:
            n = acc["number"]
            mark = " ✓" if n == active else ""
            st = acc.get("subscription_type", "?")
            kb_rows.append([{
                "text": f"📱 {n} ({st}){mark}",
                "callback_data": f"nomor:set:{n}",
            }])
        kb_rows.append([{"text": "« Menu utama", "callback_data": "menu:home"}])
        self._reply(chat_id, msg_id, "\n".join(lines), reply_markup={"inline_keyboard": kb_rows})

    def _handle_callback(self, cb: dict):
        chat_id = cb["message"]["chat"]["id"]
        msg_id = cb["message"]["message_id"]
        data = cb.get("data", "")

        # Answer callback immediately to remove loading state
        try:
            requests.post(f"{self.base}/answerCallbackQuery", json={"callback_query_id": cb["id"]}, timeout=5)
        except Exception:
            pass

        if data.startswith("menu:"):
            action = data.split(":", 1)[1]
            self._handle_menu_action(chat_id, msg_id, action)
        elif data.startswith("nomor:"):
            self._handle_number_callback(chat_id, msg_id, data)
        elif data.startswith("purchase:"):
            self._handle_purchase_callback(chat_id, msg_id, data)
        elif data.startswith("confirm:"):
            self._handle_confirm_action(chat_id, msg_id, data)
        elif data.startswith("cancel"):
            self._pending_confirm.pop(chat_id, None)
            self._edit_message(chat_id, msg_id, "Dibatalkan.")

    def _edit_message(self, chat_id: int, msg_id: int, text: str, reply_markup=None):
        payload = {
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        try:
            requests.post(f"{self.base}/editMessageText", json=payload, timeout=10)
        except Exception:
            pass

    def _handle_number_callback(self, chat_id: int, msg_id: int, data: str):
        if data == "nomor:menu":
            self._send_number_menu(chat_id, msg_id)
            return
        if data.startswith("nomor:set:"):
            try:
                msisdn = int(data.split(":", 2)[2])
            except (IndexError, ValueError):
                self._edit_message(chat_id, msg_id, "Nomor tidak valid.")
                return
            if not self._save_active_msisdn(chat_id, msisdn):
                self._edit_message(chat_id, msg_id, "Gagal menyimpan nomor aktif.")
                return
            acc = self._get_active_account(chat_id)
            st = _esc(acc.get("subscription_type", "")) if acc else ""
            self._edit_message(
                chat_id, msg_id,
                f"✅ Nomor aktif: <code>{msisdn}</code>" + (f" ({st})" if st else ""),
            )
            self._send_main_menu(chat_id)

    def _handle_menu_action(self, chat_id: int, msg_id: int, action: str):
        user = self._require_linked(chat_id)
        if not user:
            return

        api_key = get_api_key()

        if action == "home":
            self._send_main_menu(chat_id, msg_id)
            return

        if action == "nomor":
            self._send_number_menu(chat_id, msg_id)
            return

        if action == "kuota":
            acc = self._require_active_account(chat_id, msg_id)
            if acc:
                self._edit_message(chat_id, msg_id, "Mengambil data...")
                self._execute_kuota_for_account(chat_id, msg_id, acc["number"])

        elif action == "history":
            acc = self._require_active_account(chat_id, msg_id)
            if not acc:
                return
            self._edit_message(chat_id, msg_id, "Mengambil riwayat...")
            lines = [f"<b>🧾 Riwayat — {acc['number']}</b>\n"]
            try:
                from app.client.engsel import get_transaction_history
                data = get_transaction_history(api_key, acc["tokens"])
                txs = (data or {}).get("list") or []
                if not txs:
                    lines.append("(tidak ada transaksi)")
                for tx in txs[:10]:
                    title = tx.get("title", "-")
                    price = tx.get("price", "-")
                    status = tx.get("status", "-")
                    emoji = "✅" if status == "SUCCESS" else "❌" if status == "FAILED" else "⏳"
                    lines.append(f"{emoji} {title} · {price}")
            except Exception as e:
                lines.append(_tg_err(e))
            self._edit_message(chat_id, msg_id, "\n".join(lines), reply_markup=_kb_back_only())

        elif action == "unsub":
            self._edit_message(chat_id, msg_id,
                "Untuk stop paket, kirim:\n<code>/unsub</code> diikuti kode paket dari aplikasi XL.",
                reply_markup=_kb_back_menu([[{"text": "❌ Batal", "callback_data": "cancel"}]]),
            )

        elif action == "help":
            self._edit_message(chat_id, msg_id, (
                "<b>Daftar Command</b>\n\n"
                "/link &lt;user&gt; &lt;pass&gt; — Link akun WebUI\n"
                "/unlink — Hapus link\n"
                "/nomor — Ganti nomor aktif\n"
                "/menu — Menu utama\n"
                "/kuota — Info pelanggan + kuota/paket aktif\n"
                "/saldo · /paket — sama dengan /kuota\n"
                "/beli &lt;option_code&gt; — Beli paket\n"
                "/unsub &lt;quota_code&gt; — Unsubscribe\n"
                "/history — Riwayat (nomor aktif)\n\n"
                "Semua menu memakai <b>nomor aktif</b> sampai diganti di 📱 Nomor."
            ), reply_markup=_kb_back_only())

        elif action == "unlink":
            user = get_user_by_telegram(chat_id)
            if user:
                unlink_telegram(user["username"])
                self._edit_message(chat_id, msg_id, f"Akun <b>{user['username']}</b> berhasil di-unlink.")
            else:
                self._edit_message(chat_id, msg_id, "Tidak ada akun yang di-link.")

    def _execute_kuota_for_account(self, chat_id: int, msg_id: int | None, msisdn: int):
        accounts = self._get_user_accounts(chat_id)
        acc = next((a for a in accounts if a["number"] == msisdn), None)
        if not acc:
            self._reply(chat_id, msg_id, "Nomor tidak ditemukan.")
            return

        api_key = get_api_key()
        try:
            lines = _build_kuota_saldo_message(msisdn, acc, api_key, acc["tokens"])
            chunks = _chunk_lines_for_telegram(lines)
            self._send_chunks(chat_id, msg_id, chunks)
        except Exception as e:
            self._reply(chat_id, msg_id, _tg_err(e), reply_markup=_kb_back_only())

    def _handle_family_code_input(self, chat_id: int, family_code: str):
        state = self._get_state(chat_id)
        msisdn = self._get_active_msisdn(chat_id)
        if not msisdn:
            self.send(chat_id, "Belum ada nomor aktif. Gunakan /nomor atau menu 📱 Nomor.")
            return
        state["account_msisdn"] = msisdn

        accounts = self._get_user_accounts(chat_id)
        acc = next((a for a in accounts if a["number"] == msisdn), None)
        if not acc:
            self.send(chat_id, "Nomor tidak valid.")
            self._clear_state(chat_id)
            return

        self.send(chat_id, f"Mencari paket untuk family <code>{family_code}</code>...")
        api_key = get_api_key()

        try:
            from app.client.engsel import get_family
            family = get_family(api_key, acc["tokens"], family_code)
        except Exception as e:
            self.send(chat_id, f"Gagal fetch family: {e}")
            self._clear_state(chat_id)
            return

        if not family:
            self.send(chat_id, f"Family code <code>{family_code}</code> tidak ditemukan atau tidak valid.")
            self._clear_state(chat_id)
            return

        # Ambil beberapa option pertama — callback_data max 64 byte, simpan map di state
        variants = family.get("package_variants", [])
        buttons = []
        pkg_map: dict[str, str] = {}
        count = 0
        for v in variants:
            for opt in v.get("package_options", []):
                if count >= 8:
                    break
                name = opt.get("name", "-")
                code = opt.get("package_option_code", "")
                price = opt.get("price", 0)
                price_str = f"Rp{price//1000}k" if price >= 1000 else f"Rp{price}"
                key = str(count)
                pkg_map[key] = code
                buttons.append([{"text": f"{name} ({price_str})", "callback_data": f"purchase:pkg:{key}"}])
                count += 1
            if count >= 8:
                break
        state["pkg_map"] = pkg_map

        if not buttons:
            self.send(chat_id, "Tidak ada paket di family ini.")
            self._clear_state(chat_id)
            return

        buttons.append([{"text": "❌ Batal", "callback_data": "purchase:cancel"}])
        buttons.append([{"text": "« Menu utama", "callback_data": "menu:home"}])
        kb = {"inline_keyboard": buttons}
        self.send(chat_id, f"Pilih paket dari family <code>{family_code}</code>:", reply_markup=kb)
        state["step"] = "select_package"

    def _handle_option_code_input(self, chat_id: int, option_code: str):
        # Langsung proses sebagai package selection
        state = self._get_state(chat_id)
        self._handle_package_selected(chat_id, None, option_code)  # msg_id=None akan kirim new message

    # ── Purchase Flow ──

    def _start_purchase_flow(self, chat_id: int, msg_id: int | None):
        acc = self._require_active_account(chat_id, msg_id)
        if not acc:
            return

        msisdn = acc["number"]
        state = self._get_state(chat_id)
        state["account_msisdn"] = msisdn
        state["active_msisdn"] = msisdn
        state["step"] = "purchase_category"

        kb = _kb_back_menu([
            [{"text": "🔥 Hot Deals", "callback_data": "purchase:cat:hot"}],
            [{"text": "👨‍👩‍👧 By Family Code", "callback_data": "purchase:cat:family"}],
            [{"text": "🎯 By Option Code", "callback_data": "purchase:cat:option"}],
            [{"text": "🔖 Bookmark", "callback_data": "purchase:cat:bookmark"}],
            [{"text": "❌ Batal", "callback_data": "purchase:cancel"}],
        ])
        text = f"Pilih kategori pembelian\n📱 <code>{msisdn}</code>:"
        if msg_id:
            self._edit_message(chat_id, msg_id, text, reply_markup=kb)
        else:
            self.send(chat_id, text, reply_markup=kb)

    def _handle_purchase_callback(self, chat_id: int, msg_id: int, data: str):
        # data = "purchase:xxx"
        state = self._get_state(chat_id)
        parts = data.split(":", 2)
        sub = parts[1] if len(parts) > 1 else ""

        if sub == "start":
            self._start_purchase_flow(chat_id, msg_id)
            return
        elif sub == "cat":
            cat = parts[2] if len(parts) > 2 else ""
            self._handle_purchase_category(chat_id, msg_id, cat)
        elif sub == "cancel":
            self._clear_state(chat_id)
            self._edit_message(chat_id, msg_id, "Dibatalkan.")
            self._send_main_menu(chat_id)
        elif sub == "pkg":
            raw = parts[2] if len(parts) > 2 else ""
            option_code = self._resolve_pkg_code(state, raw)
            self._handle_package_selected(chat_id, msg_id, option_code)
        elif sub == "pm":
            mode = parts[2] if len(parts) > 2 else ""
            self._handle_payment_mode(chat_id, msg_id, mode)
        elif sub == "pay":
            method = parts[2] if len(parts) > 2 else ""
            self._handle_payment_method(chat_id, msg_id, method)
        elif sub == "dcy":
            try:
                idx = int(parts[2]) if len(parts) > 2 else -1
            except ValueError:
                idx = -1
            self._handle_decoy_payment(chat_id, msg_id, idx)
        elif sub == "hot":
            try:
                idx = int(parts[2]) if len(parts) > 2 else -1
            except ValueError:
                idx = -1
            self._handle_hot_deal_selected(chat_id, msg_id, idx)
        elif sub == "bm":
            try:
                idx = int(parts[2]) if len(parts) > 2 else -1
            except ValueError:
                idx = -1
            self._handle_bookmark_selected(chat_id, msg_id, idx)
        else:
            self._edit_message(chat_id, msg_id, f"Unknown purchase action: {data}")

    def _handle_purchase_category(self, chat_id: int, msg_id: int, category: str):
        state = self._get_state(chat_id)
        msisdn = self._get_active_msisdn(chat_id)
        if not msisdn:
            self._require_active_account(chat_id, msg_id)
            return
        state["account_msisdn"] = msisdn

        if category == "hot":
            self._show_hot_deals(chat_id, msg_id)
        elif category == "family":
            state["step"] = "await_family_code"
            self._edit_message(chat_id, msg_id, "Kirim <b>Family Code</b> yang mau dicari (contoh: <code>FAM123</code>):")
        elif category == "option":
            state["step"] = "await_option_code"
            self._edit_message(chat_id, msg_id, "Kirim <b>Option Code</b> (contoh: <code>U0Nf...</code>):")
        elif category == "bookmark":
            self._show_bookmark_packages(chat_id, msg_id)

    def _show_hot_deals(self, chat_id: int, msg_id: int):
        import json
        from pathlib import Path

        hot_file = Path(__file__).resolve().parents[2] / "hot_data" / "hot2.json"
        if not hot_file.exists():
            self._edit_message(chat_id, msg_id, "Hot deals data tidak tersedia.")
            self._clear_state(chat_id)
            return

        try:
            bundles = json.loads(hot_file.read_text(encoding="utf-8"))
        except Exception:
            self._edit_message(chat_id, msg_id, "Gagal baca hot deals.")
            self._clear_state(chat_id)
            return

        if not bundles:
            self._edit_message(chat_id, msg_id, "Tidak ada hot deals.")
            self._clear_state(chat_id)
            return

        buttons = []
        for i, bundle in enumerate(bundles[:6]):
            name = bundle.get("name", f"Bundle {i+1}")
            price = bundle.get("price", "")
            buttons.append([{"text": f"🔥 {name} {price}", "callback_data": f"purchase:hot:{i}"}])

        buttons.append([{"text": "❌ Batal", "callback_data": "purchase:cancel"}])
        buttons.append([{"text": "« Menu utama", "callback_data": "menu:home"}])
        kb = {"inline_keyboard": buttons}
        self._edit_message(chat_id, msg_id, "Pilih Hot Deal:", reply_markup=kb)

    def _handle_package_selected(self, chat_id: int, msg_id: int | None, option_code: str):
        state = self._get_state(chat_id)
        msisdn = self._get_active_msisdn(chat_id)
        if not msisdn:
            self._require_active_account(chat_id, msg_id)
            return
        state["account_msisdn"] = msisdn

        accounts = self._get_user_accounts(chat_id)
        acc = next((a for a in accounts if a["number"] == msisdn), None)
        if not acc:
            self._reply(chat_id, msg_id, "Nomor tidak valid.")
            return

        api_key = get_api_key()
        try:
            from app.client.engsel import get_package
            pkg = get_package(api_key, acc["tokens"], option_code)
        except Exception as e:
            self._reply(chat_id, msg_id, _tg_err(e))
            return

        if not pkg:
            self._reply(chat_id, msg_id, f"Paket tidak ditemukan.")
            return

        opt = pkg.get("package_option", {})
        name = _esc(opt.get("name", "-"))
        price = opt.get("price", 0)
        price_str = f"Rp {price:,.0f}".replace(",", ".")

        state.pop("pending_hot", None)
        state["pending_purchase"] = {
            "option_code": option_code,
            "pkg": pkg,
            "msisdn": msisdn,
        }
        state["step"] = "select_payment_mode"
        self._show_payment_mode_menu(
            chat_id, msg_id,
            f"<b>Konfirmasi Pembelian</b>\n\n📦 {name}\n💰 {price_str}\n📱 {msisdn}",
        )

    def _show_payment_mode_menu(self, chat_id: int, msg_id: int | None, header: str):
        kb = _kb_back_menu([
            [{"text": "✅ Normal (Pulsa / QRIS)", "callback_data": "purchase:pm:n"}],
            [{"text": "🎭 Decoy", "callback_data": "purchase:pm:d"}],
            [{"text": "❌ Batal", "callback_data": "purchase:cancel"}],
        ])
        text = f"{header}\n\nPilih mode pembayaran:"
        if msg_id:
            self._edit_message(chat_id, msg_id, text, reply_markup=kb)
        else:
            self._reply(chat_id, msg_id, text, reply_markup=kb)

    def _show_normal_payment_menu(self, chat_id: int, msg_id: int):
        kb = _kb_back_menu([
            [{"text": "💳 Pulsa (Balance)", "callback_data": "purchase:pay:balance"}],
            [{"text": "📱 QRIS", "callback_data": "purchase:pay:qris"}],
            [{"text": "« Mode pembayaran", "callback_data": "purchase:pm:back"}],
            [{"text": "❌ Batal", "callback_data": "purchase:cancel"}],
        ])
        self._edit_message(chat_id, msg_id, "<b>Normal</b> — pilih metode:", reply_markup=kb)

    def _show_decoy_payment_menu(self, chat_id: int, msg_id: int):
        """Decoy: langsung list slot default (decoy-*.json) di akun user."""
        uname = self._linked_username(chat_id)
        if not uname:
            self._edit_message(chat_id, msg_id, "Akun belum di-link.")
            return

        with user_cwd(uname):
            msisdn = self._get_active_msisdn(chat_id)
            if msisdn:
                try:
                    from app.service.auth import AuthInstance
                    AuthInstance.set_active_user(msisdn)
                except Exception:
                    pass
            from webui.decoy_helpers import list_default_decoy_choices
            choices = list_default_decoy_choices()

        if not choices:
            self._edit_message(
                chat_id, msg_id,
                "🎭 <b>Decoy</b>\n\nBelum ada decoy yang dikonfigurasi.\n"
                "Atur di WebUI → Settings → Decoy (<code>/settings/decoy</code>).",
                reply_markup=_kb_back_menu([
                    [{"text": "« Mode pembayaran", "callback_data": "purchase:pm:back"}],
                ]),
            )
            return

        state = self._get_state(chat_id)
        dcy_map: dict[str, str] = {}
        buttons = []
        for i, ch in enumerate(choices[:14]):
            label = (ch.get("label") or f"Decoy {i}")[:58]
            payload = {"method": ch["method"]}
            if ch.get("slot"):
                payload["slot"] = ch["slot"]
            dcy_map[str(i)] = json.dumps(payload, separators=(",", ":"))
            buttons.append([{"text": label, "callback_data": f"purchase:dcy:{i}"}])

        state["dcy_map"] = dcy_map
        buttons.append([{"text": "« Mode pembayaran", "callback_data": "purchase:pm:back"}])
        buttons.append([{"text": "❌ Batal", "callback_data": "purchase:cancel"}])
        self._edit_message(
            chat_id, msg_id,
            f"🎭 <b>Decoy</b> — pilih slot (akun <b>{_esc(uname)}</b>):",
            reply_markup={"inline_keyboard": buttons},
        )

    def _handle_payment_mode(self, chat_id: int, msg_id: int, mode: str):
        mode = (mode or "").lower()
        if mode == "back":
            state = self._get_state(chat_id)
            pending = state.get("pending_purchase")
            pending_hot = state.get("pending_hot")
            if pending:
                opt = pending["pkg"].get("package_option", {})
                name = _esc(opt.get("name", "-"))
                price = opt.get("price", 0)
                price_str = f"Rp {price:,.0f}".replace(",", ".")
                msisdn = pending.get("msisdn", "")
                self._show_payment_mode_menu(
                    chat_id, msg_id,
                    f"<b>Konfirmasi Pembelian</b>\n\n📦 {name}\n💰 {price_str}\n📱 {msisdn}",
                )
            elif pending_hot:
                bundle = pending_hot.get("bundle") or {}
                self._show_payment_mode_menu(
                    chat_id, msg_id,
                    f"<b>Hot Deal</b>\n\n📦 {_esc(bundle.get('name', '-'))}\n"
                    f"💰 {_esc(bundle.get('price', ''))}\n📱 {pending_hot.get('msisdn', '')}",
                )
            else:
                self._edit_message(chat_id, msg_id, "Tidak ada transaksi pending.")
            return
        if mode == "n":
            self._show_normal_payment_menu(chat_id, msg_id)
            return
        if mode == "d":
            state = self._get_state(chat_id)
            if state.get("pending_hot") and not state.get("pending_purchase"):
                self._edit_message(
                    chat_id, msg_id,
                    "🎭 Decoy belum didukung untuk Hot Deal bundle.\nGunakan mode Normal.",
                    reply_markup=_kb_back_menu([
                        [{"text": "« Mode pembayaran", "callback_data": "purchase:pm:back"}],
                    ]),
                )
                return
            self._show_decoy_payment_menu(chat_id, msg_id)
            return
        self._edit_message(chat_id, msg_id, "Mode pembayaran tidak dikenal.")

    def _handle_decoy_payment(self, chat_id: int, msg_id: int, idx: int):
        state = self._get_state(chat_id)
        raw = (state.get("dcy_map") or {}).get(str(idx))
        if not raw:
            self._edit_message(chat_id, msg_id, "Pilihan decoy tidak valid.")
            return
        try:
            choice = json.loads(raw)
        except json.JSONDecodeError:
            self._edit_message(chat_id, msg_id, "Data decoy rusak.")
            return

        pending = state.get("pending_purchase")
        if not pending:
            self._edit_message(chat_id, msg_id, "Tidak ada paket pending.")
            return

        msisdn = pending["msisdn"]
        accounts = self._get_user_accounts(chat_id)
        acc = next((a for a in accounts if a["number"] == msisdn), None)
        if not acc:
            self._edit_message(chat_id, msg_id, "Nomor tidak valid.")
            return

        uname = self._linked_username(chat_id)
        if not uname:
            self._edit_message(chat_id, msg_id, "Akun belum di-link.")
            return

        api_key = get_api_key()
        method = choice.get("method", "")
        slot_key = choice.get("slot")
        self._edit_message(chat_id, msg_id, "Memproses pembelian decoy...")

        try:
            with user_cwd(uname):
                from app.service.auth import AuthInstance
                from webui.routes.purchase import run_decoy_settlement
                AuthInstance.set_active_user(msisdn)
                ok, msg, qris_code = run_decoy_settlement(
                    api_key, acc["tokens"], pending["pkg"], method,
                    slot_key=slot_key,
                )
            if ok and qris_code:
                text = (
                    f"✅ {_esc(msg)}\n"
                    f"Scan / bayar:\n<code>{_esc(qris_code)}</code>"
                )
            elif ok:
                text = f"✅ {_esc(msg)}"
            else:
                text = f"❌ {_esc(msg)}"
            self._edit_message(chat_id, msg_id, text, reply_markup=_kb_back_only())
        except Exception as e:
            self._edit_message(chat_id, msg_id, _tg_err(e), reply_markup=_kb_back_only())
        self._clear_state(chat_id)

    def _handle_payment_method(self, chat_id: int, msg_id: int, method: str):
        state = self._get_state(chat_id)
        pending_hot = state.get("pending_hot")
        if pending_hot:
            self._execute_hot_purchase(chat_id, msg_id, method, pending_hot)
            self._clear_state(chat_id)
            return

        pending = state.get("pending_purchase")
        if not pending:
            self._edit_message(chat_id, msg_id, "Tidak ada transaksi pending.")
            self._clear_state(chat_id)
            return

        msisdn = pending["msisdn"]
        pkg = pending["pkg"]
        opt = pkg.get("package_option", {})

        accounts = self._get_user_accounts(chat_id)
        acc = next((a for a in accounts if a["number"] == msisdn), None)
        if not acc:
            self._edit_message(chat_id, msg_id, "Nomor tidak valid.")
            return

        api_key = get_api_key()
        method = (method or "balance").lower()
        self._edit_message(chat_id, msg_id, "Memproses pembelian...")

        try:
            msg = self._settle_single_package(api_key, acc["tokens"], pkg, method)
            self._edit_message(chat_id, msg_id, msg, reply_markup=_kb_back_only())
        except Exception as e:
            self._edit_message(chat_id, msg_id, _tg_err(e), reply_markup=_kb_back_only())

        self._clear_state(chat_id)

    def _settle_single_package(self, api_key: str, tokens: dict, pkg: dict, method: str) -> str:
        from app.client.purchase.balance import settlement_balance
        from app.client.purchase.qris import settlement_qris, get_qris_code
        from app.type_dict import PaymentItem

        opt = pkg.get("package_option", {})
        item = PaymentItem(
            item_code=opt["package_option_code"],
            product_type="",
            item_price=opt["price"],
            item_name=opt["name"],
            tax=0,
            token_confirmation=pkg["token_confirmation"],
        )
        pf = pkg.get("package_family", {}).get("payment_for", "BUY_PACKAGE")
        name = _esc(opt.get("name", "-"))

        if method == "qris":
            tx = settlement_qris(
                api_key, tokens, [item],
                payment_for=pf, ask_overwrite=False,
                overwrite_amount=item["item_price"],
            )
            if not tx or not isinstance(tx, str):
                err = tx.get("message", "QRIS gagal") if isinstance(tx, dict) else "QRIS gagal"
                return f"❌ {_esc(err)}"
            qris_code = get_qris_code(api_key, tokens, tx)
            if not qris_code:
                return f"❌ QRIS dibuat (tx <code>{_esc(tx[:16])}…</code>) tapi kode QR tidak ditemukan."
            return (
                f"✅ QRIS untuk <b>{name}</b>\n"
                f"Scan / bayar dengan kode berikut:\n<code>{_esc(qris_code)}</code>"
            )

        res = settlement_balance(
            api_key, tokens, [item],
            payment_for=pf, ask_overwrite=False,
            overwrite_amount=item["item_price"],
        )
        if isinstance(res, dict) and res.get("status") == "SUCCESS":
            return f"✅ Berhasil beli <b>{name}</b>!"
        err = res.get("message", "") if isinstance(res, dict) else ""
        return f"❌ Gagal: {_esc(err or 'Unknown error')}"

    def _handle_hot_deal_selected(self, chat_id: int, msg_id: int, idx: int):
        state = self._get_state(chat_id)
        msisdn = self._get_active_msisdn(chat_id)
        if not msisdn or idx < 0:
            self._edit_message(chat_id, msg_id, "Pilihan tidak valid.")
            return

        hot_file = PROJECT_DIR / "hot_data" / "hot2.json"
        try:
            bundles = json.loads(hot_file.read_text(encoding="utf-8"))
        except Exception:
            self._edit_message(chat_id, msg_id, "Gagal baca hot deals.")
            return

        if idx >= len(bundles):
            self._edit_message(chat_id, msg_id, "Hot deal tidak ditemukan.")
            return

        bundle = bundles[idx]
        state.pop("pending_purchase", None)
        state["pending_hot"] = {"bundle": bundle, "idx": idx, "msisdn": msisdn}
        state["step"] = "select_payment_mode"
        name = _esc(bundle.get("name", "Hot deal"))
        price = _esc(bundle.get("price", ""))
        self._show_payment_mode_menu(
            chat_id, msg_id,
            f"<b>Hot Deal</b>\n\n📦 {name}\n💰 {price}\n📱 {msisdn}",
        )

    def _execute_hot_purchase(self, chat_id: int, msg_id: int, method: str, pending_hot: dict):
        msisdn = pending_hot.get("msisdn")
        bundle = pending_hot.get("bundle") or {}
        accounts = self._get_user_accounts(chat_id)
        acc = next((a for a in accounts if a["number"] == msisdn), None)
        if not acc:
            self._edit_message(chat_id, msg_id, "Nomor tidak valid.")
            return

        api_key = get_api_key()
        method = (method or "balance").lower()
        self._edit_message(chat_id, msg_id, "Memproses hot deal...")

        try:
            from app.client.engsel import get_package_details
            from app.client.purchase.balance import settlement_balance
            from app.client.purchase.qris import settlement_qris, get_qris_code
            from app.type_dict import PaymentItem

            items: list = []
            for p in bundle.get("packages", []):
                pkg_detail = get_package_details(
                    api_key, acc["tokens"],
                    p["family_code"], p["variant_code"], p["order"],
                    p.get("is_enterprise", False), p.get("migration_type", "NONE"),
                )
                if not pkg_detail:
                    self._edit_message(chat_id, msg_id, "Gagal fetch detail paket hot deal.")
                    return
                opt = pkg_detail["package_option"]
                items.append(PaymentItem(
                    item_code=opt["package_option_code"],
                    product_type="",
                    item_price=opt["price"],
                    item_name=opt["name"],
                    tax=0,
                    token_confirmation=pkg_detail["token_confirmation"],
                ))

            if not items:
                self._edit_message(chat_id, msg_id, "Hot deal tidak punya paket.")
                return

            payment_for = bundle.get("payment_for", "BUY_PACKAGE")
            overwrite = bundle.get("overwrite_amount", -1)
            token_idx = bundle.get("token_confirmation_idx", 0)
            amount_idx = bundle.get("amount_idx", -1)
            if overwrite == -1:
                overwrite = items[amount_idx]["item_price"] if amount_idx != -1 else items[-1]["item_price"]

            name = _esc(bundle.get("name", "Hot deal"))

            if method == "qris":
                tx = settlement_qris(
                    api_key, acc["tokens"], items, payment_for, False,
                    overwrite, token_idx, amount_idx,
                )
                if not tx or not isinstance(tx, str):
                    self._edit_message(chat_id, msg_id, "❌ QRIS hot deal gagal.")
                    return
                qris_code = get_qris_code(api_key, acc["tokens"], tx)
                if qris_code:
                    self._edit_message(
                        chat_id, msg_id,
                        f"✅ QRIS <b>{name}</b>\n<code>{_esc(qris_code)}</code>",
                        reply_markup=_kb_back_only(),
                    )
                else:
                    self._edit_message(
                        chat_id, msg_id, f"✅ QRIS tx dibuat, kode QR tidak tersedia.",
                        reply_markup=_kb_back_only(),
                    )
                return

            res = settlement_balance(
                api_key, acc["tokens"], items, payment_for, False,
                overwrite, token_idx, amount_idx,
            )
            if isinstance(res, dict) and res.get("status") == "SUCCESS":
                self._edit_message(
                    chat_id, msg_id, f"✅ Hot deal <b>{name}</b> berhasil!",
                    reply_markup=_kb_back_only(),
                )
            else:
                err = res.get("message", "") if isinstance(res, dict) else ""
                self._edit_message(
                    chat_id, msg_id, f"❌ Gagal: {_esc(err or 'Unknown')}",
                    reply_markup=_kb_back_only(),
                )
        except Exception as e:
            self._edit_message(chat_id, msg_id, _tg_err(e), reply_markup=_kb_back_only())

    def _show_bookmark_packages(self, chat_id: int, msg_id: int):
        uname = self._linked_username(chat_id)
        if not uname:
            self._edit_message(chat_id, msg_id, "Akun belum di-link.")
            return

        with user_cwd(uname):
            from app.service.bookmark import BookmarkInstance
            BookmarkInstance.reload_for_current_dir()
            bookmarks = BookmarkInstance.get_bookmarks()

        if not bookmarks:
            self._edit_message(chat_id, msg_id, "Bookmark kosong. Tambahkan lewat WebUI atau CLI.")
            self._clear_state(chat_id)
            return

        state = self._get_state(chat_id)
        bm_map: dict[str, str] = {}
        buttons = []
        for i, bm in enumerate(bookmarks[:8]):
            label = f"{bm.get('family_name') or bm.get('family_code', '')[:8]} · {bm.get('option_name', '')}"[:40]
            payload = {
                "family_code": bm["family_code"],
                "is_enterprise": bm.get("is_enterprise", False),
                "variant_name": bm.get("variant_name", ""),
                "option_name": bm.get("option_name", ""),
                "order": bm.get("order", 0),
            }
            if bm.get("package_option_code"):
                payload["package_option_code"] = bm["package_option_code"]
            bm_map[str(i)] = json.dumps(payload, separators=(",", ":"))
            buttons.append([{"text": label, "callback_data": f"purchase:bm:{i}"}])
        state["bm_map"] = bm_map
        buttons.append([{"text": "❌ Batal", "callback_data": "purchase:cancel"}])
        buttons.append([{"text": "« Menu utama", "callback_data": "menu:home"}])
        self._edit_message(chat_id, msg_id, "Pilih bookmark:", reply_markup={"inline_keyboard": buttons})

    def _handle_bookmark_selected(self, chat_id: int, msg_id: int, idx: int):
        state = self._get_state(chat_id)
        raw = (state.get("bm_map") or {}).get(str(idx))
        if not raw:
            self._edit_message(chat_id, msg_id, "Bookmark tidak valid.")
            return

        try:
            bm = json.loads(raw)
        except json.JSONDecodeError:
            self._edit_message(chat_id, msg_id, "Data bookmark rusak.")
            return

        acc = self._require_active_account(chat_id, msg_id)
        if not acc:
            return
        msisdn = acc["number"]
        state["account_msisdn"] = msisdn

        direct_code = (bm.get("package_option_code") or "").strip()
        if direct_code:
            self._handle_package_selected(chat_id, msg_id, direct_code)
            return

        api_key = get_api_key()
        try:
            from app.client.engsel import get_family
            from app.service.bookmark import resolve_bookmark_option_code
            family = get_family(api_key, acc["tokens"], bm["family_code"], bm.get("is_enterprise", False))
        except Exception as e:
            self._edit_message(chat_id, msg_id, _tg_err(e))
            return

        if not family:
            self._edit_message(chat_id, msg_id, "Family bookmark tidak ditemukan.")
            return

        option_code = resolve_bookmark_option_code(family, bm)
        if not option_code:
            hint = bm.get("option_name") or bm.get("variant_name") or "?"
            self._edit_message(
                chat_id, msg_id,
                f"Paket bookmark tidak ditemukan di API.\n"
                f"Coba hapus & tambah lagi dari WebUI (🔖 di detail paket): <i>{_esc(hint)}</i>",
            )
            return

        self._handle_package_selected(chat_id, msg_id, option_code)

    def _handle_confirm_action(self, chat_id: int, msg_id: int, data: str):
        pending = self._pending_confirm.pop(chat_id, None)
        if not pending:
            self._edit_message(chat_id, msg_id, "Tidak ada aksi yang pending.")
            return

        if pending.get("expires", 0) < time.time():
            self._edit_message(chat_id, msg_id, "Konfirmasi sudah expired. Silakan ulangi command.")
            return

        action = pending.get("action")
        api_key = get_api_key()

        if action == "beli":
            self._edit_message(chat_id, msg_id, "Memproses pembelian...")
            self._execute_beli(chat_id, pending, api_key, edit_msg_id=msg_id)
        elif action == "unsub":
            self._edit_message(chat_id, msg_id, "Memproses unsubscribe...")
            self._execute_unsub(chat_id, pending, api_key, edit_msg_id=msg_id)

    # ── Commands ──

    def _cmd_start(self, chat_id: int, args: list):
        if get_user_by_telegram(chat_id):
            self._send_main_menu(chat_id)
        else:
            self.send(chat_id, (
                "<b>me-cli Telegram Bot</b>\n\n"
                "Bot ini terhubung dengan me-cli WebUI (MyXL).\n\n"
                "Langkah pertama: link akun kamu:\n"
                "<code>/link username password</code>\n\n"
                "Setelah login, ketik /menu atau kirim pesan apa saja untuk membuka menu."
            ))

    def _cmd_help(self, chat_id: int, args: list):
        self.send(chat_id, (
            "<b>Daftar Command</b>\n\n"
            "/link &lt;user&gt; &lt;pass&gt; — Link akun WebUI\n"
            "/unlink — Hapus link\n"
            "/nomor — Ganti nomor aktif\n"
            "/menu — Menu utama\n"
            "/kuota — Info pelanggan + kuota/paket aktif\n"
            "/saldo · /paket — sama dengan /kuota\n"
            "/beli &lt;option_code&gt; — Beli paket\n"
            "/unsub &lt;quota_code&gt; — Unsubscribe\n"
            "/history — Riwayat (nomor aktif)\n\n"
            "Semua fitur memakai nomor aktif sampai diganti via 📱 Nomor."
        ))

    def _cmd_link(self, chat_id: int, args: list):
        if len(args) < 2:
            self.send(chat_id, "Usage: <code>/link username password</code>")
            return
        username = args[0]
        password = " ".join(args[1:]) if len(args) > 1 else args[1]
        user = authenticate(username, password)
        if not user:
            self.send(chat_id, "Username atau password salah.")
            return
        # Check if already linked to another chat
        existing = get_user_by_telegram(chat_id)
        if existing and existing["username"] != username:
            unlink_telegram(existing["username"])
        link_telegram(username, chat_id)
        accounts = get_all_user_tokens(username)
        if len(accounts) == 1:
            self._save_active_msisdn(chat_id, accounts[0]["number"])
            self.send(
                chat_id,
                f"Berhasil link ke akun <b>{username}</b>!\n"
                f"Nomor aktif: <code>{accounts[0]['number']}</code>",
            )
            self._send_main_menu(chat_id)
        elif len(accounts) > 1:
            self.send(
                chat_id,
                f"Berhasil link ke akun <b>{username}</b>!\n"
                f"Ada {len(accounts)} nomor — pilih nomor aktif di menu 📱 Nomor.",
            )
            self._send_number_menu(chat_id, None)
        else:
            self.send(chat_id, f"Berhasil link ke <b>{username}</b>! Belum ada nomor MyXL di akun ini.")

    def _cmd_unlink(self, chat_id: int, args: list):
        user = get_user_by_telegram(chat_id)
        if not user:
            self.send(chat_id, "Tidak ada akun yang di-link.")
            return
        unlink_telegram(user["username"])
        self.send(chat_id, f"Akun <b>{user['username']}</b> berhasil di-unlink.")

    def _cmd_kuota(self, chat_id: int, args: list):
        if not self._require_linked(chat_id):
            return
        acc = self._require_active_account(chat_id)
        if not acc:
            return
        self.send(chat_id, "Mengambil data...")
        self._execute_kuota_for_account(chat_id, None, acc["number"])

    def _cmd_saldo(self, chat_id: int, args: list):
        self._cmd_kuota(chat_id, args)

    def _cmd_paket(self, chat_id: int, args: list):
        self._cmd_kuota(chat_id, args)

    def _cmd_beli(self, chat_id: int, args: list):
        user = self._require_linked(chat_id)
        if not user:
            return
        if not args:
            self.send(chat_id, "Usage: <code>/beli OPTION_CODE</code>\n\nContoh: <code>/beli U0Nf...</code>")
            return

        option_code = args[0]
        acc = self._require_active_account(chat_id)
        if not acc:
            return
        api_key = get_api_key()

        self.send(chat_id, f"Mengambil detail paket <code>{option_code}</code>...")

        try:
            from app.client.engsel import get_package
            pkg = get_package(api_key, acc["tokens"], option_code)
        except Exception as e:
            self.send(chat_id, f"Gagal fetch paket: {e}")
            return

        if not pkg:
            self.send(chat_id, f"Paket <code>{option_code}</code> tidak ditemukan.")
            return

        opt = pkg.get("package_option", {})
        name = opt.get("name", "-")
        price = opt.get("price", 0)
        price_str = f"Rp {price:,.0f}".replace(",", ".")

        # Store pending confirmation
        self._pending_confirm[chat_id] = {
            "action": "beli",
            "option_code": option_code,
            "pkg": pkg,
            "account": acc,
            "expires": time.time() + 120,
        }

        kb = _kb_back_menu([
            [
                {"text": "✅ Ya, Beli", "callback_data": "confirm:beli"},
                {"text": "❌ Batal", "callback_data": "cancel"},
            ],
        ])
        self.send(chat_id, (
            f"<b>Konfirmasi Pembelian</b>\n\n"
            f"📦 {name}\n"
            f"💰 {price_str}\n"
            f"📱 {acc['number']}\n"
            f"💳 Metode: Pulsa (Balance)\n\n"
            f"Klik tombol di bawah atau ketik <b>ya</b> / <b>batal</b>."
        ), reply_markup=kb)

    def _cmd_unsub(self, chat_id: int, args: list):
        user = self._require_linked(chat_id)
        if not user:
            return
        if not args:
            self.send(chat_id, "Usage: <code>/unsub KODE_PAKET</code>\n(Kode dari aplikasi XL / WebUI)")
            return

        quota_code = args[0]
        acc = self._require_active_account(chat_id)
        if not acc:
            return
        api_key = get_api_key()

        # Find the quota to get its name and metadata
        try:
            from app.client.engsel import send_api_request
            res = send_api_request(
                api_key, "api/v8/packages/quota-details",
                {"is_enterprise": False, "lang": "en", "family_member_id": ""},
                acc["tokens"]["id_token"], "POST",
            )
            quota_name = quota_code
            product_domain = ""
            product_sub_type = ""
            if isinstance(res, dict) and res.get("status") == "SUCCESS":
                for q in (res.get("data") or {}).get("quotas") or []:
                    if q.get("quota_code") == quota_code:
                        quota_name = q.get("name", quota_code)
                        product_domain = q.get("product_domain", "")
                        product_sub_type = q.get("product_subscription_type", "")
                        break
        except Exception:
            quota_name = quota_code
            product_domain = ""
            product_sub_type = ""

        self._pending_confirm[chat_id] = {
            "action": "unsub",
            "quota_code": quota_code,
            "quota_name": quota_name,
            "product_domain": product_domain,
            "product_subscription_type": product_sub_type,
            "account": acc,
            "expires": time.time() + 120,
        }

        kb = _kb_back_menu([
            [
                {"text": "✅ Ya, Unsubscribe", "callback_data": "confirm:unsub"},
                {"text": "❌ Batal", "callback_data": "cancel"},
            ],
        ])
        self.send(chat_id, (
            f"<b>Konfirmasi Unsubscribe</b>\n\n"
            f"📦 {_esc(quota_name)}\n"
            f"📱 {acc['number']}\n\n"
            f"Klik tombol di bawah atau ketik <b>ya</b> / <b>batal</b>."
        ), reply_markup=kb)

    def _cmd_history(self, chat_id: int, args: list):
        if not self._require_linked(chat_id):
            return
        acc = self._require_active_account(chat_id)
        if not acc:
            return
        api_key = get_api_key()
        lines = [f"<b>🧾 Riwayat — {acc['number']}</b>\n"]
        try:
            from app.client.engsel import get_transaction_history
            data = get_transaction_history(api_key, acc["tokens"])
            txs = (data or {}).get("list") or []
            if not txs:
                lines.append("(tidak ada transaksi)")
            for tx in txs[:10]:
                title = tx.get("title", "-")
                price = tx.get("price", "-")
                status = tx.get("status", "-")
                method = tx.get("payment_method_label", "")
                date = tx.get("formated_date", "")
                emoji = "✅" if status == "SUCCESS" else "❌" if status == "FAILED" else "⏳"
                lines.append(f"{emoji} {title} · {price}")
                if date or method:
                    lines.append(f"    {date} · {method}")
        except Exception as e:
            lines.append(_tg_err(e))
        self.send(chat_id, "\n".join(lines), reply_markup=_kb_back_only())

    # ── Confirmation handler ──

    def _handle_confirm(self, chat_id: int, text: str):
        pending = self._pending_confirm.pop(chat_id, None)
        if not pending:
            return

        if pending.get("expires", 0) < time.time():
            self.send(chat_id, "Konfirmasi sudah expired. Silakan ulangi command.")
            return

        answer = text.lower().strip()
        if answer not in ("ya", "y", "yes", "ok"):
            self.send(chat_id, "Dibatalkan.")
            return

        action = pending.get("action")
        api_key = get_api_key()

        if action == "beli":
            self._execute_beli(chat_id, pending, api_key)
        elif action == "unsub":
            self._execute_unsub(chat_id, pending, api_key)

    def _execute_beli(self, chat_id: int, pending: dict, api_key: str, edit_msg_id: int | None = None):
        pkg = pending["pkg"]
        acc = pending["account"]
        opt = pkg.get("package_option", {})

        from app.client.purchase.balance import settlement_balance
        from app.type_dict import PaymentItem

        item = PaymentItem(
            item_code=opt["package_option_code"],
            product_type="",
            item_price=opt["price"],
            item_name=opt["name"],
            tax=0,
            token_confirmation=pkg["token_confirmation"],
        )
        pf = pkg.get("package_family", {}).get("payment_for", "BUY_PACKAGE")

        try:
            res = settlement_balance(
                api_key, acc["tokens"], [item],
                payment_for=pf, ask_overwrite=False,
                overwrite_amount=item["item_price"],
            )
            if isinstance(res, dict) and res.get("status") == "SUCCESS":
                msg = f"✅ Berhasil beli <b>{opt['name']}</b>!"
            else:
                err = res.get("message", "") if isinstance(res, dict) else str(res)
                msg = f"❌ Gagal: {err or 'Unknown error'}"
        except Exception as e:
            msg = _tg_err(e)

        if edit_msg_id:
            self._edit_message(chat_id, edit_msg_id, msg, reply_markup=_kb_back_only())
        else:
            self.send(chat_id, msg, reply_markup=_kb_back_only())

    def _execute_unsub(self, chat_id: int, pending: dict, api_key: str, edit_msg_id: int | None = None):
        acc = pending["account"]

        from app.client.engsel import unsubscribe

        try:
            ok = unsubscribe(
                api_key, acc["tokens"],
                pending["quota_code"],
                pending.get("product_domain", ""),
                pending.get("product_subscription_type", ""),
            )
            if ok:
                msg = f"✅ Berhasil unsubscribe <b>{pending['quota_name']}</b>!"
            else:
                msg = f"❌ Gagal unsubscribe {pending['quota_name']}"
        except Exception as e:
            msg = _tg_err(e)

        if edit_msg_id:
            self._edit_message(chat_id, edit_msg_id, msg, reply_markup=_kb_back_only())
        else:
            self.send(chat_id, msg, reply_markup=_kb_back_only())
