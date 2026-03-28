// settings.js — TurboDownloader Extension Settings

(async () => {
  "use strict";

  const ALL_EXTENSIONS = [
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mp3", ".flac", ".aac", ".ogg", ".wav", ".m4a",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".iso",
    ".exe", ".msi", ".dmg", ".deb", ".pdf", ".epub",
  ];

  // ── Migrate old storage keys (host/port/username/password no longer used) ──
  await chrome.storage.local.remove(["host", "port", "username", "password"]);

  // ── Load settings ─────────────────────────────────────────────────────────

  const stored = await chrome.storage.local.get({
    dest:               "",
    intercept:          false,
    extensions:         [".mkv", ".mp4", ".avi", ".mov", ".wmv", ".mp3", ".flac",
                         ".zip", ".rar", ".7z", ".iso", ".exe", ".msi"],
    intercept_use_regex: false,
    intercept_regex:    "",
  });

  document.getElementById("dest").value               = stored.dest;
  document.getElementById("intercept").checked        = stored.intercept;
  document.getElementById("intercept-use-regex").checked = stored.intercept_use_regex;
  document.getElementById("intercept-regex").value    = stored.intercept_regex;

  // ── Toggle ext list / regex section ───────────────────────────────────────

  function applyFilterMode() {
    const useRegex = document.getElementById("intercept-use-regex").checked;
    document.getElementById("ext-section").style.display   = useRegex ? "none"  : "";
    document.getElementById("regex-section").style.display = useRegex ? ""      : "none";
  }
  applyFilterMode();
  document.getElementById("intercept-use-regex").addEventListener("change", applyFilterMode);

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

  // ── Regex test button ─────────────────────────────────────────────────────

  document.getElementById("regex-test-btn").addEventListener("click", () => {
    const val = document.getElementById("intercept-regex").value.trim();
    const fb  = document.getElementById("regex-feedback");
    if (!val) {
      fb.textContent = "Enter a regex to test.";
      fb.className = "err";
      return;
    }
    try {
      new RegExp(val, "i");
      fb.textContent = "✓ Valid regex";
      fb.className   = "ok";
    } catch (e) {
      fb.textContent = "✗ " + e.message;
      fb.className   = "err";
    }
  });

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
      dest:               document.getElementById("dest").value.trim(),
      intercept:          document.getElementById("intercept").checked,
      extensions:         [...activeExts],
      intercept_use_regex: document.getElementById("intercept-use-regex").checked,
      intercept_regex:    document.getElementById("intercept-regex").value.trim(),
      token:              null,
      tokenExpiry:        0,
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
