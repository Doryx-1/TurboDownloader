// settings.js — TurboDownloader Extension Settings

(async () => {
  "use strict";

  const ALL_EXTENSIONS = [
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mp3", ".flac", ".aac", ".ogg", ".wav", ".m4a",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".iso",
    ".exe", ".msi", ".dmg", ".deb", ".pdf", ".epub",
  ];

  // ── Load settings ─────────────────────────────────────────────────────────

  const stored = await chrome.storage.local.get({
    dest:       "",
    intercept:  false,
    extensions: [".mkv", ".mp4", ".avi", ".mov", ".wmv", ".mp3", ".flac",
                 ".zip", ".rar", ".7z", ".iso", ".exe", ".msi"],
  });

  document.getElementById("dest").value        = stored.dest;
  document.getElementById("intercept").checked = stored.intercept;

  // ── Extension tags ────────────────────────────────────────────────────────

  const extGrid    = document.getElementById("ext-grid");
  const activeExts = new Set(stored.extensions);

  ALL_EXTENSIONS.forEach(ext => {
    const tag = document.createElement("span");
    tag.className = "ext-tag" + (activeExts.has(ext) ? " active" : "");
    tag.textContent = ext;
    tag.addEventListener("click", () => {
      if (activeExts.has(ext)) { activeExts.delete(ext); tag.classList.remove("active"); }
      else                     { activeExts.add(ext);    tag.classList.add("active"); }
    });
    extGrid.appendChild(tag);
  });

  // ── Status check ──────────────────────────────────────────────────────────

  async function checkStatus() {
    const dot  = document.getElementById("status-dot");
    const text = document.getElementById("status-text");
    const sub  = document.getElementById("status-sub");
    dot.className = "status-dot checking";
    text.textContent = "Checking…";
    try {
      const result = await chrome.runtime.sendMessage({ type: "TEST_CONNECTION" });
      if (result?.ok) {
        dot.className    = "status-dot ok";
        text.textContent = "Connected to TurboDownloader ✓";
        sub.textContent  = "Ready to receive downloads";
      } else {
        dot.className    = "status-dot err";
        text.textContent = "TurboDownloader not reachable";
        sub.textContent  = result?.error || "Make sure TurboDownloader is running";
      }
    } catch (e) {
      dot.className    = "status-dot err";
      text.textContent = "Connection error";
      sub.textContent  = e.message;
    }
  }

  checkStatus();

  // ── Test button ───────────────────────────────────────────────────────────

  document.getElementById("test-btn").addEventListener("click", async () => {
    const btn = document.getElementById("test-btn");
    btn.textContent = "Testing…";
    btn.disabled = true;
    await checkStatus();
    btn.textContent = "Test connection";
    btn.disabled = false;
  });

  // ── Save ──────────────────────────────────────────────────────────────────

  document.getElementById("save-btn").addEventListener("click", async () => {
    const btn = document.getElementById("save-btn");
    btn.textContent = "Saving…";
    btn.disabled = true;

    await chrome.storage.local.set({
      dest:        document.getElementById("dest").value.trim(),
      intercept:   document.getElementById("intercept").checked,
      extensions:  [...activeExts],
      token:       null,
      tokenExpiry: 0,
    });

    chrome.runtime.sendMessage({ type: "SETTINGS_SAVED" }).catch(() => {});

    btn.textContent = "✓ Saved!";
    btn.style.background = "#2e6b3e";
    btn.disabled = false;
    setFeedback("✓ Settings saved", "success");
    setTimeout(() => { btn.textContent = "Save"; btn.style.background = ""; }, 2500);
  });

  // ── Helpers ───────────────────────────────────────────────────────────────

  function setFeedback(msg, type) {
    const el = document.getElementById("feedback");
    el.textContent = msg;
    el.className = `feedback ${type}`;
    setTimeout(() => { el.className = "feedback"; }, 4000);
  }

})();
