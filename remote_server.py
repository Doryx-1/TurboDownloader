"""
remote_server.py — Remote control server/client for TurboDownloader.

Architecture:
  Server mode : FastAPI HTTPS server (uvicorn) in a daemon thread.
  Client mode : httpx wrapper to talk to a remote server.

Security: HTTPS self-signed cert, bcrypt passwords, JWT HS256 tokens.

NOTE: do NOT add 'from __future__ import annotations' —
it breaks Pydantic v2 model resolution (ForwardRef issue with Python 3.14).
"""

import json
import ssl
import hashlib
import pathlib
import sys
import threading
import time
import datetime
from typing import Optional, TYPE_CHECKING
from logger import get_logger

_log = get_logger("remote_server")

if TYPE_CHECKING:
    pass   # avoid circular imports — TurboDownloader typed as Any below

# ── Config paths ──────────────────────────────────────────────────────────────

CONFIG_DIR = pathlib.Path.home() / ".turbodownloader"
SSL_DIR    = CONFIG_DIR / "ssl"
CERT_FILE  = SSL_DIR / "cert.pem"
KEY_FILE   = SSL_DIR / "key.pem"

TOKEN_TTL_H  = 24          # JWT validity in hours (default — overridable in settings)
TOKEN_TTL_OPTIONS = [1, 8, 24, 168]  # 1h / 8h / 24h / 7 days
DEFAULT_PORT = 9988

# ── Pydantic models — defined at module level (required for Pydantic v2 + Python 3.14) ──

try:
    from pydantic import BaseModel

    class LoginRequest(BaseModel):
        username: str
        password: str
        model_config = {"extra": "ignore"}

    class AddURLRequest(BaseModel):
        url:         str
        dest:        Optional[str]  = None
        worker_type: Optional[str]  = None   # "http" | "ytdlp" — auto-detect if None
        format_id:   Optional[str]  = None   # yt-dlp format id
        audio_only:  bool           = False  # yt-dlp audio-only mode
        model_config = {"extra": "ignore"}

    class TriggerUpdateRequest(BaseModel):
        username: Optional[str] = None
        password: Optional[str] = None
        model_config = {"extra": "ignore"}

