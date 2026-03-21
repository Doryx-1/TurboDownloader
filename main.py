import multiprocessing
import sys

# Required for PyInstaller bundled exe on Windows.
if __name__ == "__main__":
    multiprocessing.freeze_support()

    # ── Logging setup ─────────────────────────────────────────────────────────
    debug_mode = "--debug" in sys.argv
    try:
        from logger import setup_logging
        setup_logging(debug=debug_mode)
    except Exception:
        pass   # non-blocking if logger not found

    # ── Custom protocol handler ───────────────────────────────────────────────
    # If launched with a turbodownloader:// URL as argv[1], extract it.
    # This happens when the browser extension opens the protocol link.
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
                    # Show for popup without necessarily restoring main window
                    app._show_for_popup()
                    app.start_downloads()
                app.after(500, _inject)   # slight delay — let UI fully init first
        except Exception as e:
            print(f"[main] Protocol inject error: {e}")

    app.mainloop()
