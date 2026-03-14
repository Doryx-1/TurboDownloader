import multiprocessing
import sys

# Required for PyInstaller bundled exe on Windows.
# Without this, any subprocess spawn (uvicorn, ThreadPoolExecutor, etc.)
# re-executes main.py from scratch instead of running the child task,
# causing the app to launch multiple times in a loop.
if __name__ == "__main__":
    multiprocessing.freeze_support()

    from downloader import TurboDownloader
    app = TurboDownloader()
    app.mainloop()
