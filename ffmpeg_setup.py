"""
ffmpeg_setup.py — Dependency setup for TurboDownloader yt-dlp features.

ffmpeg  : looks for ffmpeg.exe next to the script / exe first, then PATH.
          Place ffmpeg.exe in the same folder as main.py (or bundle it with
          PyInstaller using --add-binary "ffmpeg.exe;.").

Node.js : installed on first use via nodeenv into ~/.turbodownloader/nodeenv.
          yt-dlp is then pointed to that node binary via --js-runtimes.
"""

import os
import sys
import shutil
import pathlib
import threading
import subprocess

# ── Paths ─────────────────────────────────────────────────────────────────────

CONFIG_DIR   = pathlib.Path.home() / ".turbodownloader"
NODEENV_DIR  = CONFIG_DIR / "nodeenv"
NODE_BIN     = NODEENV_DIR / ("Scripts" if sys.platform == "win32" else "bin") / (
                   "node.exe" if sys.platform == "win32" else "node")


def _app_dir() -> pathlib.Path:
    """Returns the directory containing the running script / bundled exe."""
    if getattr(sys, "_MEIPASS", None):
        return pathlib.Path(sys._MEIPASS)
    return pathlib.Path(os.path.dirname(os.path.abspath(__file__)))


# ── ffmpeg ────────────────────────────────────────────────────────────────────

def ffmpeg_path() -> str | None:
    """
    Returns the path to a working ffmpeg binary, or None.

    Search order:
    1. ffmpeg.exe next to the script / PyInstaller bundle
    2. System PATH
    """
    # 1. Local binary (bundled or placed by developer)
    local = _app_dir() / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if local.exists():
        return str(local)

    # 2. System PATH
    found = shutil.which("ffmpeg")
    if found:
        return found

    return None


def ffmpeg_available() -> bool:
    """Returns True if a working ffmpeg can be found."""
    p = ffmpeg_path()
    if not p:
        return False
    # Quick sanity check — hide console window on Windows (PyInstaller bundled exe)
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            [p, "-version"],
            capture_output=True, timeout=5,
            **kwargs
        )
        return result.returncode == 0
    except Exception:
        return False


def configure_yt_dlp_ffmpeg(ydl_opts: dict) -> dict:
    """
    Injects ffmpeg location into yt-dlp opts if a local binary is found.
    Returns the (possibly modified) opts dict.
    """
    p = ffmpeg_path()
    if p:
        ydl_opts["ffmpeg_location"] = str(pathlib.Path(p).parent)
    return ydl_opts


# ── Node.js / JS runtime ──────────────────────────────────────────────────────

def node_path() -> str | None:
    """Returns path to the node binary if available (local nodeenv or system)."""
    # 1. Local nodeenv
    if NODE_BIN.exists():
        return str(NODE_BIN)

    # 2. Bundled next to app
    local = _app_dir() / ("node.exe" if sys.platform == "win32" else "node")
    if local.exists():
        return str(local)

    # 3. System PATH
    return shutil.which("node")


def node_available() -> bool:
    """Returns True if node is reachable."""
    p = node_path()
    if not p:
        return False
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        r = subprocess.run([p, "--version"], capture_output=True, timeout=5, **kwargs)
        return r.returncode == 0
    except Exception:
        return False


def configure_yt_dlp_node(ydl_opts: dict) -> dict:
    """
    Injects Node.js path into yt-dlp opts.
    yt-dlp attend: js_runtimes = {"node": {"path": "/chemin/vers/node"}}
    Le chemin doit utiliser des forward slashes (important sur Windows).
    """
    p = node_path()
    if not p:
        return ydl_opts
    p_norm = p.replace("\\", "/")
    ydl_opts["js_runtimes"] = {"node": {"path": p_norm}}
    return ydl_opts


def install_nodeenv(on_progress=None, on_done=None, on_error=None):
    """
    Installs a minimal Node.js environment via nodeenv into ~/.turbodownloader/nodeenv.
    Runs in a background thread.

    NOTE: When running as a PyInstaller bundle, sys.executable points to the
    bundled .exe — calling it with "-m pip" would re-launch the whole app.
    Installation is therefore skipped in bundled mode; Node.js must be installed
    separately or bundled alongside the exe.

    Callbacks (all optional, called from the worker thread):
        on_progress(message: str)
        on_done()
        on_error(message: str)
    """
    # ── Guard: never attempt pip/nodeenv install inside a PyInstaller bundle ──
    if getattr(sys, "frozen", False):
        msg = ("Node.js auto-install is not available in the bundled version.\n"
               "yt-dlp streaming features will work without Node.js for most sites.")
        print(f"[deps] {msg}")
        if on_error:
            on_error(msg)
        return

    def _work():
        try:
            # Check nodeenv is installed
            try:
                import nodeenv  # noqa: F401
            except ImportError:
                if on_progress:
                    on_progress("Installing nodeenv via pip…")
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "nodeenv", "--quiet"],
                    check=True, capture_output=True
                )

            if on_progress:
                on_progress("Installing Node.js runtime (~50 MB, please wait)…")

            CONFIG_DIR.mkdir(parents=True, exist_ok=True)

            result = subprocess.run(
                [sys.executable, "-m", "nodeenv",
                 str(NODEENV_DIR),
                 "--node=lts",
                 "--prebuilt",     # use pre-built binary, much faster
                 "--clean-src"],
                capture_output=True,
                text=True,
                timeout=300,       # 5 min max
            )

            if result.returncode != 0:
                msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                if on_error:
                    on_error(f"nodeenv install failed: {msg[:200]}")
                return

            if node_available():
                if on_progress:
                    on_progress("Node.js installed successfully ✓")
                if on_done:
                    on_done()
            else:
                if on_error:
                    on_error("nodeenv finished but node binary not found")

        except subprocess.TimeoutExpired:
            if on_error:
                on_error("Node.js installation timed out (>5 min)")
        except Exception as e:
            if on_error:
                on_error(f"Node.js installation error: {e}")

    threading.Thread(target=_work, daemon=True).start()


# ── Combined setup status ─────────────────────────────────────────────────────

def get_status() -> dict:
    """Returns a dict summarising dependency availability."""
    return {
        "ffmpeg":       ffmpeg_available(),
        "ffmpeg_path":  ffmpeg_path(),
        "node":         node_available(),
        "node_path":    node_path(),
    }