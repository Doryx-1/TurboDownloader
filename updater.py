"""
updater.py — Automatic update checker for TurboDownloader.

Checks the latest GitHub release against the running version.
If a newer version is available, shows a popup offering to download
and install it automatically.

Usage (from downloader.py):
    from updater import check_for_updates
    check_for_updates(app, current_version="2.7.0")
"""

import threading
import os
import sys
import subprocess
import pathlib
import tempfile

from logger import get_logger
_log = get_logger("updater")

APP_VERSION    = "2.7.4"
GITHUB_REPO    = "Doryx-1/TurboDownloader"
API_URL        = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
SETUP_FILENAME = "TurboDownloader_Setup.exe"


def _parse_version(tag: str) -> tuple:
    """Converts 'v2.7.0' or '2.7.0' to (2, 7, 0)."""
    tag = tag.lstrip("v").strip()
    try:
        return tuple(int(x) for x in tag.split("."))
    except Exception:
        return (0,)


def _is_newer(remote: str, current: str) -> bool:
    """Returns True if remote version is strictly newer than current."""
    return _parse_version(remote) > _parse_version(current)


def fetch_latest_release() -> dict | None:
    """
    Calls GitHub API and returns release info dict, or None on error.
    Returns: { "tag": str, "name": str, "body": str, "download_url": str | None }
    """
    try:
        import urllib.request, json
        req = urllib.request.Request(
            API_URL,
            headers={"User-Agent": f"TurboDownloader/{APP_VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())

        tag  = data.get("tag_name", "")
        name = data.get("name", tag)
        body = data.get("body", "")

        # Find the setup exe in assets
        download_url = None
        for asset in data.get("assets", []):
            if asset.get("name", "").lower().endswith(".exe"):
                download_url = asset.get("browser_download_url")
                break

        return {"tag": tag, "name": name, "body": body, "download_url": download_url}

    except Exception as e:
        _log.debug("Update check failed: %s", e)
        return None


def check_for_updates(app, current_version: str = APP_VERSION, silent: bool = False):
    """
    Checks for updates in a background thread.
    If a newer version is found, shows a popup on the UI thread.
    silent=True → only show popup if update is available (used at startup).
    """
    def _check():
        release = fetch_latest_release()
        if release is None:
            if not silent:
                app.after(0, lambda: _show_no_update_popup(app))
            return

        if _is_newer(release["tag"], current_version):
            app.after(0, lambda: _show_update_popup(app, release, current_version))
        else:
            _log.debug("No update available (latest: %s)", release["tag"])
            if not silent:
                app.after(0, lambda: _show_no_update_popup(app))

    threading.Thread(target=_check, daemon=True, name="UpdateChecker").start()


def _show_update_popup(app, release: dict, current_version: str):
    """Shows the update available popup on the UI thread."""
    import customtkinter as ctk

    popup = ctk.CTkToplevel(app)
    popup.title("Update available")
    popup.geometry("460x280")
    popup.resizable(False, False)
    popup.grab_set()
    popup.lift()

    # Center on main window
    app.update_idletasks()
    x = app.winfo_rootx() + (app.winfo_width()  - 460) // 2
    y = app.winfo_rooty() + (app.winfo_height() - 280) // 2
    popup.geometry(f"+{max(0,x)}+{max(0,y)}")

    # Header
    ctk.CTkLabel(popup,
                 text="A new version is available!",
                 font=ctk.CTkFont(size=15, weight="bold")).pack(pady=(20, 4))

    ctk.CTkLabel(popup,
                 text=f"v{current_version}  →  {release['tag']}",
                 text_color="#1f6aa5",
                 font=ctk.CTkFont(size=13)).pack(pady=(0, 8))

    # Release notes (truncated)
    notes = (release.get("body") or "").strip()
    if notes:
        notes_display = notes[:300] + "…" if len(notes) > 300 else notes
        notes_box = ctk.CTkTextbox(popup, height=80, font=ctk.CTkFont(size=11),
                                   fg_color="#1a1a1a", border_width=0)
        notes_box.pack(fill="x", padx=20, pady=(0, 12))
        notes_box.insert("1.0", notes_display)
        notes_box.configure(state="disabled")

    # Buttons
    btn_frame = ctk.CTkFrame(popup, fg_color="transparent")
    btn_frame.pack(fill="x", padx=20, pady=(0, 16))

    def _download_and_install():
        popup.destroy()
        if release.get("download_url"):
            _launch_download_and_install(app, release["download_url"], release["tag"])
        else:
            # No direct download — open GitHub releases page
            import webbrowser
            webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")

    ctk.CTkButton(btn_frame, text="⬇  Download & install",
                  fg_color="#1f6aa5", hover_color="#1a5a8f",
                  font=ctk.CTkFont(size=13, weight="bold"),
                  command=_download_and_install).pack(side="left", padx=(0, 8))

    ctk.CTkButton(btn_frame, text="Later",
                  fg_color="transparent", border_width=1, border_color="#3a3a3a",
                  hover_color="#2a2a2a",
                  command=popup.destroy).pack(side="left")

    ctk.CTkButton(btn_frame, text="GitHub ↗",
                  fg_color="transparent", border_width=1, border_color="#3a3a3a",
                  hover_color="#2a2a2a", text_color="#888888",
                  font=ctk.CTkFont(size=11),
                  command=lambda: __import__("webbrowser").open(
                      f"https://github.com/{GITHUB_REPO}/releases/latest"
                  )).pack(side="right")


def _show_no_update_popup(app):
    """Shows 'you are up to date' popup — only when manually triggered."""
    import customtkinter as ctk

    popup = ctk.CTkToplevel(app)
    popup.title("Up to date")
    popup.geometry("320x140")
    popup.resizable(False, False)
    popup.grab_set()
    popup.lift()

    app.update_idletasks()
    x = app.winfo_rootx() + (app.winfo_width()  - 320) // 2
    y = app.winfo_rooty() + (app.winfo_height() - 140) // 2
    popup.geometry(f"+{max(0,x)}+{max(0,y)}")

    ctk.CTkLabel(popup, text="✓ TurboDownloader is up to date",
                 font=ctk.CTkFont(size=13, weight="bold")).pack(pady=(28, 6))
    ctk.CTkLabel(popup, text=f"Version {APP_VERSION}",
                 text_color="gray").pack(pady=(0, 16))
    ctk.CTkButton(popup, text="OK", width=100,
                  command=popup.destroy).pack()


def _launch_download_and_install(app, url: str, tag: str):
    """
    Downloads the setup exe to a temp folder and launches it.
    Shows a progress popup while downloading.
    """
    import customtkinter as ctk

    popup = ctk.CTkToplevel(app)
    popup.title("Downloading update…")
    popup.geometry("380x140")
    popup.resizable(False, False)
    popup.grab_set()
    popup.lift()

    app.update_idletasks()
    x = app.winfo_rootx() + (app.winfo_width()  - 380) // 2
    y = app.winfo_rooty() + (app.winfo_height() - 140) // 2
    popup.geometry(f"+{max(0,x)}+{max(0,y)}")

    lbl = ctk.CTkLabel(popup, text=f"Downloading {tag}…",
                       font=ctk.CTkFont(size=13)).pack(pady=(24, 8))
    bar = ctk.CTkProgressBar(popup, width=340)
    bar.set(0)
    bar.pack(padx=20, pady=(0, 10))
    bar.start()   # indeterminate while we don't have size

    status_lbl = ctk.CTkLabel(popup, text="", text_color="gray",
                               font=ctk.CTkFont(size=11))
    status_lbl.pack()

    def _do_download():
        try:
            import urllib.request

            dest = pathlib.Path(tempfile.gettempdir()) / SETUP_FILENAME

            def _progress(count, block_size, total):
                if total > 0:
                    pct = min(count * block_size / total, 1.0)
                    app.after(0, lambda p=pct: bar.set(p))
                    mb = count * block_size / 1_048_576
                    app.after(0, lambda m=mb: status_lbl.configure(
                        text=f"{m:.1f} MB / {total/1_048_576:.1f} MB"))

            bar.stop()
            urllib.request.urlretrieve(url, str(dest), reporthook=_progress)

            # Launch installer and quit
            app.after(0, lambda: _install(dest, popup, app))

        except Exception as e:
            _log.error("Download failed: %s", e)
            app.after(0, lambda: status_lbl.configure(
                text=f"Download failed: {e}", text_color="#cc4444"))

    threading.Thread(target=_do_download, daemon=True, name="UpdateDownload").start()


def _launch_download_and_install_silent(app, url: str):
    """
    Downloads the setup exe and installs it silently (no UI).
    Used when a remote update is triggered via the API.
    """
    def _do():
        try:
            import urllib.request
            dest = pathlib.Path(tempfile.gettempdir()) / SETUP_FILENAME
            urllib.request.urlretrieve(url, str(dest))
            _log.info("Silent update downloaded to %s", dest)
            app.after(0, lambda: _install_silent(dest, app))
        except Exception as e:
            _log.error("Silent update download failed: %s", e)
    threading.Thread(target=_do, daemon=True, name="SilentUpdateDownload").start()


def _install_silent(setup_path: pathlib.Path, app):
    """Launches the installer with /VERYSILENT flags and quits the app."""
    try:
        import subprocess
        DETACHED  = 0x00000008
        NEW_GROUP = 0x00000200
        subprocess.Popen(
            [str(setup_path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            creationflags=DETACHED | NEW_GROUP,
            close_fds=True,
        )
        app.after(1000, app._tray_quit)
    except Exception as e:
        _log.error("Silent install launch failed: %s", e)


def _install(setup_path: pathlib.Path, popup, app):
    """Launches the installer and quits TurboDownloader."""
    try:
        popup.destroy()
        # os.startfile is the most reliable way to launch an exe on Windows
        # independently of the parent process — equivalent to double-clicking
        os.startfile(str(setup_path))
        app.after(800, app._tray_quit)
    except Exception as e:
        _log.error("Install launch failed: %s", e)
        # Fallback: try subprocess with full detach flags
        try:
            DETACHED = 0x00000008   # DETACH_PROCESS
            NEW_GROUP = 0x00000200  # CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(
                [str(setup_path)],
                creationflags=DETACHED | NEW_GROUP,
                close_fds=True,
            )
            app.after(800, app._tray_quit)
        except Exception as e2:
            _log.error("Fallback install also failed: %s", e2)