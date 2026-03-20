"""
tray.py — System tray integration for TurboDownloader.

Features:
  - Minimize to tray instead of closing
  - Tray icon with context menu (Open, Start with Windows, Quit)
  - Start with Windows via registry key
  - show_for_popup() : brings only the popup to front without restoring main window
"""

import sys
import threading
import pathlib

# ── Constants ─────────────────────────────────────────────────────────────────

APP_NAME    = "TurboDownloader"
REG_KEY     = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_VALUE   = APP_NAME

# ── Registry helpers (Windows only) ──────────────────────────────────────────

def _get_exe_path() -> str:
    """Returns the path to the running executable (works frozen + source)."""
    if getattr(sys, "frozen", False):
        return sys.executable
    # Running from source — point to main.py via pythonw for no-console startup
    import os
    main = pathlib.Path(os.path.abspath(sys.argv[0]))
    return str(main)


def is_startup_enabled() -> bool:
    """Returns True if TurboDownloader is set to start with Windows."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as key:
            val, _ = winreg.QueryValueEx(key, REG_VALUE)
            return bool(val)
    except (FileNotFoundError, OSError):
        return False


def set_startup(enabled: bool) -> bool:
    """Enables or disables start with Windows. Returns True on success."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_KEY,
            access=winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                exe = _get_exe_path()
                # Add --minimized flag so it starts in tray
                value = f'"{exe}" --minimized'
                winreg.SetValueEx(key, REG_VALUE, 0, winreg.REG_SZ, value)
            else:
                try:
                    winreg.DeleteValue(key, REG_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        print(f"[tray] Registry error: {e}")
        return False


# ── TrayIcon ──────────────────────────────────────────────────────────────────

class TrayIcon:
    """
    Manages the system tray icon using pystray.
    Runs in a daemon thread so it doesn't block the main loop.
    """

    def __init__(self, app):
        self._app  = app
        self._icon = None
        self._ok   = False

        try:
            import pystray
            from PIL import Image
            self._pystray = pystray
            self._Image   = Image
            self._ok      = True
        except ImportError as e:
            print(f"[tray] pystray/Pillow not available — tray disabled: {e}")

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Builds the tray icon and starts it in a background thread."""
        if not self._ok:
            return
        icon_image = self._load_icon()
        menu       = self._build_menu()
        self._icon = self._pystray.Icon(APP_NAME, icon_image, APP_NAME, menu)
        threading.Thread(
            target=self._icon.run,
            daemon=True,
            name="TrayIcon",
        ).start()

    def stop(self):
        """Removes the tray icon."""
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    def update_menu(self):
        """Refreshes the tray menu (e.g. after toggling startup)."""
        if self._icon:
            self._icon.menu = self._build_menu()

    # ── Icon ──────────────────────────────────────────────────────────────────

    def _load_icon(self):
        """Loads icon.ico or generates a simple fallback."""
        import os
        # Try icon.ico next to exe / script
        if getattr(sys, "_MEIPASS", None):
            ico_path = pathlib.Path(sys._MEIPASS) / "icon.ico"
        else:
            ico_path = pathlib.Path(os.path.abspath(sys.argv[0])).parent / "icon.ico"

        if ico_path.exists():
            try:
                return self._Image.open(str(ico_path)).resize((64, 64))
            except Exception:
                pass

        # Fallback — blue square with ⬇
        img = self._Image.new("RGBA", (64, 64), (31, 106, 165, 255))
        return img

    # ── Menu ──────────────────────────────────────────────────────────────────

    def _build_menu(self):
        pystray = self._pystray

        startup_checked = is_startup_enabled()

        return pystray.Menu(
            pystray.MenuItem(
                "Open TurboDownloader",
                self._on_open,
                default=True,   # double-click action
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start with Windows",
                self._on_toggle_startup,
                checked=lambda item: is_startup_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit),
        )

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_open(self, icon=None, item=None):
        """Restore the main window."""
        self._app.after(0, self._app._tray_restore)

    def _on_toggle_startup(self, icon=None, item=None):
        """Toggle start with Windows."""
        current = is_startup_enabled()
        set_startup(not current)

    def _on_quit(self, icon=None, item=None):
        """Quit the application completely."""
        self._app.after(0, self._app._tray_quit)


# ── Custom protocol handler (turbodownloader://) ──────────────────────────────

PROTOCOL = "turbodownloader"
PROTOCOL_REG_KEY = f"Software\\Classes\\{PROTOCOL}"


def register_protocol(exe_path: str = None) -> bool:
    """
    Registers the turbodownloader:// custom URL protocol in the Windows registry.
    After this, opening turbodownloader://... launches the exe with the URL as argv[1].
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg
        if exe_path is None:
            exe_path = _get_exe_path()

        # HKCU\Software\Classes\turbodownloader
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOL_REG_KEY) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"URL:{APP_NAME} Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")

        # \DefaultIcon
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, f"{PROTOCOL_REG_KEY}\\DefaultIcon"
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{exe_path}",0')

        # \shell\open\command
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            f"{PROTOCOL_REG_KEY}\\shell\\open\\command"
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{exe_path}" "%1"')

        print(f"[tray] Protocol turbodownloader:// registered → {exe_path}")
        return True
    except OSError as e:
        print(f"[tray] Protocol registration error: {e}")
        return False


def unregister_protocol() -> bool:
    """Removes the turbodownloader:// protocol from the registry."""
    if sys.platform != "win32":
        return False
    try:
        import winreg, shutil as _sh

        def _del_tree(key_path):
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, key_path,
                    access=winreg.KEY_ALL_ACCESS
                ) as key:
                    while True:
                        try:
                            sub = winreg.EnumKey(key, 0)
                            _del_tree(f"{key_path}\\{sub}")
                        except OSError:
                            break
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            except FileNotFoundError:
                pass

        _del_tree(PROTOCOL_REG_KEY)
        print("[tray] Protocol turbodownloader:// unregistered")
        return True
    except OSError as e:
        print(f"[tray] Protocol unregistration error: {e}")
        return False


def parse_protocol_url(url: str) -> dict:
    """
    Parses a turbodownloader:// URL and returns a dict of params.

    Examples:
      turbodownloader://send?url=https://...        → {"action": "send", "urls": ["https://..."]}
      turbodownloader://send?url=A&url=B            → {"action": "send", "urls": ["A", "B"]}
      turbodownloader://ping                        → {"action": "ping"}
    """
    from urllib.parse import urlparse, parse_qs, unquote
    try:
        parsed = urlparse(url)
        action = parsed.netloc or parsed.path.lstrip("/") or "ping"
        params = parse_qs(parsed.query)
        urls   = [unquote(u) for u in params.get("url", [])]
        return {"action": action, "urls": urls}
    except Exception as e:
        print(f"[tray] Protocol parse error: {e}")
        return {"action": "unknown", "urls": []}