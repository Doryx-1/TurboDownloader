"""
remote_server.py — Remote control server/client for TurboDownloader.

Architecture:
  • Server mode : FastAPI HTTPS server (uvicorn) running in a daemon thread.
                  Exposes a REST API + WebSocket feed so any authorised client
                  (browser, script, or another TurboDownloader instance) can
                  monitor and control downloads.

  • Client mode : thin wrapper around httpx that talks to a remote server.
                  Used by TurboDownloader when "Connect to remote" is enabled
                  in settings.

Security:
  • HTTPS with a self-signed certificate generated on first run
    (stored in ~/.turbodownloader/ssl/).
  • Login / password stored as a bcrypt hash in settings.json.
  • Session tokens are signed JWT (HS256), expire after TOKEN_TTL_H hours.

Dependencies (all optional — graceful fallback if missing):
    pip install fastapi uvicorn[standard] python-jose[cryptography] bcrypt httpx
"""

from __future__ import annotations

import json
import ssl
import hashlib
import pathlib
import sys
import threading
import time
import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass   # avoid circular imports — TurboDownloader typed as Any below

# ── Config paths ──────────────────────────────────────────────────────────────

CONFIG_DIR = pathlib.Path.home() / ".turbodownloader"
SSL_DIR    = CONFIG_DIR / "ssl"
CERT_FILE  = SSL_DIR / "cert.pem"
KEY_FILE   = SSL_DIR / "key.pem"

TOKEN_TTL_H  = 24          # JWT validity in hours
DEFAULT_PORT = 9988

# ── Dependency check ──────────────────────────────────────────────────────────

def _check_deps() -> tuple[bool, list[str]]:
    """Returns (all_ok, missing_packages)."""
    missing = []
    for pkg, import_name in [
        ("fastapi",                "fastapi"),
        ("uvicorn",                "uvicorn"),
        ("python-jose[cryptography]", "jose"),
        ("bcrypt",                 "bcrypt"),
        ("httpx",                  "httpx"),
    ]:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg)
    return (len(missing) == 0, missing)


DEPS_OK, DEPS_MISSING = _check_deps()


# ── SSL certificate helpers ───────────────────────────────────────────────────

def _generate_self_signed_cert() -> bool:
    """
    Generates a self-signed TLS certificate using the stdlib 'ssl' module trick
    via subprocess + openssl (if available), or cryptography package.
    Saves cert.pem and key.pem into SSL_DIR.
    Returns True on success.
    """
    SSL_DIR.mkdir(parents=True, exist_ok=True)

    # Try cryptography package first (cleanest)
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import ipaddress

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "TurboDownloader"),
        ])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
            .add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                ]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        KEY_FILE.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
        print("[remote] Self-signed cert generated via cryptography ✓")
        return True

    except ImportError:
        pass

    # Fallback: openssl CLI
    import subprocess, shutil
    openssl = shutil.which("openssl")
    if openssl:
        try:
            subprocess.run([
                openssl, "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(KEY_FILE),
                "-out",    str(CERT_FILE),
                "-days",   "3650",
                "-nodes",
                "-subj",   "/CN=TurboDownloader",
            ], check=True, capture_output=True, timeout=30)
            print("[remote] Self-signed cert generated via openssl CLI ✓")
            return True
        except Exception as e:
            print(f"[remote] openssl CLI failed: {e}")

    print("[remote] Could not generate SSL cert — install 'cryptography' package")
    return False


