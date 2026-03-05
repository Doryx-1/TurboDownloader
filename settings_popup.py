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


def load_settings() -> dict:
    """Charge les paramètres depuis le fichier de config. Retourne les défauts si absent."""
    defaults = {
        "temp_dir":    DEFAULT_TEMP_DIR,
        "retry_max":   3,
        "retry_delay": 5,
        "throttle":    0,
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
        self.geometry("580x500")
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

        # Zone de contenu (haut)
        content = ctk.CTkFrame(self, fg_color="transparent")
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

        save_settings(self._settings)
        self._on_save()
        self.destroy()
