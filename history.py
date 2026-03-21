import json
import pathlib
from datetime import datetime

import customtkinter as ctk

CONFIG_DIR   = pathlib.Path.home() / ".turbodownloader"
HISTORY_FILE = CONFIG_DIR / "history.json"
MAX_ENTRIES  = 500   # entrées max conservées (FIFO)


# ─────────────────────────────────────────────────────────────── Manager

class HistoryManager:
    """Thread-safe read/write of the JSON history file."""

    def __init__(self):
        self._entries: list[dict] = self._load()

    def _load(self) -> list:
        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else []
            except Exception as e:
                print(f"[history] read error: {e}")
        return []

    def _save(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self._entries, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[history] save error: {e}")

    def log_entry(self, filename: str, url: str,
                  size_bytes: int, duration_s: float):
        entry = {
            "filename":   filename,
            "url":        url,
            "size_bytes": size_bytes,
            "duration_s": round(duration_s, 1),
            "date_iso":   datetime.now().isoformat(timespec="seconds"),
        }
        self._entries.insert(0, entry)          # most recent first
        if len(self._entries) > MAX_ENTRIES:
            self._entries = self._entries[:MAX_ENTRIES]
        self._save()

    def get_entries(self) -> list[dict]:
        return list(self._entries)

    def clear(self):
        self._entries = []
        self._save()


# ─────────────────────────────────────────────────────────────── Popup

def _center_on_master(window, master):
    window.update_idletasks()
    x = master.winfo_rootx() + (master.winfo_width()  - window.winfo_width())  // 2
    y = master.winfo_rooty() + (master.winfo_height() - window.winfo_height()) // 2
    window.geometry(f"+{max(0,x)}+{max(0,y)}")


class HistoryPopup(ctk.CTkToplevel):
    """Download history window."""

    def __init__(self, master, history_manager: HistoryManager,
                 on_redownload):
        """
        history_manager : shared instance with TurboDownloader
        on_redownload   : callback(url: str) → re-injects the URL and starts download
        """
        super().__init__(master)
        self.title("History des téléchargements")
        self.geometry("900x560")
        self.resizable(True, True)
        self.grab_set()
        self.after(50, lambda: _center_on_master(self, master))

        self._hm            = history_manager
        self._on_redownload = on_redownload

        self._build_ui()

    # ---------------------------------------------------------------- UI

    def _build_ui(self):
        # ── Titre ──────────────────────────────────────────────────────
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(top, text="History des téléchargements",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self._count_lbl = ctk.CTkLabel(top, text="", text_color="gray")
        self._count_lbl.pack(side="right")

        # ── En-têtes colonnes ──────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color="#2b2b2b")
        hdr.pack(fill="x", padx=16, pady=(0, 2))
        for text, w in [("File", 320), ("Size", 90),
                         ("Duration", 80), ("Date", 150), ("", 130)]:
            ctk.CTkLabel(hdr, text=text, width=w, anchor="w",
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color="gray").pack(side="left", padx=6, pady=4)

        # ── Liste scrollable ───────────────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(self)
        self._scroll.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        self._populate()

        # ── Bas ────────────────────────────────────────────────────────
        bot = ctk.CTkFrame(self, fg_color="#2b2b2b")
        bot.pack(fill="x", padx=0, pady=0)
        ctk.CTkButton(bot, text="Close", width=110, fg_color="#5a5a5a",
                      command=self.destroy).pack(side="right", padx=16, pady=10)
        ctk.CTkButton(bot, text="🗑  Clear history", width=180,
                      fg_color="#8B0000", hover_color="#a00000",
                      command=self._clear_history).pack(side="left", padx=16, pady=10)

    def _populate(self):
        """(Re)peuple la liste scrollable depuis l'historique."""
        # Vider l'existant
        for w in self._scroll.winfo_children():
            w.destroy()

        entries = self._hm.get_entries()
        self._count_lbl.configure(text=f"{len(entries)} entry(ies)")

        if not entries:
            ctk.CTkLabel(self._scroll, text="No downloads recorded.",
                         text_color="gray").pack(pady=30)
            return

        for entry in entries:
            self._make_row(entry)

    def _make_row(self, entry: dict):
        row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        row.pack(fill="x", pady=1)

        # Name (truncated if too long)
        name = entry.get("filename", "?")
        display_name = name if len(name) <= 42 else name[:39] + "…"
        ctk.CTkLabel(row, text=display_name, width=320, anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=6)

        # Size
        size_b = entry.get("size_bytes", 0)
        ctk.CTkLabel(row, text=self._fmt_size(size_b), width=90, anchor="w",
                     text_color="gray").pack(side="left", padx=4)

        # Duration
        dur = entry.get("duration_s", 0)
        ctk.CTkLabel(row, text=self._fmt_duration(dur), width=80, anchor="w",
                     text_color="gray").pack(side="left", padx=4)

        # Date
        date_str = entry.get("date_iso", "")
        date_display = date_str.replace("T", "  ") if date_str else "–"
        ctk.CTkLabel(row, text=date_display, width=150, anchor="w",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(side="left", padx=4)

        # Re-download button
        url = entry.get("url", "")
        ctk.CTkButton(
            row, text="↺  Re-download", width=130,
            fg_color="#1f6aa5", hover_color="#1a5a8f",
            font=ctk.CTkFont(size=12),
            command=lambda u=url: self._redownload(u),
        ).pack(side="left", padx=8)

        # Thin separator
        ctk.CTkFrame(self._scroll, height=1, fg_color="#333333").pack(fill="x")

    # ---------------------------------------------------------------- Actions

    def _redownload(self, url: str):
        self.destroy()
        self._on_redownload(url)

    def _clear_history(self):
        self._hm.clear()
        self._populate()

    # ---------------------------------------------------------------- Formatting

    @staticmethod
    def _fmt_size(b: int) -> str:
        if b <= 0:
            return "–"
        if b < 1024:
            return f"{b} B"
        if b < 1024 * 1024:
            return f"{b/1024:.1f} KB"
        if b < 1024 ** 3:
            return f"{b/1024/1024:.1f} MB"
        return f"{b/1024**3:.2f} GB"

    @staticmethod
    def _fmt_duration(s: float) -> str:
        if s <= 0:
            return "–"
        s = int(s)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}m{s:02d}s"
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m"
