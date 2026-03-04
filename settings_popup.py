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
    defaults = {"temp_dir": DEFAULT_TEMP_DIR}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                defaults.update(data)
        except Exception:
            pass
    return defaults


def save_settings(settings: dict):
    """Sauvegarde les paramètres sur disque."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print("[settings] Erreur sauvegarde :", e)


class SettingsPopup(ctk.CTkToplevel):
    """Fenêtre de paramètres de TurboDownloader."""

    def __init__(self, master, settings: dict, on_save):
        """
        settings : dict courant (modifié en place à la validation)
        on_save  : callback() appelé après sauvegarde pour que l'app recharge
        """
        super().__init__(master)
        self.title("Paramètres")
        self.geometry("560x260")
        self.resizable(False, False)
        self.grab_set()

        self._settings = settings
        self._on_save  = on_save

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 20, "pady": (12, 4)}

        # ── Titre ──────────────────────────────────────────────────────────
        ctk.CTkLabel(self, text="Paramètres",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(
                         anchor="w", padx=20, pady=(16, 8))

        # ── Dossier temporaire ─────────────────────────────────────────────
        ctk.CTkLabel(self, text="Dossier temporaire de téléchargement",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", **pad)

        ctk.CTkLabel(self,
                     text="Les fichiers sont écrits ici pendant le DL, puis déplacés à destination.",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(
                         anchor="w", padx=20, pady=(0, 6))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 4))

        self._temp_entry = ctk.CTkEntry(row, width=380)
        self._temp_entry.insert(0, self._settings.get("temp_dir", DEFAULT_TEMP_DIR))
        self._temp_entry.pack(side="left", expand=True, fill="x", padx=(0, 8))

        ctk.CTkButton(row, text="Parcourir…", width=100,
                      command=self._browse_temp).pack(side="left")

        ctk.CTkButton(self, text="Réinitialiser par défaut", width=180,
                      fg_color="transparent", border_width=1,
                      command=self._reset_default).pack(anchor="w", padx=20, pady=(0, 4))

        # ── Boutons bas ────────────────────────────────────────────────────
        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", padx=20, pady=(8, 16), side="bottom")

        ctk.CTkButton(bot, text="Annuler", width=110, fg_color="#5a5a5a",
                      command=self.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(bot, text="Enregistrer", width=130, fg_color="#1f6aa5",
                      command=self._save).pack(side="right")

    def _browse_temp(self):
        folder = filedialog.askdirectory(title="Choisir le dossier temporaire")
        if folder:
            self._temp_entry.delete(0, "end")
            self._temp_entry.insert(0, folder)

    def _reset_default(self):
        self._temp_entry.delete(0, "end")
        self._temp_entry.insert(0, DEFAULT_TEMP_DIR)

    def _save(self):
        self._settings["temp_dir"] = self._temp_entry.get().strip() or DEFAULT_TEMP_DIR
        save_settings(self._settings)
        self._on_save()
        self.destroy()
