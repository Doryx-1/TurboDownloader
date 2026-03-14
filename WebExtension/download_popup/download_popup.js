// download_popup.js — TurboDownloader destination browser popup

(async () => {
  "use strict";

  // ── Parse URLs from query string ───────────────────────────────────────────
  const params  = new URLSearchParams(window.location.search);
  const urlsRaw = params.get("urls") || "";
  const urls    = urlsRaw ? urlsRaw.split("|||").filter(Boolean) : [];

  // ── DOM refs ───────────────────────────────────────────────────────────────
  const filesBar   = document.getElementById("files-bar");
  const destPath   = document.getElementById("dest-path");
  const destClear  = document.getElementById("dest-clear");
  const upBtn      = document.getElementById("up-btn");
  const breadcrumb = document.getElementById("breadcrumb");
  const browser    = document.getElementById("file-browser");
  const selectBtn  = document.getElementById("select-btn");
  const cancelBtn  = document.getElementById("cancel-btn");
  const sendBtn    = document.getElementById("send-btn");

  // ── State ──────────────────────────────────────────────────────────────────
  let currentPath   = "";   // current browsed path on server
  let parentPath    = null;
  let selectedDest  = "";   // confirmed destination
  let settings      = {};

  // ── Load settings ──────────────────────────────────────────────────────────
  settings = await chrome.storage.local.get({
    host: "127.0.0.1", port: 9988,
    username: "", password: "",
    token: null, tokenExpiry: 0,
  });

  // ── Files summary ──────────────────────────────────────────────────────────
  if (!urls.length) {
    filesBar.textContent = "No files to send";
    sendBtn.disabled = true;
  } else if (urls.length === 1) {
    const name = decodeURIComponent(urls[0].split("/").pop().split("?")[0]);
    filesBar.innerHTML = `<strong>1 file:</strong> ${escHtml(name)}`;
  } else {
    filesBar.innerHTML = `<strong>${urls.length} files</strong> selected`;
  }

  // ── Destination display ────────────────────────────────────────────────────
  function setDest(path) {
    selectedDest = path || "";
    if (selectedDest) {
      destPath.textContent = selectedDest;
      destPath.classList.remove("empty");
      destClear.style.display = "";
    } else {
      destPath.textContent = "No folder selected — will use server default";
      destPath.classList.add("empty");
      destClear.style.display = "none";
    }
  }

  destClear.addEventListener("click", () => setDest(""));

  // ── Browse API ─────────────────────────────────────────────────────────────
  async function getToken() {
    const now = Date.now();
    if (settings.token && settings.tokenExpiry > now + 60_000) return settings.token;
    if (!settings.username || !settings.password) return null;

    try {
      const r = await fetch(
        `http://${settings.host}:${settings.port}/auth/login`,
        {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ username: settings.username, password: settings.password }),
        }
      );
      if (!r.ok) return null;
      const data = await r.json();
      const expiry = now + (data.expires_in_h || 24) * 3_600_000;
      await chrome.storage.local.set({ token: data.token, tokenExpiry: expiry });
      settings.token = data.token;
      settings.tokenExpiry = expiry;
      return data.token;
    } catch { return null; }
  }

  async function browsePath(path) {
    browser.innerHTML = '<div class="browser-loading">Loading…</div>';

    const token = await getToken();
    if (!token) {
      browser.innerHTML = '<div class="browser-empty">⚠ Cannot connect to server.<br>Check settings.</div>';
      return;
    }

    try {
      const encoded = encodeURIComponent(path);
      const r = await fetch(
        `http://${settings.host}:${settings.port}/browse?path=${encoded}`,
        { headers: { "Authorization": `Bearer ${token}` } }
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();

      currentPath = data.path || path;
      parentPath  = data.parent ?? null;

      // Update header
      breadcrumb.textContent = currentPath || "Server root (drives)";
      upBtn.disabled = parentPath === null;

      renderEntries(data.entries || []);

    } catch (e) {
      browser.innerHTML = `<div class="browser-empty">⚠ Error: ${escHtml(e.message)}</div>`;
    }
  }

  function renderEntries(entries) {
    browser.innerHTML = "";

    const dirs  = entries.filter(e => e.is_dir);
    const files = entries.filter(e => !e.is_dir);

    if (!dirs.length && !files.length) {
      browser.innerHTML = '<div class="browser-empty">Empty folder</div>';
      return;
    }

    // Folders first
    dirs.forEach(entry => {
      const row = document.createElement("div");
      row.className = "browser-item";
      row.innerHTML = `
        <span class="item-icon">📁</span>
        <span class="item-name">${escHtml(entry.name)}</span>
      `;
      row.addEventListener("click", () => browsePath(entry.path));
      browser.appendChild(row);
    });

    // Files (non-clickable, just shown for context)
    files.forEach(entry => {
      const row = document.createElement("div");
      row.className = "browser-item";
      row.style.cursor = "default";
      row.innerHTML = `
        <span class="item-icon" style="opacity:0.4">📄</span>
        <span class="item-name file">${escHtml(entry.name)}</span>
      `;
      browser.appendChild(row);
    });
  }

  // ── Controls ───────────────────────────────────────────────────────────────
  upBtn.addEventListener("click", () => {
    if (parentPath !== null) browsePath(parentPath);
  });

  selectBtn.addEventListener("click", () => {
    if (currentPath) setDest(currentPath);
  });

  cancelBtn.addEventListener("click", () => window.close());

  sendBtn.addEventListener("click", async () => {
    sendBtn.disabled = true;
    sendBtn.textContent = "Sending…";

    const result = await chrome.runtime.sendMessage({
      type: "SEND_URLS",
      urls,
      dest: selectedDest,
    });

    if (result && result.ok === result.total) {
      sendBtn.textContent = `✓ Sent ${result.ok}`;
      sendBtn.classList.add("success");
      setTimeout(() => window.close(), 1200);
    } else {
      const sent = result?.ok ?? 0;
      sendBtn.textContent = `${sent}/${urls.length} sent`;
      sendBtn.disabled = false;
    }
  });

  // ── Helper ─────────────────────────────────────────────────────────────────
  function escHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // ── Start ──────────────────────────────────────────────────────────────────
  browsePath("");

})();
