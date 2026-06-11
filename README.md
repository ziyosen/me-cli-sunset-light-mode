# me-cli-sunset

![banner](bnr.png)

CLI + **Web UI** + **Telegram bot** untuk mengelola paket, kuota, pembelian, bookmark, decoy, dan monitoring akun MyXL.

> Fork dengan perluasan WebUI/Telegram: [arifianilhamnrr/me-cli-sunset](https://github.com/arifianilhamnrr/me-cli-sunset)  
> Upstream: [purplemashu/me-cli-sunset](https://github.com/purplemashu/me-cli-sunset)

---

## Fitur

| Mode | Entry point | Keterangan |
|------|-------------|------------|
| **CLI** | `python main.py` | Menu interaktif di terminal (Termux / Linux / Windows) |
| **Web UI** | `python run-web.py` | FastAPI + browser, multi-user, port default **8089** |
| **Telegram** | (otomatis saat Web UI jalan) | Bot terhubung ke akun Web UI via `/link` |

**Web UI mencakup:**

- Login multi-user (`webui_data/`) — tiap user punya folder sendiri (token, bookmark, decoy)
- Dashboard, paket aktif, beli paket (pulsa / QRIS / decoy)
- Bookmark, hot deals, family plan, circle, store, notifikasi, transaksi
- Pengaturan decoy (`/settings/decoy`)
- Monitoring kuota + alert Telegram (`/monitoring`, `/monitoring/telegram`)

**Telegram bot (ringkas):**

- `/link username password` — hubungkan chat ke user Web UI
- `/nomor` — pilih nomor MyXL aktif
- `/kuota` — info pelanggan + kuota & paket aktif
- Menu beli paket: hot / family code / option code / bookmark
- Pembayaran: **Normal** (pulsa, QRIS) atau **Decoy** (slot `decoy-*.json` di akun user)

---

## Persyaratan

- **Python 3.10+** (disarankan 3.11 atau 3.12)
- **Git**
- File **`.env`** berisi variabel API (lihat [Environment variables](#environment-variables))
- Koneksi internet

---

## Environment variables

Salin template:

```bash
cp .env.template .env
```

Isi nilai di `.env` (biasanya dari channel Telegram proyek — lihat bagian bawah README asli / maintainer).

| Variabel | Wajib | Keterangan |
|----------|-------|------------|
| `BASE_API_URL`, `BASE_CIAM_URL`, `BASIC_AUTH`, `UA` | Ya | Endpoint & auth API |
| `API_KEY`, `ENCRYPTED_FIELD_KEY`, `AES_KEY_ASCII` | Ya | Enkripsi / signature |
| `XDATA_KEY`, `AX_API_SIG_KEY`, `X_API_BASE_SECRET` | Ya | Request signing |
| `AX_FP`, `AX_FP_KEY` | Ya | Fingerprint device |
| `WEBUI_HOST`, `WEBUI_PORT` | Tidak | Default `127.0.0.1:8089` |
| `WEBUI_DEBUG` | Tidak | `1` = tampilkan error internal di browser |

**Telegram bot** (bukan di `.env`): atur di Web UI → **Monitoring → Telegram** (`webui_data/telegram.json`) — token BotFather + enable.

---

## Setup — Linux (Debian / Ubuntu / dll.)

### 1. Clone

```bash
git clone https://github.com/arifianilhamnrr/me-cli-sunset.git
cd me-cli-sunset
```

### 2. Python venv (disarankan)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Environment

```bash
cp .env.template .env
nano .env   # isi variabel API
```

### 4. Jalankan CLI

```bash
source venv/bin/activate
python main.py
```

### 5. Jalankan Web UI (+ Telegram jika dikonfigurasi)

```bash
source venv/bin/activate
python run-web.py
```

Buka browser: **http://127.0.0.1:8089**

- Daftar / login user Web UI di halaman pertama
- Login **MyXL** (OTP) lewat menu akun setelah masuk Web UI
- Untuk akses dari LAN: set `WEBUI_HOST=0.0.0.0` di `.env` (hati-hati keamanan jaringan)

### 6. Telegram (opsional)

1. Buat bot di [@BotFather](https://t.me/BotFather), salin token
2. Web UI → **Monitoring → Telegram** → paste token, centang **enabled**, simpan
3. Di Telegram: `/link <username_webui> <password_webui>`
4. `/nomor` → pilih MSISDN → `/kuota` atau menu **Beli paket**

---

## Setup — Windows 10/11

### 1. Install tools

1. [Git for Windows](https://git-scm.com/download/win)
2. [Python 3.11+](https://www.python.org/downloads/) — centang **“Add python.exe to PATH”** saat install

### 2. Clone (PowerShell atau Git Bash)

```powershell
cd $HOME\Documents
git clone https://github.com/arifianilhamnrr/me-cli-sunset.git
cd me-cli-sunset
```

### 3. Virtual environment

**PowerShell:**

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Jika eksekusi script diblokir (sekali saja):

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Lalu:

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Environment

```powershell
copy .env.template .env
notepad .env
```

Isi semua variabel API, simpan.

### 5. Jalankan

**CLI:**

```powershell
.\venv\Scripts\Activate.ps1
python main.py
```

**Web UI:**

```powershell
.\venv\Scripts\Activate.ps1
python run-web.py
```

Browser: **http://127.0.0.1:8089**

---

## Setup — Termux (Android)

```bash
pkg update && pkg upgrade -y
pkg install git python python-pip -y
git clone https://github.com/arifianilhamnrr/me-cli-sunset.git
cd me-cli-sunset
pip install -r requirements.txt
cp .env.template .env
# edit .env (nano .env)
python main.py
```

Web UI di Termux (opsional):

```bash
python run-web.py
# buka http://127.0.0.1:8089 di browser HP
```

Atau pakai script lama:

```bash
bash setup.sh
python main.py
```

---

## Struktur data penting

```
me-cli-sunset/
├── main.py              # CLI
├── run-web.py           # Web UI server
├── app/                 # Core client & menus
├── webui/               # FastAPI, templates, Telegram bot
├── webui_data/          # JANGAN di-commit — user, session, telegram.json
├── decoy_data/          # Template decoy (per-user copy di webui_data/users/…/)
├── .env                 # Rahasia — jangan commit
└── requirements.txt
```

Setelah login Web UI, data per user ada di `webui_data/users/<username>/`:

- `refresh-tokens.json` — sesi MyXL
- `active.number` — nomor aktif
- `bookmark.json`, `decoy_data/`, `ax.fp`, dll.

---

## Decoy & bookmark

- **Bookmark:** simpan paket dari halaman detail paket (Web UI) atau CLI; Telegram/Web UI baca `bookmark.json` user
- **Decoy:** edit di **Settings → Decoy** (`/settings/decoy`); file `decoy-default-*` / `decoy-prio-*` disalin per user saat registrasi Web UI
- **Telegram beli paket → Decoy:** menampilkan daftar slot decoy yang sudah dikonfigurasi (ada `family_code` + `variant_code`)

---

## Git & push ke fork sendiri

```bash
# Identitas + remote fork (sekali)
./scripts/setup-my-github.sh "Nama Kamu" email@example.com github_username

# Commit perubahan (tanpa webui_data / .env)
./scripts/commit-my-changes.sh

git push -u origin main
```

---

## Troubleshooting

| Masalah | Solusi |
|---------|--------|
| `ModuleNotFoundError` | Aktifkan venv: `source venv/bin/activate` (Linux) atau `.\venv\Scripts\Activate.ps1` (Windows) |
| Web UI port bentrok | Ubah `WEBUI_PORT` di `.env` atau hentikan proses di port 8089 |
| Telegram tidak merespons | Cek `webui_data/telegram.json`: `enabled` + `bot_token`; restart `run-web.py` |
| Bookmark tidak ketemu di Telegram | Hapus & tambah lagi dari Web UI (supaya tersimpan `package_option_code` / order benar) |
| Kuota semua “Unlimited” | Sudah diperbaiki di fork ini — pastikan kode terbaru |

---

## Info & disclaimer

### PS

Instead of just delisting the package from the app, ensure the user cannot purchase it.  
What's the point of strong client-side security when the server doesn't enforce it?

### Terms of Service

By using this tool, you agree to comply with applicable laws and regulations and release the developer from claims arising from its use.

### Environment variables (channel)

Go to [OUR TELEGRAM CHANNEL](https://t.me/alyxcli) for the provided environment variables (paste into `.env`).

### Contact

contact@mashu.lol