"""crawl.py — CrawlMixin for TurboDownloader."""
import os
import threading
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from logger import get_logger
import ytdlp_worker

_log = get_logger("crawl")


class CrawlMixin:

    def get_all_files(self, url: str, base_url: str = None,
                      cancel_event: threading.Event = None) -> list:
        """Recursively scrapes and returns a list of (file_url, relative_path).
        Stops cleanly if cancel_event is set (STOP or timeout).
        """
        if base_url is None:
            base_url = url
        if cancel_event is None:
            cancel_event = self._scan_cancel_event

        results = []

        if cancel_event.is_set():
            return results

        try:
            r = self.req.get(url, timeout=30, allow_redirects=True)
            r.raise_for_status()
        except Exception as e:
            print("[crawl]", e)
            return results

        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a"):
            if cancel_event.is_set():
                break
            href = a.get("href", "")
            if not href or href in ("../", "./", "/"):
                continue
            full = urljoin(url, href)
            if not full.startswith(base_url.rstrip("/") + "/") and full != base_url:
                if href.startswith("http"):
                    continue
            if href.endswith("/"):
                results.extend(self.get_all_files(full, base_url, cancel_event))
            elif self._settings.get("all_files", False) or href.lower().endswith(self._active_extensions):
                rel = full[len(base_url.rstrip("/")):]
                rel = rel.lstrip("/")
                rel_dir = os.path.dirname(rel)
                results.append((full, rel_dir))

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for item in results:
            if item[0] not in seen:
                seen.add(item[0])
                unique.append(item)
        return unique

    def _probe_url(self, url: str) -> str:
        """Probes a URL to determine its nature.
        Returns: 'file' | 'directory' | 'ytdlp' | 'unknown'
        """
        if ytdlp_worker.is_ytdlp_url(url):
            return "ytdlp"
        try:
            r = self.req.head(url, timeout=15, allow_redirects=True)
            if r.status_code == 405:
                r = self.req.get(url, timeout=15, allow_redirects=True, stream=True)
                r.close()
            if not r.ok:
                return "unknown"
            ct = (r.headers.get("Content-Type") or "").lower()
            if "text/html" in ct:
                return "directory"
            return "file"
        except Exception as e:
            print(f"[probe] {e}")
            return "unknown"
