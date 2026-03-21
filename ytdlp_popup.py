"""
ytdlp_popup.py — Quality selection popup for yt-dlp downloads.

- Single videos: card with thumbnail + title + global quality selector
- Playlists: collapsible list of entries with checkboxes (all checked by default)
- Mix of both: visual separator between sections
- Global quality selector at the bottom applies to all selected items
"""

import threading
import urllib.request
import io

import customtkinter as ctk
from PIL import Image

import ytdlp_worker


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_duration(s) -> str:
    if not s or s <= 0:
        return ""
    s = int(s)
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}:{sec:02d}"
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{sec:02d}"


def _load_thumb_async(url: str, label: ctk.CTkLabel, size=(120, 68)):
    """Downloads a thumbnail and updates a CTkLabel in the background."""
    def _work():
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                data = resp.read()
            img = Image.open(io.BytesIO(data)).resize(size, Image.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=size)
            label.after(0, lambda: label.configure(image=ctk_img, text=""))
            label._thumb_img = ctk_img
        except Exception:
            pass
    threading.Thread(target=_work, daemon=True).start()


# ── Main popup ───────────────────────────────────────────────────────────────

def _center_on_master(window, master):
    window.update_idletasks()
    x = master.winfo_rootx() + (master.winfo_width()  - window.winfo_width())  // 2
    y = master.winfo_rooty() + (master.winfo_height() - window.winfo_height()) // 2
    window.geometry(f"+{max(0,x)}+{max(0,y)}")


