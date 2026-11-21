"""
Multi-Drive Storage Pool — Web Panel (Prototype v1)
====================================================
Minimum 5 Gmail accounts, no maximum limit.
Large files are split into 100/200 MB chunks, each chunk stored as a
separate file in a separate account's Google Drive, and reassembled
seamlessly for download and streaming.

Storage providers:
  - google : real Google Drive via OAuth (needs GOOGLE_CLIENT_ID/SECRET)
  - local  : TEST MODE only — folders on this disk that pretend to be
             Drives, so the whole panel can be tried before Google setup.

Run:  uvicorn app:app --host 0.0.0.0 --port 8000
"""
import hashlib
import io
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, StreamingResponse)
from jinja2 import Template

BASE = Path(__file__).resolve().parent

def _load_env_file():
    env_file = BASE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env_file()
# On a server, point DATA_DIR at a persistent disk so the index DB and
# the token key survive restarts. Locally it defaults to ./data
DATA = Path(os.environ.get("DATA_DIR", str(BASE / "data")))
DATA.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / "panel.db"
LOCAL_ROOT = DATA / "local_drives"
CACHE = DATA / "cache"
TMPD = DATA / "tmp"
for d in (LOCAL_ROOT, CACHE, TMPD):
    d.mkdir(exist_ok=True)

# ---------------------------------------------------------------- config
def load_env():
    env_file = BASE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env()
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]
MIN_ACCOUNTS = 5
GB15 = 15 * 1024 ** 3

# ---------------------------------------------------------------- crypto
_key_file = DATA / "secret.key"
if not _key_file.exists():
    _key_file.write_bytes(Fernet.generate_key())
FERNET = Fernet(_key_file.read_bytes())

def enc(text: str) -> str:
    return FERNET.encrypt(text.encode()).decode()

def dec(text: str) -> str:
    return FERNET.decrypt(text.encode()).decode()

# ---------------------------------------------------------------- db
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS accounts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT 'google',   -- google | local
        refresh_token TEXT,                        -- encrypted (google)
        folder TEXT,                               -- folder name (local)
        total_bytes INTEGER NOT NULL DEFAULT 0,
        used_bytes INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',     -- active | full | error
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS files(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        mime_type TEXT,
        size INTEGER NOT NULL,
        sha256 TEXT,
        chunk_size INTEGER NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS chunks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_id INTEGER NOT NULL,
        part_no INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
        drive_file_id TEXT NOT NULL,
        size INTEGER NOT NULL,
        start_byte INTEGER NOT NULL,
        end_byte INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY, value TEXT
    );
    INSERT OR IGNORE INTO settings(key, value) VALUES('chunk_size_mb', '100');
    """)
    c.commit()
    c.close()

def get_setting(key, default=None):
    c = db()
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    c.close()
    return row["value"] if row else default

def set_setting(key, value):
    c = db()
    c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (key, value))
    c.commit()
    c.close()

init_db()

# ---------------------------------------------------------------- google
def google_service(account):
    """Build a Drive v3 service for one connected Gmail account."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(
        token=None,
        refresh_token=dec(account["refresh_token"]),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES,
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def drive_upload(account, chunk_path: Path, name: str) -> str:
    from googleapiclient.http import MediaFileUpload
    service = google_service(account)
    media = MediaFileUpload(str(chunk_path), resumable=True)
    created = service.files().create(
        body={"name": name}, media_body=media, fields="id").execute()
    return created["id"]

