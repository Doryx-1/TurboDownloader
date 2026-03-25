"""download_engine.py — DownloadEngineMixin for TurboDownloader."""
import os
import time
import threading
import shutil
from logger import get_logger
from models import SegmentInfo
from settings_popup import DEFAULT_TEMP_DIR
import ytdlp_worker

_log = get_logger("download_engine")

CHUNK_SIZE = 1024 * 512  # 512 KB per chunk


class DownloadEngineMixin:

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
        from concurrent.futures import ThreadPoolExecutor as _TPE
        it = self.items[idx]
        total = it.total_size          # garanti non-None à cet appel
        temp_dir = self._settings.get("temp_dir", DEFAULT_TEMP_DIR)

        # ── Compute byte range for each segment ──────────────────────────
        seg_size = total // n_seg
        segments: list = []
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
        seg_executor = _TPE(max_workers=len(pending) if pending else 1)
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

    # ================================================================= Worker internals

    class _Canceled(Exception):
        """Raised inside worker sub-methods to signal clean cancellation."""

    class _Paused(Exception):
        """Raised inside worker sub-methods to signal pause/requeue."""
        def __init__(self, state: str = "paused"):
            self.state = state

    class _FatalError(Exception):
        """Raised inside worker sub-methods for non-retryable errors."""
        def __init__(self, msg: str):
            self.msg = msg

    # ── File-exists handling ─────────────────────────────────────────────────

    def _check_file_exists(self, it) -> str:
        """
        Returns: 'resume' | 'replace' | 'skip' | 'rename'
        Called from worker thread — uses ui_call for the popup.
        """
        temp_path = self._get_temp_path(it)
        # .part file exists → always resume, never ask
        if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
            return "resume"
        # Final file doesn't exist → just download
        if not os.path.exists(it.dest_path):
            return "replace"
        # Final file exists — apply configured action
        action = self._settings.get("file_exists_action", "ask")
        if action == "ask":
            if getattr(it, "from_remote", False):
                # Can't show popup on server — signal client and wait for its answer
                import threading as _thr
                it.conflict_event  = _thr.Event()
                it.conflict_action = ""
                it.state = "conflict"
                self.ui(self._update_row_ui, idx)
                self.ui(self._refresh_filter_counts)
                # Wait up to 60 s for client to respond, then default to replace
                it.conflict_event.wait(timeout=60)
                it.state = "downloading"
                return it.conflict_action or "replace"
            try:
                return self.ui_call(self._ask_file_exists_popup, it)
            except Exception:
                return "replace"   # fallback if ui_call times out
        return action

    def _ask_file_exists_popup(self, it) -> str:
        """Shows a blocking dialog. Must be called on the UI thread via ui_call."""
        import customtkinter as ctk
        result = {"action": "skip"}
        popup = ctk.CTkToplevel(self)
        popup.title("File already exists")
        popup.geometry("420x210")
        popup.resizable(False, False)
        popup.grab_set()
        popup.lift()
        self.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width()  - 420) // 2
        y = self.winfo_rooty() + (self.winfo_height() - 210) // 2
        popup.geometry(f"+{max(0,x)}+{max(0,y)}")

        name = os.path.basename(it.dest_path)
        display = name if len(name) <= 50 else name[:47] + "…"
        ctk.CTkLabel(popup, text="File already exists:",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(pady=(16, 2))
        ctk.CTkLabel(popup, text=display, text_color="gray",
                     font=ctk.CTkFont(size=11)).pack(pady=(0, 14))

        remember_var = ctk.BooleanVar(value=False)
        btn_frame = ctk.CTkFrame(popup, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 8))

        def _choose(action: str):
            result["action"] = action
            if remember_var.get():
                self._settings["file_exists_action"] = action
            popup.grab_release()
            popup.destroy()

        ctk.CTkButton(btn_frame, text="↺  Replace", width=110,
                      fg_color="#5a1515", hover_color="#3a0a0a",
                      command=lambda: _choose("replace")).pack(side="left", padx=4)
        ctk.CTkButton(btn_frame, text="↷  Skip", width=110,
                      fg_color="#2a2a2a", hover_color="#1a1a1a",
                      command=lambda: _choose("skip")).pack(side="left", padx=4)
        ctk.CTkButton(btn_frame, text="✎  Rename", width=110,
                      fg_color="#1f4a7a", hover_color="#183a5a",
                      command=lambda: _choose("rename")).pack(side="left", padx=4)
        ctk.CTkCheckBox(popup, text="Remember for this session",
                        variable=remember_var,
                        font=ctk.CTkFont(size=11)).pack(pady=(0, 12))
        popup.wait_window()
        return result["action"]

    @staticmethod
    def _make_unique_path(dest_path: str) -> str:
        """Returns dest_path with _2, _3… suffix until non-existing."""
        if not os.path.exists(dest_path):
            return dest_path
        base, ext = os.path.splitext(dest_path)
        n = 2
        while True:
            candidate = f"{base}_{n}{ext}"
            if not os.path.exists(candidate):
                return candidate
            n += 1

    def _resolve_resume(self, it) -> tuple:
        """
        Checks existing .part or dest file to compute resume offset.
        Returns (existing_size, headers_dict).
        """
        temp_path = self._get_temp_path(it)
        it.temp_path = temp_path
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
        return existing_size, headers

    def _stream_to_temp(self, idx: int, it, r, existing_size: int):
        """
        Streams response body to the temp .part file.
        Handles pause, cancel, throttle, and disk-full errors.
        Raises _Canceled, _Paused, or _FatalError as appropriate.
        """
        write_mode = "ab" if existing_size > 0 else "wb"
        last_ui    = 0.0

        try:
            with open(it.temp_path, write_mode) as f:
                for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                    if self.stop_all_event.is_set() or it.cancel_event.is_set():
                        raise self._Canceled()
                    if it.pause_event.is_set():
                        if idx in self._requeue_set:
                            self._requeue_set.discard(idx)
                            raise self._Paused("waiting")
                        raise self._Paused("paused")
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
        except (self._Canceled, self._Paused):
            raise
        except OSError as e:
            if "No space left" in str(e) or e.errno == 28:
                raise self._FatalError("Temp disk full during download")
            raise self._FatalError(str(e))

    def _move_to_dest(self, it):
        """
        Moves the completed .part file to the final destination.
        Raises _FatalError on OS error.
        """
        try:
            os.makedirs(os.path.dirname(it.dest_path), exist_ok=True)
            shutil.move(it.temp_path, it.dest_path)
            it.temp_path = ""
        except OSError as e:
            raise self._FatalError(f"Move error: {e}")

    def _finalize_success(self, idx: int, it):
        """Logs history entry and sets state to done."""
        it.state = "done"
        duration = time.time() - it.started_at
        size     = it.total_size or (it.resume_from + it.downloaded)
        self._history.log_entry(
            filename=it.filename,
            url=it.url,
            size_bytes=size,
            duration_s=duration,
        )
        self.ui(self._update_row_ui, idx)
        self.ui(self._refresh_filter_counts)

    def _check_disk_space(self, it):
        """Raises _FatalError if there is not enough free space in the temp folder."""
        if not it.total_size:
            return
        temp_dir = self._settings.get("temp_dir", DEFAULT_TEMP_DIR)
        try:
            free   = shutil.disk_usage(temp_dir).free
            needed = it.total_size - it.resume_from
            if free < needed:
                raise self._FatalError(
                    f"Temp disk full "
                    f"({self._fmt_size(free)} free, {self._fmt_size(needed)} required)"
                )
        except OSError:
            pass

    # ================================================================= Download worker

    def _download_worker(self, idx: int):
        it          = self.items[idx]
        retry_max   = int(self._settings.get("retry_max",   3))
        retry_delay = int(self._settings.get("retry_delay", 5))
        it.pause_event.clear()

        while True:
            # ── Cancel check between retries ─────────────────────────────────
            if self.stop_all_event.is_set() or it.cancel_event.is_set():
                it.state = "canceled"
                self.ui(self._update_row_ui, idx)
                self.ui(self._refresh_filter_counts)
                return

            it.state      = "downloading"
            it.started_at = time.time()
            it.error_msg  = ""
            self.ui(self._update_row_ui, idx)
            self.ui(self._refresh_filter_counts)

            # ── File-exists check ─────────────────────────────────────────────
            exists_action = self._check_file_exists(it)
            if exists_action == "skip":
                it.state = "skipped"
                self.ui(self._update_row_ui, idx)
                self.ui(self._refresh_filter_counts)
                return
            elif exists_action == "rename":
                it.dest_path = self._make_unique_path(it.dest_path)
                it.filename  = os.path.basename(it.dest_path)
                self.ui(self._update_row_ui, idx)
            elif exists_action == "replace":
                try:
                    if os.path.exists(it.dest_path):
                        os.remove(it.dest_path)
                except OSError:
                    pass
            # "resume" → fall through to _resolve_resume normally

            existing_size, headers = self._resolve_resume(it)

            try:
                with self.req.get(it.url, stream=True, allow_redirects=True,
                                  timeout=60, headers=headers) as r:

                    # Server ignores Range → restart from 0
                    if existing_size > 0 and r.status_code == 200:
                        it.resume_from = 0
                        existing_size  = 0
                        try:
                            if os.path.exists(it.temp_path):
                                os.remove(it.temp_path)
                        except OSError:
                            pass

                    if r.status_code == 416:
                        _log.debug("416 Range Not Satisfiable — retrying without resume")
                        it.resume_from = 0
                        it.downloaded  = 0
                        try:
                            if os.path.exists(it.temp_path):
                                os.remove(it.temp_path)
                        except OSError:
                            pass
                        continue

                    if r.status_code not in (200, 206):
                        r.raise_for_status()

                    ct = (r.headers.get("Content-Type") or "").lower()
                    if "text/html" in ct:
                        it.state     = "error"
                        it.error_msg = "HTML received (expired link / auth required?)"
                        self.ui(self._update_row_ui, idx)
                        self.ui(self._refresh_filter_counts)
                        return

                    # Parse total size from headers
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

                    # Already complete → skip
                    if it.total_size and existing_size >= it.total_size:
                        it.state = "skipped"
                        self.ui(self._update_row_ui, idx)
                        self.ui(self._refresh_filter_counts)
                        return

                    self._check_disk_space(it)
                    os.makedirs(os.path.dirname(it.dest_path), exist_ok=True)

                    # ── Multipart decision ────────────────────────────────────
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
                        and existing_size == 0
                    )
                    if can_multipart:
                        r.close()
                        result = self._download_multipart(idx, n_seg)
                        if result in ("done", "canceled", "paused", "error"):
                            return
                        continue

                    # ── Single-stream download ────────────────────────────────
                    try:
                        self._stream_to_temp(idx, it, r, existing_size)
                    except self._Canceled:
                        it.state = "canceled"
                        self.ui(self._update_row_ui, idx)
                        self.ui(self._refresh_filter_counts)
                        try:
                            if os.path.exists(it.temp_path):
                                os.remove(it.temp_path)
                        except OSError:
                            pass
                        return
                    except self._Paused as p:
                        it.state = p.state
                        self.ui(self._update_row_ui, idx)
                        self.ui(self._refresh_filter_counts)
                        return
                    except self._FatalError as fe:
                        it.state     = "error"
                        it.error_msg = fe.msg
                        self.ui(self._update_row_ui, idx)
                        self.ui(self._refresh_filter_counts)
                        return

                    # ── Move + finalize ───────────────────────────────────────
                    it.state = "moving"
                    self.ui(self._update_row_ui, idx)
                    self.ui(self._refresh_filter_counts)
                    try:
                        self._move_to_dest(it)
                    except self._FatalError as fe:
                        it.state     = "error"
                        it.error_msg = fe.msg
                        self.ui(self._update_row_ui, idx)
                        self.ui(self._refresh_filter_counts)
                        return

                    self._finalize_success(idx, it)
                    return

            except Exception as e:
                if self._is_retryable(e) and it.retry_count < retry_max:
                    it.retry_count += 1
                    delay = retry_delay * (2 ** (it.retry_count - 1))
                    it.state     = "downloading"
                    it.error_msg = (f"Network error, retrying "
                                    f"{it.retry_count}/{retry_max} in {delay}s…")
                    self.ui(self._update_row_ui, idx)
                    for _ in range(delay):
                        if self.stop_all_event.is_set() or it.cancel_event.is_set():
                            break
                        time.sleep(1)
                    continue
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
