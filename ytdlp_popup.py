"""
ytdlp_popup.py — Quality selection popup for yt-dlp downloads.

Shows all queued streaming URLs with their title and available qualities.
The user picks a global quality (applied to all videos) + optional Audio only mode,
then confirms to launch downloads.
"""

import threading
import urllib.request

import customtkinter as ctk
from PIL import Image
import io

import ytdlp_worker


def _fmt_size(b) -> str:
    if not b or b <= 0:
        return ""
    if b < 1024 * 1024:
        return f"{b/1024:.0f} KB"
    if b < 1024 ** 3:
        return f"{b/1024/1024:.0f} MB"
    return f"{b/1024**3:.1f} GB"


def _fmt_duration(s) -> str:
    if not s or s <= 0:
        return ""
    s = int(s)
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}:{sec:02d}"
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{sec:02d}"


class YtdlpPopup(ctk.CTkToplevel):
    """
    Popup shown before launching yt-dlp downloads.

    Parameters
    ----------
    master       : parent window
    urls         : list of streaming URLs to download
    on_confirm   : callback(confirmed_items: list[dict]) called on Start
                   Each dict has keys: url, quality (dict), audio_only (bool)
    default_dest : default destination folder
    """

    def __init__(self, master, urls: list[str], on_confirm, default_dest: str = ""):
        super().__init__(master)
        self.title("Video Quality Selection")
        self.geometry("780x640")
        self.resizable(True, True)
        self.grab_set()

        self._urls        = urls
        self._on_confirm  = on_confirm
        self._default_dest = default_dest
        self._canceled    = False

        # Intercept window close (X button) — must unblock popup_done
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        # Fetched metadata per URL: {url: dict|None}
        self._meta: dict = {u: None for u in urls}
        self._loading     = True

        # Global quality selection
        # "_best_"    = Best available
        # "_audioonly_" = Audio only (MP3)
        # "720"       = specific height as string
        self._quality_var = ctk.StringVar(value="_best_")

        self._build_ui()
        self._fetch_all_metadata()

    # ────────────────────────────────────────────────────── UI construction

    def _build_ui(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)
        self.grid_columnconfigure(0, weight=1)

        # ── Content area ─────────────────────────────────────────────────
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        content.grid_rowconfigure(1, weight=1)
        content.grid_columnconfigure(0, weight=1)

        # Title
        ctk.CTkLabel(content, text="Video Quality Selection",
                     font=ctk.CTkFont(size=15, weight="bold")).grid(
                         row=0, column=0, padx=20, pady=(16, 8), sticky="w")

        # Video list (scrollable)
        self._scroll = ctk.CTkScrollableFrame(content, fg_color="transparent")
        self._scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))

        # Loading indicator
        self._loading_lbl = ctk.CTkLabel(
            self._scroll, text="⏳  Fetching video info…",
            text_color="gray", font=ctk.CTkFont(size=13))
        self._loading_lbl.pack(pady=40)

        # ── Quality selector (bottom panel) ──────────────────────────────
        qual_panel = ctk.CTkFrame(content, fg_color="#1e1e1e",
                                  corner_radius=8, border_width=1,
                                  border_color="#2a2a2a")
        qual_panel.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))

        ctk.CTkLabel(qual_panel, text="Quality  —  applied to all videos",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#888888").pack(anchor="w", padx=14, pady=(10, 6))

        self._qual_row = ctk.CTkFrame(qual_panel, fg_color="transparent")
        self._qual_row.pack(fill="x", padx=14, pady=(0, 10))

        # Static options (always shown)
        self._build_quality_buttons(["_best_", "_audioonly_"])

        # Dynamic resolution buttons added after metadata fetch
        self._dynamic_btns: list[ctk.CTkButton] = []

        # ── Bottom bar ────────────────────────────────────────────────────
        bot = ctk.CTkFrame(self, fg_color="#2b2b2b")
        bot.grid(row=1, column=0, sticky="ew")

        ctk.CTkButton(bot, text="Cancel", width=110, fg_color="#5a5a5a",
                      command=self._cancel).pack(side="right", padx=(8, 16), pady=12)

        self._start_btn = ctk.CTkButton(
            bot, text="▶  Start downloads", width=180,
            fg_color="#1f6aa5", hover_color="#1a5a8f",
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            command=self._confirm)
        self._start_btn.pack(side="right", pady=12)

        self._dest_entry = ctk.CTkEntry(bot, width=260,
                                        placeholder_text="Destination folder…")
        if self._default_dest:
            self._dest_entry.insert(0, self._default_dest)
        self._dest_entry.pack(side="left", padx=(16, 4), pady=12)

        ctk.CTkButton(bot, text="Browse…", width=90,
                      fg_color="transparent", border_width=1,
                      command=self._browse_dest).pack(side="left", pady=12)

    def _build_quality_buttons(self, keys: list[str]):
        labels = {
            "_best_":      "⭐ Best",
            "_audioonly_": "🎵 Audio only (MP3)",
        }
        colors = {
            "_best_":      "#1f6aa5",
            "_audioonly_": "#6a3a9a",
        }
        for key in keys:
            label = labels.get(key, key)
            color = colors.get(key, "#4a4a4a")
            btn = ctk.CTkButton(
                self._qual_row,
                text=label,
                width=10,
                fg_color=color if self._quality_var.get() == key else "transparent",
                border_width=1,
                border_color=color,
                hover_color="#2a2a2a",
                command=lambda k=key, c=color: self._select_quality(k, c),
            )
            btn.pack(side="left", padx=(0, 6))
            if key not in ("_best_", "_audioonly_"):
                self._dynamic_btns.append(btn)

    # ────────────────────────────────────────────────────── Metadata fetch

    def _fetch_all_metadata(self):
        """Fetches metadata for all URLs in background threads."""
        def _fetch_one(url: str):
            meta = ytdlp_worker.fetch_formats(url)
            self._meta[url] = meta
            # Check if all done
            if all(v is not None or v == False for v in self._meta.values()):
                self.after(0, self._on_all_fetched)
            else:
                self.after(0, lambda: self._update_video_card(url, meta))

        # Mark None as "pending" — use False for "failed"
        for url in self._urls:
            t = threading.Thread(target=_fetch_one, args=(url,), daemon=True)
            t.start()

        # Timeout fallback — if stuck after 30s, show what we have
        self.after(30000, self._on_all_fetched)

    def _on_all_fetched(self):
        if not self._loading:
            return
        self._loading = False

        # Clear loading label
        self._loading_lbl.destroy()

        # Build all video cards
        for url in self._urls:
            meta = self._meta.get(url)
            self._update_video_card(url, meta)

        # Build dynamic resolution buttons from union of all available heights
        all_heights = set()
        for meta in self._meta.values():
            if meta and meta.get("qualities"):
                for q in meta["qualities"]:
                    h = q.get("height", 0)
                    if h and h < 99999:
                        all_heights.add(h)

        sorted_heights = sorted(all_heights, reverse=True)
        res_colors = "#2e6b3e"
        for h in sorted_heights:
            label = f"{h}p"
            btn = ctk.CTkButton(
                self._qual_row,
                text=label,
                width=10,
                fg_color="transparent",
                border_width=1,
                border_color=res_colors,
                hover_color="#2a2a2a",
                command=lambda k=str(h), c=res_colors: self._select_quality(k, c),
            )
            btn.pack(side="left", padx=(0, 6))
            self._dynamic_btns.append(btn)

        self._start_btn.configure(state="normal")

    def _update_video_card(self, url: str, meta: dict | None):
        """Creates or updates a video card in the scroll area."""
        card = ctk.CTkFrame(self._scroll, fg_color="#1e1e1e",
                            corner_radius=8, border_width=1,
                            border_color="#2a2a2a")
        card.pack(fill="x", pady=4, padx=2)

        # Thumbnail placeholder
        thumb_frame = ctk.CTkFrame(card, width=120, height=68,
                                   fg_color="#111111", corner_radius=4)
        thumb_frame.grid(row=0, column=0, rowspan=2, padx=(10, 12), pady=10)
        thumb_frame.grid_propagate(False)

        self._thumb_lbl = ctk.CTkLabel(thumb_frame, text="🎬",
                                       font=ctk.CTkFont(size=24))
        self._thumb_lbl.place(relx=0.5, rely=0.5, anchor="center")

        if meta:
            # Title
            title = meta.get("title", url)[:72]
            ctk.CTkLabel(card, text=title, anchor="w",
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color="#dddddd").grid(
                             row=0, column=1, padx=(0, 10), pady=(10, 2), sticky="ew")

            # Meta info (uploader + duration)
            uploader = meta.get("uploader", "")
            duration = _fmt_duration(meta.get("duration", 0))
            info_parts = [p for p in [uploader, duration] if p]
            ctk.CTkLabel(card, text="  ·  ".join(info_parts), anchor="w",
                         font=ctk.CTkFont(size=11), text_color="#666666").grid(
                             row=1, column=1, padx=(0, 10), pady=(0, 10), sticky="w")

            # Load thumbnail async
            thumb_url = meta.get("thumbnail", "")
            if thumb_url:
                threading.Thread(
                    target=self._load_thumbnail,
                    args=(thumb_url, thumb_frame, self._thumb_lbl),
                    daemon=True
                ).start()
        else:
            # Failed to fetch
            short = url[:60] + ("…" if len(url) > 60 else "")
            ctk.CTkLabel(card, text=short, anchor="w",
                         font=ctk.CTkFont(size=12), text_color="#888888").grid(
                             row=0, column=1, padx=(0, 10), pady=(10, 2), sticky="ew")
            ctk.CTkLabel(card, text="⚠ Could not fetch video info",
                         anchor="w", font=ctk.CTkFont(size=11),
                         text_color="#8B4500").grid(
                             row=1, column=1, padx=(0, 10), pady=(0, 10), sticky="w")

        card.grid_columnconfigure(1, weight=1)

    def _load_thumbnail(self, url: str, frame, label):
        """Downloads and displays a thumbnail image."""
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                data = resp.read()
            img = Image.open(io.BytesIO(data))
            img = img.resize((120, 68), Image.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(120, 68))
            self.after(0, lambda: label.configure(image=ctk_img, text=""))
            # Keep reference
            label._thumb_img = ctk_img
        except Exception:
            pass  # Keep the placeholder emoji

    # ────────────────────────────────────────────────────── Quality selection

    def _select_quality(self, key: str, active_color: str):
        self._quality_var.set(key)
        # Update all button colors
        for btn in self._qual_row.winfo_children():
            btn_text = btn.cget("text")
            # Match by command closure — simpler: recolor all, highlight selected
            border = btn.cget("border_color")
            if btn_text in ("⭐ Best", "🎵 Audio only (MP3)") or btn_text.endswith("p"):
                is_selected = (
                    (key == "_best_" and btn_text == "⭐ Best") or
                    (key == "_audioonly_" and "Audio" in btn_text) or
                    (key not in ("_best_", "_audioonly_") and btn_text == f"{key}p")
                )
                btn.configure(fg_color=border if is_selected else "transparent")

    # ────────────────────────────────────────────────────── Confirm

    def _browse_dest(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(title="Choose destination folder")
        if folder:
            self._dest_entry.delete(0, "end")
            self._dest_entry.insert(0, folder)

    def _cancel(self):
        """Close without downloading — calls on_confirm with empty list to unblock popup_done."""
        if self._canceled:
            return
        self._canceled = True
        self.destroy()
        self._on_confirm([])

    def _confirm(self):
        quality_key = self._quality_var.get()
        audio_only  = quality_key == "_audioonly_"
        dest        = self._dest_entry.get().strip() or self._default_dest

        # Build confirmed items list
        confirmed = []
        for url in self._urls:
            meta      = self._meta.get(url) or {}
            qualities = meta.get("qualities", [])

            if audio_only:
                format_id = None   # yt-dlp handles bestaudio in worker
            elif quality_key == "_best_" or not quality_key:
                format_id = None   # worker uses _best_format()
            else:
                # Find the format_id matching the chosen height
                target_h = int(quality_key)
                match = next(
                    (q for q in qualities if q.get("height") == target_h),
                    None
                )
                format_id = match["format_id"] if match else None

            confirmed.append({
                "url":        url,
                "format_id":  format_id,
                "audio_only": audio_only,
                "dest":       dest,
                "title":      meta.get("title", ""),
            })

        self.destroy()
        self._on_confirm(confirmed)
