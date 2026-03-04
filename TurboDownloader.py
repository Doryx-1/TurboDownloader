import os
import time
import queue
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, List

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, unquote

import customtkinter as ctk
from tkinter import filedialog

from concurrent.futures import ThreadPoolExecutor


VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".mov", ".wmv")
CHUNK_SIZE = 1024 * 512  # 512 KB – bon compromis latence/débit

# ---------------------------------------------------------------
# État possible : waiting / downloading / done / error / canceled / skipped
# ---------------------------------------------------------------

# -------------------- Model --------------------
@dataclass
class DownloadItem:
    url: str
    filename: str
    dest_path: str
    relative_path: str = ""          # sous-dossier relatif (arbo. originale)

    total_size: Optional[int] = None
    downloaded: int = 0
    resume_from: int = 0             # offset reprise

    started_at: float = field(default_factory=time.time)
    speed_window: deque = field(default_factory=lambda: deque(maxlen=10))
    # chaque entrée : (timestamp, bytes_depuis_dernier_sample)

    cancel_event: threading.Event = field(default_factory=threading.Event)

    # state: waiting / downloading / done / error / canceled / skipped
    state: str = "waiting"
    error_msg: str = ""


# -------------------- UI Row --------------------
class DownloadRow:
    def __init__(self, parent, name: str, on_cancel, on_remove):
        self.frame = ctk.CTkFrame(parent)
        self.frame.pack(fill="x", pady=3, padx=6)

        self.name_lbl = ctk.CTkLabel(self.frame, text=name, width=400, anchor="w")
        self.name_lbl.grid(row=0, column=0, padx=8, pady=(5, 2), sticky="w")

        self.status = ctk.CTkLabel(self.frame, text="Waiting", width=150, anchor="w")
        self.status.grid(row=0, column=1, padx=8, pady=(5, 2), sticky="w")

        self.cancel_btn = ctk.CTkButton(self.frame, text="⏸ Pause/Cancel", width=120, command=on_cancel)
        self.cancel_btn.grid(row=0, column=2, padx=6, pady=(5, 2))

        self.remove_btn = ctk.CTkButton(self.frame, text="✕ Remove", width=90, command=on_remove, state="disabled")
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



# -------------------- Popup arborescence --------------------
class FileTreeNode:
    """Nœud de l'arbre : peut être un dossier ou un fichier."""
    def __init__(self, name: str, is_dir: bool, parent=None):
        self.name      = name
        self.is_dir    = is_dir
        self.parent    = parent
        self.children: list["FileTreeNode"] = []
        self.file_url  = ""
        self.rel_dir   = ""
        self.var       = None          # ctk.BooleanVar
        self.depth     = 0
        self._propagating = False
        # UI refs (remplis dans _build_ui)
        self._row_frame   = None       # le frame-ligne dans le scroll
        self._expand_btn  = None       # bouton ▼/▶ (dossiers)
        self._expanded    = True


