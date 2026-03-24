import customtkinter as ctk

# Status → (label text, color)
_STATE_STYLE = {
    "waiting":     ("⏳ Waiting",          "#666666"),
    "downloading": ("↓ Downloading",       "#2e8b57"),
    "paused":      ("⏸ Paused",            "#5a7a9a"),
    "moving":      ("⇄ Moving…",           "#888800"),
    "done":        ("✓ Done",              "#2e6b3e"),
    "error":       ("✗ Error",             "#8B0000"),
    "canceled":    ("⊘ Canceled",          "#555555"),
    "skipped":     ("↷ Skipped",           "#555555"),
}


class DownloadRow:
    """Widget representing a single download row in the main list."""

    def __init__(self, parent, name: str, on_pause, on_cancel, on_remove,
                 on_priority=None, on_context_menu=None):
        self.frame = ctk.CTkFrame(parent, fg_color="#1e1e1e",
                                  corner_radius=8, border_width=1,
                                  border_color="#2a2a2a")
        self.frame.pack(fill="x", pady=3, padx=4)

        # ── Ligne 0 : nom + status + boutons ─────────────────────────────────
        self.name_lbl = ctk.CTkLabel(
            self.frame, text=name, anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#dddddd")
        self.name_lbl.grid(row=0, column=0, padx=(10, 6), pady=(8, 2), sticky="ew")

        self.status = ctk.CTkLabel(
            self.frame, text="⏳ Waiting", width=140, anchor="w",
            font=ctk.CTkFont(size=11), text_color="#666666")
        self.status.grid(row=0, column=1, padx=4, pady=(8, 2), sticky="w")

        # Bouton priorité — visible uniquement en état "waiting"
        self.priority_btn = ctk.CTkButton(
            self.frame, text="↑", width=32, height=28,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="transparent", border_width=1, border_color="#8B6914",
            hover_color="#2a2200", text_color="#f0a500",
            command=on_priority or (lambda: None))
        self.priority_btn.grid(row=0, column=2, padx=3, pady=(8, 2))

        # Boutons compacts
        self.pause_btn = ctk.CTkButton(
            self.frame, text="⏸", width=32, height=28,
            font=ctk.CTkFont(size=14),
            fg_color="transparent", border_width=1, border_color="#3a3a3a",
            hover_color="#2a2a2a", command=on_pause)
        self.pause_btn.grid(row=0, column=3, padx=3, pady=(8, 2))

        self.cancel_btn = ctk.CTkButton(
            self.frame, text="✕", width=32, height=28,
            font=ctk.CTkFont(size=12),
            fg_color="transparent", border_width=1, border_color="#5a1515",
            hover_color="#3a1010", text_color="#cc4444", command=on_cancel)
        self.cancel_btn.grid(row=0, column=4, padx=3, pady=(8, 2))

        self.remove_btn = ctk.CTkButton(
            self.frame, text="🗑", width=32, height=28,
            font=ctk.CTkFont(size=12),
            fg_color="transparent", border_width=1, border_color="#333333",
            hover_color="#2a2a2a",
            command=on_remove, state="disabled")
        self.remove_btn.grid(row=0, column=5, padx=(3, 10), pady=(8, 2))

        # ── Ligne 1 : barre de progression ───────────────────────────────────
        self.progress = ctk.CTkProgressBar(
            self.frame, height=6, corner_radius=3,
            progress_color="#1f6aa5", fg_color="#2a2a2a")
        self.progress.set(0)
        self.progress.grid(row=1, column=0, columnspan=6,
                           padx=10, pady=(2, 3), sticky="ew")

        # ── Ligne 2 : vitesse + ETA ───────────────────────────────────────────
        self.speed_lbl = ctk.CTkLabel(
            self.frame, text="–", width=110, anchor="w",
            font=ctk.CTkFont(size=11), text_color="#555555")
        self.speed_lbl.grid(row=2, column=0, padx=(10, 4), pady=(0, 8), sticky="w")

        self.eta_lbl = ctk.CTkLabel(
            self.frame, text="ETA –", width=120, anchor="w",
            font=ctk.CTkFont(size=11), text_color="#555555")
        self.eta_lbl.grid(row=2, column=1, padx=4, pady=(0, 8), sticky="w")

        self.frame.grid_columnconfigure(0, weight=1)

        if on_context_menu:
            for w in (self.frame, self.name_lbl, self.progress,
                      self.speed_lbl, self.eta_lbl, self.status):
                w.bind("<Button-3>", on_context_menu)

    def update_state_style(self, state: str):
        """Met à jour la couleur du status selon l'état."""
        label, color = _STATE_STYLE.get(state, ("–", "#666666"))
        self.status.configure(text_color=color)
        # La barre change de couleur selon l'état
        if state == "done":
            self.progress.configure(progress_color="#2e6b3e")
        elif state == "error":
            self.progress.configure(progress_color="#8B0000")
        elif state == "paused":
            self.progress.configure(progress_color="#5a7a9a")
        else:
            self.progress.configure(progress_color="#1f6aa5")

        # Bouton ⚡ uniquement visible en état waiting
        if state == "waiting":
            self.priority_btn.configure(state="normal")
            self.priority_btn.grid()
        else:
            self.priority_btn.configure(state="disabled")
            self.priority_btn.grid_remove()

    def set_visible(self, visible: bool):
        if visible:
            self.frame.pack(fill="x", pady=3, padx=4)
        else:
            self.frame.pack_forget()

class PlaylistGroupRow:
    """Collapsible group row for playlist downloads."""

    def __init__(self, parent, title: str, total: int,
                 on_cancel_all=None, on_remove_all=None):
        self._total    = total
        self._expanded = True
        self._child_rows: dict = {}

        self.frame = ctk.CTkFrame(parent, fg_color="#181818",
                                  corner_radius=8, border_width=1,
                                  border_color="#1f4a7a")
        self.frame.pack(fill="x", pady=4, padx=4)

        # Header
        hdr = ctk.CTkFrame(self.frame, fg_color="transparent")
        hdr.pack(fill="x", padx=8, pady=(8, 4))

        self._toggle_btn = ctk.CTkButton(
            hdr, text="▼", width=26, height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=0,
            hover_color="#2a2a2a", command=self._toggle)
        self._toggle_btn.pack(side="left", padx=(0, 4))

        short = title[:55] + "…" if len(title) > 55 else title
        ctk.CTkLabel(hdr, text=f"🎵 {short}", anchor="w",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#7ab8e8").pack(side="left", fill="x", expand=True)

        self._count_lbl = ctk.CTkLabel(hdr, text=f"0 / {total}",
                                        width=70, anchor="e",
                                        font=ctk.CTkFont(size=11),
                                        text_color="#555555")
        self._count_lbl.pack(side="right", padx=4)

        if on_cancel_all:
            ctk.CTkButton(hdr, text="✕", width=28, height=26,
                          fg_color="transparent", border_width=1,
                          border_color="#5a1515", hover_color="#3a1010",
                          text_color="#cc4444",
                          command=on_cancel_all).pack(side="right", padx=2)

        self._remove_btn = None
        if on_remove_all:
            self._remove_btn = ctk.CTkButton(
                hdr, text="🗑", width=28, height=26,
                fg_color="transparent", border_width=1,
                border_color="#333333", hover_color="#2a2a2a",
                command=on_remove_all, state="disabled")
            self._remove_btn.pack(side="right", padx=2)

        # Global progress bar
        self._progress = ctk.CTkProgressBar(self.frame, height=4,
                                             corner_radius=2,
                                             progress_color="#1f6aa5",
                                             fg_color="#2a2a2a")
        self._progress.set(0)
        self._progress.pack(fill="x", padx=8, pady=(0, 4))

        # Children container
        self._children_frame = ctk.CTkFrame(self.frame, fg_color="transparent")
        self._children_frame.pack(fill="x", padx=4, pady=(0, 4))

    def add_child(self, idx: int, row):
        self._child_rows[idx] = row
        row.frame.pack_forget()
        row.frame.pack(fill="x", pady=2, padx=4, in_=self._children_frame)

    def update_progress(self, done: int, total: int):
        self._count_lbl.configure(text=f"{done} / {total}")
        ratio = done / total if total > 0 else 0
        self._progress.set(ratio)
        if done >= total > 0:
            self._progress.configure(progress_color="#2e6b3e")
            if self._remove_btn:
                self._remove_btn.configure(state="normal")

    def _toggle(self):
        self._expanded = not self._expanded
        self._toggle_btn.configure(text="▼" if self._expanded else "▶")
        if self._expanded:
            self._children_frame.pack(fill="x", padx=4, pady=(0, 4))
        else:
            self._children_frame.pack_forget()

    def set_visible(self, visible: bool):
        if visible:
            self.frame.pack(fill="x", pady=4, padx=4)
        else:
            self.frame.pack_forget()
