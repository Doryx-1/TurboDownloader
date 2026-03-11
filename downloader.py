import os
import sys
import time
import queue
import threading
from collections import deque
from typing import Optional
from urllib.parse import urljoin, unquote
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup
import customtkinter as ctk
from tkinter import filedialog

import shutil

from models import DownloadItem, SegmentInfo
from widgets import DownloadRow
from tree_popup import FileTreePopup
from settings_popup import SettingsPopup, load_settings, DEFAULT_TEMP_DIR, DEFAULT_DEST_DIR, DEFAULT_EXTENSIONS
from history import HistoryManager, HistoryPopup
from notifier import notify_batch_done
from taskbar import TaskbarProgress
import ytdlp_worker
from ytdlp_popup import YtdlpPopup
import ffmpeg_setup
import remote_server


CHUNK_SIZE = 1024 * 512  # 512 KB per chunk


def _resource(relative_path: str) -> str:
    """Get absolute path to resource — works for dev and PyInstaller .exe"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


class TurboDownloader(ctk.CTk):

    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("TurboDownloader")
        self.geometry("1360x860")
        try:
            self.iconbitmap(_resource("icon.ico"))
        except Exception:
            pass  # icon not found — non-blocking

        # Thread-safe queue for UI updates
        self.uiq: "queue.Queue[tuple]" = queue.Queue()

        # Shared HTTP session across all workers
        self.req = requests.Session()
        self.req.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "*/*",
            "Connection": "keep-alive",
        })

        # Default destination loaded from settings (can be overridden per-batch)
        self.download_path: Optional[str] = None  # resolved at start time
        self.items: dict[int, DownloadItem] = {}  # idx → item (no gaps)
        self._next_idx: int = 0                    # next index to assign
        self.rows: dict[int, DownloadRow] = {}
        self.executor: Optional[ThreadPoolExecutor] = None
        self.stop_all_event = threading.Event()
        self._scan_cancel_event = threading.Event()  # interrompt le crawl en cours

        # Global speed — 3s sliding window
        self._speed_lock = threading.Lock()
        self._speed_samples: deque = deque()    # (timestamp, bytes)
        self._global_total_bytes = 0

        # Throttle — shared counter across all workers
        self._throttle_lock = threading.Lock()
        self._throttle_window_start = time.time()
        self._throttle_bytes_this_second = 0

        # Filtre actif in la liste de droite
        self._active_filter = "all"

        # Set of item indices currently being requeued by priority_one
        # Workers check this to decide between "paused" and "waiting" on exit
        self._requeue_set: set = set()

        # Settings (temp dir, etc.)
        self._settings = load_settings()

        # History des téléchargements
        self._history = HistoryManager()

        # Remote control server (started if enabled in settings)
        self._remote_server: remote_server.RemoteServer = None
        self._remote_client = None   # RemoteClient instance when connected as client
        self._start_remote_if_enabled()

        self._build_ui()

        # Windows taskbar — initialized after _build_ui (needs HWND)
        # Slight delay to let Tk create the window first
        self._taskbar: TaskbarProgress = None
        self.after(500, self._init_taskbar)
        self.after(1000, self._check_dependencies)
        self.after(600, self._update_remote_status_bar)   # refresh badge after UI ready

        self.after(80, self._process_ui_queue)
        self.after(1000, self._tick_global_speed)

    @property
    def _active_extensions(self) -> tuple:
        """Retourne les extensions activées in les settings (tuple pour endswith)."""
        exts = self._settings.get("extensions", DEFAULT_EXTENSIONS)
        return tuple(ext for ext, enabled in exts.items() if enabled) or (".mkv", ".mp4")

    def _init_taskbar(self):
        """Initializes the taskbar progress bar (Windows only)."""
        try:
            hwnd = self.winfo_id()
            self._taskbar = TaskbarProgress(hwnd)
        except Exception as e:
            print(f"[taskbar] init différée échouée: {e}")
            self._taskbar = TaskbarProgress(0)  # no-op fallback

    # ---------------------------------------------------------------- Remote server

    def _record_dest_history(self, dest: str):
        """Adds a destination folder to the recent history (max 10, no duplicates)."""
        if not dest:
            return
        history = self._settings.get("dest_history", [])
        if dest in history:
            history.remove(dest)
        history.insert(0, dest)
        self._settings["dest_history"] = history[:10]
        # Persist immediately (lightweight — just the history key)
        from settings_popup import save_settings
        save_settings(self._settings)

    def _start_remote_if_enabled(self):
        """Starts the remote control server if enabled in settings."""
        if not self._settings.get("remote_enabled", False):
            return
        if not self._settings.get("remote_username") or not self._settings.get("remote_password_hash"):
            print("[remote] Skipping start — username or password not configured")
            return
        self._remote_server = remote_server.RemoteServer(self, self._settings)
        ok = self._remote_server.start()
        if not ok:
            self._remote_server = None

    def _apply_remote_settings(self):
        """
        Called by on_settings_save — restarts the server if the enabled flag changed.
        """
        enabled = self._settings.get("remote_enabled", False)
        running = self._remote_server is not None and self._remote_server.is_running

        if enabled and not running:
            self._start_remote_if_enabled()
        elif not enabled and running:
            self._remote_server.stop()
            self._remote_server = None

        self._update_remote_status_bar()

    def _update_remote_status_bar(self):
        """Shows/hides/updates the remote status badge in the sidebar."""
        # Guard — bar may not exist yet during __init__
        if not hasattr(self, "_remote_bar"):
            return

        srv_running = self._remote_server is not None and self._remote_server.is_running
        cli_connected = self._remote_client is not None and self._remote_client.connected

        if srv_running:
            port = self._settings.get("remote_port", 9988)
            self._remote_bar_lbl.configure(
                text=f"📡  Server mode — listening on :{port}",
                text_color="#7aaa7a")
            self._remote_bar.configure(fg_color="#0d1f0d", border_color="#1a3a1a")
            self._remote_bar.pack(fill="x", padx=16, pady=(0, 8))
        elif cli_connected:
            host = self._settings.get("remote_client_host", "?")
            port = self._settings.get("remote_client_port", 9988)
            self._remote_bar_lbl.configure(
                text=f"🔗  Client mode — connected to {host}:{port}",
                text_color="#7aaadd")
            self._remote_bar.configure(fg_color="#0d0d1f", border_color="#1a1a3a")
            self._remote_bar.pack(fill="x", padx=16, pady=(0, 8))
        else:
            self._remote_bar.pack_forget()

    def _open_remote_control(self):
        """Opens the Remote Control popup (connect to another instance)."""
        remote_server.open_remote_control_popup(
            master=self,
            settings=self._settings,
            on_add_url=lambda url: (
                self.url_box.delete("1.0", "end"),
                self.url_box.insert("end", url),
                self._on_start(),
            ),
        )

    def _check_dependencies(self):
        """Checks ffmpeg and Node.js availability. Installs Node if missing."""
        status = ffmpeg_setup.get_status()

        if not status["ffmpeg"]:
            print("[deps] ffmpeg not found — place ffmpeg.exe next to main.py for best quality")

        if not status["node"]:
            print("[deps] Node.js not found — installing via nodeenv…")

            def on_progress(msg):
                print(f"[deps] {msg}")

            def on_done():
                print("[deps] Node.js ready ✓ — yt-dlp will use it for next downloads")

            def on_error(msg):
                print(f"[deps] Node.js install failed: {msg}")

            ffmpeg_setup.install_nodeenv(
                on_progress=on_progress,
                on_done=on_done,
                on_error=on_error,
            )
        else:
            print(f"[deps] Node.js found at {status['node_path']}")

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        # ── Root layout : sidebar gauche + zone principale droite ─────────────
        root = ctk.CTkFrame(self, fg_color="transparent")
        root.pack(fill="both", expand=True)

        # ── Sidebar ───────────────────────────────────────────────────────────
        sidebar = ctk.CTkFrame(root, width=300, corner_radius=0, fg_color="#1a1a1a")
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # Logo / titre
        title_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        title_frame.pack(fill="x", padx=16, pady=(20, 4))
        ctk.CTkLabel(title_frame, text="⬇  TurboDownloader",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(title_frame, text="v2.4", text_color="#555555",
                     font=ctk.CTkFont(size=11)).pack(anchor="w")

        # Séparateur
        ctk.CTkFrame(sidebar, height=1, fg_color="#2a2a2a").pack(fill="x", padx=0, pady=(8, 12))

        # ── URL box ───────────────────────────────────────────────────────────
        ctk.CTkLabel(sidebar, text="URLs", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#888888").pack(anchor="w", padx=16, pady=(0, 4))
        self.url_box = ctk.CTkTextbox(sidebar, height=100, wrap="none",
                                      activate_scrollbars=True,
                                      fg_color="#242424", border_width=1,
                                      border_color="#333333")
        self.url_box.pack(fill="x", padx=16, pady=(0, 4))
        ctk.CTkLabel(sidebar, text="One URL per line — spaces also work",
                     text_color="#555555", font=ctk.CTkFont(size=10)).pack(
                         anchor="w", padx=16, pady=(0, 12))

        # ── Ctrl+V global : inject URLs from clipboard if focus ≠ url_box ───
        self.bind("<Control-v>", self._on_global_paste)

        # ── Options ───────────────────────────────────────────────────────────
        ctk.CTkFrame(sidebar, height=1, fg_color="#2a2a2a").pack(fill="x", padx=0, pady=(0, 10))

        # keep_tree_var — géré dans la popup FileTreePopup directement
        self.keep_tree_var = ctk.BooleanVar(value=True)  # valeur initiale pour la popup

        # Workers — lus depuis les settings

        # ── Boutons START / STOP ──────────────────────────────────────────────
        ctk.CTkFrame(sidebar, height=1, fg_color="#2a2a2a").pack(fill="x", padx=0, pady=(0, 12))

        self.start_btn = ctk.CTkButton(
            sidebar, text="▶  Start", height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#1f6aa5", hover_color="#1a5a8f",
            command=self.start_downloads)
        self.start_btn.pack(fill="x", padx=16, pady=(0, 6))

        self.stop_btn = ctk.CTkButton(
            sidebar, text="⏹  Stop all", height=34,
            fg_color="#3a1010", hover_color="#5a1515",
            border_width=1, border_color="#8B0000",
            command=self.stop_all)
        self.stop_btn.pack(fill="x", padx=16, pady=(0, 12))

        # ── Stats globales ────────────────────────────────────────────────────
        ctk.CTkFrame(sidebar, height=1, fg_color="#2a2a2a").pack(fill="x", padx=0, pady=(0, 10))

        self.global_speed_label = ctk.CTkLabel(
            sidebar, text="–", anchor="w",
            font=ctk.CTkFont(size=18, weight="bold"))
        self.global_speed_label.pack(anchor="w", padx=16, pady=(4, 0))

        self.global_dl_label = ctk.CTkLabel(
            sidebar, text="Total: 0 B", anchor="w",
            text_color="#555555", font=ctk.CTkFont(size=11))
        self.global_dl_label.pack(anchor="w", padx=16, pady=(0, 10))

        # ── Remote status bar ─────────────────────────────────────────────────
        # Visible seulement quand le serveur tourne OU qu'un client est connecté
        self._remote_bar = ctk.CTkFrame(sidebar, fg_color="#0d1f0d",
                                        corner_radius=6, border_width=1,
                                        border_color="#1a3a1a")
        self._remote_bar_lbl = ctk.CTkLabel(
            self._remote_bar, text="", anchor="w",
            font=ctk.CTkFont(size=10), text_color="#7aaa7a")
        self._remote_bar_lbl.pack(fill="x", padx=10, pady=5)
        # Démarré masqué — affiché par _update_remote_status_bar()

        # ── Bas sidebar : Clear + Settings + History + Remote ────────────────

        bot_btns = ctk.CTkFrame(sidebar, fg_color="transparent")
        bot_btns.pack(side="bottom", fill="x", padx=16, pady=(0, 4))
        ctk.CTkButton(bot_btns, text="⚙ Settings", height=30,
                      fg_color="transparent", border_width=1, border_color="#333333",
                      font=ctk.CTkFont(size=12),
                      command=self._open_settings).pack(side="left", expand=True,
                                                        fill="x", padx=(0, 4))
        ctk.CTkButton(bot_btns, text="🕐 History", height=30,
                      fg_color="transparent", border_width=1, border_color="#333333",
                      font=ctk.CTkFont(size=12),
                      command=self._open_history).pack(side="left", expand=True,
                                                       fill="x", padx=(4, 4))
        ctk.CTkButton(bot_btns, text="📡 Remote", height=30,
                      fg_color="transparent", border_width=1, border_color="#333333",
                      font=ctk.CTkFont(size=12),
                      command=self._open_remote_control).pack(side="left", expand=True,
                                                              fill="x", padx=(0, 0))

        ctk.CTkLabel(sidebar, text="© Thomas PIERRE",
                     font=ctk.CTkFont(size=10), text_color="#333333").pack(
                         side="bottom", anchor="w", padx=16, pady=(0, 6))

        # ── Zone principale droite ────────────────────────────────────────────
        main_area = ctk.CTkFrame(root, fg_color="#111111", corner_radius=0)
        main_area.pack(side="right", fill="both", expand=True)

        # Barre du haut : titre + filtres
        top_bar = ctk.CTkFrame(main_area, fg_color="#1a1a1a", corner_radius=0, height=50)
        top_bar.pack(fill="x")
        top_bar.pack_propagate(False)

        ctk.CTkLabel(top_bar, text="Downloads",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(
                         side="left", padx=16, pady=12)

        # Compteur total dans le header
        self._total_count_lbl = ctk.CTkLabel(
            top_bar, text="", text_color="#555555",
            font=ctk.CTkFont(size=11))
        self._total_count_lbl.pack(side="left", padx=(0, 16), pady=12)

        # Bouton Clear finished — bas droite du header
        ctk.CTkButton(top_bar, text="🗑 Clear", width=80, height=28,
                      font=ctk.CTkFont(size=11),
                      fg_color="transparent", border_width=1, border_color="#333333",
                      hover_color="#2a2a2a",
                      command=self.clear_finished).pack(side="right", padx=(0, 10), pady=10)

        # Filtres compacts
        filter_frame = ctk.CTkFrame(top_bar, fg_color="transparent")
        filter_frame.pack(side="right", padx=4)

        self._filter_btns = {}
        filters = [
            ("all",         "All",          "#1f6aa5"),
            ("downloading", "↓ Active",     "#2e8b57"),
            ("paused",      "⏸ Paused",     "#5a7a9a"),
            ("waiting",     "⏳ Waiting",    "#5a5a5a"),
            ("done",        "✓ Done",       "#2e6b3e"),
            ("error",       "✗ Errors",     "#8B0000"),
        ]
        for fkey, flabel, fcolor in filters:
            btn = ctk.CTkButton(
                filter_frame, text=flabel, width=90, height=28,
                font=ctk.CTkFont(size=11),
                fg_color=fcolor if fkey == "all" else "transparent",
                border_width=1, border_color=fcolor,
                command=lambda k=fkey: self._set_filter(k),
            )
            btn.pack(side="left", padx=2)
            self._filter_btns[fkey] = btn

        # Zone scroll + empty state
        self._scroll_container = ctk.CTkFrame(main_area, fg_color="transparent")
        self._scroll_container.pack(fill="both", expand=True, padx=0, pady=0)

        # Empty state (affiché quand aucun DL)
        self._empty_frame = ctk.CTkFrame(self._scroll_container, fg_color="transparent")
        self._empty_frame.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(self._empty_frame, text="⬇",
                     font=ctk.CTkFont(size=48), text_color="#2a2a2a").pack()
        ctk.CTkLabel(self._empty_frame, text="No downloads yet",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#333333").pack(pady=(4, 2))
        ctk.CTkLabel(self._empty_frame, text="Paste a URL and hit Start",
                     text_color="#3a3a3a", font=ctk.CTkFont(size=12)).pack()

        self.scroll = ctk.CTkScrollableFrame(self._scroll_container,
                                             fg_color="transparent")
        self.scroll.pack(fill="both", expand=True, padx=6, pady=6)

        # Init empty state dès le démarrage
        self.after(100, self._refresh_filter_counts)

    # ---------------------------------------------------------------- Thread-safe UI helpers

    def ui(self, fn, *args, **kwargs):
        """Queues a function to run on the UI thread."""
        self.uiq.put((fn, args, kwargs))

    def _process_ui_queue(self):
        try:
            while True:
                fn, args, kwargs = self.uiq.get_nowait()
                try:
                    fn(*args, **kwargs)
                except Exception as e:
                    print("[UIQ]", type(e).__name__, e)
        except queue.Empty:
            pass
        self.after(80, self._process_ui_queue)

    def ui_call(self, fn, *args, **kwargs):
        """Appel synchrone depuis un thread background → attend la réponse du thread UI.
        Lève RuntimeError si le thread UI ne répond pas in les 10 secondes.
        """
        ev = threading.Event()
        box = {"v": None, "e": None}

        def _run():
            try:
                box["v"] = fn(*args, **kwargs)
            except Exception as e:
                box["e"] = e
            finally:
                ev.set()

        self.ui(_run)
        if not ev.wait(timeout=10):
            raise RuntimeError("ui_call timeout : le thread UI ne répond plus")
        if box["e"]:
            raise box["e"]
        return box["v"]

    def _get_urls(self) -> list[str]:
        """Extracts all URLs from the input box — works with raw URLs, mixed
        text (copy-paste from a webpage, an email, a markdown doc, etc.).
        Strips surrounding punctuation artifacts like trailing ) ] > , ; .
        Deduplicates while preserving order.
        """
        import re
        raw = self.url_box.get("1.0", "end")

        # Match anything that looks like an http(s) URL.
        # Brackets [] are kept — they appear in real-world URLs (release names,
        # Debrid links, etc.). Only stop at whitespace and angle/curly brackets.
        pattern = re.compile(
            r'https?://'          # scheme
            r'[^\s<>"\'{}]+'     # URL body — stops at whitespace, <>"'{}
        )
        candidates = pattern.findall(raw)

        # Strip trailing punctuation that is never part of a real URL
        trailing_junk = re.compile(r'[.,;:!?\'"}>]+$')

        seen: set = set()
        urls: list = []
        for url in candidates:
            url = trailing_junk.sub("", url)
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def _on_global_paste(self, event=None):
        """Ctrl+V global — injects clipboard URLs into url_box unless it already has focus."""
        import re
        # If url_box already has focus, let the default paste behavior happen
        focused = self.focus_get()
        if focused is self.url_box or (hasattr(focused, 'master') and focused is self.url_box._textbox):
            return  # normal paste

        try:
            clipboard = self.clipboard_get()
        except Exception:
            return

        # Extract valid URLs from clipboard
        pattern = re.compile(r'https?://[^\s<>"\'{}]+')
        trailing_junk = re.compile(r'[.,;:!?\'"}>]+$')
        urls = [trailing_junk.sub("", u) for u in pattern.findall(clipboard)]
        urls = [u for u in urls if u]

        if not urls:
            return

        # Inject into url_box (append if already has content)
        current = self.url_box.get("1.0", "end").strip()
        injection = "\n".join(urls)
        self.url_box.delete("1.0", "end")
        self.url_box.insert("1.0", (current + "\n" + injection).strip())

    def _open_settings(self):
        def on_save():
            # Update the existing dict in place (no reference reassignment)
            self._settings.update(load_settings())
            # Apply remote server changes (start/stop as needed)
            self._apply_remote_settings()
        SettingsPopup(self, self._settings, on_save)

    def _open_history(self):
        def on_redownload(url: str):
            self.url_box.delete("1.0", "end")
            self.url_box.insert("1.0", url)
            self.start_downloads()
        HistoryPopup(self, self._history, on_redownload)

    # ----------------------------------------------------------------- Crawl

    def get_all_files(self, url: str, base_url: str = None,
                      cancel_event: threading.Event = None) -> list:
        """Recursively scrapes and returns a list of (file_url, relative_path).
        Stops cleanly if cancel_event is set (STOP or timeout).
        """
        if base_url is None:
            base_url = url
        if cancel_event is None:
            cancel_event = self._scan_cancel_event

        results = []

        if cancel_event.is_set():
            return results

        try:
            r = self.req.get(url, timeout=30, allow_redirects=True)
            r.raise_for_status()
        except Exception as e:
            print("[crawl]", e)
            return results

        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a"):
            if cancel_event.is_set():
                break
            href = a.get("href", "")
            if not href or href in ("../", "./", "/"):
                continue
            full = urljoin(url, href)
            if not full.startswith(base_url.rstrip("/") + "/") and full != base_url:
                if href.startswith("http"):
                    continue
            if href.endswith("/"):
                results.extend(self.get_all_files(full, base_url, cancel_event))
            elif self._settings.get("all_files", False) or href.lower().endswith(self._active_extensions):
                rel = full[len(base_url.rstrip("/")):]
                rel = rel.lstrip("/")
                rel_dir = os.path.dirname(rel)
                results.append((full, rel_dir))

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for item in results:
            if item[0] not in seen:
                seen.add(item[0])
                unique.append(item)
        return unique

    # ----------------------------------------------------------------- Gestion des rows

    def _add_row_for_item(self, idx: int, item: DownloadItem):
        display_name = item.filename
        if getattr(item, "from_remote", False):
            display_name = f"📡 {item.filename}"
        row = DownloadRow(
            self.scroll, display_name,
            on_pause=lambda i=idx:    self.pause_one(i),
            on_cancel=lambda i=idx:   self.cancel_one(i),
            on_remove=lambda i=idx:   self.remove_one(i),
            on_priority=lambda i=idx: self.priority_one(i),
        )
        if getattr(item, "from_remote", False):
            row.frame.configure(border_color="#1a3a5a")
            row.name_lbl.configure(text_color="#7ab8e8")
        self.rows[idx] = row
        self._apply_filter_to_row(idx)

    def _add_remote_shadow_row(self, url: str, dest: str):
        """
        Adds a read-only 'shadow' row on the client side to track a download
        running on the remote server. Polls the server every 2s for updates.
        """
        import os as _os
        name = _os.path.basename(url.split("?")[0]) or url[:60]

        # Assign a local shadow index (negative to avoid collisions with real items)
        shadow_idx = -(len(self._shadow_rows) + 1) if hasattr(self, "_shadow_rows") else -1
        if not hasattr(self, "_shadow_rows"):
            self._shadow_rows = {}

        # Create a disabled DownloadRow (no actions — read-only)
        row = DownloadRow(
            self.scroll, f"📡 {name}",
            on_pause=lambda: None,
            on_cancel=lambda: None,
            on_remove=lambda: self._remove_shadow_row(shadow_idx),
            on_priority=None,
        )
        # Disable all action buttons — it's just a display row
        row.pause_btn.configure(state="disabled")
        row.cancel_btn.configure(state="disabled")
        row.status.configure(text="📡 Sent to server", text_color="#5a9acd")
        row.speed_lbl.configure(text="Remote")
        row.eta_lbl.configure(text=dest[:30] + "…" if len(dest) > 30 else dest)
        row.progress.configure(progress_color="#1a4a7a")

        self._shadow_rows[shadow_idx] = {"row": row, "url": url, "server_idx": None}
        self._refresh_filter_counts()

        # Start polling thread to follow progress on the server
        def _poll():
            import time as _time
            consecutive_done = 0
            while shadow_idx in getattr(self, "_shadow_rows", {}):
                _time.sleep(2)
                if self._remote_client is None or not self._remote_client.connected:
                    break
                try:
                    data = self._remote_client.get_status()
                    if not data:
                        continue
                    # Find matching download on server by URL
                    match = next(
                        (d for d in data.get("downloads", [])
                         if d.get("url") == url),
                        None
                    )
                    if not match:
                        continue

                    state   = match.get("state", "?")
                    pct     = match.get("progress", 0) / 100
                    speed   = match.get("speed_bps", 0)
                    total   = match.get("total", 0)
                    fname   = match.get("filename", name)

                    state_map = {
                        "downloading": ("📡 Downloading", "#2e8b57"),
                        "waiting":     ("📡 Waiting",     "#666666"),
                        "paused":      ("📡 Paused",      "#5a7a9a"),
                        "moving":      ("📡 Converting…", "#888800"),
                        "done":        ("📡 Done ✓",      "#2e6b3e"),
                        "error":       (f"📡 Error: {match.get('error','')[:30]}", "#8B0000"),
                        "canceled":    ("📡 Canceled",    "#555555"),
                    }
                    lbl, color = state_map.get(state, (f"📡 {state}", "#888888"))

                    def _ui_update(r=row, p=pct, l=lbl, c=color,
                                   sp=speed, t=total, fn=fname):
                        if shadow_idx not in getattr(self, "_shadow_rows", {}):
                            return
                        r.name_lbl.configure(text=f"📡 {fn}")
                        r.status.configure(text=l, text_color=c)
                        r.progress.set(p)
                        # Speed
                        if sp > 0:
                            if sp < 1024:
                                spd_txt = f"{sp} B/s"
                            elif sp < 1024**2:
                                spd_txt = f"{sp/1024:.1f} KB/s"
                            else:
                                spd_txt = f"{sp/1024**2:.2f} MB/s"
                            r.speed_lbl.configure(text=f"Remote · {spd_txt}")
                        if state in ("done", "error", "canceled"):
                            r.remove_btn.configure(state="normal")

                    self.ui(_ui_update)

                    if state in ("done", "error", "canceled"):
                        consecutive_done += 1
                        if consecutive_done >= 2:
                            break   # Stop polling — final state reached
                    else:
                        consecutive_done = 0

                except Exception as e:
                    print(f"[shadow-poll] Error: {e}")

        threading.Thread(target=_poll, daemon=True, name=f"ShadowPoll{shadow_idx}").start()

    def _remove_shadow_row(self, shadow_idx: int):
        """Removes a shadow row from the client display."""
        shadow = getattr(self, "_shadow_rows", {}).pop(shadow_idx, None)
        if shadow:
            shadow["row"].frame.destroy()
        self._refresh_filter_counts()

    def cancel_one(self, idx: int):
        if idx in self.items:
            it = self.items[idx]
            it.cancel_event.set()
            row = self.rows.get(idx)
            if row and it.state in ("waiting", "downloading"):
                row.status.configure(text="Canceling…")

    def pause_one(self, idx: int):
        """Toggles pause ↔ resume on an individual download row."""
        if idx not in self.items:
            return
        it = self.items[idx]
        if it.state == "downloading":
            # Pause
            it.pause_event.set()
        elif it.state == "paused":
            # Resume
            it.pause_event.clear()
            it.cancel_event.clear()
            it.state = "waiting"
            it.speed_window.clear()
            self._update_row_ui(idx)
            self.executor = self.executor or ThreadPoolExecutor(max_workers=1)
            fut = self.executor.submit(self._download_worker, idx)
            fut.add_done_callback(
                lambda f: f.exception() and print("[WORKER]", f.exception()))

    def priority_one(self, idx: int):
        """Launches a waiting download immediately.
        If all worker slots are taken, requeues the least-advanced downloading item
        back to waiting (via pause_event + _requeue_set), then submits priority first.
        """
        if idx not in self.items:
            return
        it = self.items[idx]
        if it.state != "waiting":
            return

        try:
            workers = int(self._settings.get("workers", 10))
        except Exception:
            workers = 10

        active = [(i, d) for i, d in self.items.items() if d.state == "downloading"]

        if len(active) >= workers:
            def _completion(item: DownloadItem) -> float:
                if item.total_size and item.total_size > 0:
                    return (item.resume_from + item.downloaded) / item.total_size
                return 0.0

            victim_idx, victim = min(active, key=lambda p: _completion(p[1]))

            def _swap():
                # 1. Mark victim as requeue so the worker exits to "waiting"
                self._requeue_set.add(victim_idx)
                victim.pause_event.set()

                # 2. Poll until worker exited (max 2s)
                for _ in range(40):
                    if victim.state == "waiting":
                        break
                    time.sleep(0.05)

                if idx not in self.items:
                    self._requeue_set.discard(victim_idx)
                    return

                # 3. Clear events on victim (worker already cleared _requeue_set)
                victim.pause_event.clear()
                victim.cancel_event.clear()

                # 4. Clear events on priority item and submit both
                it.cancel_event.clear()
                it.pause_event.clear()

                def _submit():
                    if idx not in self.items:
                        return
                    # Priority first, victim re-queued right after
                    fut_p = self.executor.submit(self._download_worker, idx)
                    fut_p.add_done_callback(
                        lambda f: f.exception() and print("[PRIORITY]", f.exception()))
                    fut_v = self.executor.submit(self._download_worker, victim_idx)
                    fut_v.add_done_callback(
                        lambda f: f.exception() and print("[REQUEUE]", f.exception()))

                self.ui(_submit)

            threading.Thread(target=_swap, daemon=True).start()

        else:
            # Free slot — submit directly
            it.cancel_event.clear()
            it.pause_event.clear()
            self.executor = self.executor or ThreadPoolExecutor(max_workers=1)
            fut = self.executor.submit(self._download_worker, idx)
            fut.add_done_callback(
                lambda f: f.exception() and print("[PRIORITY]", f.exception()))

    def remove_one(self, idx: int):
        row = self.rows.get(idx)
        if not row:
            return
        it = self.items[idx]
        if it.state not in ("done", "error", "canceled", "skipped"):
            return
        row.frame.destroy()
        del self.rows[idx]
        del self.items[idx]
        self._refresh_filter_counts()

    def clear_finished(self):
        for idx in list(self.rows.keys()):
            it = self.items.get(idx)
            if it and it.state in ("done", "error", "canceled", "skipped"):
                self.remove_one(idx)

    # ----------------------------------------------------------------- Formatting

    @staticmethod
    def _fmt_speed(bps: float) -> str:
        if bps < 1024:
            return f"{bps:.0f} B/s"
        if bps < 1024 * 1024:
            return f"{bps/1024:.1f} KB/s"
        return f"{bps/1024/1024:.2f} MB/s"

    @staticmethod
    def _fmt_eta(seconds: Optional[float]) -> str:
        if seconds is None or seconds <= 0 or seconds == float("inf"):
            return "ETA –"
        s = int(seconds)
        if s < 60:
            return f"ETA {s}s"
        m, s = divmod(s, 60)
        if m < 60:
            return f"ETA {m}m{s:02d}s"
        h, m = divmod(m, 60)
        return f"ETA {h}h{m:02d}m"

    @staticmethod
    def _fmt_size(b: int) -> str:
        if b < 1024:
            return f"{b} B"
        if b < 1024 * 1024:
            return f"{b/1024:.1f} KB"
        if b < 1024 ** 3:
            return f"{b/1024/1024:.1f} MB"
        return f"{b/1024**3:.2f} GB"

    # ----------------------------------------------------------------- Row UI update

    def _update_row_ui(self, idx: int):
        it = self.items[idx]
        row = self.rows.get(idx)
        if not row:
            return

        row.update_state_style(it.state)

        state_labels = {
            "waiting":     "Waiting",
            "downloading": "Downloading",
            "paused":      "Paused",
            "moving":      "Converting…" if getattr(it, "worker_type", "http") == "ytdlp" else "Moving…",
            "done":        "Done",
            "error":       f"Erreur: {it.error_msg[:40]}",
            "canceled":    "Canceled",
            "skipped":     "Already exists (skipped)",
        }
        status_text = state_labels.get(it.state, it.state)
        # Badge Remote si le DL vient d'un client distant
        if getattr(it, "from_remote", False):
            status_text = f"📡 {status_text}"
        row.status.configure(text=status_text)

        # Instantaneous speed via sliding window
        now = time.time()
        win = it.speed_window
        while win and now - win[0][0] > 4.0:
            win.popleft()
        if win:
            total_bytes = sum(s[1] for s in win)
            elapsed_w = now - win[0][0] if len(win) > 1 else 0.5
            inst_speed = total_bytes / max(elapsed_w, 0.1)
        else:
            elapsed = max(now - it.started_at, 0.2)
            inst_speed = it.downloaded / elapsed

        row.speed_lbl.configure(text=self._fmt_speed(inst_speed))

        if it.total_size and it.total_size > 0:
            p = min((it.resume_from + it.downloaded) / it.total_size, 1.0)
            row.progress.set(p)
            remaining = max(it.total_size - it.resume_from - it.downloaded, 0)
            eta = remaining / inst_speed if inst_speed > 100 else None
            row.eta_lbl.configure(text=self._fmt_eta(eta))
            done_b = it.resume_from + it.downloaded
            row.name_lbl.configure(
                text=f"{it.filename}  [{self._fmt_size(done_b)} / {self._fmt_size(it.total_size)}]"
            )
        else:
            row.progress.set(0)
            row.eta_lbl.configure(text="ETA –")

        if it.state in ("done", "error", "canceled", "skipped"):
            row.pause_btn.configure(state="disabled", text="⏸", fg_color="transparent")
            row.cancel_btn.configure(state="disabled")
            row.remove_btn.configure(state="normal")
            if it.state == "done":
                row.progress.set(1.0)
        elif it.state == "paused":
            row.pause_btn.configure(state="normal", text="▶", fg_color="transparent", border_color="#1f6aa5", text_color="#1f6aa5")
            row.cancel_btn.configure(state="normal")
            row.remove_btn.configure(state="disabled")
        elif it.state == "downloading":
            row.pause_btn.configure(state="normal", text="⏸", fg_color="transparent", border_color="#3a3a3a", text_color="#dddddd")
            row.cancel_btn.configure(state="normal")
            row.remove_btn.configure(state="disabled")
        elif it.state == "moving":
            row.pause_btn.configure(state="disabled", text="⏸", fg_color="transparent")
            row.cancel_btn.configure(state="disabled")
            row.remove_btn.configure(state="disabled")
        else:  # waiting
            row.pause_btn.configure(state="disabled", text="⏸", fg_color="transparent")
            row.cancel_btn.configure(state="normal")
            row.remove_btn.configure(state="disabled")

        self._apply_filter_to_row(idx)

    # ----------------------------------------------------------------- Filters

    def _set_filter(self, fkey: str):
        self._active_filter = fkey
        colors = {
            "all":         "#1f6aa5",
            "downloading": "#2e8b57",
            "paused":      "#1f6aa5",
            "moving":      "#5a5a5a",
            "waiting":     "#5a5a5a",
            "done":        "#2e8b57",
            "canceled":    "#8B4513",
            "error":       "#8B0000",
        }
        for k, btn in self._filter_btns.items():
            btn.configure(fg_color=colors[k] if k == fkey else "transparent")
        for idx in self.rows:
            self._apply_filter_to_row(idx)

    def _apply_filter_to_row(self, idx: int):
        row = self.rows.get(idx)
        it = self.items.get(idx)
        if not row or not it:
            return
        if self._active_filter == "all":
            row.set_visible(True)
        else:
            row.set_visible(it.state == self._active_filter)

    def _refresh_filter_counts(self):
        counts = {k: 0 for k in self._filter_btns}
        active_items = list(self.items.values())
        total = len(active_items)
        counts["all"] = total
        for it in active_items:
            if it.state in counts:
                counts[it.state] += 1
            # "active" regroupe downloading + moving + waiting pour le filtre all
        labels = {
            "all":         "All",
            "downloading": "↓ Active",
            "paused":      "⏸ Paused",
            "waiting":     "⏳ Waiting",
            "done":        "✓ Done",
            "error":       "✗ Errors",
        }
        for k, btn in self._filter_btns.items():
            cnt = counts.get(k, 0)
            btn.configure(text=f"{labels[k]}  {cnt}" if cnt else labels[k])

        # Compteur header
        self._total_count_lbl.configure(
            text=f"{total} item{'s' if total != 1 else ''}" if total else "")

        # Empty state : visible seulement si aucun item
        if total == 0:
            self._empty_frame.lift()
        else:
            self._empty_frame.lower()

    # ----------------------------------------------------------------- Global speed

    def _record_bytes(self, n: int):
        """Records n downloaded bytes — called from worker threads."""
        now = time.time()
        with self._speed_lock:
            self._speed_samples.append((now, n))
            self._global_total_bytes += n
            while self._speed_samples and now - self._speed_samples[0][0] > 3.0:
                self._speed_samples.popleft()

    def _tick_global_speed(self):
        with self._speed_lock:
            now = time.time()
            while self._speed_samples and now - self._speed_samples[0][0] > 3.0:
                self._speed_samples.popleft()
            samples = list(self._speed_samples)
            total = self._global_total_bytes

        if len(samples) >= 2:
            window_bytes = sum(s[1] for s in samples)
            window_sec = samples[-1][0] - samples[0][0]
            speed = window_bytes / max(window_sec, 0.1)
        elif samples:
            speed = samples[0][1]
        else:
            speed = 0.0

        speed_text = "–" if speed == 0.0 else self._fmt_speed(speed)
        self.global_speed_label.configure(text=speed_text)
        self.global_dl_label.configure(text=f"Total: {self._fmt_size(total)}")

        # ── Taskbar update ────────────────────────────────────────────
        if self._taskbar:
            active = [it for it in self.items.values()
                      if it.state in ("downloading", "moving", "waiting", "paused")]
            has_error = any(it.state == "error" for it in self.items.values())

            if not active:
                if has_error:
                    self._taskbar.set_error()
                else:
                    self._taskbar.clear()
            else:
                known    = [it for it in active if it.total_size]
                all_paused = all(it.state == "paused" for it in active)
                if all_paused:
                    self._taskbar.set_paused()
                elif not known:
                    self._taskbar.set_indeterminate()
                else:
                    total_dl  = sum(it.resume_from + it.downloaded for it in known)
                    total_sz  = sum(it.total_size for it in known)
                    ratio     = total_dl / total_sz if total_sz else 0.0
                    if has_error:
                        self._taskbar.set_error()
                    else:
                        self._taskbar.set_progress(ratio)

        self.after(1000, self._tick_global_speed)

    # ----------------------------------------------------------------- Download worker

    def _throttle_chunk(self, n: int):
        """Throttles the worker if the global bandwidth limit is reached.
        n = number of bytes just written.
        The limit is shared across all workers — sleep happens
        OUTSIDE the lock so other workers are not blocked during the wait.
        """
        limit_bps = self._settings.get("throttle", 0)
        if not limit_bps or limit_bps <= 0:
            return  # illimité

        limit_bps_bytes = limit_bps * 1024 * 1024
        sleep_time = 0.0

        with self._throttle_lock:
            now = time.time()
            # Reset the window every new second
            if now - self._throttle_window_start >= 1.0:
                self._throttle_window_start = now
                self._throttle_bytes_this_second = 0

            self._throttle_bytes_this_second += n

            if self._throttle_bytes_this_second >= limit_bps_bytes:
                # Compute wait time until the next window
                elapsed = time.time() - self._throttle_window_start
                sleep_time = max(0.0, 1.0 - elapsed)
                # Reset for the next second
                self._throttle_window_start = time.time() + sleep_time
                self._throttle_bytes_this_second = 0

        # Sleep OUTSIDE the lock — other workers keep running during the wait
        if sleep_time > 0:
            time.sleep(sleep_time)

    def _get_temp_path(self, it) -> str:
        """Retourne le chemin du fichier .part in le dossier temp."""
        temp_dir = self._settings.get("temp_dir", DEFAULT_TEMP_DIR)
        os.makedirs(temp_dir, exist_ok=True)
        return os.path.join(temp_dir, it.filename + ".part")

    # Errors réseau qui déclenchent un retry (pas les erreurs "métier")
    _RETRYABLE = (
        "timeout", "connectionerror", "chunkedencodingerror",
        "remotedisconnected", "connectionreset", "connectionaborted",
    )

    def _is_retryable(self, e: Exception) -> bool:
        name = type(e).__name__.lower()
        msg  = str(e).lower()
        return any(k in name or k in msg for k in self._RETRYABLE)

    def _download_multipart(self, idx: int, n_seg: int) -> str:
        """
        Downloads it.url in n_seg parallel segments.
        Returns: "done" | "canceled" | "paused" | "error" | "retry"
        """
        it = self.items[idx]
        total = it.total_size          # garanti non-None à cet appel
        temp_dir = self._settings.get("temp_dir", DEFAULT_TEMP_DIR)

        # ── Compute byte range for each segment ──────────────────────────
        seg_size = total // n_seg
        segments: list[SegmentInfo] = []
        for i in range(n_seg):
            start = i * seg_size
            end   = (start + seg_size - 1) if i < n_seg - 1 else (total - 1)
            tp    = os.path.join(temp_dir, f"{it.filename}.part.{i}")
            seg   = SegmentInfo(index=i, byte_start=start, byte_end=end, temp_path=tp)
            # Resume: if .part.N already exists and is complete, mark it done
            if os.path.exists(tp):
                got = os.path.getsize(tp)
                expected = end - start + 1
                if got >= expected:
                    seg.downloaded = expected
                    seg.done = True
                else:
                    seg.downloaded = got   # reprise partielle du segment
            segments.append(seg)
        it.segments = segments

        # Update global downloaded count from already-present segments
        it.downloaded = sum(s.downloaded for s in segments)

        # ── Launch pending segments in parallel ──────────────────────────
        pending = [s for s in segments if not s.done]
        seg_lock = threading.Lock()

        seg_futures = []
        seg_executor = ThreadPoolExecutor(max_workers=len(pending) if pending else 1)
        for seg in pending:
            f = seg_executor.submit(self._download_segment, idx, seg, seg_lock)
            seg_futures.append(f)

        # ── Wait for completion (or cancellation) ────────────────────────
        seg_executor.shutdown(wait=True)

        # ── Check final state ────────────────────────────────────────────
        if self.stop_all_event.is_set() or it.cancel_event.is_set():
            it.state = "canceled"
            self.ui(self._update_row_ui, idx)
            self.ui(self._refresh_filter_counts)
            # Clean up .part.N files
            for seg in segments:
                try:
                    if os.path.exists(seg.temp_path):
                        os.remove(seg.temp_path)
                except OSError:
                    pass
            return "canceled"

        if it.state == "paused":
            # Segments saved their progress, keep .part.N files for resume
            return "paused"

        # Check if any segment failed
        failed = [s for s in segments if s.error]
        if failed:
            err = failed[0].error
            retry_max = int(self._settings.get("retry_max", 3))
            if self._is_retryable(Exception(err)) and it.retry_count < retry_max:
                it.retry_count += 1
                # Only clean up failed segments (successful ones are kept)
                for s in failed:
                    try:
                        if os.path.exists(s.temp_path):
                            os.remove(s.temp_path)
                    except OSError:
                        pass
                it.segments = []
                return "retry"
            it.state     = "error"
            it.error_msg = f"Segment {failed[0].index} : {err}"
            self.ui(self._update_row_ui, idx)
            self.ui(self._refresh_filter_counts)
            return "error"

        # ── All les segments OK → assemblage ─────────────────────────────
        it.state = "moving"
        self.ui(self._update_row_ui, idx)
        self.ui(self._refresh_filter_counts)

        assembly_path = os.path.join(temp_dir, it.filename + ".part")
        try:
            os.makedirs(os.path.dirname(it.dest_path), exist_ok=True)
            with open(assembly_path, "wb") as out:
                for seg in segments:
                    with open(seg.temp_path, "rb") as inp:
                        shutil.copyfileobj(inp, out)
                    try:
                        os.remove(seg.temp_path)
                    except OSError:
                        pass
            shutil.move(assembly_path, it.dest_path)
        except OSError as e:
            it.state     = "error"
            it.error_msg = f"Assembly error: {e}"
            self.ui(self._update_row_ui, idx)
            self.ui(self._refresh_filter_counts)
            return "error"

        it.temp_path = ""
        it.segments  = []
        it.state     = "done"
        duration = time.time() - it.started_at
        self._history.log_entry(
            filename=it.filename, url=it.url,
            size_bytes=total, duration_s=duration,
        )
        self.ui(self._update_row_ui, idx)
        self.ui(self._refresh_filter_counts)
        return "done"

    def _download_segment(self, idx: int, seg: SegmentInfo,
                           seg_lock: threading.Lock):
        """Worker for a single segment. Downloads from seg.byte_start+seg.downloaded to seg.byte_end."""
        it = self.items[idx]

        # Compute actual start offset (segment resume)
        start_actual = seg.byte_start + seg.downloaded
        if start_actual > seg.byte_end:
            seg.done = True
            return

        headers = {"Range": f"bytes={start_actual}-{seg.byte_end}"}
        write_mode = "ab" if seg.downloaded > 0 else "wb"

        try:
            with self.req.get(it.url, stream=True, allow_redirects=True,
                              timeout=60, headers=headers) as r:
                if r.status_code not in (200, 206):
                    seg.error = f"HTTP {r.status_code}"
                    return

                last_ui = 0.0
                with open(seg.temp_path, write_mode) as f:
                    for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                        if self.stop_all_event.is_set() or it.cancel_event.is_set():
                            return   # canceled — keep .part.N for resume

                        if it.pause_event.is_set():
                            it.state = "paused"
                            self.ui(self._update_row_ui, idx)
                            self.ui(self._refresh_filter_counts)
                            return   # paused — keep .part.N

                        if not chunk:
                            continue

                        f.write(chunk)
                        n = len(chunk)
                        seg.downloaded += n

                        with seg_lock:
                            it.downloaded += n
                            it.speed_window.append((time.time(), n))

                        self._record_bytes(n)
                        self._throttle_chunk(n)

                        now = time.time()
                        if now - last_ui >= 0.20:
                            last_ui = now
                            self.ui(self._update_row_ui, idx)

            seg.done = True

        except Exception as e:
            seg.error = str(e)

    def _download_worker(self, idx: int):
        it = self.items[idx]
        retry_max   = int(self._settings.get("retry_max",   3))
        retry_delay = int(self._settings.get("retry_delay", 5))

        # Clear pause_event in case this item was requeued via priority
        it.pause_event.clear()

        while True:
            # ── Clean exit if canceled between retries ───────────────────
            if self.stop_all_event.is_set() or it.cancel_event.is_set():
                it.state = "canceled"
                self.ui(self._update_row_ui, idx)
                self.ui(self._refresh_filter_counts)
                return

            it.state = "downloading"
            it.started_at = time.time()
            it.error_msg  = ""
            self.ui(self._update_row_ui, idx)

            # Temp file path (.part)
            temp_path = self._get_temp_path(it)
            it.temp_path = temp_path

            # Resume: existing .part → resume, final file already there → skip
            existing_size = 0
            if os.path.exists(temp_path):
                existing_size = os.path.getsize(temp_path)
            elif os.path.exists(it.dest_path):
                existing_size = os.path.getsize(it.dest_path)

            it.resume_from = existing_size
            it.downloaded  = 0
            headers = {}
            if existing_size > 0:
                headers["Range"] = f"bytes={existing_size}-"

            try:
                with self.req.get(it.url, stream=True, allow_redirects=True,
                                  timeout=60, headers=headers) as r:

                    # Server doesn't support Range → restart from 0
                    if existing_size > 0 and r.status_code == 200:
                        it.resume_from = 0
                        existing_size  = 0
                        try:
                            if os.path.exists(temp_path):
                                os.remove(temp_path)
                        except OSError:
                            pass

                    if r.status_code == 416:
                        # Server doesn't support range requests — restart from scratch
                        print(f"[worker] 416 Range Not Satisfiable — retrying without resume")
                        it.resume_from = 0
                        it.downloaded  = 0
                        try:
                            if os.path.exists(temp_path):
                                os.remove(temp_path)
                        except OSError:
                            pass
                        continue   # retry the loop without range header

                    if r.status_code not in (200, 206):
                        r.raise_for_status()

                    ct = (r.headers.get("Content-Type") or "").lower()
                    if "text/html" in ct:
                        # Business error → no retry
                        it.state     = "error"
                        it.error_msg = "HTML received (expired link / auth required?)"
                        self.ui(self._update_row_ui, idx)
                        self.ui(self._refresh_filter_counts)
                        return

                    # Size depuis les headers du stream
                    cr = r.headers.get("Content-Range")
                    if cr and "/" in cr:
                        try:
                            it.total_size = int(cr.split("/")[-1])
                        except ValueError:
                            pass
                    if it.total_size is None:
                        cl = r.headers.get("Content-Length")
                        if cl and cl.isdigit():
                            it.total_size = int(cl) + existing_size

                    # File déjà complet → skip
                    if it.total_size and existing_size >= it.total_size:
                        it.state = "skipped"
                        self.ui(self._update_row_ui, idx)
                        self.ui(self._refresh_filter_counts)
                        return

                    # Proactive disk space check
                    if it.total_size:
                        temp_dir = self._settings.get("temp_dir", DEFAULT_TEMP_DIR)
                        try:
                            free   = shutil.disk_usage(temp_dir).free
                            needed = it.total_size - existing_size
                            if free < needed:
                                it.state     = "error"
                                it.error_msg = (f"Temp disk full "
                                                f"({self._fmt_size(free)} free, "
                                                f"{self._fmt_size(needed)} required)")
                                self.ui(self._update_row_ui, idx)
                                self.ui(self._refresh_filter_counts)
                                return
                        except OSError:
                            pass

                    os.makedirs(os.path.dirname(it.dest_path), exist_ok=True)

                    # ── Décision multipart ─────────────────────────────
                    n_seg = int(self._settings.get("segments", 4))
                    supports_ranges = (
                        r.headers.get("Accept-Ranges", "").lower() == "bytes"
                        or r.status_code == 206
                    )
                    can_multipart = (
                        n_seg > 1
                        and it.total_size is not None
                        and it.total_size > 0
                        and supports_ranges
                        and existing_size == 0   # no partial resume in multipart mode
                    )
                    if can_multipart:
                        r.close()
                        result = self._download_multipart(idx, n_seg)
                        if result in ("done", "canceled", "paused", "error"):
                            return
                        continue  # "retry" → prochain tour de boucle
                    # ──────────────────────────────────────────────────

                    write_mode = "ab" if existing_size > 0 else "wb"
                    last_ui    = 0.0
                    _canceled  = False

                    try:
                        with open(temp_path, write_mode) as f:
                            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                                if self.stop_all_event.is_set() or it.cancel_event.is_set():
                                    _canceled = True
                                    break
                                if it.pause_event.is_set():
                                    if idx in self._requeue_set:
                                        # Priority requeue — go back to waiting, not paused
                                        self._requeue_set.discard(idx)
                                        it.state = "waiting"
                                    else:
                                        it.state = "paused"
                                    self.ui(self._update_row_ui, idx)
                                    self.ui(self._refresh_filter_counts)
                                    return
                                if not chunk:
                                    continue

                                f.write(chunk)
                                n = len(chunk)
                                it.downloaded += n
                                it.speed_window.append((time.time(), n))
                                self._record_bytes(n)
                                self._throttle_chunk(n)

                                now = time.time()
                                if now - last_ui >= 0.20:
                                    last_ui = now
                                    self.ui(self._update_row_ui, idx)

                    except OSError as e:
                        if "No space left" in str(e) or e.errno == 28:
                            it.state     = "error"
                            it.error_msg = "Temp disk full during download"
                        else:
                            it.state     = "error"
                            it.error_msg = str(e)
                        self.ui(self._update_row_ui, idx)
                        self.ui(self._refresh_filter_counts)
                        return

                    # File .part fermé proprement
                    if _canceled:
                        it.state = "canceled"
                        self.ui(self._update_row_ui, idx)
                        self.ui(self._refresh_filter_counts)
                        try:
                            if os.path.exists(temp_path):
                                os.remove(temp_path)
                        except OSError:
                            pass
                        return

                    # Download complete → move to final destination
                    it.state = "moving"
                    self.ui(self._update_row_ui, idx)
                    self.ui(self._refresh_filter_counts)

                    try:
                        os.makedirs(os.path.dirname(it.dest_path), exist_ok=True)
                        shutil.move(temp_path, it.dest_path)
                    except OSError as e:
                        it.state     = "error"
                        it.error_msg = f"Move error: {e}"
                        self.ui(self._update_row_ui, idx)
                        self.ui(self._refresh_filter_counts)
                        return

                    it.temp_path = ""
                    it.state     = "done"
                    # ── Log historique ─────────────────────────────────
                    duration = time.time() - it.started_at
                    size     = it.total_size or (it.resume_from + it.downloaded)
                    self._history.log_entry(
                        filename=it.filename,
                        url=it.url,
                        size_bytes=size,
                        duration_s=duration,
                    )
                    # ──────────────────────────────────────────────────
                    self.ui(self._update_row_ui, idx)
                    self.ui(self._refresh_filter_counts)
                    return  # ← succès, on sort de la boucle retry

            except Exception as e:
                # Retryable error (network)?
                if self._is_retryable(e) and it.retry_count < retry_max:
                    it.retry_count += 1
                    delay = retry_delay * (2 ** (it.retry_count - 1))  # exponential backoff
                    it.state     = "downloading"
                    it.error_msg = (f"Network error, retrying "
                                    f"{it.retry_count}/{retry_max} in {delay}s…")
                    self.ui(self._update_row_ui, idx)

                    # Wait the delay, checking for cancel every second
                    for _ in range(delay):
                        if self.stop_all_event.is_set() or it.cancel_event.is_set():
                            break
                        time.sleep(1)
                    continue  # → prochain tour de boucle

                # Fatal error or retries exhausted
                if it.retry_count >= retry_max and retry_max > 0:
                    it.error_msg = f"Failed after {it.retry_count} attempt(s): {e}"
                else:
                    it.error_msg = str(e)
                it.state = "error"
                self.ui(self._update_row_ui, idx)
                self.ui(self._refresh_filter_counts)
                return

    # ----------------------------------------------------------------- yt-dlp worker

    def _ytdlp_worker(self, idx: int):
        """Delegates to the ytdlp_worker module."""
        ytdlp_worker.run(idx, self)

    # ----------------------------------------------------------------- Orchestration START / STOP

    SCAN_TIMEOUT = 60  # seconds before interrupting the crawl

    def start_downloads(self):
        if not self.download_path:
            # Fallback — ne devrait pas arriver mais sécurité
            import pathlib
            self.download_path = str(pathlib.Path.home() / "Downloads")

        urls = self._get_urls()
        if not urls:
            return

        try:
            workers = int(self._settings.get("workers", 10))
        except Exception:
            workers = 10
        workers = max(1, min(20, workers))

        self.stop_all_event.clear()
        self._scan_cancel_event.clear()
        keep_tree = self.keep_tree_var.get()

        self.start_btn.configure(state="disabled", text="Scanning…")

        def process_all_urls():
            # ── Phase 1 : probe + crawl toutes les URLs en parallèle ─────────
            all_files: list = []   # (file_url, rel_dir)
            errors:    list = []   # URLs qui ont échoué
            lock = threading.Lock()

            def handle_one(url: str):
                """Probe + éventuel crawl pour une URL — appelé dans un thread."""
                if self.stop_all_event.is_set():
                    return

                path_lower = url.split("?")[0].lower()

                if path_lower.endswith(self._active_extensions):
                    url_type = "file"
                elif url.endswith("/"):
                    url_type = "directory"
                else:
                    url_type = self._probe_url(url)
                    if url_type == "unknown":
                        with lock:
                            errors.append(url)
                        return
                    if url_type == "directory":
                        url = url.rstrip("/") + "/"

                if url_type == "ytdlp":
                    with lock:
                        all_files.append((url, "", "ytdlp"))
                elif url_type == "file":
                    with lock:
                        all_files.append((url, "", "http"))
                else:
                    # Crawl du répertoire
                    found = self.get_all_files(url)
                    with lock:
                        all_files.extend((u, r, "http") for u, r in found)

            # Lancer tous les probes/crawls en parallèle (max 8 threads)
            n = min(len(urls), 8)
            self.ui(lambda: self.title(
                f"TurboDownloader — Scanning {len(urls)} URL{'s' if len(urls)>1 else ''}…"))
            with ThreadPoolExecutor(max_workers=n) as pool:
                list(pool.map(handle_one, urls))

            if self.stop_all_event.is_set():
                self.ui(lambda: (self.start_btn.configure(state="normal", text="▶  Start"),
                                 self.title("TurboDownloader")))
                return

            # ── Séparer yt-dlp et fichiers HTTP ──────────────────────────────
            ytdlp_files = [(u, r, "ytdlp") for u, r, t in all_files if t == "ytdlp"]
            http_files  = [(u, r, "http")  for u, r, t in all_files if t == "http"]

            if not ytdlp_files and not http_files:
                msg = f"No files found ({len(errors)} error(s))" if errors else "No files found"
                self.ui(lambda m=msg: (
                    self.title(f"TurboDownloader — {m}"),
                    self.start_btn.configure(state="normal", text="▶  Start")
                ))
                return

            # Dédupliquer
            seen: set = set()
            unique_http: list = []
            for item in http_files:
                if item[0] not in seen:
                    seen.add(item[0])
                    unique_http.append(item)

            unique_ytdlp: list = []
            for item in ytdlp_files:
                if item[0] not in seen:
                    seen.add(item[0])
                    unique_ytdlp.append(item)

            popup_done = threading.Event()

            def open_popup():
                # ── Mode client : destination = chemin distant configuré ──────
                is_remote_client = (
                    self._remote_client is not None
                    and self._remote_client.connected
                )
                if is_remote_client:
                    default_dest = self._settings.get("remote_client_dest", "") \
                                   or self._settings.get("default_dest", DEFAULT_DEST_DIR)
                else:
                    default_dest = self._settings.get("default_dest", DEFAULT_DEST_DIR)

                http_confirmed  = []
                ytdlp_confirmed = []

                # One event per popup — both must be set before launching
                http_done  = threading.Event()
                ytdlp_done = threading.Event()
                if not unique_http:
                    http_done.set()
                if not unique_ytdlp:
                    ytdlp_done.set()

                def _try_launch():
                    if not http_done.is_set() or not ytdlp_done.is_set():
                        return
                    all_confirmed = http_confirmed + ytdlp_confirmed
                    if not all_confirmed:
                        popup_done.set()
                        return

                    if is_remote_client:
                        # ── Mode client : envoyer chaque URL au serveur distant ──
                        for entry in all_confirmed:
                            url  = entry[0]
                            dest = entry[5] if len(entry) > 5 else default_dest
                            result = self._remote_client.add_url(url, dest or None)
                            if result is None:
                                print(f"[remote-client] Failed to send URL: {url}")
                            else:
                                print(f"[remote-client] Sent to server: {url} → {dest}")
                                # Créer une ligne fantôme locale pour suivre le DL distant
                                self._add_remote_shadow_row(url, dest)
                        popup_done.set()
                    else:
                        # ── Mode local : comportement normal ─────────────────────
                        self._launch_downloads(all_confirmed, workers,
                                               keep_tree=True,
                                               dest_override=default_dest)
                        popup_done.set()

                def on_http_confirm(selected_files, dest_path="", kt=True):
                    # dest_path vient de la popup (peut être modifié à la main)
                    resolved_dest = dest_path or default_dest
                    self._record_dest_history(resolved_dest)
                    http_confirmed.extend(
                        (u, r, "http", None, False, resolved_dest)
                        for u, r, *_ in selected_files
                    )
                    http_done.set()
                    _try_launch()

                def on_ytdlp_confirm(items: list):
                    for item in items:
                        dest = item.get("dest", default_dest)
                        self._record_dest_history(dest)
                        ytdlp_confirmed.append((item["url"], "", "ytdlp",
                                                item["format_id"],
                                                item["audio_only"],
                                                dest))
                    ytdlp_done.set()
                    _try_launch()

                recent_dests = self._settings.get("dest_history", [])

                if unique_http:
                    # FileTreePopup expects (url, rel_dir) tuples — strip the "http" tag
                    http_pairs = [(u, r) for u, r, *_ in unique_http]
                    FileTreePopup(self, http_pairs, on_http_confirm,
                                  default_dest=default_dest, keep_tree=keep_tree,
                                  recent_dests=recent_dests)
                if unique_ytdlp:
                    ytdlp_urls = [u for u, _, __ in unique_ytdlp]
                    YtdlpPopup(self, ytdlp_urls, on_ytdlp_confirm,
                               default_dest=default_dest,
                               recent_dests=recent_dests)

                # Edge case: nothing to show
                if not unique_http and not unique_ytdlp:
                    popup_done.set()

            self.ui(open_popup)
            popup_done.wait()

            self.ui(lambda: (self.start_btn.configure(state="normal", text="▶  Start"),
                             self.title("TurboDownloader")))

        threading.Thread(target=process_all_urls, daemon=True).start()

    def _probe_url(self, url: str) -> str:
        """Probes a URL to determine its nature.
        Returns: 'file' | 'directory' | 'ytdlp' | 'unknown'
        """
        if ytdlp_worker.is_ytdlp_url(url):
            return "ytdlp"
        try:
            r = self.req.head(url, timeout=15, allow_redirects=True)
            if r.status_code == 405:
                r = self.req.get(url, timeout=15, allow_redirects=True, stream=True)
                r.close()
            if not r.ok:
                return "unknown"
            ct = (r.headers.get("Content-Type") or "").lower()
            if "text/html" in ct:
                return "directory"
            return "file"
        except Exception as e:
            print(f"[probe] {e}")
            return "unknown"



    def _launch_downloads(self, files: list, workers: int, keep_tree: bool,
                           dest_override: str = ""):
        """Launches downloads for the selection from the file tree popup.
        Each item in files is (url, rel_dir) or (url, rel_dir, worker_type).
        worker_type is 'http' (default) or 'ytdlp'.

        For large batches (400+ items), rows are created in chunks of 25
        to avoid freezing the UI thread.
        """
        self.stop_all_event.clear()
        ROW_BATCH = 25   # rows created per UI tick

        def _make_item(entry, base) -> tuple:
            """Builds a (idx, DownloadItem) pair from a file entry. Pure data — no UI."""
            file_url   = entry[0]
            rel_dir    = entry[1] if len(entry) > 1 else ""
            wtype      = entry[2] if len(entry) > 2 else "http"
            format_id  = entry[3] if len(entry) > 3 else None
            audio_only = entry[4] if len(entry) > 4 else False
            entry_dest = entry[5] if len(entry) > 5 else ""
            from_remote = entry[6] if len(entry) > 6 else False

            effective_base = entry_dest or base
            name = unquote(os.path.basename(file_url.split("?")[0]) or "file.bin")
            if wtype == "ytdlp":
                from urllib.parse import urlparse
                host = urlparse(file_url).netloc.lstrip("www.")
                suffix = " [MP3]" if audio_only else ""
                name = f"[yt-dlp] {host}{suffix}"

            dest_dir = os.path.join(effective_base, rel_dir) if rel_dir else effective_base
            dest     = os.path.join(dest_dir, name)

            # Reuse existing canceled/errored row for same dest
            existing_idx = next(
                (i for i, it in self.items.items()
                 if it.dest_path == dest
                 and it.state in ("canceled", "error", "skipped")),
                None
            )

            if existing_idx is not None:
                it = self.items[existing_idx]
                it.url          = file_url
                it.downloaded   = 0
                it.resume_from  = 0
                it.total_size   = None
                it.error_msg    = ""
                it.state        = "waiting"
                it.cancel_event = threading.Event()
                it.pause_event  = threading.Event()
                it.temp_path    = ""
                it.retry_count  = 0
                it.segments     = []
                it.speed_window.clear()
                it.worker_type   = wtype
                it.yt_format_id  = format_id   # type: ignore[attr-defined]
                it.yt_audio_only = audio_only  # type: ignore[attr-defined]
                it.from_remote   = from_remote # type: ignore[attr-defined]
                return (existing_idx, it, True)   # True = reuse
            else:
                idx = self._next_idx
                self._next_idx += 1
                it = DownloadItem(url=file_url, filename=name,
                                  dest_path=dest, relative_path=rel_dir)
                it.worker_type   = wtype        # type: ignore[attr-defined]
                it.yt_format_id  = format_id    # type: ignore[attr-defined]
                it.yt_audio_only = audio_only   # type: ignore[attr-defined]
                it.from_remote   = from_remote  # type: ignore[attr-defined]
                self.items[idx]  = it
                return (idx, it, False)          # False = new

        def init_ui_chunked(chunk: list, ready_ev: threading.Event,
                            indices_out: list, base: str):
            """Processes one chunk of entries on the UI thread, then signals ready."""
            for entry in chunk:
                idx, it, reused = _make_item(entry, base)
                if reused:
                    self._update_row_ui(idx)
                else:
                    self._add_row_for_item(idx, it)
                    self._update_row_ui(idx)
                indices_out.append(idx)
            ready_ev.set()

        def _run():
            nonlocal new_indices
            base         = dest_override or self.download_path or DEFAULT_DEST_DIR
            new_indices  = []

            # Split into chunks and submit one per UI tick — no single blocking call
            chunks = [files[i:i+ROW_BATCH] for i in range(0, len(files), ROW_BATCH)]
            # Timeout scales with batch size: 5s base + 0.1s per item
            timeout = max(10, 5 + len(files) * 0.1)

            for chunk in chunks:
                ev = threading.Event()
                self.ui(init_ui_chunked, chunk, ev, new_indices, base)
                if not ev.wait(timeout=timeout):
                    raise RuntimeError("ui_call timeout during row creation")
                # Yield briefly so the UI can repaint between chunks
                time.sleep(0.01)
            self.executor = ThreadPoolExecutor(max_workers=workers)

            batch_set    = set(new_indices)
            _lock        = threading.Lock()
            _remaining   = [len(batch_set)]
            MAX_AUTO_RETRY = 2   # automatic retry attempts for yt-dlp errors

            def _retry_errors(attempt: int):
                """Re-submits failed yt-dlp items with is_retry=True."""
                if self.stop_all_event.is_set():
                    return
                failed = [
                    i for i in batch_set
                    if i in self.items
                    and self.items[i].state == "error"
                    and getattr(self.items[i], "worker_type", "http") == "ytdlp"
                ]
                if not failed:
                    return
                print(f"[retry] attempt {attempt}/{MAX_AUTO_RETRY} — {len(failed)} item(s)")
                with _lock:
                    _remaining[0] += len(failed)
                for i in failed:
                    it = self.items[i]
                    it.state        = "waiting"
                    it.error_msg    = ""
                    it.downloaded   = 0
                    it.cancel_event = threading.Event()
                    it.pause_event  = threading.Event()
                    it.yt_retry     = True   # type: ignore[attr-defined]
                    self.ui(self._update_row_ui, i)
                    self.ui(self._refresh_filter_counts)
                    fut = self.executor.submit(self._ytdlp_worker, i)
                    fut.add_done_callback(_on_future_done)

            def _on_batch_complete():
                """Called once when all current futures are done. Handles retry logic."""
                errors = sum(1 for i in batch_set
                             if i in self.items and self.items[i].state == "error"
                             and getattr(self.items[i], "worker_type", "http") == "ytdlp")

                # Determine how many retries have been done (max retry_count across batch)
                attempt = max(
                    (getattr(self.items[i], "yt_retry_count", 0)
                     for i in batch_set if i in self.items),
                    default=0
                )

                if errors > 0 and attempt < MAX_AUTO_RETRY:
                    # Increment retry counter on all errored items
                    for i in batch_set:
                        if i in self.items and self.items[i].state == "error":
                            cur = getattr(self.items[i], "yt_retry_count", 0)
                            self.items[i].yt_retry_count = cur + 1  # type: ignore
                    time.sleep(2)   # brief pause before retry
                    _retry_errors(attempt + 1)
                    return  # notification deferred to next completion

                # All done (or no more retries) — notify
                if not self._settings.get("notifications", True):
                    return
                def _notify_thread():
                    done     = sum(1 for i in batch_set
                                   if i in self.items and self.items[i].state == "done")
                    errs     = sum(1 for i in batch_set
                                   if i in self.items and self.items[i].state == "error")
                    canceled = sum(1 for i in batch_set
                                   if i in self.items and self.items[i].state in ("canceled", "skipped"))
                    notify_batch_done(done, errs, canceled)
                threading.Thread(target=_notify_thread, daemon=True).start()

            def _on_future_done(f):
                f.exception()
                with _lock:
                    _remaining[0] -= 1
                    if _remaining[0] > 0:
                        return
                threading.Thread(target=_on_batch_complete, daemon=True).start()

            for i in new_indices:
                it = self.items[i]
                wtype = getattr(it, "worker_type", "http")
                if wtype == "ytdlp":
                    fut = self.executor.submit(self._ytdlp_worker, i)
                else:
                    fut = self.executor.submit(self._download_worker, i)
                fut.add_done_callback(_on_future_done)

        new_indices = []
        threading.Thread(target=_run, daemon=True).start()

    def stop_all(self):
        self.stop_all_event.set()
        self._scan_cancel_event.set()   # also interrupts an ongoing crawl
        for it in self.items.values():
            if it.state in ("waiting", "downloading"):
                it.cancel_event.set()
        ex = self.executor
        if ex:
            try:
                ex.shutdown(wait=False, cancel_futures=False)
            except TypeError:
                ex.shutdown(wait=False)

        def mark():
            for idx, it in list(self.items.items()):
                if it.state in ("waiting", "downloading", "moving"):
                    it.state = "canceled"
                    self._update_row_ui(idx)
                    # Remove the main .part file
                    for path in [it.temp_path, it.dest_path]:
                        if path:
                            try:
                                if os.path.exists(path):
                                    os.remove(path)
                            except OSError:
                                pass
                    # Remove multipart .part.N files
                    for seg in it.segments:
                        try:
                            if seg.temp_path and os.path.exists(seg.temp_path):
                                os.remove(seg.temp_path)
                        except OSError:
                            pass
        self.ui(mark)