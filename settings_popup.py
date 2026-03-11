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
        "remote_client_host":   "",
        "remote_client_port":   9988,
        "remote_client_user":   "",
        # destination history
        "dest_history":         [],
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


class SettingsPopup(ctk.CTkToplevel):
    """TurboDownloader settings window."""

    def __init__(self, master, settings: dict, on_save):
        super().__init__(master)
        self.title("Settings")
        self.geometry("620x960")
        self.resizable(False, False)
        self.grab_set()

        self._settings = settings
        self._on_save  = on_save

        self._build_ui()

    def _build_ui(self):
        # ── Scrollable main container ───────────────────────────────────
        # Layout : titre + sections in un frame central, boutons fixes en bas
        # grid on self cleanly separates content / buttons

        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)
        self.grid_columnconfigure(0, weight=1)

        # Content area (top) — scrollable for small screens
        content = ctk.CTkScrollableFrame(self, fg_color="transparent")
        content.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)

        # Button area (bottom) — fixed
        bot = ctk.CTkFrame(self, fg_color="#2b2b2b")
        bot.grid(row=1, column=0, sticky="ew", padx=0, pady=0)

        # ── Title ───────────────────────────────────────────────────────
        ctk.CTkLabel(content, text="Settings",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(
                         anchor="w", padx=20, pady=(16, 12))

        # ── Section: Temp folder ────────────────────────────────────────
        self._section(content, "Temporary download folder",
                      "Files written here during download, then moved to destination.")

        row_temp = ctk.CTkFrame(content, fg_color="transparent")
        row_temp.pack(fill="x", padx=20, pady=(0, 4))

        self._temp_entry = ctk.CTkEntry(row_temp)
        self._temp_entry.insert(0, self._settings.get("temp_dir", DEFAULT_TEMP_DIR))
        self._temp_entry.pack(side="left", expand=True, fill="x", padx=(0, 8))

        ctk.CTkButton(row_temp, text="Browse…", width=100,
                      command=self._browse_temp).pack(side="left")

        ctk.CTkButton(content, text="Reset to default", width=180,
                      fg_color="transparent", border_width=1,
                      command=self._reset_default).pack(anchor="w", padx=20, pady=(2, 8))

        # ── Separator ───────────────────────────────────────────────────
        ctk.CTkFrame(content, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 0))

        # ── Section: Retry ──────────────────────────────────────────────
        self._section(content, "Automatic retry",
                      "Automatically retries on network errors. Delay doubles each attempt.")

        row_retry = ctk.CTkFrame(content, fg_color="transparent")
        row_retry.pack(fill="x", padx=20, pady=(0, 8))

        ctk.CTkLabel(row_retry, text="Max attempts:").pack(side="left", padx=(0, 6))
        self._retry_max_entry = ctk.CTkEntry(row_retry, width=60)
        self._retry_max_entry.insert(0, str(self._settings.get("retry_max", 3)))
        self._retry_max_entry.pack(side="left", padx=(0, 20))

        ctk.CTkLabel(row_retry, text="Initial delay (s):").pack(side="left", padx=(0, 6))
        self._retry_delay_entry = ctk.CTkEntry(row_retry, width=60)
        self._retry_delay_entry.insert(0, str(self._settings.get("retry_delay", 5)))
        self._retry_delay_entry.pack(side="left")

        # ── Separator ───────────────────────────────────────────────────
        ctk.CTkFrame(content, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 0))

        # ── Section: Throttle ───────────────────────────────────────────
        self._section(content, "Bandwidth limit",
                      "Global limit shared across all workers. 0 = unlimited.")

        row_throttle = ctk.CTkFrame(content, fg_color="transparent")
        row_throttle.pack(fill="x", padx=20, pady=(0, 8))

        ctk.CTkLabel(row_throttle, text="Limit (MB/s):").pack(side="left", padx=(0, 6))
        self._throttle_entry = ctk.CTkEntry(row_throttle, width=80)
        self._throttle_entry.insert(0, str(self._settings.get("throttle", 0)))
        self._throttle_entry.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(row_throttle, text="0 = unlimited",
                     text_color="gray").pack(side="left")

        # ── Separator ───────────────────────────────────────────────────
        ctk.CTkFrame(content, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 0))

        # ── Section: Default destination folder ─────────────────────────
        self._section(content, "Default destination folder",
                      "Downloads go here when no custom path is set in the file tree popup.")

        row_dest = ctk.CTkFrame(content, fg_color="transparent")
        row_dest.pack(fill="x", padx=20, pady=(0, 4))

        self._dest_entry = ctk.CTkEntry(row_dest)
        self._dest_entry.insert(0, self._settings.get("default_dest", DEFAULT_DEST_DIR))
        self._dest_entry.pack(side="left", expand=True, fill="x", padx=(0, 8))

        ctk.CTkButton(row_dest, text="Browse…", width=100,
                      command=self._browse_dest).pack(side="left")

        ctk.CTkButton(content, text="Reset to Downloads", width=180,
                      fg_color="transparent", border_width=1,
                      command=self._reset_dest).pack(anchor="w", padx=20, pady=(2, 8))

        # ── Separator ───────────────────────────────────────────────────
        ctk.CTkFrame(content, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 0))

        # ── Section: Concurrent workers ──────────────────────────────────
        self._section(content, "Concurrent workers",
                      "Number of simultaneous downloads. Default: 10.")

        row_workers = ctk.CTkFrame(content, fg_color="transparent")
        row_workers.pack(fill="x", padx=20, pady=(0, 8))
        ctk.CTkLabel(row_workers, text="Workers:").pack(side="left", padx=(0, 8))
        self._workers_entry = ctk.CTkEntry(row_workers, width=60)
        self._workers_entry.insert(0, str(self._settings.get("workers", 10)))
        self._workers_entry.pack(side="left")
        ctk.CTkLabel(row_workers, text="(1–20)",
                     text_color="gray").pack(side="left", padx=(8, 0))

        # ── Separator ───────────────────────────────────────────────────
        ctk.CTkFrame(content, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 0))

        # ── Section: Notifications ──────────────────────────────────────
        self._section(content, "Desktop notifications",
                      "Alert when all downloads in a batch are complete.")

        row_notif = ctk.CTkFrame(content, fg_color="transparent")
        row_notif.pack(fill="x", padx=20, pady=(0, 8))
        self._notif_var = ctk.BooleanVar(value=self._settings.get("notifications", True))
        ctk.CTkCheckBox(row_notif, text="Enable notifications (requires plyer)",
                        variable=self._notif_var).pack(side="left")

        # ── Separator ───────────────────────────────────────────────────
        ctk.CTkFrame(content, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 0))

        # ── Section: Multipart ──────────────────────────────────────────
        self._section(content, "Multipart download",
                      "Divise chaque fichier en N segments parallèles. "
                      "Requiert Accept-Ranges sur le serveur. 1 = désactivé.")

        row_seg = ctk.CTkFrame(content, fg_color="transparent")
        row_seg.pack(fill="x", padx=20, pady=(0, 8))

        self._seg_val_lbl = ctk.CTkLabel(row_seg, text="", width=30)
        self._seg_val_lbl.pack(side="right", padx=(6, 0))

        self._seg_slider = ctk.CTkSlider(
            row_seg, from_=1, to=16, number_of_steps=15,
            command=self._on_seg_slider,
        )
        self._seg_slider.set(self._settings.get("segments", 4))
        self._seg_slider.pack(side="left", fill="x", expand=True)
        self._on_seg_slider(self._seg_slider.get())  # init label

        # ── Separator ───────────────────────────────────────────────────
        ctk.CTkFrame(content, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 0))

        # ── Section: Extensions ─────────────────────────────────────────
        self._section(content, "Downloadable extensions",
                      "Only files with these extensions will be detected during crawl.")

        # "All files" toggle — disables the extension filter entirely
        row_allfiles = ctk.CTkFrame(content, fg_color="transparent")
        row_allfiles.pack(fill="x", padx=20, pady=(0, 6))
        self._all_files_var = ctk.BooleanVar(value=self._settings.get("all_files", False))
        ctk.CTkCheckBox(
            row_allfiles,
            text="All files  (disable extension filter during crawl)",
            variable=self._all_files_var,
        ).pack(side="left")

        ext_grid = ctk.CTkFrame(content, fg_color="transparent")
        ext_grid.pack(fill="x", padx=20, pady=(0, 4))

        saved_exts: dict = self._settings.get("extensions", DEFAULT_EXTENSIONS.copy())
        self._ext_vars: dict[str, ctk.BooleanVar] = {}

        # Checkboxes for predefined extensions (2 columns)
        predefined = list(DEFAULT_EXTENSIONS.keys())
        for i, ext in enumerate(predefined):
            var = ctk.BooleanVar(value=saved_exts.get(ext, DEFAULT_EXTENSIONS[ext]))
            self._ext_vars[ext] = var
            cb = ctk.CTkCheckBox(ext_grid, text=ext, variable=var, width=100)
            cb.grid(row=i // 4, column=i % 4, padx=6, pady=2, sticky="w")

        # Row for custom extensions
        row_custom = ctk.CTkFrame(content, fg_color="transparent")
        row_custom.pack(fill="x", padx=20, pady=(4, 0))
        ctk.CTkLabel(row_custom, text="Add:").pack(side="left", padx=(0, 6))
        self._custom_ext_entry = ctk.CTkEntry(row_custom, width=100,
                                              placeholder_text=".ext")
        self._custom_ext_entry.pack(side="left", padx=(0, 8))
        ctk.CTkButton(row_custom, text="+ Add", width=90,
                      command=self._add_custom_ext).pack(side="left")

        # Frame for dynamically added custom extension checkboxes
        # No fixed height — grows only when extensions are actually added
        self._custom_ext_frame = ctk.CTkFrame(content, fg_color="transparent")
        self._custom_ext_frame.pack(fill="x", padx=20, pady=0)

        # Load already saved custom extensions (not in predefined list)
        _has_custom = False
        for ext, enabled in saved_exts.items():
            if ext not in DEFAULT_EXTENSIONS:
                self._add_custom_ext_row(ext, enabled)
                _has_custom = True
        # Only add bottom padding if there are custom ext rows
        if _has_custom:
            self._custom_ext_frame.pack_configure(pady=(0, 6))

        # ── Section: Remote control ──────────────────────────────────────
        self._build_remote_section(content)

        # ── Bottom buttons (inside bot frame) ───────────────────────────
        ctk.CTkButton(bot, text="Cancel", width=110, fg_color="#5a5a5a",
                      command=self.destroy).pack(side="right", padx=(8, 16), pady=12)
        ctk.CTkButton(bot, text="Save", width=130, fg_color="#1f6aa5",
                      command=self._save).pack(side="right", pady=12)

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

        # Password (never stored — entered each time for security)
        rc4 = ctk.CTkFrame(client_frame, fg_color="transparent")
        rc4.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(rc4, text="Password:", width=130, anchor="w").pack(side="left")
        self._rclient_pass_e = ctk.CTkEntry(rc4, width=180, show="•",
                                            placeholder_text="Not saved — enter each time")
        self._rclient_pass_e.pack(side="left")

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
            self.master._remote_client = c
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

        save_settings(self._settings)
        self._on_save()
        self.destroy()

# ─────────────────────────────────────────────────────────────── Remote Browser

class _RemoteBrowsePopup(ctk.CTkToplevel):
    """
    Modal file browser that navigates the remote server's filesystem via /browse.
    """
    def __init__(self, master, client, callback):
        super().__init__(master)
        self.title("Browse remote server")
        self.geometry("520x460")
        self.resizable(True, True)
        self.grab_set()

        self._client   = client
        self._callback = callback
        self._current  = ""   # current path on server ("" = root/drives)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(hdr, text="📂 Remote Filesystem",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")

        # Current path label
        self._path_lbl = ctk.CTkLabel(
            self, text="", text_color="#888888",
            font=ctk.CTkFont(size=10), anchor="w", wraplength=480)
        self._path_lbl.pack(fill="x", padx=14, pady=(0, 4))

        # ── Entry list ────────────────────────────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(self, height=300)
        self._scroll.pack(fill="both", expand=True, padx=12, pady=4)

        # ── Footer ────────────────────────────────────────────────────────────
        foot = ctk.CTkFrame(self, fg_color="#2b2b2b")
        foot.pack(fill="x", padx=0, pady=0)

        self._select_btn = ctk.CTkButton(
            foot, text="✔ Select this folder", width=160,
            fg_color="#1f6aa5", state="disabled",
            command=self._select_current)
        self._select_btn.pack(side="left", padx=12, pady=8)

        ctk.CTkButton(foot, text="Cancel", width=100,
                      fg_color="#5a5a5a",
                      command=self.destroy).pack(side="right", padx=12, pady=8)

        self._navigate("")

    def _navigate(self, path: str):
        """Fetches and displays folder contents for the given path."""
        self._path_lbl.configure(text="Loading…")
        self.update()

        data = self._client.browse(path)
        if data is None:
            self._path_lbl.configure(text="⚠ Could not reach server", text_color="#cc4444")
            return

        self._current = data.get("path", path)
        display_path  = self._current or "(Drives)"
        self._path_lbl.configure(text=display_path, text_color="#888888")

        # Enable "Select" only when inside a real folder
        self._select_btn.configure(
            state="normal" if self._current else "disabled")

        # Clear previous entries
        for w in self._scroll.winfo_children():
            w.destroy()

        parent = data.get("parent")
        if parent is not None:
            self._make_entry("⬆  ..", parent, is_dir=True, is_parent=True)

        entries = data.get("entries", [])
        dirs  = [e for e in entries if e["is_dir"]]
        files = [e for e in entries if not e["is_dir"]]

        for e in dirs:
            self._make_entry(f"📁  {e['name']}", e["path"], is_dir=True)
        for e in files:
            self._make_entry(f"    {e['name']}", e["path"], is_dir=False)

    def _make_entry(self, label: str, path: str, is_dir: bool, is_parent: bool = False):
        btn = ctk.CTkButton(
            self._scroll, text=label, anchor="w",
            fg_color="transparent",
            hover_color="#2a3a4a" if is_dir else "#1e1e1e",
            text_color="#cccccc" if is_dir else "#777777",
            font=ctk.CTkFont(size=12),
            height=28,
            command=(lambda p=path: self._navigate(p)) if is_dir else (lambda: None),
        )
        btn.pack(fill="x", padx=4, pady=1)

    def _select_current(self):
        if self._current:
            self._callback(self._current)
            self.destroy()