def drive_download_to_file(account, file_id: str, dest: Path):
    import io as _io
    from googleapiclient.http import MediaIoBaseDownload
    service = google_service(account)
    request = service.files().get_media(fileId=file_id)
    with open(dest, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()

def drive_delete(account, file_id: str):
    try:
        google_service(account).files().delete(fileId=file_id).execute()
    except Exception:
        pass

def drive_refresh_quota(account):
    service = google_service(account)
    about = service.about().get(fields="storageQuota,user").execute()
    quota = about.get("storageQuota", {})
    return int(quota.get("limit", 0)), int(quota.get("usage", 0))

# ---------------------------------------------------------------- providers
def provider_upload(account, chunk_path: Path, name: str) -> str:
    if account["provider"] == "local":
        dest = LOCAL_ROOT / account["folder"] / name
        shutil.copyfile(chunk_path, dest)
        return name
    return drive_upload(account, chunk_path, name)

def provider_delete(account, drive_file_id: str):
    if account["provider"] == "local":
        p = LOCAL_ROOT / account["folder"] / drive_file_id
        if p.exists():
            p.unlink()
    else:
        drive_delete(account, drive_file_id)

def chunk_local_path(account, chunk) -> Path:
    """Return a local filesystem path holding this chunk's bytes.
    Local accounts: the file itself. Google accounts: download once
    into the cache and reuse."""
    if account["provider"] == "local":
        return LOCAL_ROOT / account["folder"] / chunk["drive_file_id"]
    cached = CACHE / f"chunk_{chunk['id']}"
    if not cached.exists():
        drive_download_to_file(account, chunk["drive_file_id"], cached)
    return cached

# ---------------------------------------------------------------- helpers
def fmt(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"

def active_accounts():
    c = db()
    rows = c.execute(
        "SELECT * FROM accounts WHERE status!='error' ORDER BY id").fetchall()
    c.close()
    return rows

def pick_accounts(chunks_needed_sizes):
    """Round-robin across active accounts sorted by free space (desc).
    Returns a list of account rows, one per chunk size, or None."""
    accounts = [a for a in active_accounts()
                if (a["total_bytes"] - a["used_bytes"]) > 0]
    accounts.sort(key=lambda a: a["total_bytes"] - a["used_bytes"], reverse=True)
    if not accounts:
        return None
    reserved = {a["id"]: 0 for a in accounts}
    chosen = []
    start = int(time.time()) % len(accounts)
    for i, size in enumerate(chunks_needed_sizes):
        picked = None
        for step in range(len(accounts)):
            cand = accounts[(start + i + step) % len(accounts)]
            free = cand["total_bytes"] - cand["used_bytes"] - reserved[cand["id"]]
            if free >= size:
                picked = cand
                reserved[cand["id"]] += size
                break
        if picked is None:
            return None
        chosen.append(picked)
    return chosen

app = FastAPI(title="Multi-Drive Storage Pool")
PANEL_TEMPLATE = Template((BASE / "index.html").read_text(encoding="utf-8"))

# ---------------------------------------------------------------- panel
@app.get("/", response_class=HTMLResponse)
def home(request: Request, msg: str = "", err: str = ""):
    c = db()
    accounts = c.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    files = c.execute("""
        SELECT f.*, (SELECT COUNT(*) FROM chunks ch WHERE ch.file_id=f.id) AS n_chunks
        FROM files f ORDER BY f.id DESC""").fetchall()
    c.close()
    total = sum(a["total_bytes"] for a in accounts)
    used = sum(a["used_bytes"] for a in accounts)
    return HTMLResponse(PANEL_TEMPLATE.render({
        "accounts": accounts, "files": files,
        "total": total, "used": used, "free": total - used,
        "fmt": fmt, "msg": msg, "err": err,
        "google_ready": bool(CLIENT_ID and CLIENT_SECRET),
        "min_accounts": MIN_ACCOUNTS,
        "n_active": len([a for a in accounts if a["status"] != "error"]),
        "chunk_size_mb": int(get_setting("chunk_size_mb", "100")),
    }))

# ---------------------------------------------------------------- oauth
@app.get("/auth/connect")
def auth_connect():
    if not (CLIENT_ID and CLIENT_SECRET):
        return RedirectResponse("/?err=Google+OAuth+ekhono+setup+hoyni+.env+file-e+CLIENT_ID+ar+SECRET+boshao")
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        {"web": {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                 "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                 "token_uri": "https://oauth2.googleapis.com/token"}},
        scopes=SCOPES, redirect_uri=f"{BASE_URL}/auth/callback")
    url, _ = flow.authorization_url(access_type="offline", prompt="consent",
                                    include_granted_scopes="true")
    return RedirectResponse(url)

@app.get("/auth/callback")
def auth_callback(code: str = ""):
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    flow = Flow.from_client_config(
        {"web": {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                 "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                 "token_uri": "https://oauth2.googleapis.com/token"}},
        scopes=SCOPES, redirect_uri=f"{BASE_URL}/auth/callback")
    flow.fetch_token(code=code)
    creds = flow.credentials
    if not creds.refresh_token:
        return RedirectResponse("/?err=Google+refresh+token+deyni.+Abar+connect+try+koro")
    info = build("oauth2", "v2", credentials=creds).userinfo().get().execute()
    email = info.get("email", "unknown")
    about = build("drive", "v3", credentials=creds).about().get(
        fields="storageQuota").execute()
    quota = about.get("storageQuota", {})
    total = int(quota.get("limit", GB15))
    used = int(quota.get("usage", 0))
    c = db()
    existing = c.execute("SELECT id FROM accounts WHERE email=? AND provider='google'",
                         (email,)).fetchone()
    if existing:
        c.execute("UPDATE accounts SET refresh_token=?, total_bytes=?, used_bytes=?, status='active' WHERE id=?",
                  (enc(creds.refresh_token), total, used, existing["id"]))
    else:
        c.execute("INSERT INTO accounts(email, provider, refresh_token, total_bytes, used_bytes, status, created_at) VALUES(?,?,?,?,?,'active',?)",
                  (email, "google", enc(creds.refresh_token), total, used, time.time()))
    c.commit()
    c.close()
    return RedirectResponse(f"/?msg={email}+connect+hoiye+geche")

# ---------------------------------------------------------------- accounts
@app.post("/accounts/refresh/{account_id}")
def account_refresh(account_id: int):
    c = db()
    acc = c.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    if not acc:
        c.close()
        return RedirectResponse("/?err=Account+pawa+jayni")
    try:
        if acc["provider"] == "google":
            total, used = drive_refresh_quota(acc)
        else:
            folder = LOCAL_ROOT / acc["folder"]
            used = sum(p.stat().st_size for p in folder.glob("*") if p.is_file())
            total = acc["total_bytes"]
        status = "full" if total and used >= total * 0.999 else "active"
        c.execute("UPDATE accounts SET total_bytes=?, used_bytes=?, status=? WHERE id=?",
                  (total, used, status, account_id))
        c.commit()
        c.close()
        return RedirectResponse("/?msg=Account+refresh+hoiye+geche")
    except Exception as e:
        c.execute("UPDATE accounts SET status='error' WHERE id=?", (account_id,))
        c.commit()
        c.close()
        return RedirectResponse(f"/?err=Refresh+fail:+{e}")

@app.post("/accounts/delete/{account_id}")
def account_delete(account_id: int):
    c = db()
    n = c.execute("SELECT COUNT(*) AS n FROM chunks WHERE account_id=?",
                  (account_id,)).fetchone()["n"]
    if n:
        c.close()
        return RedirectResponse("/?err=Ei+account-e+file+er+tukra+ache,+age+delete+kora+jabe+na")
    acc = c.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    if acc and acc["provider"] == "local":
        shutil.rmtree(LOCAL_ROOT / acc["folder"], ignore_errors=True)
    c.execute("DELETE FROM accounts WHERE id=?", (account_id,))
    c.commit()
    c.close()
    return RedirectResponse("/?msg=Account+bad+deya+hoiye+geche")

@app.post("/demo/setup")
def demo_setup():
    """TEST MODE: 5 local accounts (15 GB quota each, simulated)."""
    c = db()
    existing = c.execute("SELECT COUNT(*) AS n FROM accounts WHERE provider='local'").fetchone()["n"]
    if existing:
        c.close()
        return RedirectResponse("/?msg=Test+account+agei+ache")
    for i in range(1, 6):
        folder = f"demo{i}"
        (LOCAL_ROOT / folder).mkdir(exist_ok=True)
        c.execute("INSERT INTO accounts(email, provider, folder, total_bytes, used_bytes, status, created_at) VALUES(?, 'local', ?, ?, 0, 'active', ?)",
                  (f"test{i}@local.demo", folder, GB15, time.time()))
    c.commit()
    c.close()
    return RedirectResponse("/?msg=5+ta+TEST+account+tori.+Ekhon+upload+test+korte+parba")

@app.post("/settings")
def settings(chunk_size_mb: int = Form(...)):
    if chunk_size_mb not in (100, 200):
        return RedirectResponse("/?err=Chunk+size+sudhu+100+ba+200+MB+hote+pare")
    set_setting("chunk_size_mb", str(chunk_size_mb))
    return RedirectResponse(f"/?msg=Chunk+size+{chunk_size_mb}+MB+set+hoiye+geche")

# ---------------------------------------------------------------- upload
@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    accounts = active_accounts()
    if len(accounts) < MIN_ACCOUNTS:
        return RedirectResponse(
            f"/?err=Upload+er+age+minimum+{MIN_ACCOUNTS}+ta+account+connect+lagbe+(ekhon+ache+{len(accounts)}+ta)")
    chunk_size = int(get_setting("chunk_size_mb", "100")) * 1024 * 1024
    tmp = Path(tempfile.mkstemp(dir=TMPD)[1])
    sha = hashlib.sha256()
    size = 0
    with open(tmp, "wb") as out:
        while True:
            data = await file.read(1024 * 1024)
            if not data:
                break
            sha.update(data)
            out.write(data)
            size += len(data)
    digest = sha.hexdigest()
    c = db()
    dup = c.execute("SELECT id FROM files WHERE sha256=?", (digest,)).fetchone()
    if dup:
        c.close()
        tmp.unlink(missing_ok=True)
        return RedirectResponse("/?err=Ei+file+ta+agei+upload+ache+(duplicate+atka+hoiye+che)")
    n_chunks = max(1, -(-size // chunk_size))
    sizes = [chunk_size] * (n_chunks - 1) + [size - chunk_size * (n_chunks - 1)]
    chosen = pick_accounts(sizes)
    if chosen is None:
        c.close()
        tmp.unlink(missing_ok=True)
        return RedirectResponse("/?err=Pool-e+jayga+nai+ba+account+bachai+fail")
    cur = c.execute(
        "INSERT INTO files(name, mime_type, size, sha256, chunk_size, created_at) VALUES(?,?,?,?,?,?)",
        (file.filename, file.content_type or "application/octet-stream",
         size, digest, chunk_size, time.time()))
    file_id = cur.lastrowid
    c.commit()
    uploaded = []
    try:
        with open(tmp, "rb") as src:
            for i, (acc, csize) in enumerate(zip(chosen, sizes), start=1):
                chunk_tmp = Path(tempfile.mkstemp(dir=TMPD)[1])
                with open(chunk_tmp, "wb") as cf:
                    shutil.copyfileobj(io.BytesIO(src.read(csize)), cf)
                name = f"file{file_id}_part{i:04d}.chunk"
                drive_id = provider_upload(acc, chunk_tmp, name)
                chunk_tmp.unlink(missing_ok=True)
                start = (i - 1) * chunk_size
                cur = c.execute(
                    "INSERT INTO chunks(file_id, part_no, account_id, drive_file_id, size, start_byte, end_byte) VALUES(?,?,?,?,?,?,?)",
                    (file_id, i, acc["id"], drive_id, csize, start, start + csize - 1))
                c.execute("UPDATE accounts SET used_bytes=used_bytes+? WHERE id=?",
                          (csize, acc["id"]))
                c.commit()
                uploaded.append((acc, drive_id))
    except Exception as e:
        for acc, drive_id in uploaded:
            provider_delete(acc, drive_id)
        c.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
        c.execute("DELETE FROM files WHERE id=?", (file_id,))
        c.commit()
        c.close()
        tmp.unlink(missing_ok=True)
        return RedirectResponse(f"/?err=Upload+fail:+{e}")
    c.close()
    tmp.unlink(missing_ok=True)
    return RedirectResponse(
        f"/?msg={file.filename}+upload+hoiye+geche+({n_chunks}+ta+tukra,+{len(set(a['id'] for a in chosen))}+ta+account-e)")

# ---------------------------------------------------------------- download / stream
def file_and_chunks(file_id):
    c = db()
    f = c.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    chunks = c.execute("SELECT * FROM chunks WHERE file_id=? ORDER BY part_no",
                       (file_id,)).fetchall()
    accounts = {a["id"]: a for a in c.execute("SELECT * FROM accounts").fetchall()}
    c.close()
    return f, chunks, accounts

def iter_range(chunks, accounts, start, end):
    """Yield bytes for [start, end] inclusive, fetching only needed chunks."""
    for ch in chunks:
        if ch["end_byte"] < start or ch["start_byte"] > end:
            continue
        path = chunk_local_path(accounts[ch["account_id"]], ch)
        s = max(start, ch["start_byte"]) - ch["start_byte"]
        e = min(end, ch["end_byte"]) - ch["start_byte"]
        with open(path, "rb") as fh:
            fh.seek(s)
            remaining = e - s + 1
            while remaining > 0:
                data = fh.read(min(1024 * 1024, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

@app.get("/files/{file_id}/download")
def download(file_id: int):
    f, chunks, accounts = file_and_chunks(file_id)
    if not f:
        return JSONResponse({"error": "file not found"}, status_code=404)
    return StreamingResponse(
        iter_range(chunks, accounts, 0, f["size"] - 1),
        media_type=f["mime_type"],
        headers={"Content-Disposition": f'attachment; filename="{f["name"]}"',
                 "Content-Length": str(f["size"])})

@app.get("/files/{file_id}/stream")
def stream(file_id: int, request: Request):
    f, chunks, accounts = file_and_chunks(file_id)
    if not f:
        return JSONResponse({"error": "file not found"}, status_code=404)
    range_header = request.headers.get("range")
    headers = {"Accept-Ranges": "bytes", "Content-Type": f["mime_type"]}
    if range_header and range_header.startswith("bytes="):
        spec = range_header[6:].split("-")
        start = int(spec[0]) if spec[0] else 0
        end = int(spec[1]) if len(spec) > 1 and spec[1] else f["size"] - 1
        end = min(end, f["size"] - 1)
        headers["Content-Range"] = f"bytes {start}-{end}/{f['size']}"
        headers["Content-Length"] = str(end - start + 1)
        return StreamingResponse(iter_range(chunks, accounts, start, end),
                                 status_code=206, headers=headers)
    headers["Content-Length"] = str(f["size"])
    return StreamingResponse(iter_range(chunks, accounts, 0, f["size"] - 1),
                             headers=headers)

@app.post("/files/{file_id}/delete")
def file_delete(file_id: int):
    f, chunks, accounts = file_and_chunks(file_id)
    if not f:
        return RedirectResponse("/?err=File+pawa+jayni")
    c = db()
    for ch in chunks:
        provider_delete(accounts[ch["account_id"]], ch["drive_file_id"])
        cached = CACHE / f"chunk_{ch['id']}"
        cached.unlink(missing_ok=True)
        c.execute("UPDATE accounts SET used_bytes=MAX(0, used_bytes-?) WHERE id=?",
                  (ch["size"], ch["account_id"]))
    c.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
    c.execute("DELETE FROM files WHERE id=?", (file_id,))
    c.commit()
    c.close()
    return RedirectResponse(f"/?msg={f['name']}+delete+hoiye+geche")