except ImportError:
    LoginRequest  = None   # type: ignore
    AddURLRequest = None   # type: ignore

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
    """Returns a bcrypt hash of the password. Raises RuntimeError if bcrypt is unavailable."""
    import bcrypt
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Verifies a password against a stored hash.
    Supports sha256: prefix as a one-time migration path (no new sha256 hashes are created)."""
    import bcrypt
    if hashed.startswith("sha256:"):
        # Migration path only — user must reset password to upgrade to bcrypt
        _log.warning("Password stored as unsalted SHA-256 — user should reset their password")
        return ("sha256:" + hashlib.sha256(password.encode()).hexdigest()) == hashed
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ── URL validation ────────────────────────────────────────────────────────────

def _validate_urls(raw: str) -> tuple[list[str], str | None]:
    """Validates URL schemes — only http/https accepted."""
    from urllib.parse import urlparse as _up
    urls = [u.strip() for u in raw.strip().splitlines() if u.strip()]
    if not urls:
        return [], "No URLs provided"
    bad = [u for u in urls if _up(u).scheme not in ("http", "https")]
    if bad:
        return [], f"Invalid URL scheme (only http/https allowed): {bad[0][:80]}"
    return urls, None


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _jwt_secret(settings: dict) -> str:
    """Returns (or creates) the JWT signing secret stored in settings."""
    if not settings.get("remote_jwt_secret"):
        import secrets
        settings["remote_jwt_secret"] = secrets.token_hex(32)
    return settings["remote_jwt_secret"]


def create_token(settings: dict) -> str:
    from jose import jwt
    secret  = _jwt_secret(settings)
    ttl     = int(settings.get("remote_jwt_ttl_h", TOKEN_TTL_H))
    if ttl not in TOKEN_TTL_OPTIONS:
        ttl = TOKEN_TTL_H
    payload = {
        "sub": "turbodownloader",
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=ttl),
        "iat": datetime.datetime.utcnow(),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def get_token_ttl(settings: dict) -> int:
    """Returns the configured JWT TTL in hours."""
    ttl = int(settings.get("remote_jwt_ttl_h", TOKEN_TTL_H))
    return ttl if ttl in TOKEN_TTL_OPTIONS else TOKEN_TTL_H


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

# ── Local extension token ─────────────────────────────────────────────────────

import tempfile as _tempfile
import secrets as _secrets

_LOCAL_TOKEN_FILE = pathlib.Path(_tempfile.gettempdir()) / "turbodownloader.token"
_LOCAL_TOKEN_TTL  = 3600   # 1 hour in seconds


def generate_local_token() -> str:
    """Generates a new local token and writes it to a temp file with a timestamp."""
    token = _secrets.token_hex(32)
    try:
        data = {"token": token, "generated_at": time.time()}
        _LOCAL_TOKEN_FILE.write_text(json.dumps(data), encoding="utf-8")
        import sys as _sys
        if _sys.platform != "win32":
            import os as _os
            _os.chmod(_LOCAL_TOKEN_FILE, 0o600)
    except Exception as e:
        _log.warning("Could not write local token: %s", e)
    return token


def verify_local_token(token: str) -> bool:
    """Returns True if the token matches the stored local token and has not expired."""
    try:
        raw = _LOCAL_TOKEN_FILE.read_text(encoding="utf-8").strip()
        data = json.loads(raw)
        stored_token  = data.get("token", "")
        generated_at  = data.get("generated_at", 0)
        if time.time() - generated_at > _LOCAL_TOKEN_TTL:
            _log.debug("Local token expired")
            return False
        return bool(stored_token) and token == stored_token
    except Exception:
        return False


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

        # Generate local extension token
        self._local_token = generate_local_token()

        if not DEPS_OK:
            msg = f"Remote server cannot start.\nMissing packages: {DEPS_MISSING}\n\nDEPS_OK={DEPS_OK}"
            _log.error(msg)
            self._show_error(msg)
            return False

        port      = int(self._settings.get("remote_port", DEFAULT_PORT))
        use_ssl   = True    # HTTPS — self-signed cert (must be trusted once in browser)

        if use_ssl:
            ssl_ok = ensure_ssl_cert()
            if not ssl_ok:
                msg = (f"Remote server cannot start.\nSSL certificate generation failed.\n\n"
                       f"CERT_FILE={CERT_FILE}\nKEY_FILE={KEY_FILE}\n"
                       f"Exists: cert={CERT_FILE.exists()}, key={KEY_FILE.exists()}")
                _log.error(msg)
                self._show_error(msg)
                return False

        try:
            fastapi_app = self._build_fastapi_app()
            import uvicorn
            import sys as _sys
            import os  as _os

            # ── PyInstaller fix: stdout/stderr are None in windowed exe ──────
            if _sys.stdout is None:
                _sys.stdout = open(_os.devnull, "w")
            if _sys.stderr is None:
                _sys.stderr = open(_os.devnull, "w")

            UVICORN_LOG_CONFIG = {
                "version": 1,
                "disable_existing_loggers": False,
                "formatters": {},
                "handlers":   {},
                "loggers": {
                    "uvicorn":        {"handlers": [], "level": "WARNING"},
                    "uvicorn.error":  {"handlers": [], "level": "WARNING"},
                    "uvicorn.access": {"handlers": [], "level": "WARNING", "propagate": False},
                },
            }

            cfg_kwargs = dict(
                app=fastapi_app,
                host="0.0.0.0",
                port=port,
                log_config=UVICORN_LOG_CONFIG,
                loop="asyncio",
                workers=1,
                access_log=False,
            )
            if use_ssl:
                cfg_kwargs["ssl_certfile"] = str(CERT_FILE)
                cfg_kwargs["ssl_keyfile"]  = str(KEY_FILE)

            config           = uvicorn.Config(**cfg_kwargs)
            self._server     = uvicorn.Server(config)
            self._use_ssl    = use_ssl
            self._bound_port = port
            self._thread     = threading.Thread(
                target=self._server.run,
                daemon=True,
                name="TurboRemoteServer",
            )
            self._thread.start()
            self._running = True
            proto = "https" if use_ssl else "http"
            print(f"[remote] Server started on {proto}://0.0.0.0:{port}")
            return True

        except Exception as e:
            import traceback
            msg = f"Remote server start error:\n{e}\n\n{traceback.format_exc()}"
            _log.error(msg)
            self._show_error(msg)
            return False

    @staticmethod
    def _show_error(msg: str):
        """Shows an error dialog — works even without a Tk root window."""
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("TurboDownloader — Remote Server Error", msg)
            root.destroy()
        except Exception:
            pass  # last resort — already printed above

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
        from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect, Body, Request
        from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
        from fastapi.middleware.cors import CORSMiddleware

        settings = self._settings
        app_ref  = self._app_ref

        api = FastAPI(title="TurboDownloader Remote API", version="1.0")

        _port = settings.get("remote_port", 9988)
        api.add_middleware(
            CORSMiddleware,
            allow_origins=[
                f"http://localhost:{_port}",
                f"http://127.0.0.1:{_port}",
                "http://localhost",
                "http://127.0.0.1",
            ],
            allow_origin_regex=r"chrome-extension://[a-z]{32}|moz-extension://[0-9a-f-]{36}",
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            allow_credentials=False,
        )

        bearer   = HTTPBearer(auto_error=False)

        # ── Auth dependency ───────────────────────────────────────────────────

        def require_auth(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
            if not credentials or not verify_token(credentials.credentials, settings):
                raise HTTPException(status_code=401, detail="Invalid or expired token")
            return True

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

        @api.get("/local-token")
        def get_local_token(request: Request):
            """Returns local extension token — localhost only, no auth required."""
            client_ip = request.client.host if request.client else ""
            if client_ip not in ("127.0.0.1", "::1", "localhost"):
                raise HTTPException(status_code=403, detail="Local access only")
            return {"token": self._local_token, "ttl": _LOCAL_TOKEN_TTL}

        @api.post("/auth/local")
        def auth_local(request: Request, body: dict = Body(...)):
            """Exchanges local token for a JWT — localhost only."""
            client_ip = request.client.host if request.client else ""
            if client_ip not in ("127.0.0.1", "::1", "localhost"):
                raise HTTPException(status_code=403, detail="Local access only")
            if not verify_local_token(body.get("token", "")):
                raise HTTPException(status_code=401, detail="Invalid local token")
            return {"token": create_token(settings), "expires_in_h": get_token_ttl(settings)}

        @api.post("/auth/revoke")
        def revoke_tokens(_ = Depends(require_auth)):
            """
            Revokes all existing JWT tokens by regenerating the JWT secret.
            All currently issued tokens become invalid immediately.
            """
            import secrets as _sec
            settings["remote_jwt_secret"] = _sec.token_hex(32)
            from settings_popup import save_settings as _save
            _save(settings)
            return {"status": "revoked", "message": "All tokens invalidated — re-login required"}

        # Brute-force tracking per IP
        _fail_log: dict = {}
        _FAIL_WINDOW  = 60
        _FAIL_MAX     = 10
        _LOCKOUT_SECS = 30

        @api.post("/auth/login")
        def login(req: LoginRequest = Body(...), request: Request = None):
            now = time.time()
            ip  = request.client.host if request and request.client else "unknown"
            attempts = [ts for ts in _fail_log.get(ip, []) if now - ts < _FAIL_WINDOW]
            if len(attempts) >= _FAIL_MAX:
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many attempts — retry after {_LOCKOUT_SECS} seconds",
                    headers={"Retry-After": str(_LOCKOUT_SECS)},
                )
            stored_user = settings.get("remote_username", "")
            stored_hash = settings.get("remote_password_hash", "")
            if req.username != stored_user or not verify_password(req.password, stored_hash):
                attempts.append(now)
                _fail_log[ip] = attempts
                raise HTTPException(status_code=401, detail="Bad credentials")
            _fail_log.pop(ip, None)
            return {"token": create_token(settings), "expires_in_h": get_token_ttl(settings)}

        @api.get("/version")
        def get_version():
            """Public — no auth required. Returns the server's app version."""
            from updater import APP_VERSION
            return {"version": APP_VERSION}

        @api.post("/admin/trigger-update")
        def trigger_remote_update(request: Request,
                                  body: TriggerUpdateRequest = Body(default=TriggerUpdateRequest())):
            """
            Triggers a silent auto-update on the server.
            Accepts either a valid JWT token (Authorization header) or
            {username, password} credentials in the JSON body (for version-mismatch
            cases where the client has no token yet).
            """
            import threading as _th
            # Try token auth first
            auth_ok = False
            try:
                require_auth(request)
                auth_ok = True
            except Exception:
                pass

            if not auth_ok:
                # Fallback: credentials in JSON body (never in query params)
                uname = (body.username or "").strip()
                pwd   = (body.password or "").strip()
                stored_user = settings.get("remote_username", "")
                stored_hash = settings.get("remote_password_hash", "")
                if (uname and pwd and uname == stored_user
                        and verify_password(pwd, stored_hash)):
                    auth_ok = True

            if not auth_ok:
                raise HTTPException(status_code=401, detail="Unauthorized")

            from updater import fetch_latest_release, APP_VERSION, _is_newer
            release = fetch_latest_release()
            if not release:
                return {"status": "check_failed"}
            if not _is_newer(release["tag"], APP_VERSION):
                return {"status": "already_up_to_date"}
            url        = release.get("download_url")
            sha256_url = release.get("sha256_url")
            if not url:
                return {"status": "no_download_url"}

            def _do_update():
                from updater import _launch_download_and_install_silent
                _launch_download_and_install_silent(app_ref, url, sha256_url=sha256_url)
            _th.Thread(target=_do_update, daemon=True, name="RemoteUpdate").start()
            return {"status": "update_started"}

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
        def add_download(req: AddURLRequest = Body(...), _ = Depends(require_auth)):
            """
            Injects a URL directly into the download queue — no popup, no UI interaction.
            Auto-detects yt-dlp URLs. dest is the destination folder on the server machine.
            """
            urls, err = _validate_urls(req.url)
            if err:
                raise HTTPException(status_code=400, detail=err)
            def _inject():
                try:
                    workers = int(app_ref._settings.get("workers", 10))
                except Exception:
                    workers = 10

                dest = req.dest or app_ref._settings.get("default_dest", "")

                # Worker type — use explicit if provided, else auto-detect
                try:
                    import ytdlp_worker as _yw
                    if req.worker_type:
                        wtype = req.worker_type
                    else:
                        wtype = "ytdlp" if _yw.is_ytdlp_url(req.url) else "http"
                except ImportError:
                    wtype = req.worker_type or "http"

                # (url, rel_dir, worker_type, format_id, audio_only, dest, from_remote)
                entry = (req.url, "", wtype, req.format_id, req.audio_only, dest, True)
                app_ref._launch_downloads(
                    [entry],
                    workers=workers,
                    keep_tree=False,
                    dest_override=dest,
                )
                _log.info("Queued (%s): %s", wtype, req.url[:80])

            app_ref.after(0, _inject)
            return {"status": "queued", "url": req.url, "dest": req.dest, "type": "auto"}

        @api.post("/downloads/add_interactive")
        def add_interactive(req: AddURLRequest = Body(...), _ = Depends(require_auth)):
            """
            Injects one or more URLs into the url_box and calls start_downloads(),
            exactly as if the user had pasted them manually.
            Opens the native file tree popup and brings TurboDownloader to the front.
            URLs can be newline-separated in req.url for batch injection.
            """
            _, err = _validate_urls(req.url)
            if err:
                raise HTTPException(status_code=400, detail=err)
            def _inject():
                try:
                    # ── Inject URLs into the input box ───────────────────────
                    app_ref.url_box.delete("1.0", "end")
                    app_ref.url_box.insert("end", req.url.strip())

                    # ── Bring window to front ────────────────────────────────
                    app_ref.deiconify()      # restore if minimized
                    app_ref.lift()           # raise above other windows
                    app_ref.focus_force()    # grab focus

                    # ── Trigger the normal download flow (opens tree_popup) ──
                    app_ref.start_downloads()

                    _log.info("Interactive inject: %s", req.url[:80])

                except Exception as e:
                    _log.error("Interactive inject error: %s", e)

            app_ref.after(0, _inject)
            return {"status": "interactive", "url": req.url}

        @api.post("/downloads/{idx}/pause")
        def pause_download(idx: int, _ = Depends(require_auth)):
            it = app_ref.items.get(idx)
            if not it:
                raise HTTPException(status_code=404, detail="Not found")
            app_ref.after(0, lambda: app_ref.pause_one(idx))
            return {"status": "pausing", "idx": idx}

        @api.post("/downloads/{idx}/resume")
        def resume_download(idx: int, _ = Depends(require_auth)):
            it = app_ref.items.get(idx)
            if not it:
                raise HTTPException(status_code=404, detail="Not found")
            app_ref.after(0, lambda: app_ref.pause_one(idx))  # pause_one toggles
            return {"status": "resuming", "idx": idx}

        @api.post("/downloads/{idx}/cancel")
        def cancel_download(idx: int, _ = Depends(require_auth)):
            it = app_ref.items.get(idx)
            if not it:
                raise HTTPException(status_code=404, detail="Not found")
            app_ref.after(0, lambda: app_ref.cancel_one(idx))
            return {"status": "canceling", "idx": idx}

        @api.post("/downloads/stop_all")
        def stop_all(_ = Depends(require_auth)):
            app_ref.after(0, app_ref.stop_all)
            return {"status": "stopping_all"}

        @api.post("/downloads/{idx}/remove")
        def remove_download(idx: int, _ = Depends(require_auth)):
            """Removes a finished/canceled/errored download row from the list."""
            it = app_ref.items.get(idx)
            if not it:
                raise HTTPException(status_code=404, detail="Not found")
            if it.state not in ("done", "error", "canceled", "skipped"):
                raise HTTPException(status_code=400,
                                    detail="Cannot remove active download")
            app_ref.after(0, lambda: app_ref.remove_one(idx))
            return {"status": "removed", "idx": idx}

        @api.post("/downloads/clear_done")
        def clear_done(_ = Depends(require_auth)):
            """Removes all finished/canceled/errored rows from the server list."""
            def _clear():
                to_remove = [
                    i for i, it in app_ref.items.items()
                    if it.state in ("done", "error", "canceled", "skipped")
                ]
                for i in to_remove:
                    app_ref.remove_one(i)
                print(f"[remote-server] Cleared {len(to_remove)} finished downloads")
            app_ref.after(0, _clear)
            return {"status": "cleared"}

        @api.get("/history")
        def get_history(_ = Depends(require_auth)):
            return {"entries": app_ref._history.get_entries()}

        @api.get("/browse")
        def browse_folder(path: str = "", _ = Depends(require_auth)):
            """
            Returns the contents of a folder on the server machine.
            path="" → returns drives (Windows) or "/" (Unix).
            Returns: { "path": str, "parent": str|null, "entries": [ {name, path, is_dir} ] }
            """
            import os as _os
            import sys as _sys

            # ── Root level : drives on Windows, "/" on Unix ───────────────────
            if not path:
                if _sys.platform == "win32":
                    import string
                    drives = [
                        {"name": f"{d}:\\", "path": f"{d}:\\", "is_dir": True}
                        for d in string.ascii_uppercase
                        if _os.path.exists(f"{d}:\\")
                    ]
                    return {"path": "", "parent": None, "entries": drives}
                else:
                    path = "/"

            path = _os.path.abspath(path)
            if not _os.path.isdir(path):
                raise HTTPException(status_code=404, detail="Not a directory")

            parent = str(pathlib.Path(path).parent) if path not in ("", "/") else None
            if parent == path:   # filesystem root
                parent = None

            entries = []
            try:
                with _os.scandir(path) as it:
                    items = sorted(it, key=lambda e: e.name)
                for entry in items:
                    try:
                        if entry.is_symlink():
                            continue   # skip symlinks — could point anywhere
                        is_dir = entry.is_dir(follow_symlinks=False)
                        entries.append({"name": entry.name, "path": entry.path, "is_dir": is_dir})
                    except PermissionError:
                        pass
            except PermissionError:
                raise HTTPException(status_code=403, detail="Permission denied")

            return {"path": path, "parent": parent, "entries": entries}

        @api.get("/dest_history")
        def dest_history(_ = Depends(require_auth)):
            """Returns the last used destination folders from history."""
            entries  = app_ref._history.get_entries()
            seen     = []
            seen_set = set()
            for e in entries:
                dest = str(pathlib.Path(e.get("filename", "")).parent) \
                       if e.get("filename") else ""
                # Also grab from dest_path stored in item if filename is relative
                if not dest or dest == ".":
                    continue
                if dest not in seen_set:
                    seen_set.add(dest)
                    seen.append(dest)
                if len(seen) >= 8:
                    break
            return {"destinations": seen}

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
    HTTP/HTTPS client for connecting to a remote TurboDownloader server.
    - localhost / 127.0.0.1 → plain HTTP (no SSL issues)
    - any other host        → HTTPS with SSL verification disabled (self-signed cert)
    """

    _LOOPBACK = {"localhost", "127.0.0.1", "::1"}

    def __init__(self, host: str, port: int, username: str, password: str):
        self._base    = f"https://{host}:{port}"
        self._host    = host
        self._port    = port
        self._verify  = False
        self._user    = username
        self._pass    = password
        self._token   = ""
        self._headers = {}
        self._ok      = False
        self._alive   = True   # set to False to stop heartbeat thread

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
            _log.debug("Connecting to %s...", self._base)
            r = self._httpx.post(
                f"{self._base}/auth/login",
                json={"username": self._user, "password": self._pass},
                headers={"Content-Type": "application/json"},
                verify=False,
                timeout=8,
            )
            if r.status_code == 200:
                self._token   = r.json()["token"]
                self._headers = {
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type":  "application/json",
                }
                self._ok    = True
                self._alive = True

                # ── Version check (hard block) ────────────────────────────────
                from updater import APP_VERSION
                try:
                    vr = self._httpx.get(
                        f"{self._base}/version", verify=False, timeout=5)
                    if vr.status_code == 200:
                        server_ver = vr.json().get("version", "")
                        if server_ver and server_ver != APP_VERSION:
                            # Disconnect — versions incompatible
                            self._token   = ""
                            self._headers = {}
                            self._ok      = False
                            return False, f"VERSION_MISMATCH:{server_ver}"
                except Exception:
                    pass   # Can't check version — proceed anyway

                return True, "Connected"
            return False, f"Auth failed ({r.status_code}): {r.text[:200]}"
        except Exception as e:
            print(f"[remote-client] Exception: {type(e).__name__}: {e}")
            return False, f"Connection error: {e}"

    def start_heartbeat(self, on_disconnect=None, on_reconnect=None,
                        on_version_mismatch=None,
                        interval: int = 10, max_retries: int = 5):
        """
        Starts a background thread that pings the server every `interval` seconds.
        - on_disconnect()              : called when connection is lost
        - on_reconnect()               : called when connection is restored
        - on_version_mismatch(ver)     : called (and retries stopped) on VERSION_MISMATCH
        - max_retries                  : number of reconnect attempts before giving up (0 = infinite)
        """
        import threading as _th
        import time as _t

        def _heartbeat():
            retries = 0
            was_connected = True

            while self._alive:
                _t.sleep(interval)
                if not self._alive:
                    break

                # Ping server
                result = self._get("/status")
                if result is not None:
                    if not was_connected:
                        # Reconnected!
                        print("[remote-client] Reconnected to server ✓")
                        was_connected = True
                        retries = 0
                        if on_reconnect:
                            on_reconnect()
                else:
                    if was_connected:
                        print("[remote-client] Lost connection to server")
                        was_connected = False
                        self._ok = False
                        if on_disconnect:
                            on_disconnect()

                    # Try to reconnect
                    if max_retries == 0 or retries < max_retries:
                        retries += 1
                        _log.debug("Reconnect attempt %d...", retries)
                        ok, msg = self.connect()
                        if ok:
                            _log.info("Reconnect OK")
                            was_connected = True
                            retries = 0
                            if on_reconnect:
                                on_reconnect()
                        elif msg.startswith("VERSION_MISMATCH:"):
                            server_ver = msg.split(":", 1)[1]
                            _log.warning("Heartbeat: version mismatch with server v%s — stopping retries", server_ver)
                            self._alive = False
                            if on_version_mismatch:
                                on_version_mismatch(server_ver)
                            break
                        else:
                            _log.warning("Reconnect failed: %s", msg)

        _th.Thread(target=_heartbeat, daemon=True, name="RemoteHeartbeat").start()

    def disconnect(self):
        """Signals the heartbeat thread to stop."""
        self._alive = False
        self._ok    = False

    @property
    def connected(self) -> bool:
        return self._ok

    def get_status(self) -> Optional[dict]:
        return self._get("/status")

    def add_url(self, url: str, dest: Optional[str] = None,
                worker_type: Optional[str] = None,
                format_id: Optional[str] = None,
                audio_only: bool = False) -> Optional[dict]:
        return self._post("/downloads/add", {
            "url":         url,
            "dest":        dest,
            "worker_type": worker_type,
            "format_id":   format_id,
            "audio_only":  audio_only,
        })

    def add_interactive(self, urls: list) -> Optional[dict]:
        """Injects URLs into TurboDownloader's input box and opens the native popup."""
        combined = "\n".join(urls)
        return self._post("/downloads/add_interactive", {"url": combined, "dest": None})

    def browse(self, path: str = "") -> Optional[dict]:
        """Returns folder contents from the server filesystem."""
        return self._get(f"/browse?path={path}")

    def pause(self, idx: int) -> Optional[dict]:
        return self._post(f"/downloads/{idx}/pause")

    def resume(self, idx: int) -> Optional[dict]:
        return self._post(f"/downloads/{idx}/resume")

    def cancel(self, idx: int) -> Optional[dict]:
        return self._post(f"/downloads/{idx}/cancel")

    def remove(self, idx: int) -> Optional[dict]:
        return self._post(f"/downloads/{idx}/remove")

    def clear_done(self) -> Optional[dict]:
        return self._post("/downloads/clear_done")

    def stop_all(self) -> Optional[dict]:
        return self._post("/downloads/stop_all")

    def get_history(self) -> Optional[dict]:
        return self._get("/history")

    def get_server_version(self) -> Optional[str]:
        """Returns the server's APP_VERSION string, or None on error."""
        try:
            r = self._httpx.get(
                f"{self._base}/version", verify=False, timeout=5)
            if r.status_code == 200:
                return r.json().get("version")
        except Exception:
            pass
        return None

    def trigger_remote_update(self, username: str = "", password: str = "") -> str:
        """
        Asks the server to download and silently install the latest update.
        Sends credentials in the JSON body (never in query params).
        Returns the server status string:
          "update_started"     — update download launched
          "already_up_to_date" — server is already on the latest release
          "check_failed"       — server could not reach GitHub
          "no_download_url"    — release found but no installer asset
          "error"              — network/auth error
        """
        if not self._httpx:
            return "error"
        try:
            body    = {}
            params  = {}
            headers = self._headers if self._ok else {"Content-Type": "application/json"}
            if not self._ok and username and password:
                # Send in body (v2.7.5+) AND query params (v2.7.4 compat)
                body   = {"username": username, "password": password}
                params = {"username": username, "password": password}
            r = self._httpx.post(
                f"{self._base}/admin/trigger-update",
                headers=headers, json=body, params=params,
                verify=False, timeout=15,
            )
            if r.status_code == 200:
                return r.json().get("status", "error")
            return "error"
        except Exception as e:
            _log.warning("trigger_remote_update failed: %s", e)
            return "error"

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