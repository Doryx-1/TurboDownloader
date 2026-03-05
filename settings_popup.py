import os
import json
import pathlib

import customtkinter as ctk
from tkinter import filedialog


# Chemin du fichier de config : C:/Users/<user>/.turbodownloader/settings.json
CONFIG_DIR  = pathlib.Path.home() / ".turbodownloader"
CONFIG_FILE = CONFIG_DIR / "settings.json"

# Dossier temp par défaut
DEFAULT_TEMP_DIR = str(CONFIG_DIR / "tmp")


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
    """Charge les paramètres depuis le fichier de config. Retourne les défauts si absent."""
    defaults = {
        "temp_dir":      DEFAULT_TEMP_DIR,
        "retry_max":     3,
        "retry_delay":   5,
        "throttle":      0,
        "notifications": True,
        "segments":      4,
        "extensions":    DEFAULT_EXTENSIONS.copy(),
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                defaults.update(data)
            print(f"[settings] chargé: throttle={defaults.get('throttle')}, retry={defaults.get('retry_max')}")
        except Exception as e:
            print(f"[settings] erreur lecture: {e}")
    else:
        print(f"[settings] fichier absent, défauts utilisés")
    return defaults


def save_settings(settings: dict):
    """Sauvegarde les paramètres sur disque."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        print(f"[settings] sauvegardé: {settings}")
    except Exception as e:
        print(f"[settings] erreur sauvegarde: {e}")


class SettingsPopup(ctk.CTkToplevel):
    """Fenêtre de paramètres de TurboDownloader."""

    def __init__(self, master, settings: dict, on_save):
        super().__init__(master)
        self.title("Paramètres")
        self.geometry("620x840")
        self.resizable(False, False)
        self.grab_set()

        self._settings = settings
        self._on_save  = on_save

        self._build_ui()

    def _build_ui(self):
        # ── Conteneur principal scrollable ─────────────────────────────────
        # Layout : titre + sections dans un frame central, boutons fixes en bas
        # On utilise grid sur self pour séparer proprement contenu / boutons

        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)
        self.grid_columnconfigure(0, weight=1)

        # Zone de contenu (haut) — scrollable pour petits écrans
        content = ctk.CTkScrollableFrame(self, fg_color="transparent")
        content.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)

        # Zone boutons (bas) — fixe
        bot = ctk.CTkFrame(self, fg_color="#2b2b2b")
        bot.grid(row=1, column=0, sticky="ew", padx=0, pady=0)

        # ── Titre ──────────────────────────────────────────────────────────
        ctk.CTkLabel(content, text="Paramètres",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(
                         anchor="w", padx=20, pady=(16, 12))

        # ── Section : Dossier temporaire ───────────────────────────────────
        self._section(content, "Dossier temporaire de téléchargement",
                      "Fichiers écrits ici pendant le DL, puis déplacés à destination.")

        row_temp = ctk.CTkFrame(content, fg_color="transparent")
        row_temp.pack(fill="x", padx=20, pady=(0, 4))

        self._temp_entry = ctk.CTkEntry(row_temp)
        self._temp_entry.insert(0, self._settings.get("temp_dir", DEFAULT_TEMP_DIR))
        self._temp_entry.pack(side="left", expand=True, fill="x", padx=(0, 8))

        ctk.CTkButton(row_temp, text="Parcourir…", width=100,
                      command=self._browse_temp).pack(side="left")

        ctk.CTkButton(content, text="Réinitialiser par défaut", width=180,
                      fg_color="transparent", border_width=1,
                      command=self._reset_default).pack(anchor="w", padx=20, pady=(2, 8))

        # ── Séparateur ─────────────────────────────────────────────────────
        ctk.CTkFrame(content, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 0))

        # ── Section : Retry ────────────────────────────────────────────────
        self._section(content, "Retry automatique",
                      "En cas d'erreur réseau, relance automatiquement. Délai double à chaque essai.")

        row_retry = ctk.CTkFrame(content, fg_color="transparent")
        row_retry.pack(fill="x", padx=20, pady=(0, 8))

        ctk.CTkLabel(row_retry, text="Tentatives max :").pack(side="left", padx=(0, 6))
        self._retry_max_entry = ctk.CTkEntry(row_retry, width=60)
        self._retry_max_entry.insert(0, str(self._settings.get("retry_max", 3)))
        self._retry_max_entry.pack(side="left", padx=(0, 20))

        ctk.CTkLabel(row_retry, text="Délai initial (s) :").pack(side="left", padx=(0, 6))
        self._retry_delay_entry = ctk.CTkEntry(row_retry, width=60)
        self._retry_delay_entry.insert(0, str(self._settings.get("retry_delay", 5)))
        self._retry_delay_entry.pack(side="left")

        # ── Séparateur ─────────────────────────────────────────────────────
        ctk.CTkFrame(content, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 0))

        # ── Section : Throttle ─────────────────────────────────────────────
        self._section(content, "Limitation de bande passante",
                      "Limite globale répartie entre tous les workers. 0 = illimité.")

        row_throttle = ctk.CTkFrame(content, fg_color="transparent")
        row_throttle.pack(fill="x", padx=20, pady=(0, 8))

        ctk.CTkLabel(row_throttle, text="Limite (MB/s) :").pack(side="left", padx=(0, 6))
        self._throttle_entry = ctk.CTkEntry(row_throttle, width=80)
        self._throttle_entry.insert(0, str(self._settings.get("throttle", 0)))
        self._throttle_entry.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(row_throttle, text="0 = illimité",
                     text_color="gray").pack(side="left")

        # ── Séparateur ─────────────────────────────────────────────────────
        ctk.CTkFrame(content, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 0))

        # ── Section : Notifications ────────────────────────────────────────
        self._section(content, "Notifications bureau",
                      "Alerte quand tous les téléchargements d'un batch sont terminés.")

        row_notif = ctk.CTkFrame(content, fg_color="transparent")
        row_notif.pack(fill="x", padx=20, pady=(0, 8))
        self._notif_var = ctk.BooleanVar(value=self._settings.get("notifications", True))
        ctk.CTkCheckBox(row_notif, text="Activer les notifications (requiert plyer)",
                        variable=self._notif_var).pack(side="left")

        # ── Séparateur ─────────────────────────────────────────────────────
        ctk.CTkFrame(content, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 0))

        # ── Section : Multipart ────────────────────────────────────────────
        self._section(content, "Téléchargement multipart",
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

        # ── Séparateur ─────────────────────────────────────────────────────
        ctk.CTkFrame(content, height=1, fg_color="#3a3a3a").pack(fill="x", padx=20, pady=(0, 0))

        # ── Section : Extensions ───────────────────────────────────────────
        self._section(content, "Extensions téléchargeables",
                      "Seuls les fichiers avec ces extensions seront détectés lors du crawl.")

        ext_grid = ctk.CTkFrame(content, fg_color="transparent")
        ext_grid.pack(fill="x", padx=20, pady=(0, 4))

        saved_exts: dict = self._settings.get("extensions", DEFAULT_EXTENSIONS.copy())
        self._ext_vars: dict[str, ctk.BooleanVar] = {}

        # Checkboxes pour les extensions prédéfinies (2 colonnes)
        predefined = list(DEFAULT_EXTENSIONS.keys())
        for i, ext in enumerate(predefined):
            var = ctk.BooleanVar(value=saved_exts.get(ext, DEFAULT_EXTENSIONS[ext]))
            self._ext_vars[ext] = var
            cb = ctk.CTkCheckBox(ext_grid, text=ext, variable=var, width=100)
            cb.grid(row=i // 4, column=i % 4, padx=6, pady=2, sticky="w")

        # Ligne pour extensions custom
        row_custom = ctk.CTkFrame(content, fg_color="transparent")
        row_custom.pack(fill="x", padx=20, pady=(4, 0))
        ctk.CTkLabel(row_custom, text="Ajouter :").pack(side="left", padx=(0, 6))
        self._custom_ext_entry = ctk.CTkEntry(row_custom, width=100,
                                              placeholder_text=".ext")
        self._custom_ext_entry.pack(side="left", padx=(0, 8))
        ctk.CTkButton(row_custom, text="+ Ajouter", width=90,
                      command=self._add_custom_ext).pack(side="left")

        # Frame pour les extensions custom ajoutées (checkboxes dynamiques)
        self._custom_ext_frame = ctk.CTkFrame(content, fg_color="transparent")
        self._custom_ext_frame.pack(fill="x", padx=20, pady=(2, 8))

        # Charger les extensions custom déjà sauvegardées (non prédéfinies)
        for ext, enabled in saved_exts.items():
            if ext not in DEFAULT_EXTENSIONS:
                self._add_custom_ext_row(ext, enabled)

        # ── Boutons bas (dans bot frame) ───────────────────────────────────
        ctk.CTkButton(bot, text="Annuler", width=110, fg_color="#5a5a5a",
                      command=self.destroy).pack(side="right", padx=(8, 16), pady=12)
        ctk.CTkButton(bot, text="Enregistrer", width=130, fg_color="#1f6aa5",
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
            text=f"{n}" if n > 1 else "1 (désactivé)"
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

    def _browse_temp(self):
        folder = filedialog.askdirectory(title="Choisir le dossier temporaire")
        if folder:
            self._temp_entry.delete(0, "end")
            self._temp_entry.insert(0, folder)

    def _reset_default(self):
        self._temp_entry.delete(0, "end")
        self._temp_entry.insert(0, DEFAULT_TEMP_DIR)

    def _save(self):
        print("[settings] _save appelée")

        self._settings["temp_dir"] = self._temp_entry.get().strip() or DEFAULT_TEMP_DIR

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

        # Notifications
        self._settings["notifications"] = self._notif_var.get()

        # Segments multipart
        self._settings["segments"] = int(round(self._seg_slider.get()))

        # Extensions
        self._settings["extensions"] = {
            ext: var.get() for ext, var in self._ext_vars.items()
        }

        save_settings(self._settings)
        self._on_save()
        self.destroy()
