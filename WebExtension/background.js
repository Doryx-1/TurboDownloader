// background.js — TurboDownloader Extension Service Worker
// Handles: download interception, context menu, API communication, badge
//
// Security model: extension always talks to 127.0.0.1:9988 (local TD only).
// No host/port/credentials config — TD generates a local token at startup
// that the extension fetches automatically.

// ── Constants ────────────────────────────────────────────────────────────────

const TD_HOST = "http://127.0.0.1:9988";
const TOKEN_MARGIN_MS = 5 * 60 * 1000;   // renew 5 min before expiry

// ── Default settings ─────────────────────────────────────────────────────────

const DEFAULT_SETTINGS = {
  token:       null,
  tokenExpiry: 0,
  intercept:   false,
  extensions:  [".mkv", ".mp4", ".avi", ".mov", ".wmv", ".mp3", ".flac",
                ".zip", ".rar", ".7z", ".iso", ".exe", ".msi"],
  dest:        "",
};

// ── Storage helpers ───────────────────────────────────────────────────────────

async function getSettings() {
  const stored = await chrome.storage.local.get(DEFAULT_SETTINGS);
  return { ...DEFAULT_SETTINGS, ...stored };
}

async function saveSettings(patch) {
  await chrome.storage.local.set(patch);
}

// ── Local token auth (no user config needed) ─────────────────────────────────

async function fetchLocalToken() {
  // Step 1: read the local token TD wrote to disk (via localhost endpoint)
  const r1 = await fetch(`${TD_HOST}/local-token`);
  if (!r1.ok) throw new Error(`local-token: HTTP ${r1.status}`);
  const { token: localToken } = await r1.json();

  // Step 2: exchange it for a JWT
  const r2 = await fetch(`${TD_HOST}/auth/local`, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ token: localToken }),
  });
  if (!r2.ok) throw new Error(`auth/local: HTTP ${r2.status}`);
  const { token: jwt, expires_in_h } = await r2.json();
  return { token: jwt, expiresIn: expires_in_h || 1 };
}

async function getToken() {
  const s   = await getSettings();
  const now = Date.now();

  // Reuse valid token if not close to expiry
  if (s.token && s.tokenExpiry > now + TOKEN_MARGIN_MS) {
    return s.token;
  }

  // Get a fresh JWT via the local token mechanism
  try {
    const { token, expiresIn } = await fetchLocalToken();
    const expiry = now + expiresIn * 3_600_000;
    await saveSettings({ token, tokenExpiry: expiry });
    return token;
  } catch (e) {
    console.warn("[TurboDL] Auth failed:", e.message);
    return null;
  }
}

async function apiSendUrl(url, dest) {
  const token = await getToken();
  if (!token) return { ok: false, error: "Not authenticated" };

  const endpoint = `${TD_HOST}/downloads/add`;
  try {
    const r = await fetch(endpoint, {
      method:  "POST",
      headers: {
        "Content-Type":  "application/json",
        "Authorization": `Bearer ${token}`,
      },
      body: JSON.stringify({ url, dest: dest || s.dest || null }),
    });
    if (!r.ok) {
      const txt = await r.text().catch(() => "");
      return { ok: false, error: `HTTP ${r.status} — ${txt.slice(0, 100)}` };
    }
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

async function apiGetStatus() {
  const token = await getToken();
  if (!token) return null;
  const proto = await getProto(s.host);
  try {
    const r = await fetch(`${proto}://${s.host}:${s.port}/status`, {
      headers: { "Authorization": `Bearer ${token}` },
    });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

// ── Badge helpers ────────────────────────────────────────────────────────────

async function setBadge(tabId, count) {
  const text  = count > 0 ? String(count) : "";
  const color = count > 0 ? "#1f6aa5" : "#555555";
  try {
    await chrome.action.setBadgeText({ text, tabId });
    await chrome.action.setBadgeBackgroundColor({ color, tabId });
  } catch {
    // tabId may be gone
  }
}

// ── Tab link cache (from content scripts) ───────────────────────────────────
// tabId → [{ url, text }]
const tabLinks = new Map();

// ── Send URLs interactively to TurboDownloader ───────────────────────────────
// Injects URLs into TD's url_box and triggers the native tree_popup.
// TD comes to the front and the user picks the destination there.

// ── Launch TurboDownloader via custom protocol ───────────────────────────────
// Used as fallback when the HTTP API is unreachable (TD not running).

function launchViaProcotol(urls) {
  // Encode all URLs into a single turbodownloader://send?url=...&url=... link
  const params = urls.map(u => `url=${encodeURIComponent(u)}`).join("&");
  const link   = `turbodownloader://send?${params}`;
  // chrome.tabs.create navigates to the protocol URL → Windows launches TD
  chrome.tabs.create({ url: link, active: false }, tab => {
    // Close the tab immediately — it was just used to trigger the protocol
    setTimeout(() => {
      chrome.tabs.remove(tab.id).catch(() => {});
    }, 1000);
  });
  showNotification(
    "TurboDownloader is starting… your download will begin shortly.",
    "success"
  );
}

async function sendInteractive(urls) {
  const s     = await getSettings();

  // First attempt — try HTTP API (TD already running)
  const token = await getToken();
  if (token) {
    const combined = urls.join("\n");
    try {
      const r = await fetch(`${TD_HOST}/downloads/add_interactive`, {
        method:  "POST",
        headers: {
          "Content-Type":  "application/json",
          "Authorization": `Bearer ${token}`,
        },
        body: JSON.stringify({ url: combined, dest: null }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      showNotification(
        urls.length === 1
          ? "Sent to TurboDownloader ✓"
          : `${urls.length} URLs sent to TurboDownloader ✓`,
        "success"
      );
      return;
    } catch (e) {
      console.log("[TurboDL] HTTP failed, falling back to protocol:", e.message);
    }
  }

  // Fallback — TD is not running, launch it via protocol
  launchViaProcotol(urls);
}

// ── Context menu ─────────────────────────────────────────────────────────────

function setupContextMenu() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id:       "turbo-send",
      title:    "Send to TurboDownloader",
      contexts: ["link"],
    });
    chrome.contextMenus.create({
      id:       "turbo-send-page",
      title:    "Send all links on this page",
      contexts: ["page"],
    });
  });
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === "turbo-send") {
    sendInteractive([info.linkUrl]);
  }

  if (info.menuItemId === "turbo-send-page") {
    const links = tabLinks.get(tab.id) || [];
    if (!links.length) {
      showNotification("No downloadable links found on this page.", "error");
      return;
    }
    sendInteractive(links.map(l => l.url));
  }
});

