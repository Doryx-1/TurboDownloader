import os
import json
import pathlib

import customtkinter as ctk
from tkinter import filedialog


# Config file path: C:/Users/<user>/.turbodownloader/settings.json
CONFIG_DIR  = pathlib.Path.home() / ".turbodownloader"
CONFIG_FILE = CONFIG_DIR / "settings.json"

# Default temp folder
DEFAULT_TEMP_DIR = str(CONFIG_DIR / "tmp")

# Default destination folder — system Downloads
DEFAULT_DEST_DIR = str(pathlib.Path.home() / "Downloads")


DEFAULT_EXTENSIONS = {
    ".mkv":  True,
    ".mp4":  True,
    ".avi":  True,
    ".mov":  True,
    ".wmv":  True,
    ".srt":  False,
    ".nfo":  False,
    ".jpg":  False,
}


def load_settings() -> dict:
    """Loads settings from the config file. Returns defaults if not found."""
    defaults = {
        "temp_dir":      DEFAULT_TEMP_DIR,
        "default_dest":  DEFAULT_DEST_DIR,
        "retry_max":     3,
        "retry_delay":   5,
        "throttle":      0,
        "notifications": True,
        "minimize_to_tray": True,
        "workers":       10,
        "segments":      4,
        "extensions":    DEFAULT_EXTENSIONS.copy(),
        "all_files":     False,
        # ── Remote control ─────────────────────
        "remote_enabled":       False,
        "remote_port":          9988,
        "remote_username":      "",
        "remote_password_hash": "",
        "remote_jwt_secret":    "",
        # client-side connection info
        "remote_client_host":           "",
        "remote_client_port":           9988,
        "remote_client_user":           "",
        "remote_client_password":       "",
        "remote_client_save_password":  False,
        "remote_client_autoconnect":    False,
        "remote_client_autoretry":      True,
        # destination history
        "dest_history":                 [],
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                defaults.update(data)
            print(f"[settings] loaded: throttle={defaults.get('throttle')}, retry={defaults.get('retry_max')}")
        except Exception as e:
            print(f"[settings] read error: {e}")
    else:
        print("[settings] config file not found, using defaults")
    return defaults


def save_settings(settings: dict):
    """Saves settings to disk."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        print(f"[settings] saved: {settings}")
    except Exception as e:
        print(f"[settings] save error: {e}")


def _machine_key() -> bytes:
    """
    Derives a Fernet key from a machine-specific secret.
    Uses a combination of username + hostname — stable across reboots,
    unique per machine, never stored on disk.
    """
    import hashlib, socket, os
    from cryptography.fernet import Fernet
    import base64
    seed = f"{os.getlogin()}@{socket.gethostname()}:TurboDL-salt-v1"
    digest = hashlib.sha256(seed.encode()).digest()
    return base64.urlsafe_b64encode(digest)   # 32 bytes → valid Fernet key


def _encrypt_password(plaintext: str) -> str:
    """Encrypts a password string with a machine-derived key. Returns base64 str."""
    if not plaintext:
        return ""
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_machine_key())
        return f.encrypt(plaintext.encode()).decode()
    except Exception:
        return ""   # Fallback: store empty rather than plaintext on error


def _decrypt_password(ciphertext: str) -> str:
    """Decrypts a previously encrypted password. Returns plaintext or empty string."""
    if not ciphertext:
        return ""
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_machine_key())
        return f.decrypt(ciphertext.encode()).decode()
    except Exception:
        return ""   # Token expired / wrong machine / corrupted — treat as empty


def _center_on_master(window, master):
    """Centers a Toplevel window on its master after geometry is set."""
    window.update_idletasks()
    mw = master.winfo_width()
    mh = master.winfo_height()
    mx = master.winfo_rootx()
    my = master.winfo_rooty()
    ww = window.winfo_width()
    wh = window.winfo_height()
    x  = mx + (mw - ww) // 2
    y  = my + (mh - wh) // 2
    # Keep on screen
    x  = max(0, x)
    y  = max(0, y)
    window.geometry(f"+{x}+{y}")


class SettingsPopup(ctk.CTkToplevel):
    """TurboDownloader settings window."""

    def __init__(self, master, settings: dict, on_save):
        super().__init__(master)
        self.title("Settings")
        self.geometry("1100x660")
        self.resizable(True, True)
        self.grab_set()

        self._settings = settings
        self._on_save  = on_save

        self._build_ui()
        _center_on_master(self, master)

    def _build_ui(self):
        self.grid_rowconfigure(0, weight=0)   # tab bar
        self.grid_rowconfigure(1, weight=1)   # content
        self.grid_rowconfigure(2, weight=0)   # buttons
        self.grid_columnconfigure(0, weight=1)

        # ── Tab bar ───────────────────────────────────────────────────────
        tab_bar = ctk.CTkFrame(self, fg_color="#1a1a1a", corner_radius=0)
        tab_bar.grid(row=0, column=0, sticky="ew")

        self._tab_panels: dict = {}
        self._tab_btns:   dict = {}

        tabs = [
            ("general",    "⚙  General"),
            ("downloads",  "⬇  Downloads"),
            ("extensions", "📄  Extensions"),
            ("remote",     "📡  Remote"),
            ("system",     "🖥  System"),
        ]

        for key, label in tabs:
            btn = ctk.CTkButton(
                tab_bar, text=label, height=34,
                fg_color="transparent", hover_color="#2a2a2a",
                corner_radius=0,
                font=ctk.CTkFont(size=12),
                command=lambda k=key: self._show_tab(k),
            )
            btn.pack(side="left", padx=2, pady=4)
            self._tab_btns[key] = btn

        # ── Panel container ───────────────────────────────────────────────
        self._panel_host = ctk.CTkFrame(self, fg_color="transparent")
        self._panel_host.grid(row=1, column=0, sticky="nsew")
        self._panel_host.grid_rowconfigure(0, weight=1)
        self._panel_host.grid_columnconfigure(0, weight=1)

        # ── Bottom buttons ────────────────────────────────────────────────
        bot = ctk.CTkFrame(self, fg_color="#2b2b2b", corner_radius=0)
        bot.grid(row=2, column=0, sticky="ew")
        ctk.CTkButton(bot, text="Cancel", width=110, fg_color="#5a5a5a",
                      command=self.destroy).pack(side="right", padx=(8, 16), pady=10)
        ctk.CTkButton(bot, text="Save", width=130, fg_color="#1f6aa5",
                      command=self._save).pack(side="right", pady=10)

        # ── Build each panel ──────────────────────────────────────────────
        self._build_tab_general()
        self._build_tab_downloads()
        self._build_tab_extensions()
        self._build_tab_remote()
        self._build_tab_system()

        # Show first tab
        self._show_tab("general")

    def _show_tab(self, key: str):
        """Switches to the given tab panel."""
        for k, panel in self._tab_panels.items():
            panel.grid_remove()
        for k, btn in self._tab_btns.items():
            btn.configure(fg_color="#1f6aa5" if k == key else "transparent")
        self._tab_panels[key].grid(row=0, column=0, sticky="nsew",
                                   in_=self._panel_host)

    def _make_panel(self, key: str) -> ctk.CTkScrollableFrame:
        """Creates a scrollable panel and registers it."""
        panel = ctk.CTkScrollableFrame(self._panel_host, fg_color="transparent")
        self._tab_panels[key] = panel
        return panel

    # ── Tab: General ──────────────────────────────────────────────────────
    def _build_tab_general(self):
        p = self._make_panel("general")

        # Two-column grid inside the panel
        col_frame = ctk.CTkFrame(p, fg_color="transparent")
        col_frame.pack(fill="both", expand=True, padx=4, pady=4)
        col_frame.grid_columnconfigure(0, weight=1)
        col_frame.grid_columnconfigure(1, weight=1)
        col_frame.grid_rowconfigure(0, weight=1)

        left  = ctk.CTkFrame(col_frame, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(8, 4))
        right = ctk.CTkFrame(col_frame, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 8))

        # Left — paths
        self._section(left, "Default destination folder",
                      "Downloads go here when no custom path is set in the popup.")
        row_dest = ctk.CTkFrame(left, fg_color="transparent")
        row_dest.pack(fill="x", padx=4, pady=(0, 4))
        self._dest_entry = ctk.CTkEntry(row_dest)
        self._dest_entry.insert(0, self._settings.get("default_dest", DEFAULT_DEST_DIR))
        self._dest_entry.pack(side="left", expand=True, fill="x", padx=(0, 8))
        ctk.CTkButton(row_dest, text="Browse…", width=100,
                      command=self._browse_dest).pack(side="left")
        ctk.CTkButton(left, text="Reset to Downloads", width=180,
                      fg_color="transparent", border_width=1,
                      command=self._reset_dest).pack(anchor="w", padx=4, pady=(2, 10))

        self._section(left, "Temporary download folder",
                      "Files written here during download, then moved atomically.")
        row_temp = ctk.CTkFrame(left, fg_color="transparent")
        row_temp.pack(fill="x", padx=4, pady=(0, 4))
        self._temp_entry = ctk.CTkEntry(row_temp)
        self._temp_entry.insert(0, self._settings.get("temp_dir", DEFAULT_TEMP_DIR))
        self._temp_entry.pack(side="left", expand=True, fill="x", padx=(0, 8))
        ctk.CTkButton(row_temp, text="Browse…", width=100,
                      command=self._browse_temp).pack(side="left")
        ctk.CTkButton(left, text="Reset to default", width=180,
                      fg_color="transparent", border_width=1,
                      command=self._reset_default).pack(anchor="w", padx=4, pady=(2, 10))

        # Right — workers / retry / bandwidth / multipart
        self._section(right, "Concurrent workers",
                      "Number of simultaneous downloads. Default: 10.")
        row_workers = ctk.CTkFrame(right, fg_color="transparent")
        row_workers.pack(fill="x", padx=4, pady=(0, 10))
        ctk.CTkLabel(row_workers, text="Workers:").pack(side="left", padx=(0, 8))
        self._workers_entry = ctk.CTkEntry(row_workers, width=60)
        self._workers_entry.insert(0, str(self._settings.get("workers", 10)))
        self._workers_entry.pack(side="left")
        ctk.CTkLabel(row_workers, text="(1–20)",
                     text_color="gray").pack(side="left", padx=(8, 0))

        self._section(right, "Automatic retry",
                      "Retries on network errors. Delay doubles each attempt.")
        row_retry = ctk.CTkFrame(right, fg_color="transparent")
        row_retry.pack(fill="x", padx=4, pady=(0, 10))
        ctk.CTkLabel(row_retry, text="Max:").pack(side="left", padx=(0, 6))
        self._retry_max_entry = ctk.CTkEntry(row_retry, width=60)
        self._retry_max_entry.insert(0, str(self._settings.get("retry_max", 3)))
        self._retry_max_entry.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(row_retry, text="Delay (s):").pack(side="left", padx=(0, 6))
        self._retry_delay_entry = ctk.CTkEntry(row_retry, width=60)
        self._retry_delay_entry.insert(0, str(self._settings.get("retry_delay", 5)))
        self._retry_delay_entry.pack(side="left")

        self._section(right, "Bandwidth limit",
                      "Global cap shared across all workers. 0 = unlimited.")
        row_throttle = ctk.CTkFrame(right, fg_color="transparent")
        row_throttle.pack(fill="x", padx=4, pady=(0, 10))
        ctk.CTkLabel(row_throttle, text="Limit (MB/s):").pack(side="left", padx=(0, 6))
        self._throttle_entry = ctk.CTkEntry(row_throttle, width=80)
        self._throttle_entry.insert(0, str(self._settings.get("throttle", 0)))
        self._throttle_entry.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(row_throttle, text="0 = unlimited",
                     text_color="gray").pack(side="left")

        self._section(right, "Multipart download",
                      "Splits files into N parallel segments. Requires Accept-Ranges. 1 = off.")
        row_seg = ctk.CTkFrame(right, fg_color="transparent")
        row_seg.pack(fill="x", padx=4, pady=(0, 10))
        self._seg_val_lbl = ctk.CTkLabel(row_seg, text="", width=30)
        self._seg_val_lbl.pack(side="right", padx=(6, 0))
        self._seg_slider = ctk.CTkSlider(
            row_seg, from_=1, to=16, number_of_steps=15,
            command=self._on_seg_slider)
        self._seg_slider.set(self._settings.get("segments", 4))
        self._seg_slider.pack(side="left", fill="x", expand=True)
        self._on_seg_slider(self._seg_slider.get())

    # ── Tab: Downloads ────────────────────────────────────────────────────
    def _build_tab_downloads(self):
        p = self._make_panel("downloads")

        self._section(p, "Desktop notifications",
                      "Alert when all downloads in a batch are complete.")
        row_notif = ctk.CTkFrame(p, fg_color="transparent")
        row_notif.pack(fill="x", padx=20, pady=(0, 14))
        self._notif_var = ctk.BooleanVar(value=self._settings.get("notifications", True))
        ctk.CTkCheckBox(row_notif, text="Enable notifications (requires plyer)",
                        variable=self._notif_var).pack(side="left")

        ctk.CTkFrame(p, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 4))

        self._section(p, "yt-dlp / streaming",
                      "Dependencies for streaming URL downloads (YouTube, Vimeo, etc.)")
        row_ff = ctk.CTkFrame(p, fg_color="transparent")
        row_ff.pack(fill="x", padx=20, pady=(0, 6))
        ctk.CTkLabel(row_ff, text="ffmpeg path:", width=130, anchor="w").pack(side="left")
        ctk.CTkLabel(row_ff, text="Auto-detected from app folder or PATH",
                     text_color="gray").pack(side="left")

        row_nd = ctk.CTkFrame(p, fg_color="transparent")
        row_nd.pack(fill="x", padx=20, pady=(0, 14))
        ctk.CTkLabel(row_nd, text="Node.js path:", width=130, anchor="w").pack(side="left")
        ctk.CTkLabel(row_nd, text="Auto-detected — or install via Settings → Remote",
                     text_color="gray").pack(side="left")

    # ── Tab: Extensions ───────────────────────────────────────────────────
    def _build_tab_extensions(self):
        p = self._make_panel("extensions")

        self._section(p, "Downloadable extensions",
                      "Only files with these extensions are detected during crawl.")

        row_allfiles = ctk.CTkFrame(p, fg_color="transparent")
        row_allfiles.pack(fill="x", padx=20, pady=(0, 10))
        self._all_files_var = ctk.BooleanVar(value=self._settings.get("all_files", False))
        ctk.CTkCheckBox(
            row_allfiles,
            text="All files  (disable extension filter during crawl)",
            variable=self._all_files_var,
        ).pack(side="left")

        ext_grid = ctk.CTkFrame(p, fg_color="transparent")
        ext_grid.pack(fill="x", padx=20, pady=(0, 4))

        saved_exts: dict = self._settings.get("extensions", DEFAULT_EXTENSIONS.copy())
        self._ext_vars: dict[str, ctk.BooleanVar] = {}

        predefined = list(DEFAULT_EXTENSIONS.keys())
        for i, ext in enumerate(predefined):
            var = ctk.BooleanVar(value=saved_exts.get(ext, DEFAULT_EXTENSIONS[ext]))
            self._ext_vars[ext] = var
            cb = ctk.CTkCheckBox(ext_grid, text=ext, variable=var, width=100)
            cb.grid(row=i // 4, column=i % 4, padx=6, pady=2, sticky="w")

        ctk.CTkFrame(p, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(8, 4))

        row_custom = ctk.CTkFrame(p, fg_color="transparent")
        row_custom.pack(fill="x", padx=20, pady=(4, 0))
        ctk.CTkLabel(row_custom, text="Add custom:").pack(side="left", padx=(0, 6))
        self._custom_ext_entry = ctk.CTkEntry(row_custom, width=100,
                                              placeholder_text=".ext")
        self._custom_ext_entry.pack(side="left", padx=(0, 8))
        ctk.CTkButton(row_custom, text="+ Add", width=90,
                      command=self._add_custom_ext).pack(side="left")

        self._custom_ext_frame = ctk.CTkFrame(p, fg_color="transparent")
        self._custom_ext_frame.pack(fill="x", padx=20, pady=0)

        _has_custom = False
        for ext, enabled in saved_exts.items():
            if ext not in DEFAULT_EXTENSIONS:
                self._add_custom_ext_row(ext, enabled)
                _has_custom = True
        if _has_custom:
            self._custom_ext_frame.pack_configure(pady=(0, 6))

    # ── Tab: Remote ───────────────────────────────────────────────────────
    def _build_tab_remote(self):
        p = self._make_panel("remote")
        self._build_remote_section(p)

    # ── Tab: System ───────────────────────────────────────────────────────
    def _build_tab_system(self):
        p = self._make_panel("system")

        self._section(p, "System tray & startup",
                      "Window behaviour and Windows startup.")

        try:
            import tray as _tray_mod
            startup_ok = True
        except ImportError:
            startup_ok = False

        row_sys = ctk.CTkFrame(p, fg_color="transparent")
        row_sys.pack(fill="x", padx=20, pady=(0, 6))
        self._startup_var = ctk.BooleanVar(
            value=_tray_mod.is_startup_enabled() if startup_ok else False)
        ctk.CTkCheckBox(
            row_sys, text="Start with Windows  (launches minimized in tray)",
            variable=self._startup_var,
            state="normal" if startup_ok else "disabled").pack(side="left")

        row_sys2 = ctk.CTkFrame(p, fg_color="transparent")
        row_sys2.pack(fill="x", padx=20, pady=(0, 14))
        self._minimize_tray_var = ctk.BooleanVar(
            value=self._settings.get("minimize_to_tray", True))
        ctk.CTkCheckBox(row_sys2,
                        text="Minimize to tray when closing the window",
                        variable=self._minimize_tray_var).pack(side="left")

        ctk.CTkFrame(p, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 8))

        self._section(p, "Protocol",
                      "Custom URL handler for browser extension integration.")
        ctk.CTkLabel(p, text="turbodownloader:// — registered automatically at each launch",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(
                         anchor="w", padx=20, pady=(0, 14))

        ctk.CTkFrame(p, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 8))

        self._section(p, "About", "")
        info = ctk.CTkFrame(p, fg_color="transparent")
        info.pack(fill="x", padx=20, pady=(0, 8))
        ctk.CTkLabel(info, text="Version:", width=130, anchor="w").pack(side="left")
        ctk.CTkLabel(info, text="2.5.1", text_color="gray").pack(side="left")

        info2 = ctk.CTkFrame(p, fg_color="transparent")
        info2.pack(fill="x", padx=20)
        ctk.CTkLabel(info2, text="Config folder:", width=130, anchor="w").pack(side="left")
        ctk.CTkLabel(info2, text="~/.turbodownloader/",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(side="left")

    @staticmethod
    def _section(parent, title: str, subtitle: str):
        ctk.CTkLabel(parent, text=title,
                     font=ctk.CTkFont(size=12, weight="bold")).pack(
                         anchor="w", padx=20, pady=(10, 2))
        ctk.CTkLabel(parent, text=subtitle,
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(
                         anchor="w", padx=20, pady=(0, 6))

    # ================================================================ Remote

    def _build_remote_section(self, content):
        """Builds the Remote Control section inside the settings scrollable frame."""
        ctk.CTkFrame(content, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 0))

        # ── Sub-section : Server ──────────────────────────────────────────────
        self._section(content, "Remote control — Server",
                      "Allow another TurboDownloader instance to monitor and control "
                      "downloads on this machine over HTTPS.")

        # Enable / disable toggle
        row_ena = ctk.CTkFrame(content, fg_color="transparent")
        row_ena.pack(fill="x", padx=20, pady=(0, 6))

        self._remote_enabled_var = ctk.BooleanVar(
            value=self._settings.get("remote_enabled", False))
        ctk.CTkSwitch(row_ena, text="Enable remote server",
                      variable=self._remote_enabled_var,
                      command=self._on_remote_toggle).pack(side="left")

        self._remote_status_lbl = ctk.CTkLabel(
            row_ena, text="", font=ctk.CTkFont(size=11))
        self._remote_status_lbl.pack(side="right")

        # Collapsible server config — wrapped in a fixed placeholder so that
        # pack order is preserved when the frame is shown/hidden.
        self._remote_cfg_wrapper = ctk.CTkFrame(content, fg_color="transparent")
        self._remote_cfg_wrapper.pack(fill="x", padx=0, pady=0)

        self._remote_cfg = ctk.CTkFrame(self._remote_cfg_wrapper, fg_color="#1e1e1e", corner_radius=8)
        if self._remote_enabled_var.get():
            self._remote_cfg.pack(fill="x", padx=20, pady=(0, 8))
        self._populate_remote_cfg()
        self._refresh_remote_badge()

        # ── Separator ─────────────────────────────────────────────────────────
        ctk.CTkFrame(content, height=1, fg_color="#2a2a2a").pack(fill="x", padx=20, pady=(6, 0))

        # ── Sub-section : Client ──────────────────────────────────────────────
        self._section(content, "Remote control — Client",
                      "Connect this instance to a remote TurboDownloader server.\n"
                      "When connected, downloads are sent to and run on the remote machine.")

        client_frame = ctk.CTkFrame(content, fg_color="#1e1e1e", corner_radius=8)
        client_frame.pack(fill="x", padx=20, pady=(0, 12))

        # Host
        rc = ctk.CTkFrame(client_frame, fg_color="transparent")
        rc.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(rc, text="Host / IP:", width=130, anchor="w").pack(side="left")
        self._rclient_host_e = ctk.CTkEntry(rc, width=180,
                                            placeholder_text="192.168.1.x or hostname")
        self._rclient_host_e.insert(0, self._settings.get("remote_client_host", ""))
        self._rclient_host_e.pack(side="left")

        # Port
        rc2 = ctk.CTkFrame(client_frame, fg_color="transparent")
        rc2.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(rc2, text="Port:", width=130, anchor="w").pack(side="left")
        self._rclient_port_e = ctk.CTkEntry(rc2, width=80)
        self._rclient_port_e.insert(0, str(self._settings.get("remote_client_port", 9988)))
        self._rclient_port_e.pack(side="left")

        # Username
        rc3 = ctk.CTkFrame(client_frame, fg_color="transparent")
        rc3.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(rc3, text="Username:", width=130, anchor="w").pack(side="left")
        self._rclient_user_e = ctk.CTkEntry(rc3, width=180)
        self._rclient_user_e.insert(0, self._settings.get("remote_client_user", ""))
        self._rclient_user_e.pack(side="left")

        # Password — optionally saved (local network use)
        rc4 = ctk.CTkFrame(client_frame, fg_color="transparent")
        rc4.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(rc4, text="Password:", width=130, anchor="w").pack(side="left")
        self._rclient_pass_e = ctk.CTkEntry(rc4, width=180, show="•",
                                            placeholder_text="Enter password")
        saved_pwd = _decrypt_password(self._settings.get("remote_client_password", ""))
        if saved_pwd:
            self._rclient_pass_e.insert(0, saved_pwd)
        self._rclient_pass_e.pack(side="left")

        # Save password checkbox
        self._rclient_savepwd_var = ctk.BooleanVar(
            value=self._settings.get("remote_client_save_password", False))
        ctk.CTkCheckBox(rc4, text="Save password",
                        variable=self._rclient_savepwd_var,
                        font=ctk.CTkFont(size=11),
                        width=20).pack(side="left", padx=(10, 0))

        # Auto-connect on startup + auto-retry row
        rc4b = ctk.CTkFrame(client_frame, fg_color="transparent")
        rc4b.pack(fill="x", padx=14, pady=(0, 4))
        self._rclient_autoconnect_var = ctk.BooleanVar(
            value=self._settings.get("remote_client_autoconnect", False))
        ctk.CTkCheckBox(rc4b, text="Auto-connect on startup",
                        variable=self._rclient_autoconnect_var,
                        font=ctk.CTkFont(size=11),
                        width=20).pack(side="left")
        self._rclient_autoretry_var = ctk.BooleanVar(
            value=self._settings.get("remote_client_autoretry", True))
        ctk.CTkCheckBox(rc4b, text="Auto-retry on disconnect",
                        variable=self._rclient_autoretry_var,
                        font=ctk.CTkFont(size=11),
                        width=20).pack(side="left", padx=(16, 0))

        # Default remote destination
        rc5 = ctk.CTkFrame(client_frame, fg_color="transparent")
        rc5.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(rc5, text="Remote dest.:", width=130, anchor="w").pack(side="left")
        self._rclient_dest_e = ctk.CTkEntry(rc5, width=180,
                                            placeholder_text="e.g. D:\\Medias  (path on remote PC)")
        self._rclient_dest_e.insert(0, self._settings.get("remote_client_dest", ""))
        self._rclient_dest_e.pack(side="left")
        self._rclient_browse_btn = ctk.CTkButton(
            rc5, text="📂 Browse…", width=100,
            command=self._browse_remote_dest,
            state="disabled")
        self._rclient_browse_btn.pack(side="left", padx=(6, 0))

        # Info box about remote dest behaviour
        info = ctk.CTkFrame(client_frame, fg_color="#1a2a1a", corner_radius=4)
        info.pack(fill="x", padx=14, pady=(2, 4))
        ctk.CTkLabel(info,
                     text="ℹ  The destination path is resolved on the remote machine.\n"
                          "   Leave blank to use the remote server's default folder.\n"
                          "   📂 Browse is available once connected.",
                     text_color="#7aaa7a", font=ctk.CTkFont(size=10),
                     justify="left").pack(padx=10, pady=6, anchor="w")

        # Connect / Disconnect button row
        rc6 = ctk.CTkFrame(client_frame, fg_color="transparent")
        rc6.pack(fill="x", padx=14, pady=(4, 10))

        self._rclient_status_lbl = ctk.CTkLabel(
            rc6, text="⚫ Not connected", text_color="#888888",
            font=ctk.CTkFont(size=11))
        self._rclient_status_lbl.pack(side="left")

        self._rclient_btn = ctk.CTkButton(
            rc6, text="Connect", width=110, fg_color="#1f6aa5",
            command=self._toggle_remote_client)
        self._rclient_btn.pack(side="right")
        self._refresh_client_badge()

    def _populate_remote_cfg(self):
        """Fills the collapsible remote config frame."""
        p = self._remote_cfg

        # Port
        r = ctk.CTkFrame(p, fg_color="transparent")
        r.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(r, text="Port:", width=130, anchor="w").pack(side="left")
        self._remote_port_e = ctk.CTkEntry(r, width=80)
        self._remote_port_e.insert(0, str(self._settings.get("remote_port", 9988)))
        self._remote_port_e.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(r, text="(restart needed to change port)",
                     text_color="gray", font=ctk.CTkFont(size=10)).pack(side="left")

        # Username
        r2 = ctk.CTkFrame(p, fg_color="transparent")
        r2.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(r2, text="Username:", width=130, anchor="w").pack(side="left")
        self._remote_user_e = ctk.CTkEntry(r2, width=180)
        self._remote_user_e.insert(0, self._settings.get("remote_username", ""))
        self._remote_user_e.pack(side="left")

        # Password
        r3 = ctk.CTkFrame(p, fg_color="transparent")
        r3.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(r3, text="New password:", width=130, anchor="w").pack(side="left")
        self._remote_pass_e = ctk.CTkEntry(r3, width=180, show="•",
                                           placeholder_text="Leave blank to keep current")
        self._remote_pass_e.pack(side="left")
        has_pwd = bool(self._settings.get("remote_password_hash"))
        ctk.CTkLabel(r3,
                     text="✓ Password set" if has_pwd else "⚠ No password set",
                     text_color="#2e8b57" if has_pwd else "#f0a500",
                     font=ctk.CTkFont(size=10)).pack(side="left", padx=(8, 0))

        # SSL cert
        r4 = ctk.CTkFrame(p, fg_color="transparent")
        r4.pack(fill="x", padx=14, pady=(0, 4))
        self._ssl_row = r4
        self._render_ssl_row()

        # Missing deps warning
        try:
            from remote_server import DEPS_OK, DEPS_MISSING
            if not DEPS_OK:
                warn = ctk.CTkFrame(p, fg_color="#2a1800", corner_radius=4)
                warn.pack(fill="x", padx=14, pady=(4, 0))
                ctk.CTkLabel(warn,
                             text=f"⚠  Missing packages:\n"
                                  f"pip install {chr(32).join(DEPS_MISSING)}",
                             text_color="#f0a500",
                             font=ctk.CTkFont(size=11),
                             justify="left").pack(padx=10, pady=6, anchor="w")
        except ImportError:
            pass

        ctk.CTkFrame(p, height=10, fg_color="transparent").pack()

    def _render_ssl_row(self):
        for w in self._ssl_row.winfo_children():
            w.destroy()
        ctk.CTkLabel(self._ssl_row, text="SSL cert:", width=130, anchor="w").pack(side="left")
        try:
            from remote_server import CERT_FILE, KEY_FILE
            exists = CERT_FILE.exists() and KEY_FILE.exists()
            txt    = f"✓ {CERT_FILE}" if exists else "⚠ Not yet generated"
            color  = "#2e8b57" if exists else "#f0a500"
            ctk.CTkLabel(self._ssl_row, text=txt, text_color=color,
                         font=ctk.CTkFont(size=10), wraplength=300,
                         anchor="w").pack(side="left")
            if not exists:
                ctk.CTkButton(self._ssl_row, text="Generate now", width=120,
                              fg_color="#1f6aa5",
                              command=self._gen_cert).pack(side="left", padx=(8, 0))
        except ImportError:
            ctk.CTkLabel(self._ssl_row, text="remote_server.py not found",
                         text_color="#cc4444",
                         font=ctk.CTkFont(size=10)).pack(side="left")

    def _gen_cert(self):
        from remote_server import ensure_ssl_cert
        ensure_ssl_cert()
        self._render_ssl_row()

    def _on_remote_toggle(self):
        if self._remote_enabled_var.get():
            self._remote_cfg.pack(fill="x", padx=20, pady=(0, 8))
        else:
            self._remote_cfg.pack_forget()
        self._refresh_remote_badge()

    def _refresh_remote_badge(self):
        if not self._remote_enabled_var.get():
            self._remote_status_lbl.configure(text="", text_color="gray")
            return
        srv = getattr(self.master, "_remote_server", None)
        if srv and srv.is_running:
            port = self._settings.get("remote_port", 9988)
            self._remote_status_lbl.configure(
                text=f"🟢 Running on :{port}", text_color="#2e8b57")
        else:
            self._remote_status_lbl.configure(
                text="⚫ Not running", text_color="#888888")

    def _refresh_client_badge(self):
        """Updates the client connection status badge."""
        client = getattr(self.master, "_remote_client", None)
        connected = client and client.connected
        if connected:
            host = self._settings.get("remote_client_host", "?")
            port = self._settings.get("remote_client_port", 9988)
            self._rclient_status_lbl.configure(
                text=f"🟢 Connected to {host}:{port}", text_color="#2e8b57")
            self._rclient_btn.configure(text="Disconnect", fg_color="#5a5a5a")
        else:
            self._rclient_status_lbl.configure(
                text="⚫ Not connected", text_color="#888888")
            self._rclient_btn.configure(text="Connect", fg_color="#1f6aa5")
        # Browse remote button — only active when connected
        if hasattr(self, "_rclient_browse_btn"):
            self._rclient_browse_btn.configure(
                state="normal" if connected else "disabled")

    def _toggle_remote_client(self):
        """Connect or disconnect the remote client from this settings window."""
        client = getattr(self.master, "_remote_client", None)

        # — Disconnect —
        if client and client.connected:
            self.master._remote_client = None
            self.master._update_remote_status_bar()
            self._refresh_client_badge()
            return

        # — Connect —
        host = self._rclient_host_e.get().strip()
        pwd  = self._rclient_pass_e.get()
        try:
            port = int(self._rclient_port_e.get().strip() or "9988")
        except ValueError:
            port = 9988
        user = self._rclient_user_e.get().strip()

        if not host or not user or not pwd:
            self._rclient_status_lbl.configure(
                text="⚠ Host, username and password required", text_color="#f0a500")
            return

        self._rclient_status_lbl.configure(
            text="⏳ Connecting…", text_color="#888888")
        self.update()

        try:
            from remote_server import RemoteClient
            c = RemoteClient(host, port, user, pwd)
            ok, msg = c.connect()
        except ImportError:
            self._rclient_status_lbl.configure(
                text="✗ remote_server.py not found", text_color="#cc4444")
            return

        if ok:
            # Save non-sensitive fields
            self._settings["remote_client_host"] = host
            self._settings["remote_client_port"] = port
            self._settings["remote_client_user"] = user
            self._settings["remote_client_dest"] = self._rclient_dest_e.get().strip()
            # Save password if option enabled
            if getattr(self, "_rclient_savepwd_var", None) and self._rclient_savepwd_var.get():
                self._settings["remote_client_password"] = _encrypt_password(pwd)
                self._settings["remote_client_save_password"] = True
            self.master._remote_client = c

            # Start heartbeat — auto-reconnect on disconnect
            def _on_disconnect():
                self.master.ui(self.master._update_remote_status_bar)
                print("[remote-client] Heartbeat: disconnected")

            def _on_reconnect():
                self.master.ui(self.master._update_remote_status_bar)
                print("[remote-client] Heartbeat: reconnected")

            c.start_heartbeat(
                on_disconnect=_on_disconnect,
                on_reconnect=_on_reconnect,
                interval=15,
                max_retries=0,   # infinite retries
            )

            # Start global download tracker — syncs all server DLs to client UI
            self.master._start_remote_dl_tracker()

            self.master._update_remote_status_bar()
            self._refresh_client_badge()
        else:
            self._rclient_status_lbl.configure(
                text=f"✗ {msg[:60]}", text_color="#cc4444")

    def _on_seg_slider(self, val):
        n = int(round(val))
        self._seg_val_lbl.configure(
            text=f"{n}" if n > 1 else "1 (disabled)"
        )

    def _add_custom_ext(self):
        raw = self._custom_ext_entry.get().strip().lower()
        if not raw:
            return
        ext = raw if raw.startswith(".") else f".{raw}"
        if ext in self._ext_vars:
            return  # déjà présente
        self._add_custom_ext_row(ext, True)
        self._custom_ext_frame.pack_configure(pady=(0, 6))  # ajoute l'espace une fois qu'il y a du contenu
        self._custom_ext_entry.delete(0, "end")

    def _add_custom_ext_row(self, ext: str, enabled: bool):
        var = ctk.BooleanVar(value=enabled)
        self._ext_vars[ext] = var
        row = ctk.CTkFrame(self._custom_ext_frame, fg_color="transparent")
        row.pack(side="left", padx=(0, 4))
        ctk.CTkCheckBox(row, text=ext, variable=var, width=100).pack(side="left")
        ctk.CTkButton(row, text="✕", width=28, height=24,
                      fg_color="#8B0000", hover_color="#a00000",
                      command=lambda e=ext, r=row: self._remove_custom_ext(e, r),
                      font=ctk.CTkFont(size=10)).pack(side="left", padx=(2, 0))

    def _remove_custom_ext(self, ext: str, row_frame):
        self._ext_vars.pop(ext, None)
        row_frame.destroy()
        # Si plus aucune ext custom, supprimer le padding résiduel
        if not self._custom_ext_frame.winfo_children():
            self._custom_ext_frame.pack_configure(pady=0)

    def _browse_dest(self):
        folder = filedialog.askdirectory(title="Choose default destination folder")
        if folder:
            self._dest_entry.delete(0, "end")
            self._dest_entry.insert(0, folder)

    def _browse_remote_dest(self):
        """Opens a remote file browser to pick a folder on the server."""
        client = getattr(self.master, "_remote_client", None)
        if not client or not client.connected:
            return
        _RemoteBrowsePopup(self, client, callback=lambda path: (
            self._rclient_dest_e.delete(0, "end"),
            self._rclient_dest_e.insert(0, path)
        ))

    def _reset_dest(self):
        self._dest_entry.delete(0, "end")
        self._dest_entry.insert(0, DEFAULT_DEST_DIR)

    def _browse_temp(self):
        folder = filedialog.askdirectory(title="Choisir le dossier temporaire")
        if folder:
            self._temp_entry.delete(0, "end")
            self._temp_entry.insert(0, folder)

    def _reset_default(self):
        self._temp_entry.delete(0, "end")
        self._temp_entry.insert(0, DEFAULT_TEMP_DIR)

    def _save(self):
        print("[settings] _save called")

        self._settings["temp_dir"]     = self._temp_entry.get().strip() or DEFAULT_TEMP_DIR
        self._settings["default_dest"] = self._dest_entry.get().strip() or DEFAULT_DEST_DIR

        try:
            retry_max = int(self._retry_max_entry.get().strip() or "3")
            self._settings["retry_max"] = max(0, min(10, retry_max))
        except ValueError:
            self._settings["retry_max"] = 3

        try:
            retry_delay = int(self._retry_delay_entry.get().strip() or "5")
            self._settings["retry_delay"] = max(1, min(60, retry_delay))
        except ValueError:
            self._settings["retry_delay"] = 5

        try:
            raw = self._throttle_entry.get().strip().replace(",", ".")
            throttle = float(raw) if raw else 0.0
            self._settings["throttle"] = max(0.0, throttle)
            print(f"[settings] throttle={self._settings['throttle']} MB/s")
        except ValueError as e:
            print(f"[settings] throttle parse error: {e!r}")
            self._settings["throttle"] = 0.0

        # Workers
        try:
            workers = int(self._workers_entry.get().strip() or "10")
            self._settings["workers"] = max(1, min(20, workers))
        except ValueError:
            self._settings["workers"] = 10

        # Notifications
        self._settings["notifications"] = self._notif_var.get()

        # System — tray + startup
        if getattr(self, "_minimize_tray_var", None):
            self._settings["minimize_to_tray"] = self._minimize_tray_var.get()
        if getattr(self, "_startup_var", None):
            try:
                import tray as _tray_mod
                _tray_mod.set_startup(self._startup_var.get())
            except ImportError:
                pass

        # Multipart segments
        self._settings["segments"] = int(round(self._seg_slider.get()))

        # Extensions
        self._settings["extensions"] = {
            ext: var.get() for ext, var in self._ext_vars.items()
        }

        # All files mode
        self._settings["all_files"] = self._all_files_var.get()

        # ── Remote control ─────────────────────────────────────────────
        self._settings["remote_enabled"] = self._remote_enabled_var.get()
        try:
            port = int(self._remote_port_e.get().strip() or "9988")
            self._settings["remote_port"] = max(1024, min(65535, port))
        except (ValueError, AttributeError):
            self._settings["remote_port"] = 9988

        user = getattr(self, "_remote_user_e", None)
        if user:
            self._settings["remote_username"] = user.get().strip()

        pwd_raw = getattr(self, "_remote_pass_e", None)
        if pwd_raw:
            pwd = pwd_raw.get()
            if pwd:   # blank = keep existing hash
                from remote_server import hash_password
                self._settings["remote_password_hash"] = hash_password(pwd)

        # ── Remote client ───────────────────────────────────────────────
        if getattr(self, "_rclient_host_e", None):
            self._settings["remote_client_host"] = self._rclient_host_e.get().strip()
        if getattr(self, "_rclient_user_e", None):
            self._settings["remote_client_user"] = self._rclient_user_e.get().strip()
        if getattr(self, "_rclient_dest_e", None):
            self._settings["remote_client_dest"] = self._rclient_dest_e.get().strip()
        try:
            self._settings["remote_client_port"] = int(
                self._rclient_port_e.get().strip() or "9988")
        except (ValueError, AttributeError):
            pass
        # Save password if checkbox is checked
        if getattr(self, "_rclient_savepwd_var", None):
            save_pwd = self._rclient_savepwd_var.get()
            self._settings["remote_client_save_password"] = save_pwd
            if save_pwd and getattr(self, "_rclient_pass_e", None):
                self._settings["remote_client_password"] = _encrypt_password(
                    self._rclient_pass_e.get())
            elif not save_pwd:
                self._settings["remote_client_password"] = ""
        if getattr(self, "_rclient_autoconnect_var", None):
            self._settings["remote_client_autoconnect"] = self._rclient_autoconnect_var.get()
        if getattr(self, "_rclient_autoretry_var", None):
            self._settings["remote_client_autoretry"] = self._rclient_autoretry_var.get()

        save_settings(self._settings)
        self._on_save()
        self.destroy()

# ─────────────────────────────────────────────────────────────── Remote Browser

class _RemoteBrowsePopup(ctk.CTkToplevel):
    """
    Modal file browser — can navigate either the remote server filesystem
    (via /browse) or the local filesystem, switchable via a toggle.

    Navigation:
      - Single click on a folder  → select it as destination (highlighted)
      - Double click on a folder  → enter it
      - ⬆ Up button               → go to parent directory
      - ✔ Select this folder      → confirm and close

    Large directories are paginated (PAGE_SIZE entries at a time) to avoid
    freezing the UI when a folder contains thousands of files.
    """

    PAGE_SIZE = 200   # max entries rendered at once

    def __init__(self, master, client, callback):
        super().__init__(master)
        self.title("Browse filesystem")
        self.geometry("540x520")
        self.resizable(True, True)
        self.grab_set()

        self._client      = client
        self._callback    = callback
        self._current     = ""      # current browsed path
        self._parent      = None    # parent path (None = at root)
        self._selected    = ""      # single-clicked folder
        self._all_entries = []      # full list from server or local
        self._page        = 0       # current page index
        self._last_click_time  = 0.0
        self._last_click_path  = ""
        self._mode        = "remote" if client else "local"  # "remote" | "local"
        self._fetch_gen   = 0       # incremented on each navigate — stale results are ignored

        # ── Mode toggle bar ───────────────────────────────────────────────────
        toggle_bar = ctk.CTkFrame(self, fg_color="#1a1a1a")
        toggle_bar.pack(fill="x", padx=0, pady=0)

        ctk.CTkLabel(toggle_bar, text="Browse:",
                     font=ctk.CTkFont(size=11), text_color="#666").pack(
            side="left", padx=(12, 6), pady=6)

        self._mode_var = ctk.StringVar(value=self._mode)

        self._btn_remote = ctk.CTkButton(
            toggle_bar, text="🖥 Remote server", width=130, height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#1f6aa5" if self._mode == "remote" else "transparent",
            border_width=1, border_color="#1f6aa5" if self._mode == "remote" else "#333",
            state="normal" if client else "disabled",
            command=lambda: self._switch_mode("remote"))
        self._btn_remote.pack(side="left", padx=(0, 4), pady=6)

        self._btn_local = ctk.CTkButton(
            toggle_bar, text="💻 This PC", width=100, height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#1f6aa5" if self._mode == "local" else "transparent",
            border_width=1, border_color="#1f6aa5" if self._mode == "local" else "#333",
            command=lambda: self._switch_mode("local"))
        self._btn_local.pack(side="left", pady=6)

        # ── Navigation bar ────────────────────────────────────────────────────
        nav = ctk.CTkFrame(self, fg_color="#222222")
        nav.pack(fill="x", padx=0, pady=0)

        self._up_btn = ctk.CTkButton(
            nav, text="⬆ Up", width=70, height=28,
            fg_color="transparent", border_width=1, border_color="#333333",
            font=ctk.CTkFont(size=12),
            command=self._go_up, state="disabled")
        self._up_btn.pack(side="left", padx=(10, 6), pady=6)

        self._path_lbl = ctk.CTkLabel(
            nav, text="", text_color="#888888",
            font=ctk.CTkFont(size=10), anchor="w")
        self._path_lbl.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=6)

        # ── Selected destination display ──────────────────────────────────────
        self._dest_frame = ctk.CTkFrame(self, fg_color="#0d1a0d", corner_radius=0)
        self._dest_lbl   = ctk.CTkLabel(
            self._dest_frame, text="No folder selected",
            text_color="#555555", font=ctk.CTkFont(size=11), anchor="w")
        self._dest_lbl.pack(fill="x", padx=12, pady=5)
        self._dest_frame.pack(fill="x", padx=0, pady=0)

        # ── Pagination bar (created before scroll so pack order is correct) ──
        self._pager = ctk.CTkFrame(self, fg_color="#1a1a1a")
        self._prev_btn = ctk.CTkButton(
            self._pager, text="◀ Prev", width=90, height=26,
            fg_color="transparent", border_width=1, border_color="#333",
            font=ctk.CTkFont(size=11), command=self._prev_page)
        self._prev_btn.pack(side="left", padx=8, pady=4)
        self._page_lbl = ctk.CTkLabel(
            self._pager, text="", text_color="#666", font=ctk.CTkFont(size=11))
        self._page_lbl.pack(side="left", expand=True)
        self._next_btn = ctk.CTkButton(
            self._pager, text="Next ▶", width=90, height=26,
            fg_color="transparent", border_width=1, border_color="#333",
            font=ctk.CTkFont(size=11), command=self._next_page)
        self._next_btn.pack(side="right", padx=8, pady=4)
        # pager shown only when needed (pack_forget by default)

        # ── Entry list ────────────────────────────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(self)
        self._scroll.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Footer ────────────────────────────────────────────────────────────
        foot = ctk.CTkFrame(self, fg_color="#2b2b2b")
        foot.pack(fill="x", padx=0, pady=0)

        self._select_btn = ctk.CTkButton(
            foot, text="✔ Select this folder", width=170,
            fg_color="#1f6aa5", state="disabled",
            command=self._confirm_selection)
        self._select_btn.pack(side="left", padx=12, pady=8)

        ctk.CTkButton(foot, text="Cancel", width=100,
                      fg_color="#5a5a5a",
                      command=self.destroy).pack(side="right", padx=12, pady=8)

        self._navigate("")
        _center_on_master(self, master)

    # ── Mode switch ────────────────────────────────────────────────────────────

    def _switch_mode(self, mode: str):
        """Switches between remote and local filesystem browsing."""
        if mode == self._mode:
            return
        self._mode    = mode
        self._current = ""
        self._parent  = None
        self._selected = ""
        self._dest_lbl.configure(text="No folder selected", text_color="#555555")
        self._dest_frame.configure(fg_color="#0d1a0d")
        self._select_btn.configure(state="disabled")

        # Update toggle button styles
        self._btn_remote.configure(
            fg_color="#1f6aa5" if mode == "remote" else "transparent",
            border_color="#1f6aa5" if mode == "remote" else "#333")
        self._btn_local.configure(
            fg_color="#1f6aa5" if mode == "local" else "transparent",
            border_color="#1f6aa5" if mode == "local" else "#333")

        self._navigate("")

    # ── Navigation ─────────────────────────────────────────────────────────────

    def _navigate(self, path: str):
        """Fetches folder contents — remote via API, local via os.listdir."""
        self._fetch_gen += 1
        gen = self._fetch_gen

        self._path_lbl.configure(text="Loading…", text_color="#888888")
        self._up_btn.configure(state="disabled")
        for w in self._scroll.winfo_children():
            w.destroy()

        if self._mode == "local":
            # Local is instant — run in thread anyway to keep UI responsive
            import threading as _th
            def _fetch_local():
                self._navigate_local(path, gen)
            _th.Thread(target=_fetch_local, daemon=True, name="BrowseFetch").start()
        else:
            import threading as _th
            def _fetch():
                data = self._client.browse(path)
                # Only apply result if this is still the latest navigation
                if self._fetch_gen == gen:
                    self.after(0, lambda: self._on_data(data, path))
            _th.Thread(target=_fetch, daemon=True, name="BrowseFetch").start()

    def _navigate_local(self, path: str, gen: int = 0):
        """Builds folder data from the local filesystem."""
        import os as _os, sys as _sys

        if not path:
            if _sys.platform == "win32":
                import string
                entries = [
                    {"name": f"{d}:\\", "path": f"{d}:\\", "is_dir": True}
                    for d in string.ascii_uppercase
                    if _os.path.exists(f"{d}:\\")
                ]
                data = {"path": "", "parent": None, "entries": entries}
            else:
                path = "/"
                data = self._build_local_data("/")
        else:
            data = self._build_local_data(path)

        if self._fetch_gen == gen:
            self.after(0, lambda: self._on_data(data, path))

    def _build_local_data(self, path: str) -> dict:
        """Reads a local directory and returns data in the same format as /browse."""
        import os as _os, pathlib as _pl
        p = _pl.Path(path)
        parent = str(p.parent) if str(p.parent) != str(p) else None

        entries = []
        try:
            for name in sorted(_os.listdir(path)):
                full = _os.path.join(path, name)
                try:
                    is_dir = _os.path.isdir(full)
                    entries.append({"name": name, "path": full, "is_dir": is_dir})
                except PermissionError:
                    pass
        except PermissionError:
            pass
        return {"path": path, "parent": parent, "entries": entries}

    def _on_data(self, data, requested_path: str):
        """Called on UI thread once server data is available."""
        if not self.winfo_exists():
            return

        if data is None:
            self._path_lbl.configure(
                text="⚠ Could not reach server", text_color="#cc4444")
            return

        self._current     = data.get("path", requested_path)
        self._parent      = data.get("parent")
        self._all_entries = data.get("entries", [])
        self._page        = 0

        # Update nav bar
        self._path_lbl.configure(
            text=self._current or "(Drives)", text_color="#888888")
        self._up_btn.configure(
            state="normal" if (self._parent is not None or self._current) else "disabled")

        # Auto-select current folder as destination (user navigated here intentionally)
        if self._current:
            short = self._current if len(self._current) <= 55 else "…" + self._current[-52:]
            self._dest_lbl.configure(text=f"📁 {short}", text_color="#7aaa7a")
            self._dest_frame.configure(fg_color="#0d1f0d")
            self._select_btn.configure(state="normal")
        else:
            self._dest_lbl.configure(text="No folder selected", text_color="#555555")
            self._dest_frame.configure(fg_color="#0d1a0d")
            self._select_btn.configure(state="disabled")

        self._render_page()

    def _render_page(self):
        """Renders PAGE_SIZE entries for the current page."""
        # Clear
        for w in self._scroll.winfo_children():
            w.destroy()

        # Reset scroll position to top
        try:
            self._scroll._parent_canvas.yview_moveto(0)
        except Exception:
            pass

        dirs  = [e for e in self._all_entries if e["is_dir"]]
        files = [e for e in self._all_entries if not e["is_dir"]]
        ordered = dirs + files

        total      = len(ordered)
        page_count = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        start      = self._page * self.PAGE_SIZE
        end        = min(start + self.PAGE_SIZE, total)
        page_items = ordered[start:end]

        for entry in page_items:
            self._make_entry(entry)

        # Pagination bar
        if page_count > 1:
            self._page_lbl.configure(
                text=f"Page {self._page + 1} / {page_count}  ({total} items)")
            self._prev_btn.configure(state="normal" if self._page > 0 else "disabled")
            self._next_btn.configure(state="normal" if self._page < page_count - 1 else "disabled")
            self._pager.pack(fill="x", padx=0)
        else:
            self._pager.pack_forget()

    def _make_entry(self, entry: dict):
        """Creates one row. Single click = select, double click = enter (dirs only)."""
        is_dir = entry["is_dir"]
        path   = entry["path"]
        name   = entry["name"]
        label  = f"📁  {name}" if is_dir else f"    {name}"

        btn = ctk.CTkButton(
            self._scroll, text=label, anchor="w",
            fg_color="transparent",
            hover_color="#1a2a3a" if is_dir else "#1a1a1a",
            text_color="#cccccc" if is_dir else "#555555",
            font=ctk.CTkFont(size=12),
            height=28,
            command=lambda p=path, d=is_dir: self._on_click(p, d),
        )
        btn.pack(fill="x", padx=4, pady=1)

        # Highlight if this is the selected folder
        if is_dir and path == self._selected:
            btn.configure(fg_color="#1a3a1a", border_width=1, border_color="#2a5a2a")

    def _on_click(self, path: str, is_dir: bool):
        """Click on a folder → enter it immediately. Select button confirms current folder."""
        if not is_dir:
            return
        # Reset selection when navigating
        self._selected = ""
        self._dest_lbl.configure(text="No folder selected", text_color="#555555")
        self._dest_frame.configure(fg_color="#0d1a0d")
        self._select_btn.configure(state="disabled")
        self._navigate(path)

    def _go_up(self):
        target = self._parent if self._parent is not None else ""
        self._selected = ""
        self._dest_lbl.configure(text="No folder selected", text_color="#555555")
        self._dest_frame.configure(fg_color="#0d1a0d")
        self._select_btn.configure(state="disabled")
        self._navigate(target)

    def _prev_page(self):
        if self._page > 0:
            self._page -= 1
            self._render_page()

    def _next_page(self):
        total      = len(self._all_entries)
        page_count = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        if self._page < page_count - 1:
            self._page += 1
            self._render_page()

    def _confirm_selection(self):
        path = self._current
        if path:
            self._callback(path)
            self.destroy()
