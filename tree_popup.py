import os
from urllib.parse import unquote

import customtkinter as ctk


# Extensions → icons
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
    """Tree node: can be a folder or a file."""

    def __init__(self, name: str, is_dir: bool, parent=None):
        self.name      = name
        self.is_dir    = is_dir
        self.parent    = parent
        self.children: list["FileTreeNode"] = []
        self.file_url  = ""
        self.rel_dir   = ""
        self.var       = None           # ctk.BooleanVar — assigné in _create_row
        self.depth     = 0
        self._propagating = False
        # UI refs — remplis in _build_ui
        self._row_frame  = None         # frame-ligne in le scroll
        self._expand_btn = None         # bouton ▼/▶ (dossiers uniquement)
        self._expanded   = True
        self._search_hidden = False     # hidden by the search filter


class FileTreePopup(ctk.CTkToplevel):
    """Popup de sélection — liste PLATE in le scroll (pas de frames imbriqués)."""

    def __init__(self, master, files: list, on_confirm, default_dest: str = "", keep_tree: bool = True):
        """
        files        : list of (file_url, rel_dir) returned by get_all_files
        on_confirm   : callback(selected_files: list[(url, rel_dir)], dest_path: str)
        default_dest : pre-filled destination path from settings
        """
        super().__init__(master)
        self.title("Select files to download")
        self.geometry("800x680")
        self.resizable(True, True)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: self._cancel())

        self._on_confirm   = on_confirm
        self._default_dest = default_dest
        self._keep_tree    = keep_tree
        self._root_nodes: list[FileTreeNode] = []
        self._all_nodes:  list[FileTreeNode] = []   # ordre DFS complet
        self._all_file_nodes: list[FileTreeNode] = []
        self._sort_mode: str = "none"   # "none" | "az" | "za" | "dir"

        self._build_tree(files)
        self._build_ui()

    # ---------------------------------------------------------------- Logical tree

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

        # Compute depth + flat DFS order
        def dfs(nodes, depth):
            for n in nodes:
                n.depth = depth
                self._all_nodes.append(n)
                if n.is_dir:
                    dfs(n.children, depth + 1)
        dfs(self._root_nodes, 0)

        # Save original order so it can be restored after sorting
        self._original_order: dict[int, list] = {}   # id(node) → children in ordre original
        def save_order(nodes):
            self._original_root_order = list(nodes)
            for n in nodes:
                if n.is_dir:
                    self._original_order[id(n)] = list(n.children)
                    save_order(n.children)
        save_order(self._root_nodes)

    # ---------------------------------------------------------------- UI

    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(top, text="Select files to download",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self._count_lbl = ctk.CTkLabel(top, text="", text_color="gray")
        self._count_lbl.pack(side="right")

        self._keep_tree_var = ctk.BooleanVar(value=self._keep_tree)
        ctk.CTkCheckBox(top, text="Keep folder structure",
                        variable=self._keep_tree_var,
                        font=ctk.CTkFont(size=12),
                        command=self._on_keep_tree_toggle).pack(side="right", padx=(0, 16))

        # ── Search bar ───────────────────────────────────────────────────
        search_bar = ctk.CTkFrame(self, fg_color="transparent")
        search_bar.pack(fill="x", padx=14, pady=(0, 4))
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._on_search())
        search_entry = ctk.CTkEntry(
            search_bar,
            placeholder_text="🔍  Search for a file or folder…",
            textvariable=self._search_var,
        )
        search_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            search_bar, text="✕", width=32,
            fg_color="transparent", border_width=1,
            command=self._clear_search,
        ).pack(side="left")

        btn_bar = ctk.CTkFrame(self, fg_color="transparent")
        btn_bar.pack(fill="x", padx=14, pady=(0, 6))
        ctk.CTkButton(btn_bar, text="Check all",   width=140,
                      command=self._check_all).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_bar, text="Uncheck all", width=140,
                      command=self._uncheck_all).pack(side="left", padx=(0, 20))

        # ── Sort buttons ─────────────────────────────────────────────────
        ctk.CTkLabel(btn_bar, text="Sort:", text_color="gray").pack(side="left", padx=(0, 4))

        self._sort_btns: dict[str, ctk.CTkButton] = {}
        sort_defs = [
            ("az",  "Name A→Z"),
            ("za",  "Name Z→A"),
            ("dir", "Folders first"),
        ]
        for key, label in sort_defs:
            btn = ctk.CTkButton(
                btn_bar, text=label, width=130,
                fg_color="transparent", border_width=1,
                command=lambda k=key: self._apply_sort(k),
            )
            btn.pack(side="left", padx=3)
            self._sort_btns[key] = btn

        self._scroll = ctk.CTkScrollableFrame(self)
        self._scroll.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        # Une ligne par nœud, directement in _scroll (liste plate)
        for node in self._all_nodes:
            self._create_row(node)

        self._refresh_count()

        # ── Destination bar ───────────────────────────────────────────────────
        dest_bar = ctk.CTkFrame(self, fg_color="#232323")
        dest_bar.pack(fill="x", padx=0, pady=0)

        ctk.CTkLabel(dest_bar, text="📁  Destination:",
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=(14, 6), pady=10)

        self._dest_entry = ctk.CTkEntry(dest_bar, placeholder_text="Choose a folder…")
        if self._default_dest:
            self._dest_entry.insert(0, self._default_dest)
        self._dest_entry.pack(side="left", expand=True, fill="x", padx=(0, 8), pady=10)

        ctk.CTkButton(dest_bar, text="Browse…", width=90,
                      command=self._browse_dest).pack(side="left", padx=(0, 14), pady=10)

        # ── Action buttons ────────────────────────────────────────────────────
        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", padx=14, pady=(6, 12))
        ctk.CTkButton(bot, text="Cancel", width=120, fg_color="#5a5a5a",
                      command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(bot, text="⬇  Start download", fg_color="#1f6aa5",
                      command=self._confirm).pack(side="right")

    def _create_row(self, node: FileTreeNode):
        """Crée une ligne plate pour ce nœud, directement in _scroll."""
        node.var = ctk.BooleanVar(value=True)

        row = ctk.CTkFrame(self._scroll, fg_color="transparent", height=30)
        row.pack(fill="x", pady=0)
        row.pack_propagate(False)   # hauteur fixe → pas d'espace résiduel
        node._row_frame = row

        indent_px = node.depth * 24  # indentation in pixels

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
            # Invisible spacer for alignment
            spacer = ctk.CTkLabel(row, text="", width=indent_px + 28, height=24)
            spacer.pack(side="left")

        icon = "📁 " if node.is_dir else _file_icon(node.name)
        dir_color = self._dir_text_color()
        cb = ctk.CTkCheckBox(
            row,
            text=f"{icon}{node.name}",
            variable=node.var,
            font=ctk.CTkFont(size=12, weight="bold" if node.is_dir else "normal"),
            text_color=dir_color if node.is_dir else ctk.ThemeManager.theme["CTkLabel"]["text_color"],
            command=lambda n=node: self._on_check(n),
        )
        cb.pack(side="left", padx=4)
        node._checkbox = cb

    # ---------------------------------------------------------------- Sorting

    def _apply_sort(self, mode: str):
        """Sorts the tree by the requested mode, then repacks all rows."""
        # Toggle: clicking the same mode twice resets to "none" (original order)
        if self._sort_mode == mode:
            mode = "none"

        self._sort_mode = mode

        # Update button visual state
        for k, btn in self._sort_btns.items():
            btn.configure(fg_color="#1f6aa5" if k == mode else "transparent")

        # Recursively sort children of each node + _root_nodes
        self._sort_nodes(self._root_nodes, mode)

        # Reconstruire _all_nodes en DFS in le nouvel ordre
        self._all_nodes.clear()
        def dfs(nodes):
            for n in nodes:
                self._all_nodes.append(n)
                if n.is_dir:
                    dfs(n.children)
        dfs(self._root_nodes)

        # Repacker toutes les lignes in le nouvel ordre DFS
        # On dépaque tout d'abord, puis on repaque in l'ordre (en respectant la visibilité)
        self._repack_all()

    def _sort_nodes(self, nodes: list, mode: str):
        """Recursive in-place sort of the node list and their children."""
        if mode == "none":
            # Restore original order from the saved map
            # Always apply from _root_nodes to cover all levels
            self._root_nodes[:] = list(self._original_root_order)

            def restore(node_list):
                for n in node_list:
                    if n.is_dir and id(n) in self._original_order:
                        n.children[:] = list(self._original_order[id(n)])
                        restore(n.children)

            restore(self._root_nodes)
            return

        if mode == "az":
            nodes.sort(key=lambda n: n.name.lower())
        elif mode == "za":
            nodes.sort(key=lambda n: n.name.lower(), reverse=True)
        elif mode == "dir":
            nodes.sort(key=lambda n: (0 if n.is_dir else 1, n.name.lower()))

        for node in nodes:
            if node.is_dir and node.children:
                self._sort_nodes(node.children, mode)

    # ---------------------------------------------------------------- Search

    def _on_search(self):
        query = self._search_var.get().strip().lower()

        if not query:
            # Tout réafficher — remettre _search_hidden à False partout
            for node in self._all_nodes:
                node._search_hidden = False
        else:
            # 1. Mark all nodes as hidden by default
            for node in self._all_nodes:
                node._search_hidden = True

            # 2. For each file (leaf) whose name matches: make it visible
            #    + walk up the parent chain to make them visible too
            for node in self._all_nodes:
                if not node.is_dir and query in node.name.lower():
                    node._search_hidden = False
                    p = node.parent
                    while p:
                        p._search_hidden = False
                        p = p.parent

            # 3. Folders whose NAME matches are also visible (with all their content)
            for node in self._all_nodes:
                if node.is_dir and query in node.name.lower():
                    self._show_subtree(node)

        # Repack all rows according to the new visibility
        self._repack_all()

    def _show_subtree(self, node: FileTreeNode):
        """Makes a node and all its descendants visible (for folders whose name matches)."""
        node._search_hidden = False
        # Walk up parents too
        p = node.parent
        while p:
            p._search_hidden = False
            p = p.parent
        # Walk down children
        for child in node.children:
            child._search_hidden = False
            if child.is_dir:
                self._show_subtree(child)

    def _clear_search(self):
        self._search_var.set("")

    def _repack_all(self):
        """Dépaque tout puis repaque in l'ordre DFS en respectant _is_visible()."""
        for node in self._all_nodes:
            node._row_frame.pack_forget()
        for node in self._all_nodes:
            if self._is_visible(node):
                node._row_frame.pack(fill="x", pady=0)

    # ---------------------------------------------------------------- Expand / collapse

    def _toggle_expand(self, node: FileTreeNode):
        node._expanded = not node._expanded
        node._expand_btn.configure(text="▼" if node._expanded else "▶")
        self._apply_visibility_subtree(node)

    def _get_descendants(self, node: FileTreeNode) -> list:
        """Returns all descendants of a node in DFS order."""
        result = []
        for child in node.children:
            result.append(child)
            if child.is_dir:
                result.extend(self._get_descendants(child))
        return result

    def _is_visible(self, node: FileTreeNode) -> bool:
        """A node is visible if: not hidden by search AND all ancestors are expanded."""
        if node._search_hidden:
            return False
        p = node.parent
        while p:
            if not p._expanded or p._search_hidden:
                return False
            p = p.parent
        return True

    def _apply_visibility_subtree(self, toggled_node: FileTreeNode):
        """Repackage les descendants du nœud togglé à leur position DFS exacte.

        Stratégie : on cherche in _all_nodes le premier nœud visible qui suit
        tout le sous-arbre — il est déjà packé et sert d'ancre pour pack(before=).
        Ainsi les descendants réinsérés arrivent juste avant lui, pas en bas de liste.
        """
        descendants = self._get_descendants(toggled_node)
        if not descendants:
            return

        # Set of DFS indices of descendants for fast lookup
        desc_set = set(id(n) for n in descendants)

        # Trouver l'index DFS du dernier descendant in _all_nodes
        last_desc_idx = max(
            i for i, n in enumerate(self._all_nodes) if id(n) in desc_set
        )

        # First visible node AFTER the entire subtree → insertion anchor
        anchor = None
        for n in self._all_nodes[last_desc_idx + 1:]:
            if self._is_visible(n):
                anchor = n
                break

        # 1. Unpack all descendants
        for node in descendants:
            node._row_frame.pack_forget()

        # 2. Repaqueter les visibles in l'ordre DFS, ancrés avant le successeur
        visible = [n for n in descendants if self._is_visible(n)]
        for i, node in enumerate(visible):
            if anchor is not None:
                node._row_frame.pack(fill="x", pady=0, before=anchor._row_frame)
            else:
                node._row_frame.pack(fill="x", pady=0)

    # ---------------------------------------------------------------- Checkboxes

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

    # ---------------------------------------------------------------- Counter

    def _refresh_count(self):
        total   = len(self._all_file_nodes)
        checked = sum(1 for n in self._all_file_nodes if n.var.get())
        self._count_lbl.configure(text=f"{checked} / {total} file(s) selected")

    # ---------------------------------------------------------------- Confirmation

    def _dir_text_color(self):
        """Returns folder label color depending on keep_tree toggle."""
        return "#dddddd" if self._keep_tree_var.get() else "#555555"

    def _on_keep_tree_toggle(self):
        """Refresh folder label colors when keep_tree is toggled."""
        color = self._dir_text_color()
        for node in self._all_nodes:
            if node.is_dir and node._checkbox:
                node._checkbox.configure(text_color=color)

    def _browse_dest(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(title="Choose destination folder")
        if folder:
            self._dest_entry.delete(0, "end")
            self._dest_entry.insert(0, folder)

    def _cancel(self):
        """Close without selection — unblocks popup_done."""
        self.destroy()
        self._on_confirm([], "", False)

    def _confirm(self):
        selected   = [(n.file_url, n.rel_dir) for n in self._all_file_nodes if n.var.get()]
        dest_path  = self._dest_entry.get().strip()
        keep_tree  = self._keep_tree_var.get()
        self.destroy()
        self._on_confirm(selected, dest_path, keep_tree)