class FileTreePopup(ctk.CTkToplevel):
    """Popup de sélection – liste PLATE dans le scroll (pas de frames imbriqués)."""

    def __init__(self, master, files: list, on_confirm):
        super().__init__(master)
        self.title("Sélection des fichiers à télécharger")
        self.geometry("760x620")
        self.resizable(True, True)
        self.grab_set()

        self._on_confirm      = on_confirm
        self._root_nodes: list[FileTreeNode] = []
        self._all_nodes:  list[FileTreeNode] = []   # ordre DFS complet
        self._all_file_nodes: list[FileTreeNode] = []

        self._build_tree(files)
        self._build_ui()

    # ---------------------------------------------------------------- Arbre logique
    def _build_tree(self, files):
        dir_map: dict[str, FileTreeNode] = {}

        def get_or_create_dir(path: str) -> FileTreeNode:
            if path in dir_map:
                return dir_map[path]
            parts = path.replace("\\", "/").split("/")
            cur = ""
            par = None
            for part in parts:
                if not part:
                    continue
                cur = (cur + "/" + part).lstrip("/")
                if cur not in dir_map:
                    node = FileTreeNode(part, is_dir=True)
                    dir_map[cur] = node
                    if par is None:
                        self._root_nodes.append(node)
                    else:
                        node.parent = par
                        par.children.append(node)
                par = dir_map[cur]
            return dir_map[path]

        for file_url, rel_dir in files:
            name = unquote(os.path.basename(file_url.split("?")[0]) or "file.bin")
            fn = FileTreeNode(name, is_dir=False)
            fn.file_url = file_url
            fn.rel_dir  = rel_dir
            if rel_dir:
                dn = get_or_create_dir(rel_dir)
                fn.parent = dn
                dn.children.append(fn)
            else:
                self._root_nodes.append(fn)
            self._all_file_nodes.append(fn)

        # Calculer depth + ordre DFS plat
        def dfs(nodes, depth):
            for n in nodes:
                n.depth = depth
                self._all_nodes.append(n)
                if n.is_dir:
                    dfs(n.children, depth + 1)
        dfs(self._root_nodes, 0)

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(top, text="Sélectionnez les fichiers à télécharger",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self._count_lbl = ctk.CTkLabel(top, text="", text_color="gray")
        self._count_lbl.pack(side="right")

        btn_bar = ctk.CTkFrame(self, fg_color="transparent")
        btn_bar.pack(fill="x", padx=14, pady=(0, 6))
        ctk.CTkButton(btn_bar, text="☑ Tout cocher",   width=140,
                      command=self._check_all).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_bar, text="☐ Tout décocher", width=140,
                      command=self._uncheck_all).pack(side="left")

        self._scroll = ctk.CTkScrollableFrame(self)
        self._scroll.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        # Créer UNE ligne par nœud, directement dans self._scroll (liste plate)
        for node in self._all_nodes:
            self._create_row(node)

        self._refresh_count()

        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkButton(bot, text="✕ Annuler", width=120, fg_color="#5a5a5a",
                      command=self.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(bot, text="🚀 Lancer le téléchargement", fg_color="#1f6aa5",
                      command=self._confirm).pack(side="right")

    def _create_row(self, node: FileTreeNode):
        """Crée une ligne plate pour ce nœud, empaquetée directement dans _scroll."""
        node.var = ctk.BooleanVar(value=True)

        row = ctk.CTkFrame(self._scroll, fg_color="transparent", height=30)
        row.pack(fill="x", pady=0)
        row.pack_propagate(False)   # hauteur fixe → pas d'espace résiduel
        node._row_frame = row

        indent_px = node.depth * 24

        if node.is_dir:
            expand_btn = ctk.CTkButton(
                row, text="▼", width=24, height=24,
                fg_color="transparent", hover_color="#3a3a3a",
                font=ctk.CTkFont(size=10),
                command=lambda n=node: self._toggle_expand(n),
            )
            expand_btn.pack(side="left", padx=(indent_px + 4, 0))
            node._expand_btn = expand_btn
        else:
            # Spacer invisible pour l'alignement
            spacer = ctk.CTkLabel(row, text="", width=indent_px + 28, height=24)
            spacer.pack(side="left")

        icon = "📁 " if node.is_dir else "🎬 "
        cb = ctk.CTkCheckBox(
            row,
            text=f"{icon}{node.name}",
            variable=node.var,
            font=ctk.CTkFont(size=12, weight="bold" if node.is_dir else "normal"),
            command=lambda n=node: self._on_check(n),
        )
        cb.pack(side="left", padx=4)
        node._checkbox = cb

    # ---------------------------------------------------------------- Expand / collapse
    def _toggle_expand(self, node: FileTreeNode):
        node._expanded = not node._expanded
        node._expand_btn.configure(text="▼" if node._expanded else "▶")
        # Optimisation : on ne repackage que les descendants de ce nœud,
        # pas toute la liste — évite le flash sur les grosses arborescences
        self._apply_visibility_subtree(node)

    def _get_descendants(self, node: FileTreeNode) -> list:
        """Retourne tous les descendants d'un nœud en ordre DFS."""
        result = []
        for child in node.children:
            result.append(child)
            if child.is_dir:
                result.extend(self._get_descendants(child))
        return result

    def _is_visible(self, node: FileTreeNode) -> bool:
        """Un nœud est visible si tous ses ancêtres sont expanded."""
        p = node.parent
        while p:
            if not p._expanded:
                return False
            p = p.parent
        return True

    def _apply_visibility_subtree(self, toggled_node: FileTreeNode):
        """Repackage uniquement les descendants du nœud togglé.
        Les autres lignes ne sont pas touchées → pas de flash global.
        Stratégie : unpack descendants → repack les visibles dans l'ordre DFS.
        """
        descendants = self._get_descendants(toggled_node)
        if not descendants:
            return

        # 1. Dépaqueter tous les descendants
        for node in descendants:
            node._row_frame.pack_forget()

        # 2. Repaqueter uniquement les visibles dans l'ordre DFS
        for node in descendants:
            if self._is_visible(node):
                node._row_frame.pack(fill="x", pady=0)

    # ---------------------------------------------------------------- Coches
    def _on_check(self, node: FileTreeNode):
        if node._propagating:
            return
        self._set_subtree(node, node.var.get())
        self._update_parents(node)
        self._refresh_count()

    def _set_subtree(self, node: FileTreeNode, val: bool):
        node._propagating = True
        node.var.set(val)
        node._propagating = False
        for child in node.children:
            self._set_subtree(child, val)

    def _update_parents(self, node: FileTreeNode):
        p = node.parent
        while p:
            all_checked = all(c.var.get() for c in p.children)
            p._propagating = True
            p.var.set(all_checked)
            p._propagating = False
            p = p.parent

    def _check_all(self):
        for n in self._root_nodes:
            self._set_subtree(n, True)
        self._refresh_count()

    def _uncheck_all(self):
        for n in self._root_nodes:
            self._set_subtree(n, False)
        self._refresh_count()

    # ---------------------------------------------------------------- Compteur
    def _refresh_count(self):
        total   = len(self._all_file_nodes)
        checked = sum(1 for n in self._all_file_nodes if n.var.get())
        self._count_lbl.configure(text=f"{checked} / {total} fichier(s) sélectionné(s)")

    # ---------------------------------------------------------------- Confirmation
    def _confirm(self):
        selected = [(n.file_url, n.rel_dir) for n in self._all_file_nodes if n.var.get()]
        self.destroy()
        self._on_confirm(selected)


# -------------------- App --------------------
class TurboDownloader(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("TurboDownloader v2")
        self.geometry("1360x860")

        self.uiq: "queue.Queue[tuple]" = queue.Queue()

        self.req = requests.Session()
        self.req.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "*/*",
            "Connection": "keep-alive",
        })

        self.download_path: Optional[str] = None
        self.items: List[DownloadItem] = []
        self.rows: dict[int, DownloadRow] = {}
        self.executor: Optional[ThreadPoolExecutor] = None
        self.stop_all_event = threading.Event()

        # Vitesse globale : fenêtre glissante sur 3 s
        self._speed_lock = threading.Lock()
        self._speed_samples: deque = deque()  # (timestamp, bytes)
        self._global_total_bytes = 0

        # Filtre actif
        self._active_filter = "all"

        self._build_ui()
        self.after(80, self._process_ui_queue)
        self.after(1000, self._tick_global_speed)

    # ------------------------------------------------------------------ UI --
    def _build_ui(self):
        main = ctk.CTkFrame(self)
        main.pack(fill="both", expand=True, padx=10, pady=10)

        left = ctk.CTkFrame(main, width=370)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)

        right = ctk.CTkFrame(main)
        right.pack(side="right", fill="both", expand=True)

        # ---- Panneau gauche ----
        ctk.CTkLabel(left, text="URL du répertoire").pack(anchor="w", padx=12, pady=(12, 0))
        self.url_entry = ctk.CTkEntry(left, width=340)
        self.url_entry.pack(padx=12, pady=6)

        ctk.CTkButton(left, text="📁 Choisir dossier de destination", command=self.choose_folder).pack(padx=12, pady=(4, 2), fill="x")
        self.folder_label = ctk.CTkLabel(left, text="Dossier: (non choisi)", wraplength=340, justify="left", text_color="gray")
        self.folder_label.pack(anchor="w", padx=12, pady=(0, 8))

        # Arborescence originale
        self.keep_tree_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(left, text="Conserver l'arborescence originale", variable=self.keep_tree_var).pack(anchor="w", padx=12, pady=(0, 10))

        ctk.CTkLabel(left, text="Téléchargements simultanés (1–20)").pack(anchor="w", padx=12)
        self.worker_entry = ctk.CTkEntry(left, width=80)
        self.worker_entry.insert(0, "8")
        self.worker_entry.pack(anchor="w", padx=12, pady=6)

        # Vitesse globale
        self.global_speed_label = ctk.CTkLabel(left, text="Vitesse globale: –", font=ctk.CTkFont(size=14, weight="bold"))
        self.global_speed_label.pack(anchor="w", padx=12, pady=(6, 2))

        self.global_dl_label = ctk.CTkLabel(left, text="Total téléchargé: 0 MB", text_color="gray")
        self.global_dl_label.pack(anchor="w", padx=12, pady=(0, 10))

        # Boutons START / STOP
        btn_row = ctk.CTkFrame(left, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(4, 4))
        self.start_btn = ctk.CTkButton(btn_row, text="🚀 START", command=self.start_downloads, fg_color="#1f6aa5")
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.stop_btn = ctk.CTkButton(btn_row, text="⛔ STOP ALL", command=self.stop_all, fg_color="#8B0000")
        self.stop_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))

        ctk.CTkButton(left, text="🧹 Effacer terminés/annulés", command=self.clear_finished).pack(fill="x", padx=12, pady=(6, 4))

        ctk.CTkLabel(left, text="TurboDownloader v2 • © Thomas PIERRE",
                     font=ctk.CTkFont(size=11), text_color="gray").pack(side="bottom", anchor="sw", padx=12, pady=8)

        # ---- Panneau droit ----
        # Header avec filtres
        top_bar = ctk.CTkFrame(right, fg_color="transparent")
        top_bar.pack(fill="x", padx=10, pady=(10, 4))

        ctk.CTkLabel(top_bar, text="Téléchargements", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")

        # Compteurs par état
        filter_frame = ctk.CTkFrame(top_bar, fg_color="transparent")
        filter_frame.pack(side="right")

        self._filter_btns = {}
        filters = [
            ("all",        "Tous",       "#1f6aa5"),
            ("downloading","En cours",   "#2e8b57"),
            ("waiting",    "En attente", "#5a5a5a"),
            ("done",       "Terminés",   "#2e8b57"),
            ("canceled",   "Annulés",    "#8B4513"),
            ("error",      "Erreurs",    "#8B0000"),
        ]
        for fkey, flabel, fcolor in filters:
            btn = ctk.CTkButton(
                filter_frame, text=f"{flabel} (0)", width=110,
                fg_color=fcolor if fkey == "all" else "transparent",
                border_width=1, border_color=fcolor,
                command=lambda k=fkey: self._set_filter(k)
            )
            btn.pack(side="left", padx=3)
            self._filter_btns[fkey] = btn

        self.scroll = ctk.CTkScrollableFrame(right)
        self.scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ---------------------------------------------------------------- Helpers
    def ui(self, fn, *args, **kwargs):
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
        """Appel synchrone depuis un thread background → UI thread."""
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
        ev.wait()
        if box["e"]:
            raise box["e"]
        return box["v"]

    def choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.download_path = folder
            self.folder_label.configure(text=f"📂 {folder}", text_color="white")

    # ----------------------------------------------------------------- Crawl
    def get_all_files(self, url: str, base_url: str = None):
        """Scrape récursivement et retourne liste de (file_url, relative_path)."""
        if base_url is None:
            base_url = url
        results = []
        try:
            r = self.req.get(url, timeout=30, allow_redirects=True)
            r.raise_for_status()
        except Exception as e:
            print("[crawl]", e)
            return results

        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if not href or href in ("../", "./", "/"):
                continue
            full = urljoin(url, href)
            # Rester dans le sous-arbre de l'URL de base
            if not full.startswith(base_url.rstrip("/") + "/") and full != base_url:
                if href.startswith("http"):
                    continue
            if href.endswith("/"):
                results.extend(self.get_all_files(full, base_url))
            elif href.lower().endswith(VIDEO_EXTENSIONS):
                # Chemin relatif par rapport à la base
                rel = full[len(base_url.rstrip("/")):]
                rel = rel.lstrip("/")
                rel_dir = os.path.dirname(rel)
                results.append((full, rel_dir))

        # déduplique
        seen = set()
        unique = []
        for item in results:
            if item[0] not in seen:
                seen.add(item[0])
                unique.append(item)
        return unique

    # ------------------------------------------------- Row management
    def _add_row_for_item(self, idx: int, item: DownloadItem):
        row = DownloadRow(
            self.scroll, item.filename,
            on_cancel=lambda i=idx: self.cancel_one(i),
            on_remove=lambda i=idx: self.remove_one(i),
        )
        self.rows[idx] = row
        # Appliquer filtre courant
        self._apply_filter_to_row(idx)

    def cancel_one(self, idx: int):
        if 0 <= idx < len(self.items):
            it = self.items[idx]
            it.cancel_event.set()
            row = self.rows.get(idx)
            if row and it.state in ("waiting", "downloading"):
                row.status.configure(text="Annulation…")

    def remove_one(self, idx: int):
        row = self.rows.get(idx)
        if not row:
            return
        it = self.items[idx]
        if it.state not in ("done", "error", "canceled", "skipped"):
            return
        row.frame.destroy()
        del self.rows[idx]
        # Retirer aussi de self.items pour que les compteurs soient corrects
        # On marque l'item comme "removed" plutôt que de changer les indices
        self.items[idx] = None
        self._refresh_filter_counts()

    def clear_finished(self):
        for idx in list(self.rows.keys()):
            it = self.items[idx]
            if it and it.state in ("done", "error", "canceled", "skipped"):
                self.remove_one(idx)

    # ------------------------------------------------ Format
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

    # -------------------------------------------- Update row UI
    def _update_row_ui(self, idx: int):
        it = self.items[idx]
        row = self.rows.get(idx)
        if not row:
            return

        state_labels = {
            "waiting":     "⏳ En attente",
            "downloading": "⬇️ En cours",
            "done":        "✅ Terminé",
            "error":       f"❌ Erreur: {it.error_msg[:40]}",
            "canceled":    "⏹ Annulé",
            "skipped":     "⏭ Existant (ignoré)",
        }
        row.status.configure(text=state_labels.get(it.state, it.state))

        # Vitesse instantanée via fenêtre glissante
        now = time.time()
        win = it.speed_window
        # Purger les samples > 4s
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
            # Afficher taille dans le label de nom
            done_b = it.resume_from + it.downloaded
            row.name_lbl.configure(
                text=f"{it.filename}  [{self._fmt_size(done_b)} / {self._fmt_size(it.total_size)}]"
            )
        else:
            row.progress.set(0)
            row.eta_lbl.configure(text="ETA –")

        if it.state in ("done", "error", "canceled", "skipped"):
            row.cancel_btn.configure(state="disabled")
            row.remove_btn.configure(state="normal")
            if it.state == "done":
                row.progress.set(1.0)
        else:
            row.cancel_btn.configure(state="normal")
            row.remove_btn.configure(state="disabled")

        # Appliquer visibilité selon filtre actif
        self._apply_filter_to_row(idx)

    # ------------------------------------------ Filtres
    def _set_filter(self, fkey: str):
        self._active_filter = fkey
        for k, btn in self._filter_btns.items():
            active = k == fkey
            colors = {
                "all":        "#1f6aa5",
                "downloading":"#2e8b57",
                "waiting":    "#5a5a5a",
                "done":       "#2e8b57",
                "canceled":   "#8B4513",
                "error":      "#8B0000",
            }
            btn.configure(fg_color=colors[k] if active else "transparent")
        for idx in self.rows:
            self._apply_filter_to_row(idx)

    def _apply_filter_to_row(self, idx: int):
        row = self.rows.get(idx)
        it = self.items[idx] if idx < len(self.items) else None
        if not row or not it:
            return
        if self._active_filter == "all":
            row.set_visible(True)
        else:
            row.set_visible(it.state == self._active_filter)

    def _refresh_filter_counts(self):
        counts = {k: 0 for k in self._filter_btns}
        active_items = [it for it in self.items if it is not None]
        counts["all"] = len(active_items)
        for it in active_items:
            if it.state in counts:
                counts[it.state] += 1
        labels = {
            "all":        "Tous",
            "downloading":"En cours",
            "waiting":    "En attente",
            "done":       "Terminés",
            "canceled":   "Annulés",
            "error":      "Erreurs",
        }
        for k, btn in self._filter_btns.items():
            btn.configure(text=f"{labels[k]} ({counts.get(k, 0)})")

    # ------------------------------------------ Vitesse globale
    def _record_bytes(self, n: int):
        """Appelé depuis le worker à chaque chunk."""
        now = time.time()
        with self._speed_lock:
            self._speed_samples.append((now, n))
            self._global_total_bytes += n
            # Purger > 3s
            while self._speed_samples and now - self._speed_samples[0][0] > 3.0:
                self._speed_samples.popleft()

    def _tick_global_speed(self):
        with self._speed_lock:
            # Purger les samples trop vieux (> 3s)
            now = time.time()
            while self._speed_samples and now - self._speed_samples[0][0] > 3.0:
                self._speed_samples.popleft()
            samples = list(self._speed_samples)
            total = self._global_total_bytes

        # S'il n'y a plus de samples récents → plus aucun DL actif → vitesse = 0
        if len(samples) >= 2:
            window_bytes = sum(s[1] for s in samples)
            window_sec = samples[-1][0] - samples[0][0]
            speed = window_bytes / max(window_sec, 0.1)
        elif samples:
            speed = samples[0][1]
        else:
            speed = 0.0

        speed_text = "–" if speed == 0.0 else self._fmt_speed(speed)
        self.global_speed_label.configure(text=f"Vitesse globale: {speed_text}")
        self.global_dl_label.configure(text=f"Total téléchargé: {self._fmt_size(total)}")
        self.after(1000, self._tick_global_speed)

    # -------------------------------------------- Worker
    def _download_worker(self, idx: int):
        it = self.items[idx]
        it.state = "downloading"
        it.started_at = time.time()
        it.error_msg = ""
        self.ui(self._update_row_ui, idx)

        # ---- Détection fichier existant (reprise) ----
        existing_size = 0
        if os.path.exists(it.dest_path):
            existing_size = os.path.getsize(it.dest_path)

        # Préparer reprise (Range header si fichier partiel)
        it.resume_from = existing_size
        it.downloaded = 0
        headers = {}
        if existing_size > 0:
            headers["Range"] = f"bytes={existing_size}-"

        try:
            with self.req.get(it.url, stream=True, allow_redirects=True,
                              timeout=60, headers=headers) as r:

                # Si le serveur ne supporte pas Range, on repart de 0
                if existing_size > 0 and r.status_code == 200:
                    it.resume_from = 0
                    existing_size = 0

                if r.status_code not in (200, 206):
                    r.raise_for_status()

                ct = (r.headers.get("Content-Type") or "").lower()
                if "text/html" in ct:
                    it.state = "error"
                    it.error_msg = "HTML reçu (lien expiré / auth ?)"
                    self.ui(self._update_row_ui, idx)
                    self.ui(self._refresh_filter_counts)
                    return

                # Lire la taille directement depuis les headers du GET stream
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

                # Fichier déjà complet → skip (détectable maintenant qu'on a la taille)
                if it.total_size and existing_size >= it.total_size:
                    it.state = "skipped"
                    self.ui(self._update_row_ui, idx)
                    self.ui(self._refresh_filter_counts)
                    return

                os.makedirs(os.path.dirname(it.dest_path), exist_ok=True)

                write_mode = "ab" if existing_size > 0 else "wb"
                last_ui = 0.0

                with open(it.dest_path, write_mode) as f:
                    for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                        if self.stop_all_event.is_set() or it.cancel_event.is_set():
                            it.state = "canceled"
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

                        now = time.time()
                        if now - last_ui >= 0.20:
                            last_ui = now
                            self.ui(self._update_row_ui, idx)

                it.state = "done"
                self.ui(self._update_row_ui, idx)
                self.ui(self._refresh_filter_counts)

        except Exception as e:
            it.state = "error"
            it.error_msg = str(e)
            self.ui(self._update_row_ui, idx)
            self.ui(self._refresh_filter_counts)

    # --------------------------------- Start / Stop
    def start_downloads(self):
        if not self.download_path:
            self.folder_label.configure(text="⚠️ Choisis d'abord un dossier de destination", text_color="orange")
            return

        url = self.url_entry.get().strip()
        if not url:
            return

        try:
            workers = int(self.worker_entry.get().strip() or "8")
        except Exception:
            workers = 8
        workers = max(1, min(20, workers))
        self.worker_entry.delete(0, "end")
        self.worker_entry.insert(0, str(workers))

        self.stop_all_event.clear()
        keep_tree = self.keep_tree_var.get()

        # Désactiver le bouton pendant le scan
        self.start_btn.configure(state="disabled", text="🔍 Scan…")

        def crawl_then_popup():
            files = self.get_all_files(url)

            def restore_btn():
                self.start_btn.configure(state="normal", text="🚀 START")

            if not files:
                self.ui(restore_btn)
                self.ui(lambda: self.folder_label.configure(
                    text="⚠️ Aucun fichier vidéo trouvé à cette URL", text_color="orange"))
                return

            # Ouvrir la popup sur le thread UI
            def open_popup():
                restore_btn()
                def on_confirm(selected_files):
                    if not selected_files:
                        return
                    self._launch_downloads(selected_files, workers, keep_tree)

                FileTreePopup(self, files, on_confirm)

            self.ui(open_popup)

        threading.Thread(target=crawl_then_popup, daemon=True).start()

    def _launch_downloads(self, files: list, workers: int, keep_tree: bool):
        """Lance les téléchargements sur la liste sélectionnée (appelé après popup)."""
        self.stop_all_event.clear()

        def init_ui():
            base = self.download_path
            start_idx = len(self.items)
            new_indices = []
            for file_url, rel_dir in files:
                name = unquote(os.path.basename(file_url.split("?")[0]) or "file.bin")
                if keep_tree and rel_dir:
                    dest_dir = os.path.join(base, rel_dir)
                else:
                    dest_dir = base
                dest = os.path.join(dest_dir, name)
                it = DownloadItem(
                    url=file_url,
                    filename=name,
                    dest_path=dest,
                    relative_path=rel_dir,
                )
                self.items.append(it)
                self._add_row_for_item(start_idx, it)
                self._update_row_ui(start_idx)
                new_indices.append(start_idx)
                start_idx += 1
            return new_indices

        def _run():
            nonlocal new_indices
            new_indices = self.ui_call(init_ui)
            self.executor = ThreadPoolExecutor(max_workers=workers)
            for i in new_indices:
                fut = self.executor.submit(self._download_worker, i)
                fut.add_done_callback(lambda f: f.exception() and print("[WORKER]", f.exception()))

        new_indices = []
        threading.Thread(target=_run, daemon=True).start()

    def stop_all(self):
        self.stop_all_event.set()
        for it in self.items:
            if it and it.state in ("waiting", "downloading"):
                it.cancel_event.set()
        ex = self.executor
        if ex:
            try:
                ex.shutdown(wait=False, cancel_futures=False)
            except TypeError:
                ex.shutdown(wait=False)

        def mark():
            for idx, it in enumerate(self.items):
                if it and it.state in ("waiting", "downloading"):
                    it.state = "canceled"
                    self._update_row_ui(idx)
        self.ui(mark)


if __name__ == "__main__":
    app = TurboDownloader()
    app.mainloop()