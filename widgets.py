import customtkinter as ctk


class DownloadRow:
    """Widget représentant une ligne de téléchargement dans la liste principale."""

    def __init__(self, parent, name: str, on_cancel, on_remove):
        self.frame = ctk.CTkFrame(parent)
        self.frame.pack(fill="x", pady=3, padx=6)

        self.name_lbl = ctk.CTkLabel(self.frame, text=name, width=400, anchor="w")
        self.name_lbl.grid(row=0, column=0, padx=8, pady=(5, 2), sticky="w")

        self.status = ctk.CTkLabel(self.frame, text="Waiting", width=150, anchor="w")
        self.status.grid(row=0, column=1, padx=8, pady=(5, 2), sticky="w")

        self.cancel_btn = ctk.CTkButton(self.frame, text="Pause/Cancel", width=120, command=on_cancel)
        self.cancel_btn.grid(row=0, column=2, padx=6, pady=(5, 2))

        self.remove_btn = ctk.CTkButton(self.frame, text="Remove", width=90, command=on_remove, state="disabled")
        self.remove_btn.grid(row=0, column=3, padx=6, pady=(5, 2))

        self.progress = ctk.CTkProgressBar(self.frame, height=12)
        self.progress.set(0)
        self.progress.grid(row=1, column=0, columnspan=4, padx=8, pady=(2, 2), sticky="ew")

        self.speed_lbl = ctk.CTkLabel(self.frame, text="–", width=120, anchor="w")
        self.speed_lbl.grid(row=2, column=0, padx=8, pady=(0, 5), sticky="w")

        self.eta_lbl = ctk.CTkLabel(self.frame, text="ETA –", width=140, anchor="w")
        self.eta_lbl.grid(row=2, column=1, padx=8, pady=(0, 5), sticky="w")

        self.frame.grid_columnconfigure(0, weight=1)

    def set_visible(self, visible: bool):
        if visible:
            self.frame.pack(fill="x", pady=3, padx=6)
        else:
            self.frame.pack_forget()
