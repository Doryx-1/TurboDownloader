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
from settings_popup import SettingsPopup, load_settings, DEFAULT_TEMP_DIR, DEFAULT_EXTENSIONS
from history import HistoryManager, HistoryPopup
from notifier import notify_batch_done
from taskbar import TaskbarProgress


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

        self.download_path: Optional[str] = None
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

        # Settings (temp dir, etc.)
        self._settings = load_settings()

        # History des téléchargements
        self._history = HistoryManager()

        self._build_ui()

        # Windows taskbar — initialized after _build_ui (needs HWND)
        # Slight delay to let Tk create the window first
        self._taskbar: TaskbarProgress = None
        self.after(500, self._init_taskbar)

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

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        main = ctk.CTkFrame(self)
        main.pack(fill="both", expand=True, padx=10, pady=10)

        left = ctk.CTkFrame(main, width=370)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)

        right = ctk.CTkFrame(main)
        right.pack(side="right", fill="both", expand=True)

        # ---- Left panel ----
        ctk.CTkLabel(left, text="URL(s) — one per line").pack(anchor="w", padx=12, pady=(12, 0))
        self.url_box = ctk.CTkTextbox(left, width=340, height=90,
                                      wrap="none", activate_scrollbars=True)
        self.url_box.pack(padx=12, pady=(4, 2), fill="x")
        ctk.CTkLabel(left, text="Paste multiple URLs, one per line.",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(
                         anchor="w", padx=12, pady=(0, 4))

        ctk.CTkButton(left, text="Choose destination folder",
                      command=self.choose_folder).pack(padx=12, pady=(4, 2), fill="x")
        self.folder_label = ctk.CTkLabel(left, text="Folder: (not selected)",
                                         wraplength=340, justify="left", text_color="gray")
        self.folder_label.pack(anchor="w", padx=12, pady=(0, 8))

        self.keep_tree_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(left, text="Keep original folder structure",
                        variable=self.keep_tree_var).pack(anchor="w", padx=12, pady=(0, 10))

        ctk.CTkLabel(left, text="Simultaneous downloads (1–20)").pack(anchor="w", padx=12)
        self.worker_entry = ctk.CTkEntry(left, width=80)
        self.worker_entry.insert(0, "8")
        self.worker_entry.pack(anchor="w", padx=12, pady=6)

        self.global_speed_label = ctk.CTkLabel(left, text="Global speed: –",
                                               font=ctk.CTkFont(size=14, weight="bold"))
        self.global_speed_label.pack(anchor="w", padx=12, pady=(6, 2))

        self.global_dl_label = ctk.CTkLabel(left, text="Total downloaded: 0 MB",
                                            text_color="gray")
        self.global_dl_label.pack(anchor="w", padx=12, pady=(0, 10))

        btn_row = ctk.CTkFrame(left, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(4, 4))
        self.start_btn = ctk.CTkButton(btn_row, text="START",
                                       command=self.start_downloads, fg_color="#1f6aa5")
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.stop_btn = ctk.CTkButton(btn_row, text="STOP ALL",
                                      command=self.stop_all, fg_color="#8B0000")
        self.stop_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))

        ctk.CTkButton(left, text="Clear finished/canceled",
                      command=self.clear_finished).pack(fill="x", padx=12, pady=(6, 4))

        ctk.CTkLabel(left, text="TurboDownloader • © Thomas PIERRE",
                     font=ctk.CTkFont(size=11), text_color="gray").pack(
                         side="bottom", anchor="sw", padx=12, pady=(0, 6))

        # Ligne boutons bas (Settings + History côte à côte)
        bot_btns = ctk.CTkFrame(left, fg_color="transparent")
        bot_btns.pack(side="bottom", fill="x", padx=12, pady=(4, 2))
        ctk.CTkButton(bot_btns, text="Settings",
                      fg_color="transparent", border_width=1,
                      command=self._open_settings).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(bot_btns, text="History",
                      fg_color="transparent", border_width=1,
                      command=self._open_history).pack(side="left", expand=True, fill="x", padx=(4, 0))

        # ---- Right panel ----
        top_bar = ctk.CTkFrame(right, fg_color="transparent")
        top_bar.pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkLabel(top_bar, text="Downloads",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")

        filter_frame = ctk.CTkFrame(top_bar, fg_color="transparent")
        filter_frame.pack(side="right")

        self._filter_btns = {}
        filters = [
            ("all",         "All",        "#1f6aa5"),
            ("downloading", "Downloading",    "#2e8b57"),
            ("paused",      "Paused",    "#1f6aa5"),
            ("moving",      "Moving", "#5a5a5a"),
            ("waiting",     "Waiting",  "#5a5a5a"),
            ("done",        "Done",    "#2e8b57"),
            ("canceled",    "Canceled",     "#8B4513"),
            ("error",       "Errors",     "#8B0000"),
        ]
        for fkey, flabel, fcolor in filters:
            btn = ctk.CTkButton(
                filter_frame, text=f"{flabel} (0)", width=110,
                fg_color=fcolor if fkey == "all" else "transparent",
                border_width=1, border_color=fcolor,
                command=lambda k=fkey: self._set_filter(k),
            )
            btn.pack(side="left", padx=3)
            self._filter_btns[fkey] = btn

        self.scroll = ctk.CTkScrollableFrame(right)
        self.scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

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
        """Returns the list of entered URLs.
        Accepted separators: newline OR space(s).
        Empty entries and duplicates are ignored, order is preserved.
        """
        raw = self.url_box.get("1.0", "end").strip()
        tokens = raw.split()   # split() with no arg splits on any whitespace (space, tab, newline)
        # Keep only tokens that look like a URL (start with http)
        seen = set()
        urls = []
        for t in tokens:
            t = t.strip()
            if t and t.lower().startswith("http") and t not in seen:
                seen.add(t)
                urls.append(t)
        return urls

    def _open_settings(self):
        def on_save():
            # Update the existing dict in place (no reference reassignment)
            self._settings.update(load_settings())
        SettingsPopup(self, self._settings, on_save)

    def _open_history(self):
        def on_redownload(url: str):
            self.url_box.delete("1.0", "end")
            self.url_box.insert("1.0", url)
            self.start_downloads()
        HistoryPopup(self, self._history, on_redownload)

    def choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.download_path = folder
            self.folder_label.configure(text=f"{folder}", text_color="white")

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
            elif href.lower().endswith(self._active_extensions):
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
        row = DownloadRow(
            self.scroll, item.filename,
            on_pause=lambda i=idx:  self.pause_one(i),
            on_cancel=lambda i=idx: self.cancel_one(i),
            on_remove=lambda i=idx: self.remove_one(i),
        )
        self.rows[idx] = row
        self._apply_filter_to_row(idx)

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

        state_labels = {
            "waiting":     "Waiting",
            "downloading": "Downloading",
            "paused":      "Paused",
            "moving":      "Moving…",
            "done":        "Done",
            "error":       f"Erreur: {it.error_msg[:40]}",
            "canceled":    "Canceled",
            "skipped":     "Already exists (skipped)",
        }
        row.status.configure(text=state_labels.get(it.state, it.state))

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
            row.pause_btn.configure(state="disabled", text="Pause", fg_color="#5a5a5a")
            row.cancel_btn.configure(state="disabled")
            row.remove_btn.configure(state="normal")
            if it.state == "done":
                row.progress.set(1.0)
        elif it.state == "paused":
            row.pause_btn.configure(state="normal", text="Resume", fg_color="#1f6aa5")
            row.cancel_btn.configure(state="normal")
            row.remove_btn.configure(state="disabled")
        elif it.state == "downloading":
            row.pause_btn.configure(state="normal", text="Pause", fg_color="#5a5a5a")
            row.cancel_btn.configure(state="normal")
            row.remove_btn.configure(state="disabled")
        elif it.state == "moving":
            row.pause_btn.configure(state="disabled", text="Pause", fg_color="#5a5a5a")
            row.cancel_btn.configure(state="disabled")
            row.remove_btn.configure(state="disabled")
        else:  # waiting
            row.pause_btn.configure(state="disabled", text="Pause", fg_color="#5a5a5a")
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
        counts["all"] = len(active_items)
        for it in active_items:
            if it.state in counts:
                counts[it.state] += 1
        labels = {
            "all":         "All",
            "downloading": "Downloading",
            "paused":      "Paused",
            "moving":      "Moving",
            "waiting":     "Waiting",
            "done":        "Done",
            "canceled":    "Canceled",
            "error":       "Errors",
        }
        for k, btn in self._filter_btns.items():
            btn.configure(text=f"{labels[k]} ({counts.get(k, 0)})")

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
        self.global_speed_label.configure(text=f"Global speed: {speed_text}")
        self.global_dl_label.configure(text=f"Total downloaded: {self._fmt_size(total)}")

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

    # ----------------------------------------------------------------- Orchestration START / STOP

    SCAN_TIMEOUT = 60  # seconds before interrupting the crawl

    def start_downloads(self):
        if not self.download_path:
            self.folder_label.configure(
                text="Please choose a destination folder first", text_color="orange")
            return

        urls = self._get_urls()
        if not urls:
            return

        try:
            workers = int(self.worker_entry.get().strip() or "8")
        except Exception:
            workers = 8
        workers = max(1, min(20, workers))
        self.worker_entry.delete(0, "end")
        self.worker_entry.insert(0, str(workers))

        self.stop_all_event.clear()
        self._scan_cancel_event.clear()
        keep_tree = self.keep_tree_var.get()

        # Traiter chaque URL séquentiellement in un thread dédié
        self.start_btn.configure(state="disabled", text="Scanning…")

        def process_all_urls():
            for url in urls:
                if self.stop_all_event.is_set():
                    break
                self._process_one_url(url, workers, keep_tree)
            self.ui(lambda: self.start_btn.configure(state="normal", text="START"))

        threading.Thread(target=process_all_urls, daemon=True).start()

    def _process_one_url(self, url: str, workers: int, keep_tree: bool):
        """Processes a single URL: direct file or crawl + popup."""
        # Normalize the URL
        if not url.split("?")[0].lower().endswith(self._active_extensions) and not url.endswith("/"):
            url = url + "/"

        # ── Case 1: direct file URL ──────────────────────────────────────
        if url.split("?")[0].lower().endswith(self._active_extensions):
            name = unquote(os.path.basename(url.split("?")[0]) or "file.bin")
            self._launch_downloads([(url, "")], workers, keep_tree)
            self.ui(lambda n=name: self.folder_label.configure(
                text=f"Direct download: {n}", text_color="white"))
            return

        # ── Case 2: directory URL → crawl ───────────────────────────────
        self.ui(lambda u=url: self.folder_label.configure(
            text=f"Scanning: {u[:60]}…", text_color="gray"))

        self._scan_cancel_event.clear()
        timer = threading.Timer(self.SCAN_TIMEOUT, self._scan_cancel_event.set)
        timer.daemon = True
        timer.start()

        files = self.get_all_files(url)
        timed_out = self._scan_cancel_event.is_set()
        timer.cancel()

        if self.stop_all_event.is_set():
            return

        if not files:
            msg = ("Scan interrupted (60s timeout) — no files found"
                   if timed_out else f"No files found: {url[:50]}")
            self.ui(lambda m=msg: self.folder_label.configure(text=m, text_color="orange"))
            return

        warning = (" ⚠ Scan interrupted after 60s — list may be incomplete"
                   if timed_out else "")

        # open_popup must run on the UI thread and block until confirmed
        # Use an Event to synchronize
        popup_done = threading.Event()

        def open_popup():
            def on_confirm(selected_files):
                if selected_files:
                    self._launch_downloads(selected_files, workers, keep_tree)
                popup_done.set()

            FileTreePopup(self, files, on_confirm)
            if warning:
                self.folder_label.configure(text=warning, text_color="orange")

        self.ui(open_popup)
        # Wait for the user to confirm (or close) the popup before processing the next URL
        popup_done.wait()

    def _launch_downloads(self, files: list, workers: int, keep_tree: bool):
        """Launches downloads for the selection from the file tree popup."""
        self.stop_all_event.clear()

        def init_ui():
            base = self.download_path
            new_indices = []
            for file_url, rel_dir in files:
                name = unquote(os.path.basename(file_url.split("?")[0]) or "file.bin")
                dest_dir = os.path.join(base, rel_dir) if keep_tree and rel_dir else base
                dest = os.path.join(dest_dir, name)

                # Reuse an existing row if the same file was previously canceled/errored
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
                    self._update_row_ui(existing_idx)
                    new_indices.append(existing_idx)
                else:
                    idx = self._next_idx
                    self._next_idx += 1
                    it = DownloadItem(url=file_url, filename=name,
                                      dest_path=dest, relative_path=rel_dir)
                    self.items[idx] = it
                    self._add_row_for_item(idx, it)
                    self._update_row_ui(idx)
                    new_indices.append(idx)

            return new_indices

        def _run():
            nonlocal new_indices
            new_indices = self.ui_call(init_ui)
            self.executor = ThreadPoolExecutor(max_workers=workers)

            # Atomic counters for batch completion notification
            batch_set    = set(new_indices)
            _lock        = threading.Lock()
            _remaining   = [len(batch_set)]   # mutable list for closure

            def _on_future_done(f):
                f.exception()  # log l'exception si présente
                with _lock:
                    _remaining[0] -= 1
                    if _remaining[0] > 0:
                        return
                # All les workers du batch sont terminés
                if not self._settings.get("notifications", True):
                    return
                # Appel in un thread séparé : notify_batch_done peut bloquer (appel système)
                def _notify_thread():
                    done     = sum(1 for i in batch_set
                                   if i in self.items and self.items[i].state == "done")
                    errors   = sum(1 for i in batch_set
                                   if i in self.items and self.items[i].state == "error")
                    canceled = sum(1 for i in batch_set
                                   if i in self.items and self.items[i].state in ("canceled", "skipped"))
                    notify_batch_done(done, errors, canceled)
                threading.Thread(target=_notify_thread, daemon=True).start()

            for i in new_indices:
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