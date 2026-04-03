// popup.js — TurboDownloader Extension Popup

// ── Download progress WebSocket ─────────────────────────────────────────────
let _dlWs = null;
let _dlRenderScheduled = false;

function _throttledRender(items) {
  if (_dlRenderScheduled) return;
  _dlRenderScheduled = true;
  setTimeout(() => {
    _dlRenderScheduled = false;
    _renderDownloads(items);
  }, 500);
}

function _connectDlWs(token) {
  if (_dlWs) { try { _dlWs.close(); } catch(e){} }
  try {
    _dlWs = new WebSocket(`ws://127.0.0.1:9988/ws?token=${encodeURIComponent(token)}`);
    _dlWs.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        _throttledRender(data.downloads || []);
      } catch(err) {}
    };
    _dlWs.onclose = () => {
      _dlWs = null;
      const sec = document.getElementById('dl-section');
      if (sec) sec.style.display = 'none';
    };
    _dlWs.onerror = () => {
      if (_dlWs) _dlWs.close();
    };
  } catch(e) {}
}

function _renderDownloads(items) {
  const active = (items || []).filter(i =>
    ['downloading', 'waiting', 'paused', 'moving'].includes(i.state));
  const section = document.getElementById('dl-section');
  const list = document.getElementById('dl-list');
  if (!section || !list) return;

  if (!active.length) {
    section.style.display = 'none';
    return;
  }
  section.style.display = 'block';

  list.innerHTML = active.map(i => {
    const name = (i.filename || 'Unknown').slice(0, 35);
    const pct = (i.total && i.total > 0)
      ? Math.round(((i.downloaded || 0) / i.total) * 100)
      : (i.progress || 0);
    const speed = i.speed_bps > 0
      ? _fmtSpeed(i.speed_bps)
      : '';
    const stateColor = {
      'downloading': '#2e8b57',
      'waiting':     '#666',
      'paused':      '#5a7a9a',
      'moving':      '#888800',
    }[i.state] || '#666';

    return `<div class="dl-row">
      <span class="dl-name" title="${i.filename || ''}">${name}</span>
      <div class="dl-bar-bg">
        <div class="dl-bar-fill" style="width:${pct}%;background:${stateColor}"></div>
      </div>
      <span class="dl-meta">${pct}%${speed ? ' · ' + speed : ''} ·
        <span style="color:${stateColor}">${i.state}</span></span>
    </div>`;
  }).join('');
}

function _fmtSpeed(bps) {
  if (bps >= 1048576) return (bps/1048576).toFixed(1) + ' MB/s';
  if (bps >= 1024)    return (bps/1024).toFixed(0) + ' KB/s';
  return bps + ' B/s';
}

// ────────────────────────────────────────────────────────────────────────────

