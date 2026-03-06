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

    def __init__(self, parent, name: str, on_pause, on_cancel, on_remove, on_priority=None):
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