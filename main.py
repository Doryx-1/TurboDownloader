import multiprocessing
import sys
import socket as _socket

# Required for PyInstaller bundled exe on Windows.
if __name__ == "__main__":
    multiprocessing.freeze_support()

    # ── Single instance lock ──────────────────────────────────────────────────
    # Uses a local socket as a mutex. If port already bound → another instance
    # is running. Send it a FOCUS signal and exit immediately.
    _LOCK_PORT  = 19988   # internal IPC — never exposed externally
    _lock_server = None

    def _try_single_instance() -> bool:
        global _lock_server
        try:
            srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 0)
            srv.bind(("127.0.0.1", _LOCK_PORT))
            srv.listen(5)
            _lock_server = srv
            # Listen for focus signals from future instances
            import threading
            def _listen():
                while True:
                    try:
                        conn, _ = srv.accept()
                        msg = conn.recv(64).decode(errors="ignore").strip()
                        conn.close()
                        if msg == "FOCUS" and "app" in globals():
                            globals()["app"].after(0, globals()["app"]._tray_restore)
                    except Exception:
                        break
            threading.Thread(target=_listen, daemon=True,
                             name="InstanceLock").start()
            return True
        except OSError:
            # Port busy → another instance running — send FOCUS and exit
            try:
                c = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                c.settimeout(1.0)
                c.connect(("127.0.0.1", _LOCK_PORT))
                c.sendall(b"FOCUS")
                c.close()
            except Exception:
                pass
            return False

    if not _try_single_instance():
        sys.exit(0)

    # ── Logging setup ─────────────────────────────────────────────────────────
    debug_mode = "--debug" in sys.argv
    try:
        from logger import setup_logging
        setup_logging(debug=debug_mode)
    except Exception:
        pass

    # ── Custom protocol handler ───────────────────────────────────────────────
    protocol_url = None
    if len(sys.argv) > 1 and sys.argv[1].startswith("turbodownloader://"):
        protocol_url = sys.argv[1]

    # Register the protocol on every launch (safe — idempotent)
    try:
        import tray as _tray_mod
        _tray_mod.register_protocol()
    except Exception as e:
        print(f"[main] Protocol registration failed: {e}")

    from downloader import TurboDownloader
    app = TurboDownloader()

    # If launched via protocol URL → inject URLs and start
    if protocol_url:
        try:
            from tray import parse_protocol_url
            parsed = parse_protocol_url(protocol_url)
            if parsed["action"] == "send" and parsed["urls"]:
                def _inject():
                    urls_text = "\n".join(parsed["urls"])
                    app.url_box.delete("1.0", "end")
                    app.url_box.insert("end", urls_text)
                    app._show_for_popup()
                    app.start_downloads()
                app.after(500, _inject)
        except Exception as e:
            print(f"[main] Protocol inject error: {e}")

    app.mainloop()