// ── Download interception ────────────────────────────────────────────────────

chrome.downloads.onCreated.addListener(async (item) => {
  const s = await getSettings();
  if (!s.intercept) return;
  if (!s.username || !s.password) return;

  const url   = item.url || item.finalUrl || "";
  const lower = url.split("?")[0].toLowerCase();
  const match = s.extensions.some(ext => lower.endsWith(ext));
  if (!match) return;

  // Cancel the native download and send to TurboDownloader interactively
  chrome.downloads.cancel(item.id, async () => {
    chrome.downloads.erase({ id: item.id });
    sendInteractive([url]);
  });
});

// ── Notifications ────────────────────────────────────────────────────────────

function showNotification(message, type) {
  const iconUrl = type === "success"
    ? "icons/icon48.png"
    : "icons/icon48.png";

  chrome.notifications.create({
    type:    "basic",
    iconUrl,
    title:   "TurboDownloader",
    message,
    priority: 1,
  });
}

// ── Messages from content.js and popup.js ───────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    switch (msg.type) {

      // Content script reports found links for this tab
      case "LINKS_FOUND": {
        const tabId = sender.tab?.id;
        if (tabId == null) break;
        tabLinks.set(tabId, msg.links);
        await setBadge(tabId, msg.links.length);
        break;
      }

      // Popup requests link list for current tab
      case "GET_LINKS": {
        const links = tabLinks.get(msg.tabId) || [];
        sendResponse({ links });
        break;
      }

      // Popup sends selected URLs
      case "SEND_URLS": {
        // Use interactive mode — TD opens its own tree_popup and comes to front
        const s     = await getSettings();
        const token = await getToken();
        if (!token) {
          sendResponse({ ok: 0, total: msg.urls.length, error: "Not authenticated" });
          break;
        }
        const proto    = await getProto(s.host);
        const combined = msg.urls.join("\n");
        try {
          const r = await fetch(`${proto}://${s.host}:${s.port}/downloads/add_interactive`, {
            method:  "POST",
            headers: {
              "Content-Type":  "application/json",
              "Authorization": `Bearer ${token}`,
            },
            body: JSON.stringify({ url: combined, dest: null }),
          });
          if (r.ok) {
            sendResponse({ ok: msg.urls.length, total: msg.urls.length });
          } else {
            sendResponse({ ok: 0, total: msg.urls.length, error: `HTTP ${r.status}` });
          }
        } catch (e) {
          sendResponse({ ok: 0, total: msg.urls.length, error: e.message });
        }
        break;
      }

      // Settings page — test connection (no credentials needed — local token only)
      case "TEST_CONNECTION": {
        try {
          const { token } = await fetchLocalToken();
          sendResponse({ ok: true });
        } catch (e) {
          sendResponse({ ok: false, error: e.message });
        }
        break;
      }

      // Settings saved — invalidate token
      case "SETTINGS_SAVED": {
        await saveSettings({ token: null, tokenExpiry: 0 });
        setupContextMenu();
        break;
      }

      // Popup requests server status
      case "GET_STATUS": {
        const status = await apiGetStatus();
        sendResponse({ status });
        break;
      }

      // Rescan page
      case "RESCAN": {
        try {
          await chrome.scripting.executeScript({
            target: { tabId: msg.tabId },
            files:  ["content.js"],
          });
        } catch {}
        break;
      }
    }
  })();
  return true; // keep sendResponse channel open for async
});

// ── Tab cleanup ──────────────────────────────────────────────────────────────

chrome.tabs.onRemoved.addListener(tabId => {
  tabLinks.delete(tabId);
});

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status === "loading") {
    tabLinks.delete(tabId);
    setBadge(tabId, 0);
  }
});

// ── Init ─────────────────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  setupContextMenu();
});

setupContextMenu();
