// content.js — TurboDownloader
// Scans the page for downloadable links and reports them to background.js

(function () {
  "use strict";

  const EXTENSIONS = [
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mp3", ".flac", ".aac", ".ogg", ".wav", ".m4a",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".iso",
    ".exe", ".msi", ".dmg", ".deb", ".rpm",
    ".pdf", ".epub",
  ];

  function isDownloadable(href) {
    if (!href) return false;
    try {
      const url = new URL(href);
      if (!["http:", "https:"].includes(url.protocol)) return false;
      const path = url.pathname.toLowerCase().split("?")[0];
      return EXTENSIONS.some(ext => path.endsWith(ext));
    } catch {
      return false;
    }
  }

  function getLinkText(el) {
    const text = (el.textContent || el.getAttribute("title") || el.getAttribute("aria-label") || "")
      .trim()
      .replace(/\s+/g, " ")
      .slice(0, 80);
    if (text) return text;
    // Fallback: filename from URL
    try {
      return decodeURIComponent(new URL(el.href).pathname.split("/").pop()) || el.href.slice(0, 60);
    } catch {
      return el.href.slice(0, 60);
    }
  }

  function scanLinks() {
    const seen = new Set();
    const links = [];

    document.querySelectorAll("a[href]").forEach(el => {
      const href = el.href;
      if (!isDownloadable(href)) return;
      if (seen.has(href)) return;
      seen.add(href);
      links.push({
        url:  href,
        text: getLinkText(el),
      });
    });

    return links;
  }

  function report() {
    const links = scanLinks();
    chrome.runtime.sendMessage({ type: "LINKS_FOUND", links });
  }

  // Initial scan
  report();

  // Re-scan on DOM mutations (dynamic pages)
  const observer = new MutationObserver(() => {
    clearTimeout(observer._timer);
    observer._timer = setTimeout(report, 800);
  });

  observer.observe(document.body, {
    childList: true,
    subtree:   true,
  });

})();
