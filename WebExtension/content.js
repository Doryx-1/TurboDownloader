// content.js — TurboDownloader
// Scans the page for downloadable links, reports them to background.js,
// and injects inline download buttons + a "Download all" bar.

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
    } catch { return false; }
  }

  function getLinkText(el) {
    const text = (el.textContent || el.getAttribute("title") || el.getAttribute("aria-label") || "")
      .trim().replace(/\s+/g, " ").slice(0, 80);
    if (text) return text;
    try {
      return decodeURIComponent(new URL(el.href).pathname.split("/").pop()) || el.href.slice(0, 60);
    } catch { return el.href.slice(0, 60); }
  }

  function scanLinks() {
    const seen = new Set();
    const links = [];
    document.querySelectorAll("a[href]").forEach(el => {
      const href = el.href;
      if (!isDownloadable(href)) return;
      if (seen.has(href)) return;
      seen.add(href);
      links.push({ url: href, text: getLinkText(el), el });
    });
    return links;
  }

  // ── Send to TurboDownloader ────────────────────────────────────────────────

  function sendUrls(urls, btn) {
    if (btn) { btn.textContent = "⏳"; btn.disabled = true; }
    chrome.runtime.sendMessage({ type: "SEND_URLS", urls, dest: null }, result => {
      if (!btn) return;
      if (result && result.ok > 0) {
        btn.textContent = "✓";
        btn.style.cssText += ";background:#1a3a1a;border-color:#2a5a2a;color:#7aaa7a";
        setTimeout(() => {
          btn.textContent = btn._orig || "⬇";
          btn.style.cssText = btn.style.cssText
            .replace(/;?background:[^;]+/g, "")
            .replace(/;?border-color:[^;]+/g, "")
            .replace(/;?color:[^;]+/g, "");
          btn.disabled = false;
        }, 3000);
      } else {
        btn.textContent = "✗";
        btn.style.color = "#cc4444";
        setTimeout(() => {
          btn.textContent = btn._orig || "⬇";
          btn.style.color = "";
          btn.disabled = false;
        }, 3000);
      }
    });
  }

  // ── Styles ─────────────────────────────────────────────────────────────────

  const BTN_CLASS = "turbodl-btn";
  const BAR_ID    = "turbodl-bar";

  function injectStyles() {
    if (document.getElementById("turbodl-styles")) return;
    const s = document.createElement("style");
    s.id = "turbodl-styles";
    s.textContent = `
      .${BTN_CLASS} {
        display: inline-flex; align-items: center; justify-content: center;
        margin-left: 8px; padding: 1px 7px;
        font-size: 11px; font-weight: 600; line-height: 1.6;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #0d1a2a; color: #7ab8e8;
        border: 1px solid #1f4a7a; border-radius: 4px;
        cursor: pointer; text-decoration: none !important;
        vertical-align: middle; white-space: nowrap; user-select: none;
        transition: background 0.15s, color 0.15s;
      }
      .${BTN_CLASS}:hover  { background: #1a3a5a; color: #aad4f8; border-color: #2a6aaa; }
      .${BTN_CLASS}:disabled { opacity: 0.6; cursor: not-allowed; }

      #${BAR_ID} {
        position: sticky; top: 0; z-index: 9999;
        display: flex; align-items: center; gap: 10px;
        padding: 8px 16px;
        background: #0d1a2a; border-bottom: 1px solid #1f4a7a;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-size: 13px; color: #7ab8e8;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
      }
      #${BAR_ID} .tbl { flex:1; font-size:12px; color:#5a8ab8; }
      #${BAR_ID} .tbb {
        padding: 5px 14px; font-size: 12px; font-weight: 600;
        background: #1f6aa5; color: #fff; border: none; border-radius: 5px;
        cursor: pointer; font-family: inherit; transition: background 0.15s;
      }
      #${BAR_ID} .tbb:hover    { background: #1a5a8f; }
      #${BAR_ID} .tbb.success  { background: #1a3a1a; color: #7aaa7a; }
      #${BAR_ID} .tbc {
        background: transparent; border: none; color: #3a5a7a;
        font-size: 16px; cursor: pointer; padding: 0 4px; line-height: 1;
        font-family: inherit; transition: color 0.15s;
      }
      #${BAR_ID} .tbc:hover { color: #7ab8e8; }
    `;
    document.head.appendChild(s);
  }

  // ── Inject button next to link ─────────────────────────────────────────────

  function injectBtn(linkEl) {
    if (linkEl._turboDLInjected) return;
    linkEl._turboDLInjected = true;

    const btn = document.createElement("button");
    btn.className = BTN_CLASS;
    btn.textContent = "⬇";
    btn._orig = "⬇";
    btn.title = "Send to TurboDownloader";
    btn.addEventListener("click", e => {
      e.preventDefault();
      e.stopPropagation();
      sendUrls([linkEl.href], btn);
    });

    linkEl.parentNode.insertBefore(btn, linkEl.nextSibling);
  }

  // ── Top bar ────────────────────────────────────────────────────────────────

  function injectBar(links) {
    const existing = document.getElementById(BAR_ID);
    if (existing) existing.remove();
    if (links.length < 2) return;

    const bar = document.createElement("div");
    bar.id = BAR_ID;

    const label = document.createElement("span");
    label.className = "tbl";
    label.textContent = `TurboDownloader — ${links.length} file${links.length > 1 ? "s" : ""} detected`;

    const allBtn = document.createElement("button");
    allBtn.className = "tbb";
    allBtn.textContent = `⬇  Download all (${links.length})`;
    allBtn.addEventListener("click", () => {
      sendUrls(links.map(l => l.url), allBtn);
      allBtn._orig = allBtn.textContent;
    });

    const closeBtn = document.createElement("button");
    closeBtn.className = "tbc";
    closeBtn.textContent = "✕";
    closeBtn.title = "Dismiss";
    closeBtn.addEventListener("click", () => bar.remove());

    bar.appendChild(label);
    bar.appendChild(allBtn);
    bar.appendChild(closeBtn);
    document.body.insertBefore(bar, document.body.firstChild);
  }

  // ── Main ───────────────────────────────────────────────────────────────────

  function run() {
    injectStyles();
    const links = scanLinks();
    chrome.runtime.sendMessage({ type: "LINKS_FOUND", links });
    links.forEach(({ el }) => injectBtn(el));
    injectBar(links);
  }

  run();

  // Re-scan on DOM mutations — only inject missing buttons, don't rebuild bar
  const observer = new MutationObserver(() => {
    clearTimeout(observer._timer);
    observer._timer = setTimeout(() => {
      const links = scanLinks();
      chrome.runtime.sendMessage({ type: "LINKS_FOUND", links });
      links.forEach(({ el }) => injectBtn(el));
    }, 800);
  });

  observer.observe(document.body, { childList: true, subtree: true });

})();
