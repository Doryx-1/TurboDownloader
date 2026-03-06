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
        self.geometry("620x840")
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
        self._custom_ext_frame = ctk.CTkFrame(content, fg_color="transparent")
        self._custom_ext_frame.pack(fill="x", padx=20, pady=(2, 8))

        # Load already saved custom extensions (not in predefined list)
        for ext, enabled in saved_exts.items():
            if ext not in DEFAULT_EXTENSIONS:
                self._add_custom_ext_row(ext, enabled)

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

    def _browse_dest(self):
        folder = filedialog.askdirectory(title="Choose default destination folder")
        if folder:
            self._dest_entry.delete(0, "end")
            self._dest_entry.insert(0, folder)

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

        save_settings(self._settings)
        self._on_save()
        self.destroy()