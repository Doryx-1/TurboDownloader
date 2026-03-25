import os
import json
import pathlib

import customtkinter as ctk
from tkinter import filedialog


# Config file path: C:/Users/<user>/.turbodownloader/settings.json
CONFIG_DIR  = pathlib.Path.home() / ".turbodownloader"
CONFIG_FILE = CONFIG_DIR / "settings.json"
DEST_HISTORY_FILE = CONFIG_DIR / "dest_history.json"
DEST_HISTORY_MAX  = 10


def load_dest_history() -> list:
    """Loads the destination history from its dedicated file."""
    if DEST_HISTORY_FILE.exists():
        try:
            import json as _json
            data = _json.loads(DEST_HISTORY_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            pass
    return []


def save_dest_history(history: list):
    """Saves the destination history to its dedicated file."""
    import json as _json
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        DEST_HISTORY_FILE.write_text(
            _json.dumps(history[:DEST_HISTORY_MAX], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        print(f"[dest_history] save error: {e}")

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
        "check_updates":    True,    # check for updates at startup
        "workers":       10,
        "segments":      4,
        "file_exists_action": "ask",   # ask / replace / skip / rename
        "extensions":    DEFAULT_EXTENSIONS.copy(),
        "all_files":     False,
        # ── Remote control ─────────────────────
        "remote_enabled":       False,
        "remote_port":          9988,
        "remote_username":      "",
        "remote_password_hash": "",
        "remote_jwt_secret":    "",
        "remote_jwt_ttl_h":     24,    # JWT token validity: 1 / 8 / 24 / 168h
        # client-side connection info
        "remote_client_host":           "",
        "remote_client_port":           9988,
        "remote_client_user":           "",
        "remote_client_password":       "",
        "remote_client_save_password":  False,
        "remote_client_autoconnect":    False,
        "remote_client_autoretry":      True,
        # destination history — loaded from dedicated file, not settings.json
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

    # Override dest_history from its dedicated file (more up-to-date)
    defaults["dest_history"] = load_dest_history()
    return defaults


def save_settings(settings: dict):
    """Saves settings to disk. Restricts file permissions on Unix."""
    import sys as _sys
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        # Restrict read permissions — file contains JWT secret + encrypted password
        if _sys.platform != "win32":
            import os as _os
            _os.chmod(CONFIG_FILE, 0o600)
    except Exception as e:
        print(f"[settings] save error: {e}")


def _machine_key() -> bytes:
    """
    Returns (or generates) a random Fernet key stored in ~/.turbodownloader/keystore.
    Generated once, persisted across reboots, unique per installation.
    """
    import pathlib, sys
    from cryptography.fernet import Fernet
    keystore = pathlib.Path.home() / ".turbodownloader" / "keystore"
    if keystore.exists():
        return keystore.read_bytes()
    key = Fernet.generate_key()
    keystore.parent.mkdir(parents=True, exist_ok=True)
    keystore.write_bytes(key)
    if sys.platform != "win32":
        import os as _os
        _os.chmod(keystore, 0o600)
    return key


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

        # Clipboard monitor
        row_clip = ctk.CTkFrame(p, fg_color="transparent")
        row_clip.pack(fill="x", padx=20, pady=(0, 14))
        self._clipboard_var = ctk.BooleanVar(
            value=self._settings.get("clipboard_monitor", False))
        ctk.CTkCheckBox(row_clip,
                        text="Monitor clipboard for URLs  (auto-suggest when a file URL is copied)",
                        variable=self._clipboard_var,
                        command=self._on_clipboard_toggle).pack(side="left")

        ctk.CTkFrame(p, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 4))

        self._section(p, "File already exists",
                      "What to do when the destination file already exists.")
        row_fe = ctk.CTkFrame(p, fg_color="transparent")
        row_fe.pack(fill="x", padx=20, pady=(0, 14))
        ctk.CTkLabel(row_fe, text="Default action:", width=130, anchor="w").pack(side="left")
        self._file_exists_var = ctk.StringVar(
            value=self._settings.get("file_exists_action", "ask"))
        fe_menu = ctk.CTkOptionMenu(
            row_fe, variable=self._file_exists_var, width=160,
            values=["ask", "replace", "skip", "rename"],
        )
        fe_menu.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(row_fe,
                     text="ask = popup each time  |  rename = add _2, _3…",
                     text_color="gray", font=ctk.CTkFont(size=10)).pack(side="left")

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
        import updater as _upd_ver
        ctk.CTkLabel(info, text=_upd_ver.APP_VERSION, text_color="gray").pack(side="left")

        # Check for updates toggle + manual button
        row_upd = ctk.CTkFrame(p, fg_color="transparent")
        row_upd.pack(fill="x", padx=20, pady=(0, 8))
        self._check_updates_var = ctk.BooleanVar(
            value=self._settings.get("check_updates", True))
        ctk.CTkCheckBox(row_upd,
                        text="Check for updates at startup",
                        variable=self._check_updates_var).pack(side="left")
        ctk.CTkButton(row_upd, text="Check now",
                      width=110, fg_color="transparent",
                      border_width=1, border_color="#3a3a3a",
                      hover_color="#2a2a2a",
                      font=ctk.CTkFont(size=11),
                      command=self._manual_check_update).pack(side="right")

        info2 = ctk.CTkFrame(p, fg_color="transparent")
        info2.pack(fill="x", padx=20)
        ctk.CTkLabel(info2, text="Config folder:", width=130, anchor="w").pack(side="left")
        ctk.CTkLabel(info2, text="~/.turbodownloader/",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(side="left")

    def _on_clipboard_toggle(self):
        self._settings["clipboard_monitor"] = self._clipboard_var.get()
        save_settings(self._settings)
        if hasattr(self.master, "_apply_clipboard_monitor"):
            self.master._apply_clipboard_monitor()

    def _manual_check_update(self):
        """Triggers a manual update check — shows popup even if up to date."""
        import updater as _upd
        _upd.check_for_updates(self.master, silent=False)

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
        """Builds the Remote Control section — two side-by-side cards (Server | Client)."""
        ctk.CTkFrame(content, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 8))
        self._section(content, "Remote control",
                      "Server: expose this instance over HTTPS.   "
                      "Client: control a remote TurboDownloader instance.")

        # ── Two-column card container ─────────────────────────────────────────
        cards = ctk.CTkFrame(content, fg_color="transparent")
        cards.pack(fill="x", padx=20, pady=(0, 16))
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)

        srv_card = ctk.CTkFrame(cards, fg_color="#1e1e1e", corner_radius=8)
        srv_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        cli_card = ctk.CTkFrame(cards, fg_color="#1e1e1e", corner_radius=8)
        cli_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self._populate_server_card(srv_card)
        self._populate_client_card(cli_card)

    def _populate_server_card(self, p):
        """Fills the Server card."""
        # ── Title ─────────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(p, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 6))
        ctk.CTkLabel(hdr, text="📡  Server",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")

        # ── Enable toggle + status ────────────────────────────────────────────
        row_ena = ctk.CTkFrame(p, fg_color="transparent")
        row_ena.pack(fill="x", padx=14, pady=(0, 6))
        self._remote_enabled_var = ctk.BooleanVar(
            value=self._settings.get("remote_enabled", False))
        ctk.CTkSwitch(row_ena, text="Enabled",
                      variable=self._remote_enabled_var,
                      command=self._on_remote_toggle).pack(side="left")
        self._remote_status_lbl = ctk.CTkLabel(
            row_ena, text="", font=ctk.CTkFont(size=11))
        self._remote_status_lbl.pack(side="right")

        ctk.CTkFrame(p, height=1, fg_color="#2a2a2a").pack(fill="x", padx=10, pady=(0, 6))

        # ── Port ──────────────────────────────────────────────────────────────
        r = ctk.CTkFrame(p, fg_color="transparent")
        r.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(r, text="Port:", width=110, anchor="w").pack(side="left")
        self._remote_port_e = ctk.CTkEntry(r, width=70)
        self._remote_port_e.insert(0, str(self._settings.get("remote_port", 9988)))
        self._remote_port_e.pack(side="left", padx=(0, 6))
        ctk.CTkLabel(r, text="(restart to change)",
                     text_color="gray", font=ctk.CTkFont(size=10)).pack(side="left")

        # ── Username ──────────────────────────────────────────────────────────
        r2 = ctk.CTkFrame(p, fg_color="transparent")
        r2.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(r2, text="Username:", width=110, anchor="w").pack(side="left")
        self._remote_user_e = ctk.CTkEntry(r2, width=160)
        self._remote_user_e.insert(0, self._settings.get("remote_username", ""))
        self._remote_user_e.pack(side="left")

        # ── Password ──────────────────────────────────────────────────────────
        r3 = ctk.CTkFrame(p, fg_color="transparent")
        r3.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(r3, text="New password:", width=110, anchor="w").pack(side="left")
        self._remote_pass_e = ctk.CTkEntry(r3, width=160, show="•",
                                           placeholder_text="Leave blank to keep")
        self._remote_pass_e.pack(side="left")
        has_pwd = bool(self._settings.get("remote_password_hash"))
        ctk.CTkLabel(r3,
                     text="✓ Set" if has_pwd else "⚠ Not set",
                     text_color="#2e8b57" if has_pwd else "#f0a500",
                     font=ctk.CTkFont(size=10)).pack(side="left", padx=(6, 0))

        # ── SSL cert ──────────────────────────────────────────────────────────
        r4 = ctk.CTkFrame(p, fg_color="transparent")
        r4.pack(fill="x", padx=14, pady=(0, 4))
        self._ssl_row = r4
        self._render_ssl_row()

        # ── JWT TTL + Revoke ──────────────────────────────────────────────────
        r5 = ctk.CTkFrame(p, fg_color="transparent")
        r5.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(r5, text="Token:", width=110, anchor="w").pack(side="left")
        self._jwt_ttl_var = ctk.StringVar(
            value=str(self._settings.get("remote_jwt_ttl_h", 24)))
        ctk.CTkOptionMenu(r5, variable=self._jwt_ttl_var, width=80,
                          values=["1", "8", "24", "168"]).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(r5, text="h", text_color="gray",
                     font=ctk.CTkFont(size=10)).pack(side="left")
        ctk.CTkButton(r5, text="🔄 Revoke tokens", width=130,
                      fg_color="transparent", border_width=1, border_color="#5a1515",
                      text_color="#cc4444", hover_color="#2a1010",
                      font=ctk.CTkFont(size=11),
                      command=self._revoke_tokens).pack(side="right")

        # ── Missing deps warning ──────────────────────────────────────────────
        try:
            from remote_server import DEPS_OK, DEPS_MISSING
            if not DEPS_OK:
                warn = ctk.CTkFrame(p, fg_color="#2a1800", corner_radius=4)
                warn.pack(fill="x", padx=14, pady=(4, 0))
                ctk.CTkLabel(warn,
                             text=f"⚠  Missing:\npip install {' '.join(DEPS_MISSING)}",
                             text_color="#f0a500", font=ctk.CTkFont(size=10),
                             justify="left").pack(padx=10, pady=6, anchor="w")
        except ImportError:
            pass

        ctk.CTkFrame(p, height=10, fg_color="transparent").pack()
        self._refresh_remote_badge()

    def _populate_client_card(self, p):
        """Fills the Client card."""
        # ── Title ─────────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(p, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 6))
        ctk.CTkLabel(hdr, text="🔗  Client",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")

        ctk.CTkFrame(p, height=1, fg_color="#2a2a2a").pack(fill="x", padx=10, pady=(0, 6))

        # ── Profiles ──────────────────────────────────────────────────────────
        rcp = ctk.CTkFrame(p, fg_color="transparent")
        rcp.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(rcp, text="Profiles:", width=80, anchor="w").pack(side="left")
        self._profiles = list(self._settings.get("remote_profiles", []))
        profile_names = [pr["name"] for pr in self._profiles] or ["(none)"]
        self._rclient_profile_cb = ctk.CTkComboBox(
            rcp, values=profile_names, width=150, state="readonly",
            command=self._load_profile)
        self._rclient_profile_cb.pack(side="left")
        ctk.CTkButton(rcp, text="💾", width=28, height=26,
                      command=self._save_profile,
                      fg_color="#1a2a1a", hover_color="#2a3a2a").pack(side="left", padx=(4, 0))
        ctk.CTkButton(rcp, text="🗑", width=28, height=26,
                      command=self._delete_profile,
                      fg_color="#2a1a1a", hover_color="#3a2a2a").pack(side="left", padx=(4, 0))

        # ── Host + Port on same row ───────────────────────────────────────────
        rc = ctk.CTkFrame(p, fg_color="transparent")
        rc.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(rc, text="Host / IP:", width=80, anchor="w").pack(side="left")
        self._rclient_host_e = ctk.CTkEntry(rc, width=140,
                                            placeholder_text="192.168.1.x")
        self._rclient_host_e.insert(0, self._settings.get("remote_client_host", ""))
        self._rclient_host_e.pack(side="left")
        ctk.CTkLabel(rc, text=":", text_color="gray").pack(side="left", padx=(4, 2))
        self._rclient_port_e = ctk.CTkEntry(rc, width=58)
        self._rclient_port_e.insert(0, str(self._settings.get("remote_client_port", 9988)))
        self._rclient_port_e.pack(side="left")

        # ── Username ──────────────────────────────────────────────────────────
        rc3 = ctk.CTkFrame(p, fg_color="transparent")
        rc3.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(rc3, text="Username:", width=80, anchor="w").pack(side="left")
        self._rclient_user_e = ctk.CTkEntry(rc3, width=160)
        self._rclient_user_e.insert(0, self._settings.get("remote_client_user", ""))
        self._rclient_user_e.pack(side="left")

        # ── Password + save checkbox ──────────────────────────────────────────
        rc4 = ctk.CTkFrame(p, fg_color="transparent")
        rc4.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(rc4, text="Password:", width=80, anchor="w").pack(side="left")
        self._rclient_pass_e = ctk.CTkEntry(rc4, width=160, show="•",
                                            placeholder_text="Password")
        saved_pwd = _decrypt_password(self._settings.get("remote_client_password", ""))
        if saved_pwd:
            self._rclient_pass_e.insert(0, saved_pwd)
        self._rclient_pass_e.pack(side="left")
        self._rclient_savepwd_var = ctk.BooleanVar(
            value=self._settings.get("remote_client_save_password", False))
        ctk.CTkCheckBox(rc4, text="Save", variable=self._rclient_savepwd_var,
                        font=ctk.CTkFont(size=11), width=20).pack(side="left", padx=(8, 0))

        # ── Auto-connect + Auto-retry ─────────────────────────────────────────
        rc4b = ctk.CTkFrame(p, fg_color="transparent")
        rc4b.pack(fill="x", padx=14, pady=(2, 4))
        self._rclient_autoconnect_var = ctk.BooleanVar(
            value=self._settings.get("remote_client_autoconnect", False))
        ctk.CTkCheckBox(rc4b, text="Auto-connect on startup",
                        variable=self._rclient_autoconnect_var,
                        font=ctk.CTkFont(size=11), width=20).pack(anchor="w")
        self._rclient_autoretry_var = ctk.BooleanVar(
            value=self._settings.get("remote_client_autoretry", True))
        ctk.CTkCheckBox(rc4b, text="Auto-retry on disconnect",
                        variable=self._rclient_autoretry_var,
                        font=ctk.CTkFont(size=11), width=20).pack(anchor="w", pady=(4, 0))

        # ── Remote destination ────────────────────────────────────────────────
        rc5 = ctk.CTkFrame(p, fg_color="transparent")
        rc5.pack(fill="x", padx=14, pady=(4, 2))
        ctk.CTkLabel(rc5, text="Remote dest.:", width=80, anchor="w").pack(side="left")
        self._rclient_dest_e = ctk.CTkEntry(rc5, width=140,
                                            placeholder_text="D:\\Medias")
        self._rclient_dest_e.insert(0, self._settings.get("remote_client_dest", ""))
        self._rclient_dest_e.pack(side="left")
        self._rclient_browse_btn = ctk.CTkButton(
            rc5, text="📂 Browse…", width=90,
            command=self._browse_remote_dest, state="disabled")
        self._rclient_browse_btn.pack(side="left", padx=(6, 0))

        # ── Info note ─────────────────────────────────────────────────────────
        info = ctk.CTkFrame(p, fg_color="#1a2a1a", corner_radius=4)
        info.pack(fill="x", padx=14, pady=(2, 6))
        ctk.CTkLabel(info,
                     text="ℹ  Path resolved on remote machine.\n"
                          "   📂 Browse available once connected.",
                     text_color="#7aaa7a", font=ctk.CTkFont(size=10),
                     justify="left").pack(padx=8, pady=5, anchor="w")

        # ── Status + Connect button ───────────────────────────────────────────
        rc6 = ctk.CTkFrame(p, fg_color="transparent")
        rc6.pack(fill="x", padx=14, pady=(2, 10))
        self._rclient_status_lbl = ctk.CTkLabel(
            rc6, text="⚫ Not connected", text_color="#888888",
            font=ctk.CTkFont(size=11))
        self._rclient_status_lbl.pack(side="left")
        self._rclient_btn = ctk.CTkButton(
            rc6, text="Connect", width=100, fg_color="#1f6aa5",
            command=self._toggle_remote_client)
        self._rclient_btn.pack(side="right")
        self._refresh_client_badge()

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

    def _revoke_tokens(self):
        """Regenerates the JWT secret — invalidates all existing tokens immediately."""
        import secrets as _sec
        self._settings["remote_jwt_secret"] = _sec.token_hex(32)
        from settings_popup import save_settings
        save_settings(self._settings)
        # Notify UI
        import customtkinter as _ctk
        popup = _ctk.CTkToplevel(self)
        popup.title("Tokens revoked")
        popup.geometry("360x120")
        popup.grab_set()
        _ctk.CTkLabel(popup,
                      text="✓ All tokens revoked.\nExisting clients must re-login.",
                      font=_ctk.CTkFont(size=13)).pack(expand=True)
        _ctk.CTkButton(popup, text="OK", command=popup.destroy).pack(pady=(0, 14))

    def _on_remote_toggle(self):
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

    def _profile_combo_values(self):
        return [p["name"] for p in self._profiles] if self._profiles else ["(none)"]

    def _load_profile(self, name):
        """Fill host/port/user/pass fields from the selected profile."""
        profile = next((p for p in self._profiles if p["name"] == name), None)
        if not profile:
            return
        for entry in [self._rclient_host_e]:
            entry.delete(0, "end")
            entry.insert(0, profile.get("host", ""))
        for entry in [self._rclient_port_e]:
            entry.delete(0, "end")
            entry.insert(0, str(profile.get("port", 9988)))
        for entry in [self._rclient_user_e]:
            entry.delete(0, "end")
            entry.insert(0, profile.get("user", ""))
        self._rclient_pass_e.delete(0, "end")
        self._rclient_pass_e.insert(0, _decrypt_password(profile.get("password", "")))

    def _save_profile(self):
        """Save current fields as a new or existing profile."""
        host = self._rclient_host_e.get().strip()
        port = self._rclient_port_e.get().strip()
        user = self._rclient_user_e.get().strip()
        pwd  = self._rclient_pass_e.get()
        if not host:
            return
        # Ask name via simple dialog
        import tkinter.simpledialog as sd
        name = sd.askstring("Save profile", "Profile name:", parent=self)
        if not name:
            return
        name = name.strip()
        # Update or add
        existing = next((p for p in self._profiles if p["name"] == name), None)
        entry = {"name": name, "host": host, "port": int(port or 9988),
                 "user": user, "password": _encrypt_password(pwd)}
        if existing:
            self._profiles[self._profiles.index(existing)] = entry
        else:
            self._profiles.append(entry)
        self._settings["remote_profiles"] = self._profiles
        save_settings(self._settings)
        self._rclient_profile_cb.configure(values=self._profile_combo_values())
        self._rclient_profile_cb.set(name)

    def _delete_profile(self):
        """Delete the currently selected profile."""
        name = self._rclient_profile_cb.get()
        self._profiles = [p for p in self._profiles if p["name"] != name]
        self._settings["remote_profiles"] = self._profiles
        save_settings(self._settings)
        vals = self._profile_combo_values()
        self._rclient_profile_cb.configure(values=vals)
        self._rclient_profile_cb.set(vals[0])

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

            def _on_version_mismatch(server_ver):
                from updater import APP_VERSION
                def _show():
                    self.master._remote_client = None
                    self.master._update_remote_status_bar()
                    if self.winfo_exists():
                        self._rclient_status_lbl.configure(
                            text=f"✗ Version incompatible — serveur v{server_ver}",
                            text_color="#cc4444")
                        self._show_version_mismatch_popup(
                            host=host, port=port, user=user, pwd=pwd,
                            server_ver=server_ver, client_ver=APP_VERSION,
                            client=c)
                self.master.ui(_show)

            c.start_heartbeat(
                on_disconnect=_on_disconnect,
                on_reconnect=_on_reconnect,
                on_version_mismatch=_on_version_mismatch,
                interval=15,
                max_retries=0,   # infinite retries
            )

            # Start global download tracker — syncs all server DLs to client UI
            self.master._start_remote_dl_tracker()

            self.master._update_remote_status_bar()
            self._refresh_client_badge()
        elif msg.startswith("VERSION_MISMATCH:"):
            server_ver = msg.split(":", 1)[1]
            from updater import APP_VERSION
            self._rclient_status_lbl.configure(
                text=f"✗ Version incompatible — serveur v{server_ver}",
                text_color="#cc4444")
            self._show_version_mismatch_popup(
                host=host, port=port, user=user, pwd=pwd,
                server_ver=server_ver, client_ver=APP_VERSION,
                client=c)
        else:
            self._rclient_status_lbl.configure(
                text=f"✗ {msg[:60]}", text_color="#cc4444")

    def _show_version_mismatch_popup(self, host, port, user, pwd,
                                      server_ver, client_ver, client):
        """Shows a blocking popup when client and server versions differ."""
        from updater import _parse_version
        server_is_older = _parse_version(server_ver) < _parse_version(client_ver)

        popup = ctk.CTkToplevel(self)
        popup.title("Version incompatible")
        popup.geometry("440x220")
        popup.resizable(False, False)
        popup.grab_set()
        _center_on_master(popup, self)

        ctk.CTkLabel(popup, text="⚠ Connexion refusée — versions incompatibles",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#f0a500").pack(pady=(20, 6))
        ctk.CTkLabel(popup,
                     text=f"Serveur : v{server_ver}     |     Ce client : v{client_ver}",
                     font=ctk.CTkFont(size=12), text_color="#888888").pack(pady=(0, 14))

        btn_frame = ctk.CTkFrame(popup, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20)

        def _update_server():
            popup.destroy()
            status = client.trigger_remote_update(username=user, password=pwd)
            if status == "update_started":
                self._rclient_status_lbl.configure(
                    text="⏳ MAJ lancée — reconnexion automatique…", text_color="#f0a500")
                import threading as _th
                _th.Thread(target=_poll_server_update, daemon=True,
                           name="ServerUpdatePoller").start()
            elif status == "already_up_to_date":
                self._rclient_status_lbl.configure(
                    text="ℹ Serveur déjà à la dernière version", text_color="#888888")
            elif status == "check_failed":
                self._rclient_status_lbl.configure(
                    text="✗ Le serveur n'a pas pu joindre GitHub", text_color="#cc4444")
            else:
                self._rclient_status_lbl.configure(
                    text="✗ Impossible de déclencher la MAJ serveur", text_color="#cc4444")

        def _poll_server_update(timeout=180, interval=12):
            """Background thread: waits for server to restart after update, then auto-connects."""
            import time as _t
            from remote_server import RemoteClient
            deadline = _t.time() + timeout
            _t.sleep(interval)   # give the server time to start its download
            while _t.time() < deadline:
                try:
                    c2 = RemoteClient(host, port, user, pwd)
                    ok, msg = c2.connect()
                    if ok:
                        def _apply():
                            self.master._remote_client = c2
                            self._settings["remote_client_host"] = host
                            self._settings["remote_client_port"] = port
                            self._settings["remote_client_user"] = user
                            self.master._update_remote_status_bar()
                            self.master._start_remote_dl_tracker()
                            if self.winfo_exists():
                                self._rclient_status_lbl.configure(
                                    text=f"Connecté à {host}:{port}", text_color="#44cc44")
                        self.master.ui(_apply)
                        return
                    # Version still mismatched or other error — keep waiting silently
                except Exception:
                    pass
                _t.sleep(interval)
            # Timeout — let the user reconnect manually
            def _timeout():
                if self.winfo_exists():
                    self._rclient_status_lbl.configure(
                        text="✗ Timeout — reconnectez manuellement après la MAJ",
                        text_color="#cc4444")
            self.master.ui(_timeout)

        def _update_client():
            popup.destroy()
            import updater as _upd
            _upd.check_for_updates(self.master, silent=False)

        if server_is_older:
            # Client is newer → only the server needs updating
            ctk.CTkButton(btn_frame, text="⬆ Mettre à jour le serveur",
                          fg_color="#1f6aa5", hover_color="#1a5a8f",
                          font=ctk.CTkFont(size=12),
                          command=_update_server).pack(side="left", padx=(0, 8))
        else:
            # Server is newer → only the client needs updating
            ctk.CTkButton(btn_frame, text="⬆ Mettre à jour ce client",
                          fg_color="#2a6a2a", hover_color="#1a5a1a",
                          font=ctk.CTkFont(size=12),
                          command=_update_client).pack(side="left", padx=(0, 8))

        ctk.CTkButton(btn_frame, text="Annuler",
                      fg_color="transparent", border_width=1, border_color="#444",
                      command=popup.destroy).pack(side="right")

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
        _RemoteBrowsePopup(self, client, callback=lambda path, mode="remote": (
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
        if getattr(self, "_clipboard_var", None):
            self._settings["clipboard_monitor"] = self._clipboard_var.get()
        if getattr(self, "_file_exists_var", None):
            self._settings["file_exists_action"] = self._file_exists_var.get()
        if getattr(self, "_check_updates_var", None):
            self._settings["check_updates"] = self._check_updates_var.get()

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

        if getattr(self, "_jwt_ttl_var", None):
            try:
                self._settings["remote_jwt_ttl_h"] = int(self._jwt_ttl_var.get())
            except ValueError:
                pass

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
    Modal file browser — navigates either the remote server filesystem (via /browse)
    or the local filesystem, switchable via a toggle.

    Local mode ("This PC"):
      - Split pane: drives panel (1/4) on the left, folder tree (3/4) on the right
      - Opens at the user's home directory by default
    Remote mode:
      - Single-pane list, same as before

    Both modes have a reactive search bar and A→Z / Z→A sort toggle.
    """

    PAGE_SIZE = 200

    def __init__(self, master, client, callback):
        super().__init__(master)
        self.title("Browse filesystem")
        self.geometry("680x560")
        self.resizable(True, True)
        self.grab_set()

        self._client      = client
        self._callback    = callback
        self._current     = ""
        self._parent      = None
        self._all_entries = []
        self._page        = 0
        self._mode        = "remote" if client else "local"
        self._fetch_gen   = 0
        self._sort_asc    = True   # True = A→Z, False = Z→A

        # ── Mode toggle bar ───────────────────────────────────────────────────
        toggle_bar = ctk.CTkFrame(self, fg_color="#1a1a1a")
        toggle_bar.pack(fill="x", padx=0, pady=0)

        ctk.CTkLabel(toggle_bar, text="Browse:",
                     font=ctk.CTkFont(size=11), text_color="#666").pack(
            side="left", padx=(12, 6), pady=6)

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

        # ── Search + sort bar (both modes) ────────────────────────────────────
        filter_bar = ctk.CTkFrame(self, fg_color="#1e1e1e")
        filter_bar.pack(fill="x", padx=0, pady=0)

        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        ctk.CTkEntry(filter_bar, textvariable=self._search_var,
                     placeholder_text="🔍 Filter…",
                     height=28, font=ctk.CTkFont(size=11)).pack(
            side="left", fill="x", expand=True, padx=(10, 6), pady=5)

        self._sort_btn = ctk.CTkButton(
            filter_bar, text="A→Z", width=52, height=28,
            fg_color="transparent", border_width=1, border_color="#333",
            font=ctk.CTkFont(size=11),
            command=self._toggle_sort)
        self._sort_btn.pack(side="left", padx=(0, 10), pady=5)

        # ── Content area — single pane (remote) or split pane (local) ─────────
        self._content = ctk.CTkFrame(self, fg_color="transparent")
        self._content.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Remote single-pane ────────────────────────────────────────────────
        self._remote_pane = ctk.CTkFrame(self._content, fg_color="transparent")

        nav = ctk.CTkFrame(self._remote_pane, fg_color="#222222")
        nav.pack(fill="x", padx=0, pady=0)
        self._up_btn = ctk.CTkButton(
            nav, text="⬆ Up", width=70, height=28,
            fg_color="transparent", border_width=1, border_color="#333333",
            font=ctk.CTkFont(size=12), command=self._go_up, state="disabled")
        self._up_btn.pack(side="left", padx=(10, 6), pady=6)
        self._path_lbl = ctk.CTkLabel(
            nav, text="", text_color="#888888",
            font=ctk.CTkFont(size=10), anchor="w")
        self._path_lbl.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=6)

        self._pager = ctk.CTkFrame(self._remote_pane, fg_color="#1a1a1a")
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

        self._scroll = ctk.CTkScrollableFrame(self._remote_pane)
        self._scroll.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Local split-pane ──────────────────────────────────────────────────
        self._local_pane = ctk.CTkFrame(self._content, fg_color="transparent")

        # Left: drives panel (fixed width 140)
        left = ctk.CTkFrame(self._local_pane, fg_color="#1a1a1a", width=140)
        left.pack(side="left", fill="y", padx=0, pady=0)
        left.pack_propagate(False)
        ctk.CTkLabel(left, text="Drives", font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="#555").pack(anchor="w", padx=10, pady=(8, 4))
        self._drives_scroll = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self._drives_scroll.pack(fill="both", expand=True, padx=2, pady=(0, 4))

        # Right: nav + tree
        right = ctk.CTkFrame(self._local_pane, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=0, pady=0)

        right_nav = ctk.CTkFrame(right, fg_color="#222222")
        right_nav.pack(fill="x", padx=0, pady=0)
        self._up_btn_local = ctk.CTkButton(
            right_nav, text="⬆ Up", width=70, height=28,
            fg_color="transparent", border_width=1, border_color="#333333",
            font=ctk.CTkFont(size=12), command=self._go_up_local, state="disabled")
        self._up_btn_local.pack(side="left", padx=(8, 6), pady=6)
        self._path_lbl_local = ctk.CTkLabel(
            right_nav, text="", text_color="#888888",
            font=ctk.CTkFont(size=10), anchor="w")
        self._path_lbl_local.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=6)

        self._tree_scroll = ctk.CTkScrollableFrame(right)
        self._tree_scroll.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Show the right pane based on initial mode ─────────────────────────
        if self._mode == "remote":
            self._remote_pane.pack(fill="both", expand=True)
        else:
            self._local_pane.pack(fill="both", expand=True)

        # ── Selected destination display ──────────────────────────────────────
        self._dest_frame = ctk.CTkFrame(self, fg_color="#0d1a0d", corner_radius=0)
        self._dest_lbl   = ctk.CTkLabel(
            self._dest_frame, text="No folder selected",
            text_color="#555555", font=ctk.CTkFont(size=11), anchor="w")
        self._dest_lbl.pack(fill="x", padx=12, pady=5)
        self._dest_frame.pack(fill="x", padx=0, pady=0)

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

        # ── Initial navigation ────────────────────────────────────────────────
        if self._mode == "local":
            self._populate_drives()
            import pathlib as _pl
            self._navigate_local_tree(str(_pl.Path.home()), self._fetch_gen)
        else:
            self._navigate("")

        _center_on_master(self, master)

    # ── Mode switch ────────────────────────────────────────────────────────────

    def _switch_mode(self, mode: str):
        if mode == self._mode:
            return
        self._mode    = mode
        self._current = ""
        self._parent  = None
        self._search_var.set("")
        self._reset_dest_display()

        self._btn_remote.configure(
            fg_color="#1f6aa5" if mode == "remote" else "transparent",
            border_color="#1f6aa5" if mode == "remote" else "#333")
        self._btn_local.configure(
            fg_color="#1f6aa5" if mode == "local" else "transparent",
            border_color="#1f6aa5" if mode == "local" else "#333")

        if mode == "local":
            self._remote_pane.pack_forget()
            self._local_pane.pack(fill="both", expand=True)
            self._populate_drives()
            import pathlib as _pl
            self._navigate_local_tree(str(_pl.Path.home()), self._fetch_gen)
        else:
            self._local_pane.pack_forget()
            self._remote_pane.pack(fill="both", expand=True)
            self._navigate("")

    # ── Remote navigation (single pane) ───────────────────────────────────────

    def _navigate(self, path: str):
        """Remote-mode: fetch folder from server."""
        self._fetch_gen += 1
        gen = self._fetch_gen
        self._path_lbl.configure(text="Loading…", text_color="#888888")
        self._up_btn.configure(state="disabled")
        for w in self._scroll.winfo_children():
            w.destroy()
        import threading as _th
        def _fetch():
            data = self._client.browse(path)
            if self._fetch_gen == gen:
                self.after(0, lambda: self._on_data_remote(data, path))
        _th.Thread(target=_fetch, daemon=True, name="BrowseFetch").start()

    def _on_data_remote(self, data, requested_path: str):
        if not self.winfo_exists():
            return
        if data is None:
            self._path_lbl.configure(text="⚠ Could not reach server", text_color="#cc4444")
            return
        self._current     = data.get("path", requested_path)
        self._parent      = data.get("parent")
        self._all_entries = data.get("entries", [])
        self._page        = 0
        self._path_lbl.configure(text=self._current or "(Root)", text_color="#888888")
        self._up_btn.configure(
            state="normal" if (self._parent is not None or self._current) else "disabled")
        self._update_dest_display()
        self._render_remote()

    def _render_remote(self):
        for w in self._scroll.winfo_children():
            w.destroy()
        try:
            self._scroll._parent_canvas.yview_moveto(0)
        except Exception:
            pass
        entries = self._filtered_sorted_entries()
        total      = len(entries)
        page_count = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        start      = self._page * self.PAGE_SIZE
        end        = min(start + self.PAGE_SIZE, total)
        for entry in entries[start:end]:
            self._make_entry_in(self._scroll, entry, self._on_click_remote)
        if page_count > 1:
            self._page_lbl.configure(
                text=f"Page {self._page + 1} / {page_count}  ({total} items)")
            self._prev_btn.configure(state="normal" if self._page > 0 else "disabled")
            self._next_btn.configure(state="normal" if self._page < page_count - 1 else "disabled")
            self._pager.pack(fill="x", padx=0)
        else:
            self._pager.pack_forget()

    def _on_click_remote(self, path: str, is_dir: bool):
        if not is_dir:
            return
        self._reset_dest_display()
        self._navigate(path)

    def _go_up(self):
        target = self._parent if self._parent is not None else ""
        self._reset_dest_display()
        self._navigate(target)

    def _prev_page(self):
        if self._page > 0:
            self._page -= 1
            self._render_remote()

    def _next_page(self):
        total = len(self._filtered_sorted_entries())
        page_count = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        if self._page < page_count - 1:
            self._page += 1
            self._render_remote()

    # ── Local split-pane navigation ───────────────────────────────────────────

    def _populate_drives(self):
        """Fills the left drives panel."""
        import os as _os, sys as _sys, string as _st
        for w in self._drives_scroll.winfo_children():
            w.destroy()
        if _sys.platform == "win32":
            drives = [f"{d}:\\" for d in _st.ascii_uppercase if _os.path.exists(f"{d}:\\")]
        else:
            drives = ["/"]
        for drv in drives:
            ctk.CTkButton(
                self._drives_scroll, text=drv, anchor="w",
                fg_color="transparent", hover_color="#2a3a4a",
                text_color="#aaaaaa", font=ctk.CTkFont(size=12),
                height=30,
                command=lambda p=drv: self._navigate_local_tree(p, self._fetch_gen + 1)
            ).pack(fill="x", padx=2, pady=1)

    def _navigate_local_tree(self, path: str, gen: int):
        self._fetch_gen  = gen
        self._page       = 0
        self._search_var.set("")
        self._path_lbl_local.configure(text="Loading…", text_color="#888888")
        self._up_btn_local.configure(state="disabled")
        for w in self._tree_scroll.winfo_children():
            w.destroy()
        import threading as _th
        def _fetch():
            data = self._build_local_data(path)
            if self._fetch_gen == gen:
                self.after(0, lambda: self._on_data_local(data, path))
        _th.Thread(target=_fetch, daemon=True, name="BrowseFetch").start()

    def _on_data_local(self, data: dict, requested_path: str):
        if not self.winfo_exists():
            return
        self._current     = data.get("path", requested_path)
        self._parent      = data.get("parent")
        self._all_entries = data.get("entries", [])
        short = self._current if len(self._current) <= 58 else "…" + self._current[-55:]
        self._path_lbl_local.configure(text=short, text_color="#888888")
        self._up_btn_local.configure(
            state="normal" if self._parent is not None else "disabled")
        self._update_dest_display()
        self._render_local_tree()

    def _render_local_tree(self):
        for w in self._tree_scroll.winfo_children():
            w.destroy()
        try:
            self._tree_scroll._parent_canvas.yview_moveto(0)
        except Exception:
            pass
        for entry in self._filtered_sorted_entries():
            self._make_entry_in(self._tree_scroll, entry, self._on_click_local)

    def _on_click_local(self, path: str, is_dir: bool):
        if not is_dir:
            return
        self._reset_dest_display()
        gen = self._fetch_gen + 1
        self._navigate_local_tree(path, gen)

    def _go_up_local(self):
        if self._parent is None:
            return
        self._reset_dest_display()
        gen = self._fetch_gen + 1
        self._navigate_local_tree(self._parent, gen)

    # ── Shared helpers ─────────────────────────────────────────────────────────

    def _build_local_data(self, path: str) -> dict:
        import os as _os, pathlib as _pl
        p      = _pl.Path(path)
        parent = str(p.parent) if str(p.parent) != str(p) else None
        entries = []
        try:
            for name in _os.listdir(path):
                full = _os.path.join(path, name)
                try:
                    entries.append({"name": name, "path": full,
                                    "is_dir": _os.path.isdir(full)})
                except PermissionError:
                    pass
        except PermissionError:
            pass
        return {"path": path, "parent": parent, "entries": entries}

    def _filtered_sorted_entries(self) -> list:
        """Returns entries filtered by search and sorted per current sort mode."""
        query = self._search_var.get().strip().lower()
        dirs  = [e for e in self._all_entries if e["is_dir"]]
        files = [e for e in self._all_entries if not e["is_dir"]]
        if query:
            dirs  = [e for e in dirs  if query in e["name"].lower()]
            files = [e for e in files if query in e["name"].lower()]
        key = lambda e: e["name"].lower()
        dirs.sort(key=key,  reverse=not self._sort_asc)
        files.sort(key=key, reverse=not self._sort_asc)
        return dirs + files

    def _apply_filter(self):
        """Called whenever search text changes."""
        self._page = 0
        if self._mode == "remote":
            self._render_remote()
        else:
            self._render_local_tree()

    def _toggle_sort(self):
        self._sort_asc = not self._sort_asc
        self._sort_btn.configure(text="A→Z" if self._sort_asc else "Z→A")
        self._page = 0
        if self._mode == "remote":
            self._render_remote()
        else:
            self._render_local_tree()

    def _make_entry_in(self, container, entry: dict, on_click):
        is_dir = entry["is_dir"]
        path   = entry["path"]
        name   = entry["name"]
        label  = f"📁  {name}" if is_dir else f"    {name}"
        btn = ctk.CTkButton(
            container, text=label, anchor="w",
            fg_color="transparent",
            hover_color="#1a2a3a" if is_dir else "#1a1a1a",
            text_color="#cccccc" if is_dir else "#555555",
            font=ctk.CTkFont(size=12), height=28,
            command=lambda p=path, d=is_dir: on_click(p, d),
        )
        btn.pack(fill="x", padx=4, pady=1)

    def _update_dest_display(self):
        if self._current:
            short = self._current if len(self._current) <= 60 else "…" + self._current[-57:]
            self._dest_lbl.configure(text=f"📁 {short}", text_color="#7aaa7a")
            self._dest_frame.configure(fg_color="#0d1f0d")
            self._select_btn.configure(state="normal")
        else:
            self._reset_dest_display()

    def _reset_dest_display(self):
        self._dest_lbl.configure(text="No folder selected", text_color="#555555")
        self._dest_frame.configure(fg_color="#0d1a0d")
        self._select_btn.configure(state="disabled")

    def _confirm_selection(self):
        path = self._current
        if path:
            self._callback(path, self._mode)
            self.destroy()