class YtdlpPopup(ctk.CTkToplevel):
    """
    Quality selection popup for yt-dlp downloads.

    Parameters
    ----------
    master        : parent window
    urls          : list of streaming URLs
    on_confirm    : callback(items: list[dict])
                    Each dict: {url, format_id, audio_only, dest, title}
                    Called with [] on cancel.
    default_dest  : default destination folder string
    """

    def __init__(self, master, urls: list[str], on_confirm, default_dest: str = "",
                 recent_dests: list = None):
        super().__init__(master)
        self.title("Video Quality Selection")
        self.geometry("820x700")
        self.resizable(True, True)
        self.grab_set()
        self.after(50, lambda: _center_on_master(self, master))

        self._urls         = urls
        self._on_confirm   = on_confirm
        # If no default dest, fall back to most recently used
        if not default_dest and recent_dests:
            default_dest = next((d for d in recent_dests if d), "")

        self._default_dest = default_dest
        self._recent_dests = [d for d in (recent_dests or []) if d and d != default_dest]
        self._canceled     = False

        # Fetched metadata per URL: None=pending, False=failed, dict=ok
        self._meta: dict = {u: None for u in urls}

        # Global quality: "_best_" | "_audioonly_" | "720" (height str)
        self._quality_var = ctk.StringVar(value="_best_")

        # Playlist checkbox vars: {playlist_url: {entry_url: BooleanVar}}
        self._playlist_vars: dict[str, dict[str, ctk.BooleanVar]] = {}

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._build_ui()
        self._fetch_all()

    # ─────────────────────────────────────────────────────── UI construction

    def _build_ui(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)
        self.grid_columnconfigure(0, weight=1)

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=0, column=0, sticky="nsew")
        content.grid_rowconfigure(1, weight=1)
        content.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(content, text="Video Quality Selection",
                     font=ctk.CTkFont(size=15, weight="bold")).grid(
                         row=0, column=0, padx=20, pady=(16, 8), sticky="w")

        self._scroll = ctk.CTkScrollableFrame(content, fg_color="transparent")
        self._scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))

        self._loading_lbl = ctk.CTkLabel(
            self._scroll, text="⏳  Fetching video info…",
            text_color="gray", font=ctk.CTkFont(size=13))
        self._loading_lbl.pack(pady=40)

        # Quality panel
        qual_panel = ctk.CTkFrame(content, fg_color="#1e1e1e",
                                  corner_radius=8, border_width=1,
                                  border_color="#2a2a2a")
        qual_panel.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))

        ctk.CTkLabel(qual_panel, text="Quality  —  applied to all selected videos",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#888888").pack(anchor="w", padx=14, pady=(10, 6))

        self._qual_row = ctk.CTkFrame(qual_panel, fg_color="transparent")
        self._qual_row.pack(fill="x", padx=14, pady=(0, 10))

        for key, label, color in [
            ("_best_",      "⭐ Best",             "#1f6aa5"),
            ("_audioonly_", "🎵 Audio only (MP3)", "#6a3a9a"),
        ]:
            self._make_qual_btn(key, label, color)

        self._dyn_btns: list = []

        # Bottom bar
        bot = ctk.CTkFrame(self, fg_color="#2b2b2b")
        bot.grid(row=1, column=0, sticky="ew")

        ctk.CTkButton(bot, text="Cancel", width=110, fg_color="#5a5a5a",
                      command=self._cancel).pack(side="right", padx=(8, 16), pady=12)

        self._start_btn = ctk.CTkButton(
            bot, text="▶  Start downloads", width=180,
            fg_color="#1f6aa5", hover_color="#1a5a8f",
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled", command=self._confirm)
        self._start_btn.pack(side="right", pady=12)

        self._dest_entry = ctk.CTkEntry(bot, width=260,
                                        placeholder_text="Destination folder…")
        if self._default_dest:
            self._dest_entry.insert(0, self._default_dest)
        self._dest_entry.pack(side="left", padx=(16, 4), pady=12)

        if self._recent_dests:
            ctk.CTkButton(bot, text="▾", width=30,
                          fg_color="#2a2a2a", hover_color="#3a3a3a",
                          font=ctk.CTkFont(size=14),
                          command=self._show_dest_history).pack(side="left", padx=(0, 4), pady=12)

        ctk.CTkButton(bot, text="Browse…", width=90,
                      fg_color="transparent", border_width=1,
                      command=self._browse_dest).pack(side="left", pady=12)

    def _make_qual_btn(self, key: str, label: str, color: str):
        is_sel = self._quality_var.get() == key
        btn = ctk.CTkButton(
            self._qual_row, text=label, width=10,
            fg_color=color if is_sel else "transparent",
            border_width=1, border_color=color,
            hover_color="#2a2a2a",
            command=lambda k=key, c=color: self._select_quality(k, c))
        btn.pack(side="left", padx=(0, 6))
        return btn

    # ─────────────────────────────────────────────────────── Metadata fetch

    def _fetch_all(self):
        fetched   = [0]
        total     = len(self._urls)
        lock      = threading.Lock()
        triggered = [False]

        def _fetch_one(url: str):
            meta = ytdlp_worker.fetch_formats(url)
            with lock:
                self._meta[url] = meta if meta is not None else False
                fetched[0] += 1
                done = fetched[0] >= total
            if done and not triggered[0]:
                triggered[0] = True
                self.after(0, self._on_all_fetched)

        for url in self._urls:
            threading.Thread(target=_fetch_one, args=(url,), daemon=True).start()

        self.after(45000, lambda: (
            triggered[0] or (triggered.__setitem__(0, True) or self._on_all_fetched())
        ))

    def _on_all_fetched(self):
        try:
            if self._loading_lbl.winfo_exists():
                self._loading_lbl.destroy()
        except Exception:
            pass

        single_metas   = []
        playlist_metas = []

        for url in self._urls:
            meta = self._meta.get(url)
            if not meta or not isinstance(meta, dict):
                single_metas.append((url, None))
            elif meta.get("type") == "playlist":
                playlist_metas.append((url, meta))
            else:
                single_metas.append((url, meta))

        has_singles   = bool(single_metas)
        has_playlists = bool(playlist_metas)

        if has_singles:
            if has_playlists:
                self._section_header("🎬  Videos")
            for url, meta in single_metas:
                self._make_video_card(url, meta)

        if has_singles and has_playlists:
            ctk.CTkFrame(self._scroll, height=2,
                         fg_color="#333333").pack(fill="x", pady=(12, 4), padx=4)

        for url, meta in playlist_metas:
            self._make_playlist_section(url, meta)

        # Dynamic resolution buttons
        all_heights = set()
        for meta in self._meta.values():
            if not meta or not isinstance(meta, dict):
                continue
            for q in meta.get("qualities", []):
                h = q.get("height", 0)
                if h and h < 99999:
                    all_heights.add(h)
            for entry in meta.get("entries", []):
                for q in entry.get("qualities", []):
                    h = q.get("height", 0)
                    if h and h < 99999:
                        all_heights.add(h)

        for h in sorted(all_heights, reverse=True):
            btn = self._make_qual_btn(str(h), f"{h}p", "#2e6b3e")
            self._dyn_btns.append(btn)

        self._start_btn.configure(state="normal")

    # ─────────────────────────────────────────────────────── Card builders

    def _section_header(self, text: str):
        ctk.CTkLabel(self._scroll, text=text, anchor="w",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#888888").pack(fill="x", padx=8, pady=(8, 4))

    def _make_video_card(self, url: str, meta):
        card = ctk.CTkFrame(self._scroll, fg_color="#1e1e1e",
                            corner_radius=8, border_width=1,
                            border_color="#2a2a2a")
        card.pack(fill="x", pady=4, padx=2)
        card.grid_columnconfigure(1, weight=1)

        tf = ctk.CTkFrame(card, width=120, height=68,
                          fg_color="#111111", corner_radius=4)
        tf.grid(row=0, column=0, rowspan=2, padx=(10, 12), pady=10)
        tf.grid_propagate(False)
        tl = ctk.CTkLabel(tf, text="🎬", font=ctk.CTkFont(size=24))
        tl.place(relx=0.5, rely=0.5, anchor="center")

        if meta:
            ctk.CTkLabel(card, text=(meta.get("title") or url)[:72], anchor="w",
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color="#dddddd").grid(
                             row=0, column=1, padx=(0, 10), pady=(10, 2), sticky="ew")
            parts = [p for p in [meta.get("uploader", ""),
                                  _fmt_duration(meta.get("duration", 0))] if p]
            ctk.CTkLabel(card, text="  ·  ".join(parts), anchor="w",
                         font=ctk.CTkFont(size=11),
                         text_color="#666666").grid(
                             row=1, column=1, padx=(0, 10), pady=(0, 10), sticky="w")
            if meta.get("thumbnail"):
                _load_thumb_async(meta["thumbnail"], tl)
        else:
            ctk.CTkLabel(card, text=(url[:72] + "…" if len(url) > 72 else url),
                         anchor="w", font=ctk.CTkFont(size=12),
                         text_color="#888888").grid(
                             row=0, column=1, padx=(0, 10), pady=(10, 2), sticky="ew")
            ctk.CTkLabel(card, text="⚠ Could not fetch video info", anchor="w",
                         font=ctk.CTkFont(size=11), text_color="#8B4500").grid(
                             row=1, column=1, padx=(0, 10), pady=(0, 10), sticky="w")

    def _make_playlist_section(self, url: str, meta: dict):
        entries = meta.get("entries", [])

        # Header
        header = ctk.CTkFrame(self._scroll, fg_color="#1a1a2e",
                              corner_radius=8, border_width=1,
                              border_color="#2a2a4a")
        header.pack(fill="x", pady=(6, 2), padx=2)
        header.grid_columnconfigure(0, weight=1)

        title    = meta.get("title") or url
        uploader = meta.get("uploader") or ""
        count    = len(entries)

        ctk.CTkLabel(header, text=f"📋  {title[:65]}", anchor="w",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#aaaaff").grid(
                         row=0, column=0, padx=12, pady=(8, 2), sticky="ew")
        ctk.CTkLabel(header,
                     text=f"{uploader}{'  ·  ' if uploader else ''}{count} video{'s' if count != 1 else ''}",
                     anchor="w", font=ctk.CTkFont(size=11),
                     text_color="#666699").grid(
                         row=1, column=0, padx=12, pady=(0, 6), sticky="w")

        btn_row = ctk.CTkFrame(header, fg_color="transparent")
        btn_row.grid(row=0, column=1, rowspan=2, padx=(0, 10), pady=6)
        ctk.CTkButton(btn_row, text="✓ All", width=70, height=26,
                      fg_color="transparent", border_width=1,
                      border_color="#3a3a6a", font=ctk.CTkFont(size=11),
                      command=lambda u=url: self._select_all_playlist(u, True)
                      ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(btn_row, text="✕ None", width=70, height=26,
                      fg_color="transparent", border_width=1,
                      border_color="#3a3a6a", font=ctk.CTkFont(size=11),
                      command=lambda u=url: self._select_all_playlist(u, False)
                      ).pack(side="left")

        # Entry rows
        entry_vars = {}
        ef = ctk.CTkFrame(self._scroll, fg_color="#161616",
                          corner_radius=6, border_width=1,
                          border_color="#222222")
        ef.pack(fill="x", pady=(0, 6), padx=2)

        for i, entry in enumerate(entries):
            var = ctk.BooleanVar(value=True)
            entry_vars[entry["url"]] = var

            row = ctk.CTkFrame(ef, fg_color="transparent")
            row.pack(fill="x", padx=6, pady=1)
            row.grid_columnconfigure(2, weight=1)

            ctk.CTkCheckBox(row, text="", variable=var, width=24).grid(
                row=0, column=0, padx=(4, 6), pady=4)

            # Small thumbnail
            tframe = ctk.CTkFrame(row, width=80, height=46,
                                  fg_color="#111111", corner_radius=3)
            tframe.grid(row=0, column=1, padx=(0, 8), pady=4)
            tframe.grid_propagate(False)
            tlbl = ctk.CTkLabel(tframe, text="🎬", font=ctk.CTkFont(size=14))
            tlbl.place(relx=0.5, rely=0.5, anchor="center")
            if entry.get("thumbnail"):
                _load_thumb_async(entry["thumbnail"], tlbl, size=(80, 46))

            title_text = f"{i+1:02d}.  {(entry.get('title') or entry['url'])[:60]}"
            ctk.CTkLabel(row, text=title_text, anchor="w",
                         font=ctk.CTkFont(size=11),
                         text_color="#cccccc").grid(
                             row=0, column=2, padx=(0, 8), pady=4, sticky="ew")

            dur = _fmt_duration(entry.get("duration", 0))
            if dur:
                ctk.CTkLabel(row, text=dur, width=55, anchor="e",
                             font=ctk.CTkFont(size=11),
                             text_color="#555555").grid(
                                 row=0, column=3, padx=(0, 8), pady=4)

            if i < len(entries) - 1:
                ctk.CTkFrame(ef, height=1, fg_color="#222222").pack(fill="x", padx=6)

        self._playlist_vars[url] = entry_vars

    # ─────────────────────────────────────────────────────── Quality control

    def _select_quality(self, key: str, active_color: str):
        self._quality_var.set(key)
        for btn in self._qual_row.winfo_children():
            text   = btn.cget("text")
            border = btn.cget("border_color")
            sel = (
                (key == "_best_"      and "Best"  in text) or
                (key == "_audioonly_" and "Audio" in text) or
                (key not in ("_best_", "_audioonly_") and text == f"{key}p")
            )
            btn.configure(fg_color=border if sel else "transparent")

    def _select_all_playlist(self, playlist_url: str, value: bool):
        for var in self._playlist_vars.get(playlist_url, {}).values():
            var.set(value)

    # ─────────────────────────────────────────────────────── Actions

    def _browse_dest(self):
        # Si le master est en mode client connecté → browse distant
        client = getattr(self.master, "_remote_client", None)
        if client and getattr(client, "connected", False):
            from settings_popup import _RemoteBrowsePopup
            _RemoteBrowsePopup(self, client, callback=lambda path: (
                self._dest_entry.delete(0, "end"),
                self._dest_entry.insert(0, path),
            ))
            return
        # Sinon browse local
        from tkinter import filedialog
        folder = filedialog.askdirectory(title="Choose destination folder")
        if folder:
            self._dest_entry.delete(0, "end")
            self._dest_entry.insert(0, folder)

    def _show_dest_history(self):
        """Shows a dropdown of recently used destination folders."""
        if not self._recent_dests:
            return
        import customtkinter as _ctk
        menu = _ctk.CTkToplevel(self)
        menu.overrideredirect(True)
        menu.attributes("-topmost", True)
        x = self._dest_entry.winfo_rootx()
        y = self._dest_entry.winfo_rooty() + self._dest_entry.winfo_height()
        w = self._dest_entry.winfo_width() + 34
        menu.geometry(f"{w}x{min(len(self._recent_dests) * 36, 220)}+{x}+{y}")
        # NO grab_set() — would block parent window clicks
        scroll = _ctk.CTkScrollableFrame(menu, fg_color="#1e1e1e")
        scroll.pack(fill="both", expand=True)

        def _close_menu(event=None):
            if menu.winfo_exists():
                menu.destroy()

        def _pick(path):
            self._dest_entry.delete(0, "end")
            self._dest_entry.insert(0, path)
            _close_menu()

        for path in self._recent_dests:
            display = path if len(path) <= 55 else "…" + path[-52:]
            _ctk.CTkButton(
                scroll, text=display, anchor="w",
                fg_color="transparent", hover_color="#2a3a4a",
                text_color="#cccccc", font=_ctk.CTkFont(size=11),
                height=30,
                command=lambda p=path: _pick(p),
            ).pack(fill="x", padx=2, pady=1)

        def _on_click_outside(event):
            if not menu.winfo_exists():
                return
            mx, my = menu.winfo_rootx(), menu.winfo_rooty()
            mw, mh = menu.winfo_width(), menu.winfo_height()
            if not (mx <= event.x_root <= mx + mw and my <= event.y_root <= my + mh):
                _close_menu()

        self.bind("<Button-1>", _on_click_outside, add=True)
        menu.bind("<Destroy>", lambda e: self.unbind("<Button-1>"))

    def _cancel(self):
        if self._canceled:
            return
        self._canceled = True
        self.destroy()
        self._on_confirm([])

    def _confirm(self):
        quality_key = self._quality_var.get()
        audio_only  = quality_key == "_audioonly_"
        dest        = self._dest_entry.get().strip() or self._default_dest
        confirmed   = []

        for url in self._urls:
            meta = self._meta.get(url)

            if not meta or not isinstance(meta, dict):
                confirmed.append({
                    "url": url, "format_id": None,
                    "audio_only": audio_only, "dest": dest, "title": "",
                })
                continue

            if meta.get("type") == "playlist":
                entry_vars = self._playlist_vars.get(url, {})
                for entry in meta.get("entries", []):
                    eurl = entry["url"]
                    var  = entry_vars.get(eurl)
                    if var and not var.get():
                        continue
                    confirmed.append({
                        "url":        eurl,
                        "format_id":  self._resolve_format(quality_key, entry.get("qualities", [])),
                        "audio_only": audio_only,
                        "dest":       dest,
                        "title":      entry.get("title", ""),
                    })
            else:
                confirmed.append({
                    "url":        url,
                    "format_id":  self._resolve_format(quality_key, meta.get("qualities", [])),
                    "audio_only": audio_only,
                    "dest":       dest,
                    "title":      meta.get("title", ""),
                })

        self.destroy()
        self._on_confirm(confirmed)

    def _resolve_format(self, quality_key: str, qualities: list):
        if quality_key in ("_best_", "_audioonly_", ""):
            return None
        try:
            target_h = int(quality_key)
        except ValueError:
            return None
        match = next((q for q in qualities if q.get("height") == target_h), None)
        return match["format_id"] if match else None
