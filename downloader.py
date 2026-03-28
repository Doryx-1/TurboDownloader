import os
import sys
import time
import json
import queue
import pathlib
import threading
from collections import deque
from typing import Optional
from urllib.parse import unquote
from concurrent.futures import ThreadPoolExecutor
from logger import get_logger

_CONFIG_DIR = pathlib.Path.home() / ".turbodownloader"
_QUEUE_FILE = _CONFIG_DIR / "queue.json"

_log = get_logger("downloader")
import requests
import customtkinter as ctk

from models import DownloadItem
from widgets import DownloadRow
from tree_popup import FileTreePopup
from settings_popup import SettingsPopup, load_settings, DEFAULT_DEST_DIR, DEFAULT_EXTENSIONS
from history import HistoryManager, HistoryPopup
from notifier import notify_batch_done
from taskbar import TaskbarProgress
import ytdlp_worker
from ytdlp_popup import YtdlpPopup
import ffmpeg_setup
import remote_server
import tray as tray_module
import updater as _updater

from speed_tracker import SpeedTrackerMixin
from crawl import CrawlMixin
from row_manager import RowManagerMixin
from remote_tracker import RemoteTrackerMixin
from download_engine import DownloadEngineMixin


def _resource(relative_path: str) -> str:
    """Get absolute path to resource — works for dev and PyInstaller .exe"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


class TurboDownloader(DownloadEngineMixin, RemoteTrackerMixin, RowManagerMixin, CrawlMixin, SpeedTrackerMixin, ctk.CTk):

    def __init__(self):
        super().__init__()

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
        self._items_lock = threading.Lock()         # protects self.items
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

        # Per-host connection semaphores (Feature 10)
        self._host_semaphores: dict = {}
        self._host_sem_lock   = threading.Lock()

        # Settings (temp dir, etc.)
        self._settings = load_settings()
        ctk.set_appearance_mode(self._settings.get("appearance_mode", "dark"))

        # History des téléchargements
        self._history = HistoryManager()

        # Remote control server (started if enabled in settings)
        self._remote_server: remote_server.RemoteServer = None
        self._remote_client = None   # RemoteClient instance when connected as client

        # Shadow rows — tracks downloads running on remote server (client mode)
        self._shadow_rows:      dict = {}
        self._shadow_counter:   int  = 0
        self._shadow_last_order: list = []
        self._start_remote_if_enabled()

        self._build_ui()
        self.after(100, self._restore_queue)

        # ── Tray icon ─────────────────────────────────────────────────────────
        self._tray = tray_module.TrayIcon(self)
        self._tray.start()

        # Close button → minimize to tray instead of quitting
        self.protocol("WM_DELETE_WINDOW", self._on_close_btn)

        # Start minimized if launched with --minimized flag (startup with Windows)
        if "--minimized" in sys.argv:
            self.after(100, self.withdraw)

        # Windows taskbar — initialized after _build_ui (needs HWND)
        # Slight delay to let Tk create the window first
        self._taskbar: TaskbarProgress = None
        self.after(500, self._init_taskbar)
        self.after(1000, self._check_dependencies)
        self.after(3000, self._check_for_updates_startup)
        self.after(600, self._update_remote_status_bar)   # refresh badge after UI ready

        self.after(80, self._process_ui_queue)
        self.after(1000, self._tick_global_speed)

        # Clipboard monitor
        self._clipboard_last_hash = None
        self._clipboard_banner    = None
        self._clipboard_thread    = None
        self._apply_clipboard_monitor()

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
    # (RemoteTrackerMixin)

    # ── Tray / window lifecycle ───────────────────────────────────────────────

    def _on_close_btn(self):
        """X button — minimize to tray or quit depending on settings."""
        if self._settings.get("minimize_to_tray", True):
            self.withdraw()
        else:
            self._tray_quit()

    def _tray_restore(self):
        """Restore window from tray."""
        self.deiconify()
        self.lift()
        self.focus_force()

    def _tray_quit(self):
        """Quit completely — save queue, stop server, cleanup, destroy."""
        self._save_queue()   # save before stopping workers
        try:
            if self._remote_server:
                self._remote_server.stop()
            if self._remote_client:
                self._remote_client.disconnect()
            self.stop_all_event.set()
            for it in self.items.values():
                it.cancel_event.set()   # stop workers cleanly without deleting .part files
            if self.executor:
                try:
                    self.executor.shutdown(wait=False, cancel_futures=False)
                except TypeError:
                    self.executor.shutdown(wait=False)
            self._tray.stop()
        except Exception:
            pass
        self.destroy()

    def _save_queue(self):
        """Persists waiting/downloading items to disk before quit."""
        saveable = [
            it.to_dict()
            for it in self.items.values()
            if it.state in ("waiting", "paused", "downloading")
            and not it.from_remote
        ]
        try:
            _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            if saveable:
                with open(_QUEUE_FILE, "w", encoding="utf-8") as f:
                    json.dump(saveable, f, indent=2, ensure_ascii=False)
            elif _QUEUE_FILE.exists():
                _QUEUE_FILE.unlink()
        except Exception as e:
            print(f"[queue] save error: {e}")

    def _restore_queue(self):
        """Reloads the saved queue after startup."""
        if not _QUEUE_FILE.exists():
            return
        try:
            data = json.loads(_QUEUE_FILE.read_text(encoding="utf-8"))
            count = 0
            for d in data:
                it = DownloadItem.from_dict(d)
                if not os.path.isdir(os.path.dirname(it.dest_path)):
                    continue
                idx = self._next_idx
                self._next_idx += 1
                with self._items_lock:
                    self.items[idx] = it
                self._add_row_for_item(idx, it)
                count += 1
            if count:
                self.after(300, lambda n=count: self._show_restore_banner(n))
        except Exception as e:
            print(f"[queue] restore error: {e}")
        finally:
            try:
                _QUEUE_FILE.unlink(missing_ok=True)
            except Exception:
                pass

    def _show_restore_banner(self, count: int):
        """Shows a brief info banner when downloads are restored from queue."""
        import customtkinter as _ctk
        banner = _ctk.CTkFrame(
            self, fg_color=("#d8e8f8", "#1a2a3a"),
            corner_radius=0, border_width=1,
            border_color=("#4a8aaa", "#1f4a6a"))
        banner.place(relx=0, rely=0, relwidth=1, anchor="nw")
        _ctk.CTkLabel(
            banner,
            text=f"↺  {count} download(s) restored from previous session — click Start to resume",
            font=_ctk.CTkFont(size=11),
            text_color=("#1a4a6a", "#7ab4d4"),
        ).pack(side="left", padx=12, pady=6)
        _ctk.CTkButton(
            banner, text="✕", width=24, height=24,
            fg_color="transparent", border_width=0,
            text_color=("#1a4a6a", "#7ab4d4"),
            command=banner.destroy,
        ).pack(side="right", padx=8)
        self.after(8000, lambda: banner.destroy() if banner.winfo_exists() else None)

    def _show_for_popup(self):
        """
        Called when a URL arrives via the extension while TD is minimized.
        Brings only the popup to front without restoring the full main window.
        The popup will appear on top of whatever is on screen.
        """
        # Just make sure the root window exists so popups can attach to it
        # Don't deiconify — keep main window hidden if it was hidden
        self.lift()

    def _disconnect_remote(self):
        """Legacy — disconnect whichever is active (used by old code paths)."""
        self._disconnect_client()
        self._disconnect_server()

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

    # ─────────────────────────────────────────── Clipboard monitor

    def _apply_clipboard_monitor(self):
        """Start or stop the clipboard polling thread based on settings."""
        enabled = self._settings.get("clipboard_monitor", False)
        running = (self._clipboard_thread is not None
                   and self._clipboard_thread.is_alive())
        if enabled and not running:
            self._clipboard_stop = threading.Event()
            self._clipboard_thread = threading.Thread(
                target=self._clipboard_poll_loop, daemon=True, name="ClipboardMonitor")
            self._clipboard_thread.start()
        elif not enabled and running:
            self._clipboard_stop.set()
            self._clipboard_thread = None

    def _clipboard_poll_loop(self):
        import hashlib
        import re
        FILE_RE = re.compile(
            r'https?://\S+\.(' + '|'.join(
                e.lstrip('.') for e in self._active_extensions
            ) + r')(\?[^\s]*)?$', re.IGNORECASE)

        while not self._clipboard_stop.is_set():
            self._clipboard_stop.wait(1.5)
            if self._clipboard_stop.is_set():
                break
            try:
                text = self.clipboard_get().strip()
            except Exception:
                continue
            h = hashlib.md5(text.encode()).hexdigest()
            if h == self._clipboard_last_hash:
                continue
            self._clipboard_last_hash = h
            if not FILE_RE.match(text):
                continue
            # Don't suggest if the URL is already in the input box
            try:
                current = self.url_box.get("1.0", "end").strip()
                if text in current:
                    continue
            except Exception:
                pass
            self.ui(self._show_clipboard_banner, text)

    def _show_clipboard_banner(self, url: str):
        """Show a non-blocking banner suggesting to add clipboard URL."""
        if self._clipboard_banner is not None:
            try:
                self._clipboard_banner.destroy()
            except Exception:
                pass
        banner = ctk.CTkFrame(self._scroll_container, fg_color=("gray85", "#1a2a3a"),
                              corner_radius=6, border_width=1, border_color="#2a5a8a")
        banner.place(relx=0.5, rely=0.0, anchor="n", relwidth=0.96, y=6)
        self._clipboard_banner = banner

        inner = ctk.CTkFrame(banner, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=6)
        ctk.CTkLabel(inner, text="📋 Clipboard URL detected:",
                     font=ctk.CTkFont(size=11), text_color=("#1a4a8a", "#7ab4d4")).pack(side="left")
        ctk.CTkLabel(inner, text=url[:60] + ("…" if len(url) > 60 else ""),
                     font=ctk.CTkFont(size=10), text_color=("gray40", "#aaaaaa")).pack(side="left", padx=(6, 0))
        ctk.CTkButton(inner, text="Add to queue", width=110, height=24,
                      fg_color="#1f6aa5", hover_color="#1a5a8f",
                      font=ctk.CTkFont(size=11),
                      command=lambda: self._clipboard_add(url)).pack(side="right")
        ctk.CTkButton(inner, text="✕", width=24, height=24,
                      fg_color="transparent", hover_color="#3a3a3a",
                      command=self._dismiss_clipboard_banner).pack(side="right", padx=(0, 4))
        # Auto-dismiss after 5 seconds
        self.after(5000, self._dismiss_clipboard_banner)

    def _dismiss_clipboard_banner(self):
        if self._clipboard_banner is not None:
            try:
                self._clipboard_banner.destroy()
            except Exception:
                pass
            self._clipboard_banner = None

    def _clipboard_add(self, url: str):
        """Add clipboard URL to the input box and dismiss the banner."""
        self._dismiss_clipboard_banner()
        try:
            current = self.url_box.get("1.0", "end").strip()
            self.url_box.delete("1.0", "end")
            self.url_box.insert("1.0", (current + "\n" + url).strip())
        except Exception:
            pass

    def _check_for_updates_startup(self):
        """Checks for updates silently at startup — popup only if newer version found."""
        if not self._settings.get("check_updates", True):
            return
        try:
            _updater.check_for_updates(self, silent=True)
        except Exception as e:
            _log.debug("Update check error: %s", e)

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
        sidebar = ctk.CTkFrame(root, width=300, corner_radius=0, fg_color=("gray92", "#1a1a1a"))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # Logo / titre
        title_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        title_frame.pack(fill="x", padx=16, pady=(20, 4))
        ctk.CTkLabel(title_frame, text="⬇  TurboDownloader",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(title_frame, text=f"v{_updater.APP_VERSION}", text_color="#555555",
                     font=ctk.CTkFont(size=11)).pack(anchor="w")

        # Séparateur
        ctk.CTkFrame(sidebar, height=1, fg_color=("gray75", "#2a2a2a")).pack(fill="x", padx=0, pady=(8, 12))

        # ── URL box ───────────────────────────────────────────────────────────
        ctk.CTkLabel(sidebar, text="URLs", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#888888").pack(anchor="w", padx=16, pady=(0, 4))
        self.url_box = ctk.CTkTextbox(sidebar, height=100, wrap="none",
                                      activate_scrollbars=True,
                                      border_width=1,
                                      border_color=("gray70", "#333333"))
        self.url_box.pack(fill="x", padx=16, pady=(0, 4))
        ctk.CTkLabel(sidebar, text="One URL per line — spaces also work",
                     text_color="#555555", font=ctk.CTkFont(size=10)).pack(
                         anchor="w", padx=16, pady=(0, 12))

        # ── Ctrl+V global : inject URLs from clipboard if focus ≠ url_box ───
        self.bind("<Control-v>", self._on_global_paste)

        # ── Options ───────────────────────────────────────────────────────────
        ctk.CTkFrame(sidebar, height=1, fg_color=("gray75", "#2a2a2a")).pack(fill="x", padx=0, pady=(0, 10))

        # keep_tree_var — géré dans la popup FileTreePopup directement
        self.keep_tree_var = ctk.BooleanVar(value=True)  # valeur initiale pour la popup

        # Workers — lus depuis les settings

        # ── Boutons START / STOP ──────────────────────────────────────────────
        ctk.CTkFrame(sidebar, height=1, fg_color=("gray75", "#2a2a2a")).pack(fill="x", padx=0, pady=(0, 12))

        self.start_btn = ctk.CTkButton(
            sidebar, text="▶  Start", height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#1f6aa5", hover_color="#1a5a8f",
            command=self.start_downloads)
        self.start_btn.pack(fill="x", padx=16, pady=(0, 6))

        self.pause_all_btn = ctk.CTkButton(
            sidebar, text="⏸  Pause all", height=34,
            fg_color=("gray80", "#1a1a2e"), hover_color=("gray70", "#2a2a4e"),
            text_color=("#1a1a6a", "#9999cc"),
            border_width=1, border_color="#4a4a8a",
            command=self._toggle_pause_all)
        self.pause_all_btn.pack(fill="x", padx=16, pady=(0, 6))

        self.stop_btn = ctk.CTkButton(
            sidebar, text="⏹  Stop all", height=34,
            fg_color=("gray80", "#3a1010"), hover_color=("gray70", "#5a1515"),
            text_color=("#6a0000", "#cc8888"),
            border_width=1, border_color="#8B0000",
            command=self.stop_all)
        self.stop_btn.pack(fill="x", padx=16, pady=(0, 12))

        # ── Stats globales ────────────────────────────────────────────────────
        ctk.CTkFrame(sidebar, height=1, fg_color=("gray75", "#2a2a2a")).pack(fill="x", padx=0, pady=(0, 10))

        self.global_speed_label = ctk.CTkLabel(
            sidebar, text="–", anchor="w",
            font=ctk.CTkFont(size=18, weight="bold"))
        self.global_speed_label.pack(anchor="w", padx=16, pady=(4, 0))

        self.global_dl_label = ctk.CTkLabel(
            sidebar, text="Total: 0 B", anchor="w",
            text_color="#555555", font=ctk.CTkFont(size=11))
        self.global_dl_label.pack(anchor="w", padx=16, pady=(0, 2))

        self.queue_eta_label = ctk.CTkLabel(
            sidebar, text="", anchor="w",
            text_color="#555555", font=ctk.CTkFont(size=11))
        self.queue_eta_label.pack(anchor="w", padx=16, pady=(0, 10))

        # ── Remote status bars ────────────────────────────────────────────────
        # Deux barres distinctes : une pour le serveur, une pour le client.
        # Chacune est indépendante — on peut être serveur ET client en même temps.

        # Barre serveur (verte)
        self._remote_srv_bar = ctk.CTkFrame(sidebar, fg_color=("gray90", "#0d1f0d"),
                                            corner_radius=6, border_width=1,
                                            border_color=("#1a6a1a", "#1a3a1a"))
        self._remote_srv_lbl = ctk.CTkLabel(
            self._remote_srv_bar, text="", anchor="w",
            font=ctk.CTkFont(size=10), text_color=("#1a6a1a", "#7aaa7a"))
        self._remote_srv_lbl.pack(side="left", fill="x", expand=True, padx=10, pady=5)
        ctk.CTkButton(
            self._remote_srv_bar, text="✕", width=26, height=20,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", hover_color="#3a1a1a",
            text_color="#cc7777", border_width=0,
            command=self._disconnect_server).pack(side="right", padx=(0, 6), pady=4)

        # Barre client (bleue)
        self._remote_cli_bar = ctk.CTkFrame(sidebar, fg_color=("gray90", "#0d0d1f"),
                                            corner_radius=6, border_width=1,
                                            border_color=("#1a1a8a", "#1a1a3a"))
        self._remote_cli_lbl = ctk.CTkLabel(
            self._remote_cli_bar, text="", anchor="w",
            font=ctk.CTkFont(size=10), text_color=("#1a1a8a", "#7aaadd"))
        self._remote_cli_lbl.pack(side="left", fill="x", expand=True, padx=10, pady=5)
        ctk.CTkButton(
            self._remote_cli_bar, text="✕", width=26, height=20,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", hover_color=("gray80", "#1a1a3a"),
            text_color=("#1a1a8a", "#7aaadd"), border_width=0,
            command=self._disconnect_client).pack(side="right", padx=(0, 6), pady=4)
        # Démarrées masquées — affichées par _update_remote_status_bar()

        # ── Bas sidebar : Clear + Settings + History + Remote ────────────────

        bot_btns = ctk.CTkFrame(sidebar, fg_color="transparent")
        bot_btns.pack(side="bottom", fill="x", padx=16, pady=(0, 4))
        ctk.CTkButton(bot_btns, text="⚙ Settings", height=30,
                      fg_color="transparent", border_width=1, border_color=("gray65", "#333333"),
                      text_color=("gray10", "#dddddd"), font=ctk.CTkFont(size=12),
                      command=self._open_settings).pack(side="left", expand=True,
                                                        fill="x", padx=(0, 4))
        ctk.CTkButton(bot_btns, text="🕐 History", height=30,
                      fg_color="transparent", border_width=1, border_color=("gray65", "#333333"),
                      text_color=("gray10", "#dddddd"), font=ctk.CTkFont(size=12),
                      command=self._open_history).pack(side="left", expand=True,
                                                       fill="x", padx=(4, 4))
        ctk.CTkButton(bot_btns, text="📡 Remote", height=30,
                      fg_color="transparent", border_width=1, border_color=("gray65", "#333333"),
                      text_color=("gray10", "#dddddd"), font=ctk.CTkFont(size=12),
                      command=self._open_remote_control).pack(side="left", expand=True,
                                                              fill="x", padx=(0, 0))

        # Clear remote button — visible only in client mode
        bot_btns2 = ctk.CTkFrame(sidebar, fg_color="transparent")
        bot_btns2.pack(side="bottom", fill="x", padx=16, pady=(0, 2))
        self._clear_remote_btn = ctk.CTkButton(
            bot_btns2, text="🗑 Clear remote done", height=26,
            fg_color="transparent", border_width=1, border_color="#3a1a1a",
            text_color="#cc7777", hover_color="#2a1010",
            font=ctk.CTkFont(size=11),
            command=self._remote_clear_done)
        self._clear_remote_btn.pack(fill="x")
        self._clear_remote_btn.pack_forget()   # hidden until client connects

        ctk.CTkLabel(sidebar, text="© Doryx-1",
                     font=ctk.CTkFont(size=10), text_color="gray").pack(
                         side="bottom", anchor="w", padx=16, pady=(0, 6))

        # ── Zone principale droite ────────────────────────────────────────────
        main_area = ctk.CTkFrame(root, fg_color=("gray95", "#111111"), corner_radius=0)
        main_area.pack(side="right", fill="both", expand=True)

        # Barre du haut : titre + filtres
        top_bar = ctk.CTkFrame(main_area, fg_color=("gray90", "#1a1a1a"), corner_radius=0, height=50)
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

        # ── Sort dropdown ──────────────────────────────────────────────────────
        self._sort_var = ctk.StringVar(value="Recent")
        ctk.CTkOptionMenu(
            top_bar,
            variable=self._sort_var,
            values=["Recent", "Name A→Z", "Name Z→A", "Status", "Size ↓"],
            width=130, height=28,
            font=ctk.CTkFont(size=11),
            fg_color=("gray85", "#2a2a2a"),
            button_color=("gray75", "#333333"),
            text_color=("gray10", "#dddddd"),
            command=self._apply_sort_order,
        ).pack(side="left", padx=(4, 0), pady=10)

        # Bouton Retry errors — visible uniquement si items en erreur
        self._retry_errors_btn = ctk.CTkButton(
            top_bar, text="↺  Retry errors", width=110, height=28,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color="#8B0000",
            hover_color=("#3a0000", "#3a0000"),
            text_color=("#8B0000", "#cc4444"),
            command=self._retry_all_failed)
        # Not packed yet — shown dynamically in _refresh_filter_counts

        # Bouton Clear finished — bas droite du header
        ctk.CTkButton(top_bar, text="🗑 Clear", width=80, height=28,
                      font=ctk.CTkFont(size=11),
                      fg_color="transparent", border_width=1, border_color=("gray65", "#333333"),
                      hover_color=("gray80", "#2a2a2a"),
                      text_color=("gray10", "#dddddd"),
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
                text_color="white" if fkey == "all" else ("gray10", "#dddddd"),
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
                     font=ctk.CTkFont(size=48), text_color=("gray60", "#555555")).pack()
        ctk.CTkLabel(self._empty_frame, text="No downloads yet",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=("gray40", "#aaaaaa")).pack(pady=(4, 2))
        ctk.CTkLabel(self._empty_frame, text="Paste a URL and hit Start",
                     text_color=("gray50", "#888888"), font=ctk.CTkFont(size=12)).pack()

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
        """
        Drains the UI queue on the main thread.

        Hot deduplication: _update_row_ui(idx) and _refresh_filter_counts()
        are collapsed — only the last queued call per idx survives each cycle.
        This prevents queue bloat when workers emit many progress events per second.
        """
        try:
            # Drain everything available right now into a local list
            pending = []
            try:
                while True:
                    pending.append(self.uiq.get_nowait())
            except queue.Empty:
                pass

            if not pending:
                return

            # ── Deduplicate hot calls ────────────────────────────────────────
            # Keep last _update_row_ui per idx, keep last _refresh_filter_counts
            deduped   = []
            row_seen  : dict = {}   # idx → position in deduped
            filter_pos: int  = -1

            for item in pending:
                fn, args, kwargs = item
                if fn is self._update_row_ui and args:
                    idx = args[0]
                    if idx in row_seen:
                        deduped[row_seen[idx]] = None   # invalidate previous
                    row_seen[idx] = len(deduped)
                    deduped.append(item)
                elif fn is self._refresh_filter_counts:
                    if filter_pos >= 0:
                        deduped[filter_pos] = None      # invalidate previous
                    filter_pos = len(deduped)
                    deduped.append(item)
                else:
                    deduped.append(item)

            # ── Execute surviving calls ──────────────────────────────────────
            for item in deduped:
                if item is None:
                    continue
                fn, args, kwargs = item
                try:
                    fn(*args, **kwargs)
                except Exception as e:
                    print("[UIQ]", type(e).__name__, e)

        finally:
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

    def _get_host_sem(self, url: str) -> threading.Semaphore:
        """Returns (or creates) the per-host semaphore for the given URL."""
        from urllib.parse import urlparse
        host  = urlparse(url).netloc.lower()
        limit = max(1, int(self._settings.get("max_per_host", 3)))
        with self._host_sem_lock:
            if host not in self._host_semaphores:
                self._host_semaphores[host] = threading.Semaphore(limit)
            return self._host_semaphores[host]

    def _reset_host_semaphores(self):
        """Clears all per-host semaphores (called after settings change)."""
        with self._host_sem_lock:
            self._host_semaphores.clear()

    def _fire_webhook(self, payload: dict):
        """POSTs payload as JSON to the configured webhook URL (non-blocking)."""
        url = self._settings.get("webhook_url", "").strip()
        if not url or not self._settings.get("webhook_enabled", False):
            return
        try:
            import urllib.request
            import json as _json
            data = _json.dumps(payload).encode()
            req  = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"},
                method="POST")
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            from logger import get_logger
            get_logger("webhook").warning("Webhook failed: %s", e)

    def _retry_all_failed(self):
        urls = [it.url for it in self.items.values() if it.state == "error"]
        if not urls:
            return
        self.url_box.delete("1.0", "end")
        self.url_box.insert("1.0", "\n".join(urls))
        self.start_downloads()

    def _open_settings(self):
        def on_save():
            # Update the existing dict in place (no reference reassignment)
            self._settings.update(load_settings())
            # Apply remote server changes (start/stop as needed)
            self._apply_remote_settings()
            # Apply clipboard monitor toggle
            self._apply_clipboard_monitor()
            # Reset per-host semaphores so the new limit takes effect immediately
            self._reset_host_semaphores()
        SettingsPopup(self, self._settings, on_save)

    def _open_history(self):
        def on_redownload(url: str):
            self.url_box.delete("1.0", "end")
            self.url_box.insert("1.0", url)
            self.start_downloads()
        HistoryPopup(self, self._history, on_redownload)

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
            "moving":      "Converting…" if it.worker_type == "ytdlp" else "Moving…",
            "done":        "Done",
            "error":       f"Erreur: {it.error_msg[:40]}",
            "canceled":    "Canceled",
            "skipped":     "Already exists (skipped)",
            "conflict":    "⚠ File exists — client deciding…",
        }
        status_text = state_labels.get(it.state, it.state)
        # Badge Remote si le DL vient d'un client distant
        if it.from_remote:
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
            row.pause_btn.configure(state="normal", text="⏸", fg_color="transparent", border_color=("gray60", "#3a3a3a"), text_color=("gray15", "#dddddd"))
            row.cancel_btn.configure(state="normal")
            row.remove_btn.configure(state="disabled")
        elif it.state == "moving":
            row.pause_btn.configure(state="disabled", text="⏸", fg_color="transparent")
            row.cancel_btn.configure(state="disabled")
            row.remove_btn.configure(state="disabled")
        # ── Rafraîchir la progression du groupe playlist ─────────────────────
        gid = getattr(it, "playlist_group_id", None)
        if gid and hasattr(self, "_playlist_groups") and gid in self._playlist_groups:
            total = getattr(it, "playlist_total", 0)
            done  = sum(1 for i2, it2 in self.items.items()
                        if getattr(it2, "playlist_group_id", None) == gid
                        and it2.state in ("done", "error", "canceled", "skipped"))
            self._playlist_groups[gid].update_progress(done, total)
        # ────────────────────────────────────────────────────────────────────

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
            active = (k == fkey)
            btn.configure(
                fg_color=colors[k] if active else "transparent",
                text_color="white" if active else ("gray10", "#dddddd"),
            )
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
        total = len(active_items) + len(self._shadow_rows)
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

        # Bouton Retry errors — affiché seulement si des items sont en erreur
        err_count = counts.get("error", 0)
        if err_count > 0:
            self._retry_errors_btn.configure(
                text=f"↺  Retry errors ({err_count})")
            self._retry_errors_btn.pack(side="right", padx=(0, 6), pady=10)
        else:
            self._retry_errors_btn.pack_forget()

        # Compteur header
        self._total_count_lbl.configure(
            text=f"{total} item{'s' if total != 1 else ''}" if total else "")

        # Empty state : visible seulement si aucun item
        if total == 0:
            self._empty_frame.lift()
            self.scroll.lower()
        else:
            self._empty_frame.lower()
            self.scroll.lift()

    # ----------------------------------------------------------------- Orchestration START / STOP

    SCAN_TIMEOUT = 60  # seconds before interrupting the crawl

    def start_downloads(self):
        # Guard against concurrent calls (e.g. rapid extension/protocol triggers at startup).
        # The button is disabled while a scan+popup is in progress; skip silently if so.
        # The URL(s) already injected into url_box remain visible for a manual retry.
        if str(self.start_btn.cget("state")) == "disabled":
            return

        # If window is hidden (tray), bring popups to front without restoring main window
        if not self.winfo_viewable():
            self._show_for_popup()

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
                local_urls: set = set()  # URLs to download locally (bypassing server)

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
                        # ── Mode client : séparer les téléchargements locaux et distants ──
                        remote_entries = [e for e in all_confirmed if e[0] not in local_urls]
                        local_entries  = [e for e in all_confirmed if e[0] in local_urls]

                        # Télécharger localement les URLs choisies via "This PC"
                        if local_entries:
                            self._launch_downloads(local_entries, workers,
                                                   keep_tree=True)

                        # Envoyer au serveur les URLs distantes
                        remote_dest_setting = self._settings.get("remote_client_dest", "").strip() or None
                        for entry in remote_entries:
                            url        = entry[0]
                            wtype      = entry[2] if len(entry) > 2 else "http"
                            format_id  = entry[3] if len(entry) > 3 else None
                            audio_only = entry[4] if len(entry) > 4 else False
                            entry_dest = entry[5] if len(entry) > 5 else ""
                            # Priorité : dest choisi dans la popup > remote_client_dest des settings
                            remote_dest = (entry_dest.strip() or remote_dest_setting) or None
                            result = self._remote_client.add_url(
                                url, remote_dest,
                                worker_type=wtype,
                                format_id=format_id,
                                audio_only=bool(audio_only),
                            )
                            if result is None:
                                _log.warning("Failed to send URL: %s", url[:80])
                            else:
                                _log.debug("Sent to server: %s", url[:80])
                                self._add_remote_shadow_row(url, remote_dest or "")
                        popup_done.set()
                    else:
                        # ── Mode local : comportement normal ─────────────────────
                        self._launch_downloads(all_confirmed, workers,
                                               keep_tree=True,
                                               dest_override=default_dest)
                        popup_done.set()

                def on_http_confirm(selected_files, dest_path="", kt=True, is_local=False):
                    # dest_path vient de la popup (peut être modifié à la main)
                    resolved_dest = dest_path or default_dest
                    self._record_dest_history(resolved_dest)
                    if is_local:
                        for u, r, *_ in selected_files:
                            local_urls.add(u)
                    http_confirmed.extend(
                        (u, r, "http", None, False, resolved_dest)
                        for u, r, *_ in selected_files
                    )
                    http_done.set()
                    _try_launch()

                def on_ytdlp_confirm(items: list):
                    import uuid as _uuid
                    # Build group_id per playlist_url
                    group_ids: dict = {}
                    for item in items:
                        purl = item.get("playlist_url")
                        if purl and item.get("playlist_total", 0) > 1:
                            if purl not in group_ids:
                                group_ids[purl] = _uuid.uuid4().hex[:8]
                    for item in items:
                        dest = item.get("dest", default_dest)
                        self._record_dest_history(dest)
                        if item.get("is_local", False):
                            local_urls.add(item["url"])
                        purl  = item.get("playlist_url")
                        gid   = group_ids.get(purl) if purl else None
                        ytdlp_confirmed.append((
                            item["url"], "", "ytdlp",
                            item["format_id"],
                            item["audio_only"],
                            dest,
                            False,                           # from_remote
                            gid,                             # playlist_group_id
                            item.get("playlist_title", ""),  # playlist_group_title
                            item.get("playlist_index", 0),   # playlist_index
                            item.get("playlist_total", 0),   # playlist_total
                        ))
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
            if not popup_done.wait(timeout=300):   # 5 min safety net
                _log.warning("popup_done timed out — restoring start button")
            self.ui(lambda: (self.start_btn.configure(state="normal", text="▶  Start"),
                             self.title("TurboDownloader")))

        def _safe_process_all_urls():
            try:
                process_all_urls()
            except Exception:
                _log.exception("Unexpected error in process_all_urls")
                self.ui(lambda: (self.start_btn.configure(state="normal", text="▶  Start"),
                                 self.title("TurboDownloader")))

        threading.Thread(target=_safe_process_all_urls, daemon=True).start()

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
            file_url    = entry[0]
            rel_dir     = entry[1] if len(entry) > 1 else ""
            wtype       = entry[2] if len(entry) > 2 else "http"
            format_id   = entry[3] if len(entry) > 3 else None
            audio_only  = entry[4] if len(entry) > 4 else False
            entry_dest  = entry[5] if len(entry) > 5 else ""
            from_remote = entry[6] if len(entry) > 6 else False
            group_id    = entry[7] if len(entry) > 7 else None
            group_title = entry[8] if len(entry) > 8 else ""
            group_index = entry[9] if len(entry) > 9 else 0
            group_total = entry[10] if len(entry) > 10 else 0

            effective_base = entry_dest or base
            _decoded_url = unquote(file_url.split("?")[0])
            name = os.path.basename(_decoded_url) or "file.bin"
            # Strip path-traversal and OS-illegal characters from the filename
            import re as _re
            name = _re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name) or "file.bin"
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
                it.worker_type          = wtype
                it.yt_format_id         = format_id
                it.yt_audio_only        = audio_only  # type: ignore[attr-defined]
                it.from_remote          = from_remote # type: ignore[attr-defined]
                it.playlist_group_id    = group_id
                it.playlist_group_title = group_title
                it.playlist_index       = group_index
                it.playlist_total       = group_total
                return (existing_idx, it, True)   # True = reuse
            else:
                with self._items_lock:
                    idx = self._next_idx
                    self._next_idx += 1
                it = DownloadItem(url=file_url, filename=name,
                                  dest_path=dest, relative_path=rel_dir)
                it.worker_type          = wtype
                it.yt_format_id         = format_id
                it.yt_audio_only        = audio_only
                it.from_remote          = from_remote  # type: ignore[attr-defined]
                it.playlist_group_id    = group_id
                it.playlist_group_title = group_title
                it.playlist_index       = group_index
                it.playlist_total       = group_total
                with self._items_lock:
                    self.items[idx] = it
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
            self._refresh_filter_counts()
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
            # Shutdown previous executor before creating a new one (fix resource leak)
            if self.executor is not None:
                self.executor.shutdown(wait=False, cancel_futures=False)
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
                    it.yt_retry     = True
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
                    # Group items by playlist — send one notif per playlist
                    # and one global notif for non-playlist items
                    groups_seen: set = set()
                    standalone_done = standalone_errs = standalone_canceled = 0
                    for i in batch_set:
                        if i not in self.items:
                            continue
                        it2  = self.items[i]
                        gid2 = getattr(it2, "playlist_group_id", None)
                        if gid2:
                            if gid2 not in groups_seen:
                                groups_seen.add(gid2)
                                members = [j for j in batch_set
                                           if j in self.items
                                           and getattr(self.items[j], "playlist_group_id", None) == gid2]
                                p_done  = sum(1 for j in members if self.items[j].state == "done")
                                p_errs  = sum(1 for j in members if self.items[j].state == "error")
                                p_canc  = sum(1 for j in members if self.items[j].state in ("canceled","skipped"))
                                title   = getattr(it2, "playlist_group_title", "") or "Playlist"
                                from notifier import notify
                                msg = f"{p_done} downloaded"
                                if p_errs:   msg += f", {p_errs} errors"
                                if p_canc:   msg += f", {p_canc} skipped"
                                notify(f"Playlist done — {title[:40]}", msg)
                        else:
                            if it2.state == "done":     standalone_done += 1
                            elif it2.state == "error":  standalone_errs += 1
                            elif it2.state in ("canceled","skipped"): standalone_canceled += 1
                    if standalone_done + standalone_errs + standalone_canceled > 0:
                        notify_batch_done(standalone_done, standalone_errs, standalone_canceled)

                # ── Batch webhook ──────────────────────────────────────────────
                if (self._settings.get("webhook_enabled") and
                        self._settings.get("webhook_on") == "batch"):
                    done_items = [
                        it3 for it3 in (self.items.get(i) for i in batch_set) if it3
                    ]
                    n_done    = sum(1 for it3 in done_items if it3.state == "done")
                    n_errors  = sum(1 for it3 in done_items if it3.state == "error")
                    n_skipped = sum(1 for it3 in done_items
                                    if it3.state in ("canceled", "skipped"))
                    files = [
                        {"name": it3.filename, "url": it3.url,
                         "size": it3.total_size or 0, "dest": it3.dest_path}
                        for it3 in done_items if it3.state == "done"
                    ]
                    threading.Thread(
                        target=self._fire_webhook,
                        args=({"event": "batch_done", "done": n_done,
                               "errors": n_errors, "canceled": n_skipped,
                               "files": files},),
                        daemon=True,
                    ).start()

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
                wtype = it.worker_type
                if wtype == "ytdlp":
                    fut = self.executor.submit(self._ytdlp_worker, i)
                else:
                    fut = self.executor.submit(self._download_worker, i)
                fut.add_done_callback(_on_future_done)

        new_indices = []
        threading.Thread(target=_run, daemon=True).start()

    def _toggle_pause_all(self):
        """Pause all downloading items, or resume all paused items."""
        downloading = [idx for idx, it in self.items.items() if it.state == "downloading"]
        paused      = [idx for idx, it in self.items.items() if it.state == "paused"]
        if downloading:
            for idx in downloading:
                self.pause_one(idx)
            self.pause_all_btn.configure(text="▶  Resume all")
        elif paused:
            for idx in paused:
                self.pause_one(idx)
            self.pause_all_btn.configure(text="⏸  Pause all")

    def stop_all(self):
        self.stop_all_event.set()
        self._scan_cancel_event.set()   # also interrupts an ongoing crawl
        for it in self.items.values():
            if it.state in ("waiting", "downloading"):
                it.cancel_event.set()
        ex = self.executor
        self.executor = None   # prevent reuse after stop
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