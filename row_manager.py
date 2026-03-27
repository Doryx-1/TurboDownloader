"""row_manager.py — RowManagerMixin for TurboDownloader."""
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import messagebox
from logger import get_logger
from models import DownloadItem
from widgets import DownloadRow

_log = get_logger("row_manager")


class RowManagerMixin:

    def _add_row_for_item(self, idx: int, item: DownloadItem):
        from widgets import PlaylistGroupRow
        display_name = item.filename
        if item.from_remote:
            display_name = f"📡 {item.filename}"
        row = DownloadRow(
            self.scroll, display_name,
            on_pause=lambda i=idx:    self.pause_one(i),
            on_cancel=lambda i=idx:   self.cancel_one(i),
            on_remove=lambda i=idx:   self.remove_one(i),
            on_priority=lambda i=idx: self.priority_one(i),
            on_context_menu=self._make_context_menu(idx),
        )
        if item.from_remote:
            row.frame.configure(border_color="#1a3a5a")
            row.name_lbl.configure(text_color="#7ab8e8")

        # ── Playlist group ────────────────────────────────────────────────────
        gid = getattr(item, "playlist_group_id", None)
        if gid and getattr(item, "playlist_total", 0) > 1:
            if not hasattr(self, "_playlist_groups"):
                self._playlist_groups: dict = {}
            if gid not in self._playlist_groups:
                title = getattr(item, "playlist_group_title", "") or "Playlist"
                total = getattr(item, "playlist_total", 0)
                def _cancel_all(g=gid):
                    for i, it in list(self.items.items()):
                        if getattr(it, "playlist_group_id", None) == g:
                            self.cancel_one(i)
                def _remove_all(g=gid):
                    for i, it in list(self.items.items()):
                        if getattr(it, "playlist_group_id", None) == g:
                            self.remove_one(i)
                    grp = self._playlist_groups.pop(g, None)
                    if grp:
                        grp.frame.destroy()
                self._playlist_groups[gid] = PlaylistGroupRow(
                    self.scroll, title, total,
                    on_cancel_all=_cancel_all,
                    on_remove_all=_remove_all,
                )
            self._playlist_groups[gid].add_child(idx, row)
        # ─────────────────────────────────────────────────────────────────────

        self.rows[idx] = row
        self._apply_filter_to_row(idx)
        if len(self.rows) > 1:
            self._apply_sort_order()

    def _add_remote_shadow_row(self, url: str, dest: str,
                               server_idx: int = None) -> int:
        """
        Adds a controllable shadow row for a download running on the remote server.
        Controls (pause/resume/cancel/remove) call the server API directly.
        Returns the shadow_idx assigned.
        """
        import os as _os
        name = _os.path.basename(url.split("?")[0]) or url[:60]
        self._shadow_counter -= 1
        shadow_idx = self._shadow_counter

        def _remote_pause():
            si = self._shadow_rows.get(shadow_idx, {}).get("server_idx")
            if si is None:
                return
            if not self._remote_client:
                return
            it_state = self._shadow_rows.get(shadow_idx, {}).get("state", "")
            if it_state == "paused":
                threading.Thread(target=lambda: self._remote_client.resume(si),
                                 daemon=True).start()
            else:
                threading.Thread(target=lambda: self._remote_client.pause(si),
                                 daemon=True).start()

        def _remote_cancel():
            si = self._shadow_rows.get(shadow_idx, {}).get("server_idx")
            if si is None:
                return
            if not self._remote_client:
                return
            threading.Thread(target=lambda: self._remote_client.cancel(si),
                             daemon=True).start()

        def _remote_remove():
            si = self._shadow_rows.get(shadow_idx, {}).get("server_idx")
            if si is not None and self._remote_client:
                threading.Thread(target=lambda: self._remote_client.remove(si),
                                 daemon=True).start()
            self._remove_shadow_row(shadow_idx)

        row = DownloadRow(
            self.scroll, f"📡 {name}",
            on_pause=_remote_pause,
            on_cancel=_remote_cancel,
            on_remove=_remote_remove,
            on_priority=None,
            on_context_menu=self._make_shadow_context_menu(shadow_idx),
        )
        # Buttons active — controls are wired to server API
        row.pause_btn.configure(state="normal")
        row.cancel_btn.configure(state="normal")
        row.remove_btn.configure(state="disabled")
        row.status.configure(text="📡 Sent to server", text_color="#5a9acd")
        row.speed_lbl.configure(text="Remote")
        row.eta_lbl.configure(text=dest[:30] + "…" if len(dest) > 30 else dest)
        row.progress.configure(progress_color="#1a4a7a")

        self._shadow_rows[shadow_idx] = {
            "row":        row,
            "url":        url,
            "server_idx": server_idx,
            "state":      "waiting",
            "created_at": time.time(),   # pour watchdog fantôme
        }
        row.frame.pack(fill="x", pady=3, padx=4)
        self._refresh_filter_counts()
        return shadow_idx

    def _remove_shadow_row(self, shadow_idx: int):
        """Removes a shadow row from the client display."""
        shadow = self._shadow_rows.pop(shadow_idx, None)
        if shadow:
            shadow["row"].frame.destroy()
        self._refresh_filter_counts()

    def _make_shadow_context_menu(self, shadow_idx: int):
        """Context menu for shadow (remote) rows."""
        def handler(event):
            shadow = self._shadow_rows.get(shadow_idx)
            if not shadow:
                return
            menu = tk.Menu(self, tearoff=0, bg="#2a2a2a", fg="#dddddd",
                           activebackground="#1f6aa5", activeforeground="#ffffff",
                           relief="flat", bd=0)
            menu.add_command(
                label="📋  Copier l'URL",
                command=lambda u=shadow["url"]: (
                    self.clipboard_clear(), self.clipboard_append(u)))
            menu.add_separator()
            menu.add_command(
                label="💀  Force remove",
                command=lambda: self._force_remove_shadow(shadow_idx))
            menu.tk_popup(event.x_root, event.y_root)
        return handler

    def _force_remove_shadow(self, shadow_idx: int):
        """Force-removes a shadow row (even if stuck/unreachable)."""
        self._remove_shadow_row(shadow_idx)

    def cancel_one(self, idx: int):
        with self._items_lock:
            it = self.items.get(idx)
        if it is not None:
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

    def priority_one(self, idx: int):
        """Launches a waiting download immediately.
        If all worker slots are taken, requeues the least-advanced downloading item
        back to waiting (via pause_event + _requeue_set), then submits priority first.
        """
        import time
        if idx not in self.items:
            return
        it = self.items[idx]
        if it.state != "waiting":
            return

        try:
            workers = int(self._settings.get("workers", 10))
        except Exception:
            workers = 10

        active = [(i, d) for i, d in self.items.items() if d.state == "downloading"]

        if len(active) >= workers:
            def _completion(item: DownloadItem) -> float:
                if item.total_size and item.total_size > 0:
                    return (item.resume_from + item.downloaded) / item.total_size
                return 0.0

            victim_idx, victim = min(active, key=lambda p: _completion(p[1]))

            def _swap():
                # 1. Mark victim as requeue so the worker exits to "waiting"
                self._requeue_set.add(victim_idx)
                victim.pause_event.set()

                # 2. Poll until worker exited (max 2s)
                for _ in range(40):
                    if victim.state == "waiting":
                        break
                    time.sleep(0.05)

                if idx not in self.items:
                    self._requeue_set.discard(victim_idx)
                    return

                # 3. Clear events on victim (worker already cleared _requeue_set)
                victim.pause_event.clear()
                victim.cancel_event.clear()

                # 4. Clear events on priority item and submit both
                it.cancel_event.clear()
                it.pause_event.clear()

                def _submit():
                    if idx not in self.items:
                        return
                    # Priority first, victim re-queued right after
                    fut_p = self.executor.submit(self._download_worker, idx)
                    fut_p.add_done_callback(
                        lambda f: f.exception() and print("[PRIORITY]", f.exception()))
                    fut_v = self.executor.submit(self._download_worker, victim_idx)
                    fut_v.add_done_callback(
                        lambda f: f.exception() and print("[REQUEUE]", f.exception()))

                self.ui(_submit)

            threading.Thread(target=_swap, daemon=True).start()

        else:
            # Free slot — submit directly
            it.cancel_event.clear()
            it.pause_event.clear()
            self.executor = self.executor or ThreadPoolExecutor(max_workers=1)
            fut = self.executor.submit(self._download_worker, idx)
            fut.add_done_callback(
                lambda f: f.exception() and print("[PRIORITY]", f.exception()))

    def remove_one(self, idx: int):
        row = self.rows.get(idx)
        if not row:
            return
        with self._items_lock:
            it = self.items.get(idx)
            if it is None or it.state not in ("done", "error", "canceled", "skipped"):
                return
            del self.items[idx]
        row.frame.destroy()
        del self.rows[idx]
        self._refresh_filter_counts()

    def clear_finished(self):
        for idx in list(self.rows.keys()):
            it = self.items.get(idx)
            if it and it.state in ("done", "error", "canceled", "skipped"):
                self.remove_one(idx)

    # ----------------------------------------------------------------- Context menu

    def _make_context_menu(self, idx: int):
        """Returns a <Button-3> handler that shows a right-click context menu for download row idx."""
        def handler(event):
            item = self.items.get(idx)
            if not item:
                return
            menu = tk.Menu(self, tearoff=0, bg="#2a2a2a", fg="#dddddd",
                           activebackground="#1f6aa5", activeforeground="#ffffff",
                           relief="flat", bd=0)

            # ── Ouvrir le dossier (local uniquement) ─────────────────────────
            dest_dir = os.path.dirname(item.dest_path)
            is_local = not getattr(item, "from_remote", False)
            if is_local and os.path.isdir(dest_dir):
                menu.add_command(
                    label="📂  Ouvrir le dossier",
                    command=lambda: os.startfile(dest_dir))
            else:
                menu.add_command(label="📂  Ouvrir le dossier", state="disabled")

            # ── Supprimer le fichier (done uniquement) ────────────────────────
            if item.state == "done" and is_local and os.path.isfile(item.dest_path):
                menu.add_command(
                    label="🗑  Supprimer le fichier",
                    command=lambda i=idx: self._delete_downloaded_file(i))
            else:
                menu.add_command(label="🗑  Supprimer le fichier", state="disabled")

            menu.add_separator()

            # ── Effacer la ligne ──────────────────────────────────────────────
            if item.state in ("done", "error", "canceled", "skipped"):
                menu.add_command(
                    label="✕  Effacer la ligne",
                    command=lambda i=idx: self.remove_one(i))
            else:
                menu.add_command(label="✕  Effacer la ligne", state="disabled")

            menu.add_separator()

            # ── Copier l'URL — toujours actif ─────────────────────────────────
            menu.add_command(
                label="📋  Copier l'URL",
                command=lambda u=item.url: (
                    self.clipboard_clear(), self.clipboard_append(u)))

            # ── Relancer ──────────────────────────────────────────────────────
            if item.state in ("done", "error", "canceled"):
                menu.add_command(
                    label="↺  Relancer",
                    command=lambda u=item.url: self._relaunch_download(u))
            else:
                menu.add_command(label="↺  Relancer", state="disabled")

            # ── Renommer ──────────────────────────────────────────────────────
            if item.state == "done" and is_local and os.path.isfile(item.dest_path):
                menu.add_command(
                    label="✏  Renommer",
                    command=lambda i=idx: self._rename_file(i))
            else:
                menu.add_command(label="✏  Renommer", state="disabled")

            menu.add_separator()

            # ── Force remove — toujours actif ─────────────────────────────────
            menu.add_command(
                label="💀  Force remove",
                command=lambda i=idx: self._force_remove_local(i))

            menu.tk_popup(event.x_root, event.y_root)

        return handler

    def _delete_downloaded_file(self, idx: int):
        """Deletes the downloaded file from disk after user confirmation."""
        item = self.items.get(idx)
        if not item or item.state != "done":
            return
        path = item.dest_path
        if not os.path.isfile(path):
            return
        name = os.path.basename(path)
        if not messagebox.askyesno(
            "Supprimer le fichier",
            f"Supprimer définitivement :\n{name}\n\nCette action est irréversible.",
            icon="warning", parent=self
        ):
            return
        try:
            os.remove(path)
            _log.info("Deleted file: %s", path)
        except OSError as e:
            messagebox.showerror("Erreur", f"Impossible de supprimer le fichier :\n{e}", parent=self)

    def _relaunch_download(self, url: str):
        """Re-injects a URL into the input box and starts the download."""
        try:
            self.url_box.delete("1.0", "end")
            self.url_box.insert("1.0", url)
            self.start_downloads()
        except Exception:
            pass

    def _rename_file(self, idx: int):
        """Renames the downloaded file on disk and updates the row."""
        from tree_popup import _ask_folder_name
        it = self.items.get(idx)
        if not it:
            return
        base, ext = os.path.splitext(it.filename)
        new_base = _ask_folder_name(self, title="Rename file", default=base)
        if not new_base or new_base == base:
            return
        new_name = new_base + ext
        new_path = os.path.join(os.path.dirname(it.dest_path), new_name)
        try:
            os.rename(it.dest_path, new_path)
            it.filename  = new_name
            it.dest_path = new_path
            row = self.rows.get(idx)
            if row:
                row.name_lbl.configure(text=new_name)
        except OSError as e:
            messagebox.showerror("Rename error", str(e), parent=self)

    def _force_remove_local(self, idx: int):
        """Force-removes any row regardless of state. Cancels if still active."""
        it = self.items.get(idx)
        if it:
            it.cancel_event.set()
            it.state = "canceled"
        row = self.rows.get(idx)
        if row:
            row.frame.destroy()
        self.rows.pop(idx, None)
        self.items.pop(idx, None)
        self._refresh_filter_counts()

    def _apply_sort_order(self, _=None):
        """Re-orders download rows according to the current sort setting."""
        key = getattr(self, "_sort_var", None)
        key = key.get() if key else "Recent"

        STATE_ORDER = {
            "downloading": 0, "waiting": 1, "paused": 2, "moving": 3,
            "done": 4, "error": 5, "canceled": 6, "skipped": 7,
        }

        def sort_key(idx):
            it = self.items.get(idx)
            if not it:
                return (9, 0, "")
            if key == "Name A→Z":
                return (0, 0, it.filename.lower())
            if key == "Name Z→A":
                return (0, 0, it.filename.lower())
            if key == "Status":
                return (STATE_ORDER.get(it.state, 9), 0, "")
            if key == "Size ↓":
                return (0, -(it.total_size or 0), "")
            return (0, -idx, "")   # "Recent" = last added on top

        reverse = (key == "Name Z→A")
        ordered = sorted(self.rows.keys(), key=sort_key, reverse=reverse)

        for idx in ordered:
            row = self.rows[idx]
            row.frame.pack_forget()
            row.frame.pack(fill="x", pady=3, padx=4)
