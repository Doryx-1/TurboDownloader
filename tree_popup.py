import os
from urllib.parse import unquote

import customtkinter as ctk


# Extensions → icônes
_VIDEO_EXTS    = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"}
_SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
_IMAGE_EXTS    = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}
_NFO_EXTS      = {".nfo"}


def _file_icon(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in _VIDEO_EXTS:
        return "🎬 "
    if ext in _SUBTITLE_EXTS:
        return "📄 "
    if ext in _IMAGE_EXTS:
        return "🖼 "
    if ext in _NFO_EXTS:
        return "📋 "
    return "📎 "


class FileTreeNode:
    """Nœud de l'arbre : peut être un dossier ou un fichier."""

    def __init__(self, name: str, is_dir: bool, parent=None):
        self.name      = name
        self.is_dir    = is_dir
        self.parent    = parent
        self.children: list["FileTreeNode"] = []
        self.file_url  = ""
        self.rel_dir   = ""
        self.var       = None           # ctk.BooleanVar — assigné dans _create_row
        self.depth     = 0
        self._propagating = False
        # UI refs — remplis dans _build_ui
        self._row_frame  = None         # frame-ligne dans le scroll
        self._expand_btn = None         # bouton ▼/▶ (dossiers uniquement)
        self._expanded   = True


class FileTreePopup(ctk.CTkToplevel):
    """Popup de sélection — liste PLATE dans le scroll (pas de frames imbriqués)."""

    def __init__(self, master, files: list, on_confirm):
        """
        files      : liste de (file_url, rel_dir) retournée par get_all_files
        on_confirm : callback(selected_files: list[(url, rel_dir)])
        """
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
        ctk.CTkButton(btn_bar, text="Tout cocher",   width=140,
                      command=self._check_all).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_bar, text="Tout décocher", width=140,
                      command=self._uncheck_all).pack(side="left")

        self._scroll = ctk.CTkScrollableFrame(self)
        self._scroll.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        # Une ligne par nœud, directement dans _scroll (liste plate)
        for node in self._all_nodes:
            self._create_row(node)

        self._refresh_count()

        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkButton(bot, text="Annuler", width=120, fg_color="#5a5a5a",
                      command=self.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(bot, text="Lancer le téléchargement", fg_color="#1f6aa5",
                      command=self._confirm).pack(side="right")

    def _create_row(self, node: FileTreeNode):
        """Crée une ligne plate pour ce nœud, directement dans _scroll."""
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

        icon = "📁 " if node.is_dir else _file_icon(node.name)
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
        """Repackage les descendants du nœud togglé à leur position DFS exacte.

        Stratégie : on cherche dans _all_nodes le premier nœud visible qui suit
        tout le sous-arbre — il est déjà packé et sert d'ancre pour pack(before=).
        Ainsi les descendants réinsérés arrivent juste avant lui, pas en bas de liste.
        """
        descendants = self._get_descendants(toggled_node)
        if not descendants:
            return

        # Ensemble des indices DFS des descendants pour les retrouver rapidement
        desc_set = set(id(n) for n in descendants)

        # Trouver l'index DFS du dernier descendant dans _all_nodes
        last_desc_idx = max(
            i for i, n in enumerate(self._all_nodes) if id(n) in desc_set
        )

        # Premier nœud visible APRÈS le sous-arbre complet → ancre d'insertion
        anchor = None
        for n in self._all_nodes[last_desc_idx + 1:]:
            if self._is_visible(n):
                anchor = n
                break

        # 1. Dépaqueter tous les descendants
        for node in descendants:
            node._row_frame.pack_forget()

        # 2. Repaqueter les visibles dans l'ordre DFS, ancrés avant le successeur
        visible = [n for n in descendants if self._is_visible(n)]
        for i, node in enumerate(visible):
            if anchor is not None:
                node._row_frame.pack(fill="x", pady=0, before=anchor._row_frame)
            else:
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