"""remote_tracker.py — RemoteTrackerMixin for TurboDownloader."""
import threading
from logger import get_logger
import remote_server

_log = get_logger("remote_tracker")


class RemoteTrackerMixin:

    def _record_dest_history(self, dest: str):
        """Adds a destination folder to the recent history (max 10, no duplicates)."""
        if not dest:
            return
        from settings_popup import load_dest_history, save_dest_history
        history = load_dest_history()
        if dest in history:
            history.remove(dest)
        history.insert(0, dest)
        save_dest_history(history)
        # Keep in-memory settings in sync for popups that read it this session
        self._settings["dest_history"] = history[:10]

    def _start_remote_if_enabled(self):
        """Starts the remote control server if enabled in settings.
        Also starts the local extension server (always) and auto-connects as client."""

        # ── Server ────────────────────────────────────────────────────────────
        if self._settings.get("remote_enabled", False):
            if self._settings.get("remote_username") and self._settings.get("remote_password_hash"):
                self._remote_server = remote_server.RemoteServer(self, self._settings)
                ok = self._remote_server.start()
                if not ok:
                    self._remote_server = None
            else:
                print("[remote] Skipping server start — username or password not configured")

        # ── Client auto-connect ───────────────────────────────────────────────
        if self._settings.get("remote_client_autoconnect", False):
            host = self._settings.get("remote_client_host", "")
            port = int(self._settings.get("remote_client_port", 9988))
            user = self._settings.get("remote_client_user", "")
            try:
                from settings_popup import _decrypt_password
                pwd = _decrypt_password(self._settings.get("remote_client_password", ""))
            except Exception:
                pwd = self._settings.get("remote_client_password", "")

            if host and user and pwd:
                _log.info("Auto-connecting to %s:%s...", host, port)
                def _auto_connect():
                    try:
                        c = remote_server.RemoteClient(host, port, user, pwd)
                        ok, msg = c.connect()
                        if ok:
                            self._remote_client = c
                            auto_retry = self._settings.get("remote_client_autoretry", True)
                            if auto_retry:
                                c.start_heartbeat(
                                    on_disconnect=lambda: self.ui(self._update_remote_status_bar),
                                    on_reconnect=lambda: self.ui(self._update_remote_status_bar),
                                    interval=15,
                                    max_retries=0,
                                )
                            self._start_remote_dl_tracker()
                            self.ui(self._update_remote_status_bar)
                            _log.info("Auto-connect OK")
                        else:
                            _log.warning("Auto-connect failed: %s", msg)
                    except Exception as e:
                        _log.error("Auto-connect error: %s", e)
                import threading as _th
                _th.Thread(target=_auto_connect, daemon=True,
                           name="AutoConnect").start()
            else:
                _log.debug("Auto-connect skipped — missing host/user/password")

    def _apply_remote_settings(self):
        """
        Called by on_settings_save — restarts the server if the enabled flag changed.
        """
        enabled = self._settings.get("remote_enabled", False)
        running = self._remote_server is not None and self._remote_server.is_running

        if enabled and not running:
            self._start_remote_if_enabled()
        elif not enabled and running:
            self._remote_server.stop()
            self._remote_server = None

        self._update_remote_status_bar()

    def _update_remote_status_bar(self):
        """Shows/hides/updates the two remote status badges independently."""
        if not hasattr(self, "_remote_srv_bar"):
            return

        srv_running   = self._remote_server is not None and self._remote_server.is_running
        cli_connected = self._remote_client is not None and self._remote_client.connected

        # ── Server bar ───────────────────────────────────────────────────────
        if srv_running:
            port = self._settings.get("remote_port", 9988)
            self._remote_srv_lbl.configure(
                text=f"📡  Server — listening on :{port}")
            self._remote_srv_bar.pack(fill="x", padx=16, pady=(0, 4))
        else:
            self._remote_srv_bar.pack_forget()

        # ── Client bar ───────────────────────────────────────────────────────
        if cli_connected:
            host = self._settings.get("remote_client_host", "?")
            port = self._settings.get("remote_client_port", 9988)
            self._remote_cli_lbl.configure(
                text=f"🔗  Client — connected to {host}:{port}")
            self._remote_cli_bar.pack(fill="x", padx=16, pady=(0, 8))
            # Show clear button
            if hasattr(self, "_clear_remote_btn"):
                self._clear_remote_btn.pack(fill="x")
        else:
            self._remote_cli_bar.pack_forget()
            if hasattr(self, "_clear_remote_btn"):
                self._clear_remote_btn.pack_forget()

    def _disconnect_server(self):
        """Stop the remote server."""
        if self._remote_server is not None:
            self._remote_server.stop()
            self._remote_server = None
            self._settings["remote_enabled"] = False
            from settings_popup import save_settings
            save_settings(self._settings)
        self._update_remote_status_bar()

    def _disconnect_client(self):
        """Disconnect from remote server and return to local mode."""
        if self._remote_client is not None:
            self._remote_client.disconnect()   # stops heartbeat thread
            self._remote_client = None
        self._update_remote_status_bar()

    def _start_remote_dl_tracker(self):
        """
        Single background thread that polls the server every 2s and
        auto-creates/updates shadow rows for ALL downloads on the server.
        """
        import threading as _th
        import time as _t

        def _tracker():
            server_to_shadow: dict = {}

            while True:
                _t.sleep(2)
                client = self._remote_client
                if client is None or not client.connected:
                    break
                try:
                    data = client.get_status()
                    if not data:
                        continue

                    downloads = data.get("downloads", [])

                    # Collect all row updates, apply in a single UI call
                    pending_updates = []

                    for dl in downloads:
                        server_idx = dl.get("idx")
                        state      = dl.get("state", "")
                        url        = dl.get("url", "")
                        fname      = dl.get("filename", "")
                        pct        = dl.get("progress", 0) / 100
                        speed      = dl.get("speed_bps", 0)
                        err        = dl.get("error", "")

                        if server_idx is None:
                            continue

                        if server_idx not in server_to_shadow:
                            existing = next(
                                (s_idx for s_idx, s in
                                 self._shadow_rows.items()
                                 if s.get("server_idx") == server_idx
                                 or s.get("url") == url),
                                None
                            )
                            if existing is not None:
                                server_to_shadow[server_idx] = existing
                                if existing in self._shadow_rows:
                                    self._shadow_rows[existing]["server_idx"] = server_idx
                            else:
                                ev = _th.Event()
                                def _create(u=url, si=server_idx):
                                    shadow_idx = self._add_remote_shadow_row(u, "", si)
                                    server_to_shadow[si] = shadow_idx
                                    ev.set()
                                self.ui(_create)
                                ev.wait(timeout=1.0)
                                continue

                        shadow_idx = server_to_shadow.get(server_idx)
                        if shadow_idx is None:
                            continue
                        shadow = self._shadow_rows.get(shadow_idx)
                        if not shadow:
                            server_to_shadow.pop(server_idx, None)
                            continue

                        shadow["state"]      = state
                        shadow["server_idx"] = server_idx

                        state_map = {
                            "downloading": ("📡 Downloading", "#2e8b57"),
                            "waiting":     ("📡 Waiting",     "#666666"),
                            "paused":      ("📡 Paused",      "#5a7a9a"),
                            "moving":      ("📡 Converting…", "#888800"),
                            "done":        ("📡 Done ✓",      "#2e6b3e"),
                            "error":       (f"📡 Error: {err[:30]}", "#8B0000"),
                            "canceled":    ("📡 Canceled",    "#555555"),
                        }
                        lbl, color = state_map.get(state, (f"📡 {state}", "#888888"))
                        is_final  = state in ("done", "error", "canceled")
                        is_paused = state == "paused"

                        pending_updates.append((
                            shadow["row"], lbl, color, pct, speed, fname,
                            is_final, is_paused, shadow_idx, server_idx
                        ))
                        if is_final:
                            server_to_shadow.pop(server_idx, None)

                    if pending_updates:
                        def _batch_update(updates=pending_updates):
                            for (r, l, c, p, sp, fn, final, paused, si, srv) in updates:
                                if si not in self._shadow_rows:
                                    continue
                                if fn:
                                    r.name_lbl.configure(text=f"📡 {fn}")
                                r.status.configure(text=l, text_color=c)
                                r.progress.set(p)
                                if sp > 0:
                                    spd = (f"{sp} B/s" if sp < 1024
                                           else f"{sp/1024:.1f} KB/s" if sp < 1024**2
                                           else f"{sp/1024**2:.2f} MB/s")
                                    r.speed_lbl.configure(text=f"Remote · {spd}")
                                can_control = srv is not None and not final
                                r.pause_btn.configure(
                                    text="▶" if paused else "⏸",
                                    state="normal" if can_control else "disabled")
                                r.cancel_btn.configure(
                                    state="normal" if can_control else "disabled")
                                r.remove_btn.configure(
                                    state="normal" if final else "disabled")
                            self._sort_shadow_rows()
                        self.ui(_batch_update)

                except Exception as e:
                    _log.debug("DL tracker error: %s", e)

        _th.Thread(target=_tracker, daemon=True, name="RemoteDLTracker").start()
        _log.info("Download tracker started")

    def _sort_shadow_rows(self, force: bool = False):
        """Sorts shadow rows — active on top, finished at bottom.
        Only re-packs widgets when the order has actually changed (dirty flag).
        """
        shadows = self._shadow_rows
        if len(shadows) < 2:
            return
        PRIORITY = {"downloading": 0, "waiting": 1, "paused": 2,
                    "moving": 3, "done": 4, "canceled": 5, "error": 6}
        sorted_keys = [k for k, _ in sorted(
            shadows.items(),
            key=lambda kv: PRIORITY.get(kv[1].get("state", "done"), 4)
        )]
        # Only re-pack if order changed or forced
        if not force and sorted_keys == getattr(self, "_shadow_last_order", None):
            return
        self._shadow_last_order = sorted_keys
        for k in sorted_keys:
            shadows[k]["row"].frame.pack_forget()
            shadows[k]["row"].frame.pack(fill="x", pady=3, padx=4)

    def _remote_clear_done(self):
        """Removes finished shadow rows locally and clears them on the server."""
        client = self._remote_client
        if client:
            threading.Thread(target=client.clear_done, daemon=True).start()
        to_remove = [
            s_idx for s_idx, s in self._shadow_rows.items()
            if s.get("state", "") in ("done", "error", "canceled")
        ]
        for s_idx in to_remove:
            self._remove_shadow_row(s_idx)