def ensure_ssl_cert() -> bool:
    """Makes sure cert+key exist. Generates them if not. Returns True if ready."""
    if CERT_FILE.exists() and KEY_FILE.exists():
        return True
    return _generate_self_signed_cert()


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Returns a bcrypt hash of the password (or SHA-256 fallback)."""
    try:
        import bcrypt
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        # Fallback — less secure but avoids hard crash
        return "sha256:" + hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    """Verifies a password against a stored hash."""
    try:
        import bcrypt
        if hashed.startswith("sha256:"):
            # Migration path — bcrypt now available
            return ("sha256:" + hashlib.sha256(password.encode()).hexdigest()) == hashed
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ImportError:
        return ("sha256:" + hashlib.sha256(password.encode()).hexdigest()) == hashed


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _jwt_secret(settings: dict) -> str:
    """Returns (or creates) the JWT signing secret stored in settings."""
    if not settings.get("remote_jwt_secret"):
        import secrets
        settings["remote_jwt_secret"] = secrets.token_hex(32)
    return settings["remote_jwt_secret"]


def create_token(settings: dict) -> str:
    from jose import jwt
    secret = _jwt_secret(settings)
    payload = {
        "sub": "turbodownloader",
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=TOKEN_TTL_H),
        "iat": datetime.datetime.utcnow(),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_token(token: str, settings: dict) -> bool:
    try:
        from jose import jwt, JWTError
        secret = _jwt_secret(settings)
        jwt.decode(token, secret, algorithms=["HS256"])
        return True
    except Exception:
        return False


# ═════════════════════════════════════════════════════════════════════════════
# SERVER
# ═════════════════════════════════════════════════════════════════════════════

class RemoteServer:
    """
    Runs a FastAPI HTTPS server in a background daemon thread.
    The 'app_ref' is a weak reference to the TurboDownloader instance so we
    can call its methods to inspect and control downloads.
    """

    def __init__(self, app_ref, settings: dict):
        self._app_ref  = app_ref     # TurboDownloader instance
        self._settings = settings
        self._thread: Optional[threading.Thread] = None
        self._server   = None        # uvicorn Server instance
        self._running  = False

    # ---------------------------------------------------------------- Start/Stop

    def start(self) -> bool:
        """Starts the server. Returns True if started successfully."""
        if self._running:
            return True

        if not DEPS_OK:
            print(f"[remote] Cannot start server — missing packages: {DEPS_MISSING}")
            return False

        if not ensure_ssl_cert():
            print("[remote] Cannot start server — SSL cert unavailable")
            return False

        port = int(self._settings.get("remote_port", DEFAULT_PORT))

        try:
            fastapi_app = self._build_fastapi_app()
            import uvicorn

            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))

            config = uvicorn.Config(
                fastapi_app,
                host="0.0.0.0",
                port=port,
                ssl_certfile=str(CERT_FILE),
                ssl_keyfile=str(KEY_FILE),
                log_level="warning",
                loop="asyncio",
            )
            self._server = uvicorn.Server(config)
            self._thread = threading.Thread(
                target=self._server.run,
                daemon=True,
                name="TurboRemoteServer",
            )
            self._thread.start()
            self._running = True
            print(f"[remote] Server started on https://0.0.0.0:{port}")
            return True

        except Exception as e:
            print(f"[remote] Start error: {e}")
            return False

    def stop(self):
        """Gracefully shuts down the server."""
        if self._server and self._running:
            self._server.should_exit = True
            self._running = False
            print("[remote] Server stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ---------------------------------------------------------------- FastAPI app

    def _build_fastapi_app(self):
        from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect
        from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
        from fastapi.middleware.cors import CORSMiddleware
        from pydantic import BaseModel

        api = FastAPI(title="TurboDownloader Remote API", version="1.0")

        api.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        bearer = HTTPBearer(auto_error=False)
        settings = self._settings
        app_ref  = self._app_ref

        # ── Auth dependency ───────────────────────────────────────────────────

        def require_auth(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
            if not credentials or not verify_token(credentials.credentials, settings):
                raise HTTPException(status_code=401, detail="Invalid or expired token")
            return True

        # ── Pydantic models ───────────────────────────────────────────────────

        class LoginRequest(BaseModel):
            username: str
            password: str

        class AddURLRequest(BaseModel):
            url: str
            dest: Optional[str] = None

        # ── Helper: serialise a DownloadItem for the API ──────────────────────

        def _item_dict(idx: int) -> dict:
            it = app_ref.items.get(idx)
            if it is None:
                return {}
            total = it.total_size or 0
            pct   = round(it.downloaded / total * 100, 1) if total else 0
            # Compute per-item speed from speed_window
            now    = time.time()
            window = [(t, b) for t, b in it.speed_window if now - t <= 3]
            speed  = sum(b for _, b in window) / 3 if window else 0
            return {
                "idx":       idx,
                "filename":  it.filename,
                "url":       it.url,
                "state":     it.state,
                "progress":  pct,
                "downloaded": it.downloaded,
                "total":     total,
                "speed_bps": int(speed),
                "error":     it.error_msg,
            }

        # ── Endpoints ─────────────────────────────────────────────────────────

        @api.post("/auth/login")
        def login(req: LoginRequest):
            stored_user = settings.get("remote_username", "")
            stored_hash = settings.get("remote_password_hash", "")
            if req.username != stored_user or not verify_password(req.password, stored_hash):
                raise HTTPException(status_code=401, detail="Bad credentials")
            return {"token": create_token(settings), "expires_in_h": TOKEN_TTL_H}

        @api.get("/status")
        def get_status(_ = Depends(require_auth)):
            items_list = [_item_dict(i) for i in sorted(app_ref.items.keys())]
            # Global speed
            now = time.time()
            with app_ref._speed_lock:
                samples = [(t, b) for t, b in app_ref._speed_samples if now - t <= 3]
            global_speed = sum(b for _, b in samples) / 3 if samples else 0
            counts = {}
            for it in app_ref.items.values():
                counts[it.state] = counts.get(it.state, 0) + 1
            return {
                "global_speed_bps": int(global_speed),
                "counts":           counts,
                "downloads":        items_list,
            }

        @api.get("/downloads/{idx}")
        def get_download(idx: int, _ = Depends(require_auth)):
            d = _item_dict(idx)
            if not d:
                raise HTTPException(status_code=404, detail="Not found")
            return d

        @api.post("/downloads/add")
        def add_download(req: AddURLRequest, _ = Depends(require_auth)):
            """Injects a URL exactly as if the user had pasted it."""
            def _inject():
                app_ref.url_box.delete("1.0", "end")
                app_ref.url_box.insert("end", req.url)
                app_ref._on_start()
            app_ref.after(0, _inject)
            return {"status": "queued", "url": req.url}

        @api.post("/downloads/{idx}/pause")
        def pause_download(idx: int, _ = Depends(require_auth)):
            it = app_ref.items.get(idx)
            if not it:
                raise HTTPException(status_code=404, detail="Not found")
            app_ref.after(0, lambda: app_ref._pause_item(idx))
            return {"status": "pausing", "idx": idx}

        @api.post("/downloads/{idx}/resume")
        def resume_download(idx: int, _ = Depends(require_auth)):
            it = app_ref.items.get(idx)
            if not it:
                raise HTTPException(status_code=404, detail="Not found")
            app_ref.after(0, lambda: app_ref._resume_item(idx))
            return {"status": "resuming", "idx": idx}

        @api.post("/downloads/{idx}/cancel")
        def cancel_download(idx: int, _ = Depends(require_auth)):
            it = app_ref.items.get(idx)
            if not it:
                raise HTTPException(status_code=404, detail="Not found")
            app_ref.after(0, lambda: app_ref._cancel_item(idx))
            return {"status": "canceling", "idx": idx}

        @api.post("/downloads/stop_all")
        def stop_all(_ = Depends(require_auth)):
            app_ref.after(0, app_ref.stop_all)
            return {"status": "stopping_all"}

        @api.get("/history")
        def get_history(_ = Depends(require_auth)):
            return {"entries": app_ref._history.get_entries()}

        # ── WebSocket live feed ───────────────────────────────────────────────

        @api.websocket("/ws")
        async def websocket_feed(ws: WebSocket, token: str = ""):
            """
            Real-time progression feed.
            Client must pass ?token=<jwt> in the query string.
            Sends a JSON snapshot every second while connected.
            """
            import asyncio

            if not verify_token(token, settings):
                await ws.close(code=4001, reason="Unauthorized")
                return

            await ws.accept()
            try:
                while True:
                    now     = time.time()
                    payload = {
                        "ts": now,
                        "downloads": [
                            _item_dict(i) for i in sorted(app_ref.items.keys())
                        ],
                    }
                    await ws.send_json(payload)
                    await asyncio.sleep(1)
            except WebSocketDisconnect:
                pass
            except Exception as e:
                print(f"[remote] WebSocket error: {e}")

        return api


# ═════════════════════════════════════════════════════════════════════════════
# CLIENT
# ═════════════════════════════════════════════════════════════════════════════

class RemoteClient:
    """
    Thin HTTPS client for connecting to a remote TurboDownloader server.
    Uses httpx with SSL verification disabled (self-signed cert).
    """

    def __init__(self, host: str, port: int, username: str, password: str):
        self._base    = f"https://{host}:{port}"
        self._user    = username
        self._pass    = password
        self._token   = ""
        self._headers = {}
        self._ok      = False

        try:
            import httpx
            self._httpx = httpx
        except ImportError:
            self._httpx = None
            print("[remote-client] httpx not installed — pip install httpx")

    def connect(self) -> tuple[bool, str]:
        """
        Authenticates against the remote server.
        Returns (success, message).
        """
        if not self._httpx:
            return False, "httpx package missing"

        try:
            r = self._httpx.post(
                f"{self._base}/auth/login",
                json={"username": self._user, "password": self._pass},
                verify=False,   # self-signed cert
                timeout=8,
            )
            if r.status_code == 200:
                self._token   = r.json()["token"]
                self._headers = {"Authorization": f"Bearer {self._token}"}
                self._ok      = True
                return True, "Connected"
            return False, f"Auth failed ({r.status_code}): {r.text[:100]}"
        except Exception as e:
            return False, f"Connection error: {e}"

    @property
    def connected(self) -> bool:
        return self._ok

    def get_status(self) -> Optional[dict]:
        return self._get("/status")

    def add_url(self, url: str, dest: Optional[str] = None) -> Optional[dict]:
        return self._post("/downloads/add", {"url": url, "dest": dest})

    def pause(self, idx: int) -> Optional[dict]:
        return self._post(f"/downloads/{idx}/pause")

    def resume(self, idx: int) -> Optional[dict]:
        return self._post(f"/downloads/{idx}/resume")

    def cancel(self, idx: int) -> Optional[dict]:
        return self._post(f"/downloads/{idx}/cancel")

    def stop_all(self) -> Optional[dict]:
        return self._post("/downloads/stop_all")

    def get_history(self) -> Optional[dict]:
        return self._get("/history")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get(self, path: str) -> Optional[dict]:
        if not self._ok or not self._httpx:
            return None
        try:
            r = self._httpx.get(
                f"{self._base}{path}",
                headers=self._headers,
                verify=False,
                timeout=10,
            )
            return r.json() if r.status_code == 200 else None
        except Exception as e:
            print(f"[remote-client] GET {path} error: {e}")
            return None

    def _post(self, path: str, body: Optional[dict] = None) -> Optional[dict]:
        if not self._ok or not self._httpx:
            return None
        try:
            r = self._httpx.post(
                f"{self._base}{path}",
                json=body or {},
                headers=self._headers,
                verify=False,
                timeout=10,
            )
            return r.json() if r.status_code == 200 else None
        except Exception as e:
            print(f"[remote-client] POST {path} error: {e}")
            return None


# ═════════════════════════════════════════════════════════════════════════════
# REMOTE CONTROL POPUP  (connect to a remote TurboDownloader instance)
# ═════════════════════════════════════════════════════════════════════════════

def open_remote_control_popup(master, settings: dict, on_add_url):
    """
    Opens a Toplevel window that lets the user connect to a remote server
    and monitor / control its downloads.
    """
    try:
        import customtkinter as ctk
    except ImportError:
        return

    win = ctk.CTkToplevel(master)
    win.title("Remote Control")
    win.geometry("780x560")
    win.resizable(True, True)
    win.grab_set()

    client: list[Optional[RemoteClient]] = [None]   # mutable container

    # ── Connection bar ────────────────────────────────────────────────────────
    top = ctk.CTkFrame(win, fg_color="#1e1e1e")
    top.pack(fill="x", padx=0, pady=0)

    ctk.CTkLabel(top, text="Host:").pack(side="left", padx=(16, 4), pady=10)
    host_e = ctk.CTkEntry(top, width=160,
                           placeholder_text=settings.get("remote_client_host", "192.168.1.x"))
    host_e.insert(0, settings.get("remote_client_host", ""))
    host_e.pack(side="left", padx=(0, 8), pady=10)

    ctk.CTkLabel(top, text="Port:").pack(side="left", padx=(0, 4))
    port_e = ctk.CTkEntry(top, width=70,
                           placeholder_text=str(DEFAULT_PORT))
    port_e.insert(0, str(settings.get("remote_client_port", DEFAULT_PORT)))
    port_e.pack(side="left", padx=(0, 8), pady=10)

    ctk.CTkLabel(top, text="User:").pack(side="left", padx=(0, 4))
    user_e = ctk.CTkEntry(top, width=100)
    user_e.insert(0, settings.get("remote_client_user", ""))
    user_e.pack(side="left", padx=(0, 8), pady=10)

    ctk.CTkLabel(top, text="Password:").pack(side="left", padx=(0, 4))
    pass_e = ctk.CTkEntry(top, width=120, show="•")
    pass_e.pack(side="left", padx=(0, 8), pady=10)

    status_lbl = ctk.CTkLabel(top, text="⚫ Disconnected", text_color="#888888",
                               font=ctk.CTkFont(size=11))
    status_lbl.pack(side="right", padx=16)

    connect_btn = ctk.CTkButton(top, text="Connect", width=100, fg_color="#1f6aa5")
    connect_btn.pack(side="right", padx=(0, 8), pady=10)

    # ── Downloads table ───────────────────────────────────────────────────────
    list_frame = ctk.CTkScrollableFrame(win, fg_color="#181818")
    list_frame.pack(fill="both", expand=True, padx=0, pady=(4, 0))

    # ── Bottom action bar ─────────────────────────────────────────────────────
    bot = ctk.CTkFrame(win, fg_color="#2b2b2b")
    bot.pack(fill="x", padx=0, pady=0)

    add_url_e = ctk.CTkEntry(bot, placeholder_text="Add URL on remote…")
    add_url_e.pack(side="left", fill="x", expand=True, padx=(16, 8), pady=10)

    ctk.CTkButton(bot, text="⬇  Send", width=100, fg_color="#2e8b57",
                  command=lambda: _remote_add()).pack(side="left", padx=(0, 8))
    ctk.CTkButton(bot, text="⏹  Stop all", width=110, fg_color="#8B0000",
                  command=lambda: _remote_stop_all()).pack(side="left", padx=(0, 8))
    ctk.CTkButton(bot, text="Close", width=90, fg_color="#5a5a5a",
                  command=win.destroy).pack(side="right", padx=16)

    # ── Polling loop ──────────────────────────────────────────────────────────
    _poll_running = [True]

    def _render_downloads(downloads: list):
        for w in list_frame.winfo_children():
            w.destroy()
        if not downloads:
            ctk.CTkLabel(list_frame, text="No downloads on remote.",
                         text_color="#666666").pack(pady=20)
            return
        for d in downloads:
            row = ctk.CTkFrame(list_frame, fg_color="#222222",
                               corner_radius=6, border_width=1,
                               border_color="#2e2e2e")
            row.pack(fill="x", padx=8, pady=3)

            top_r = ctk.CTkFrame(row, fg_color="transparent")
            top_r.pack(fill="x", padx=10, pady=(6, 2))

            name = d.get("filename", "?")
            if len(name) > 48:
                name = name[:45] + "…"
            ctk.CTkLabel(top_r, text=name,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         anchor="w", text_color="#dddddd").pack(side="left")

            state = d.get("state", "?")
            state_colors = {
                "downloading": "#2e8b57", "done": "#2e6b3e",
                "error": "#8B0000", "paused": "#5a7a9a",
                "waiting": "#666666", "canceled": "#555555",
            }
            ctk.CTkLabel(top_r, text=state,
                         text_color=state_colors.get(state, "#888888"),
                         font=ctk.CTkFont(size=11)).pack(side="right")

            # Progress bar
            pct = d.get("progress", 0) / 100
            bar = ctk.CTkProgressBar(row, height=5, corner_radius=2,
                                     progress_color="#1f6aa5", fg_color="#2a2a2a")
            bar.set(pct)
            bar.pack(fill="x", padx=10, pady=(0, 3))

            # Speed + buttons
            bot_r = ctk.CTkFrame(row, fg_color="transparent")
            bot_r.pack(fill="x", padx=10, pady=(0, 6))

            spd = d.get("speed_bps", 0)
            spd_txt = _fmt_speed(spd)
            ctk.CTkLabel(bot_r, text=spd_txt, text_color="#555555",
                         font=ctk.CTkFont(size=11)).pack(side="left")
            ctk.CTkLabel(bot_r, text=f"{d.get('progress',0):.1f}%",
                         text_color="#555555",
                         font=ctk.CTkFont(size=11)).pack(side="left", padx=(10, 0))

            idx = d["idx"]
            if state in ("downloading", "waiting"):
                ctk.CTkButton(bot_r, text="⏸", width=32, height=24,
                              fg_color="transparent", border_width=1,
                              border_color="#3a3a3a",
                              command=lambda i=idx: _remote_pause(i)).pack(side="right", padx=2)
            if state == "paused":
                ctk.CTkButton(bot_r, text="▶", width=32, height=24,
                              fg_color="transparent", border_width=1,
                              border_color="#3a3a3a",
                              command=lambda i=idx: _remote_resume(i)).pack(side="right", padx=2)
            if state not in ("done", "canceled", "error"):
                ctk.CTkButton(bot_r, text="✕", width=32, height=24,
                              fg_color="transparent", border_width=1,
                              border_color="#5a1515", text_color="#cc4444",
                              command=lambda i=idx: _remote_cancel(i)).pack(side="right", padx=2)

    def _poll():
        if not _poll_running[0] or not client[0] or not client[0].connected:
            return
        data = client[0].get_status()
        if data:
            win.after(0, _render_downloads, data.get("downloads", []))
        win.after(2000, _poll)

    def _do_connect():
        host = host_e.get().strip()
        port = int(port_e.get().strip() or str(DEFAULT_PORT))
        user = user_e.get().strip()
        pwd  = pass_e.get()
        if not host or not user or not pwd:
            status_lbl.configure(text="⚠ Fill all fields", text_color="#f0a500")
            return
        status_lbl.configure(text="⏳ Connecting…", text_color="#888888")
        win.update()

        c = RemoteClient(host, port, user, pwd)
        ok, msg = c.connect()
        if ok:
            client[0] = c
            settings["remote_client_host"] = host
            settings["remote_client_port"] = port
            settings["remote_client_user"] = user
            connect_btn.configure(text="Disconnect", fg_color="#5a5a5a",
                                  command=_do_disconnect)
            status_lbl.configure(text="🟢 Connected", text_color="#2e8b57")
            win.after(200, _poll)
        else:
            status_lbl.configure(text=f"🔴 {msg[:60]}", text_color="#cc4444")

    def _do_disconnect():
        client[0] = None
        connect_btn.configure(text="Connect", fg_color="#1f6aa5",
                              command=_do_connect)
        status_lbl.configure(text="⚫ Disconnected", text_color="#888888")
        for w in list_frame.winfo_children():
            w.destroy()

    def _remote_add():
        url = add_url_e.get().strip()
        if not url or not client[0]:
            return
        client[0].add_url(url)
        add_url_e.delete(0, "end")

    def _remote_pause(idx):
        if client[0]:
            client[0].pause(idx)

    def _remote_resume(idx):
        if client[0]:
            client[0].resume(idx)

    def _remote_cancel(idx):
        if client[0]:
            client[0].cancel(idx)

    def _remote_stop_all():
        if client[0]:
            client[0].stop_all()

    connect_btn.configure(command=_do_connect)

    def _on_close():
        _poll_running[0] = False
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", _on_close)


# ── Format helpers ────────────────────────────────────────────────────────────

def _fmt_speed(bps: int) -> str:
    if bps <= 0:
        return "–"
    if bps < 1024:
        return f"{bps} B/s"
    if bps < 1024 ** 2:
        return f"{bps/1024:.1f} KB/s"
    return f"{bps/1024**2:.2f} MB/s"