(async () => {
  "use strict";

  // ── State ──────────────────────────────────────────────────────────────────
  let allLinks    = [];
  let selectedSet = new Set();
  let currentTab  = null;

  // ── DOM refs ───────────────────────────────────────────────────────────────
  const statusDot      = document.getElementById("status-dot");
  const statusBar      = document.getElementById("status-bar");
  const statusText     = document.getElementById("status-text");
  const statusQueue    = document.getElementById("status-queue");
  const interceptToggle= document.getElementById("intercept-toggle");
  const linksCount     = document.getElementById("links-count");
  const linkList       = document.getElementById("link-list");
  const selectAllBtn   = document.getElementById("select-all");
  const selectNoneBtn  = document.getElementById("select-none");
  const rescanBtn      = document.getElementById("rescan-btn");
  const destInput      = document.getElementById("dest-input");
  const selectedCount  = document.getElementById("selected-count");
  const sendBtn        = document.getElementById("send-btn");
  const settingsBtn    = document.getElementById("settings-btn");

  // ── Init ───────────────────────────────────────────────────────────────────

  // Load persisted settings
  const stored = await chrome.storage.local.get({
    intercept: false,
    dest:      "",
  });

  interceptToggle.checked = stored.intercept;
  destInput.value         = stored.dest || "";

  // Get current tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentTab = tab;

  // Check server status
  checkStatus();

  // Load links for this tab
  loadLinks();

  // ── Server status ──────────────────────────────────────────────────────────

  async function checkStatus() {
    // Test connection via local token — no credentials needed
    const result = await sendMsg({ type: "TEST_CONNECTION" });
    if (result?.ok) {
      const { status } = await sendMsg({ type: "GET_STATUS" });
      const active = (status?.downloads || []).filter(d =>
        ["downloading", "waiting"].includes(d.state)
      ).length;
      setStatus("connected",
        "Connected — TurboDownloader",
        active > 0 ? `${active} active` : ""
      );
      // Connect WebSocket for live download progress
      const stored = await chrome.storage.local.get({ token: null });
      if (stored.token) {
        _connectDlWs(stored.token);
      }
    } else {
      setStatus("disconnected",
        "Cannot reach TurboDownloader",
        result?.error ? result.error.slice(0, 50) : "Make sure TD is running"
      );
    }
  }

  function setStatus(state, text, queue) {
    statusDot.className = `status-dot ${state}`;
    statusText.textContent = text;
    statusQueue.textContent = queue;
    if (state === "disconnected") {
      statusBar.classList.add("error");
    } else {
      statusBar.classList.remove("error");
    }
  }

  // ── Link loading ───────────────────────────────────────────────────────────

  async function loadLinks() {
    linksCount.textContent = "Scanning…";
    linkList.innerHTML = "";

    const { links } = await sendMsg({ type: "GET_LINKS", tabId: currentTab.id });
    allLinks = links || [];
    renderLinks();
  }

  function renderLinks() {
    const count = allLinks.length;
    linksCount.textContent = count > 0
      ? `${count} downloadable link${count > 1 ? "s" : ""} found`
      : "No downloadable links found";

    linkList.innerHTML = "";

    if (!count) {
      linkList.innerHTML = '<div class="empty-state">No downloadable links found on this page.</div>';
      updateFooter();
      return;
    }

    allLinks.forEach((link, i) => {
      const ext = getExt(link.url);
      const item = document.createElement("div");
      item.className = "link-item" + (selectedSet.has(link.url) ? " checked" : "");
      item.innerHTML = `
        <input type="checkbox" data-idx="${i}" ${selectedSet.has(link.url) ? "checked" : ""}>
        <div class="link-info">
          <div class="link-name" title="${escHtml(link.url)}">${escHtml(link.text || link.url)}</div>
          <div class="link-url">${escHtml(shortUrl(link.url))}</div>
        </div>
        <span class="link-ext">${escHtml(ext)}</span>
      `;

      const cb = item.querySelector("input");
      item.addEventListener("click", e => {
        if (e.target !== cb) cb.click();
      });
      cb.addEventListener("change", () => {
        if (cb.checked) {
          selectedSet.add(link.url);
          item.classList.add("checked");
        } else {
          selectedSet.delete(link.url);
          item.classList.remove("checked");
        }
        updateFooter();
      });

      linkList.appendChild(item);
    });

    updateFooter();
  }

  function updateFooter() {
    const n = selectedSet.size;
    selectedCount.textContent = n > 0 ? `${n} selected` : "0 selected";
    sendBtn.disabled = n === 0;
  }

  // ── Actions ────────────────────────────────────────────────────────────────

  selectAllBtn.addEventListener("click", () => {
    allLinks.forEach(l => selectedSet.add(l.url));
    renderLinks();
  });

  selectNoneBtn.addEventListener("click", () => {
    selectedSet.clear();
    renderLinks();
  });

  rescanBtn.addEventListener("click", async () => {
    rescanBtn.textContent = "…";
    rescanBtn.disabled = true;
    selectedSet.clear();
    await sendMsg({ type: "RESCAN", tabId: currentTab.id });
    await sleep(600);
    await loadLinks();
    rescanBtn.textContent = "↻ Rescan";
    rescanBtn.disabled = false;
  });

  interceptToggle.addEventListener("change", async () => {
    await chrome.storage.local.set({ intercept: interceptToggle.checked });
    await sendMsg({ type: "SETTINGS_SAVED" });
  });

  destInput.addEventListener("change", async () => {
    await chrome.storage.local.set({ dest: destInput.value.trim() });
  });

  settingsBtn.addEventListener("click", () => {
    chrome.runtime.openOptionsPage();
  });

  sendBtn.addEventListener("click", async () => {
    if (selectedSet.size === 0) return;

    sendBtn.textContent = "Sending…";
    sendBtn.classList.add("sending");
    sendBtn.disabled = true;

    const urls = [...selectedSet];
    const dest = destInput.value.trim();
    const result = await sendMsg({ type: "SEND_URLS", urls, dest });

    if (result.ok === result.total) {
      sendBtn.textContent = `✓ Sent ${result.ok}`;
      sendBtn.classList.remove("sending");
      sendBtn.classList.add("success");
    } else {
      sendBtn.textContent = `${result.ok}/${result.total} sent`;
      sendBtn.classList.remove("sending");
      sendBtn.classList.add("error");
    }

    await sleep(1800);
    sendBtn.textContent = "⬇ Send to TurboDownloader";
    sendBtn.classList.remove("success", "error", "sending");
    sendBtn.disabled = selectedSet.size === 0;
  });

  // ── Helpers ────────────────────────────────────────────────────────────────

  function sendMsg(msg) {
    return chrome.runtime.sendMessage(msg).catch(() => ({}));
  }

  function sleep(ms) {
    return new Promise(r => setTimeout(r, ms));
  }

  function getExt(url) {
    try {
      const path = new URL(url).pathname.toLowerCase();
      const dot = path.lastIndexOf(".");
      return dot >= 0 ? path.slice(dot) : "";
    } catch { return ""; }
  }

  function shortUrl(url) {
    try {
      const u = new URL(url);
      return u.hostname + u.pathname.slice(0, 50);
    } catch { return url.slice(0, 60); }
  }

  function escHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

})();
