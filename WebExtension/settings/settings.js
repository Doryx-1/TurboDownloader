// settings.js — TurboDownloader Extension Settings Page

(async () => {
  "use strict";

  const ALL_EXTENSIONS = [
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mp3", ".flac", ".aac", ".ogg", ".wav", ".m4a",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".iso",
    ".exe", ".msi", ".dmg", ".deb",
    ".pdf", ".epub",
  ];

  // ── Load stored settings ─────────────────────────────────────────────────

  const stored = await chrome.storage.local.get({
    host:       "127.0.0.1",
    port:       9988,
    username:   "",
    password:   "",
    dest:       "",
    intercept:  false,
    extensions: [".mkv", ".mp4", ".avi", ".mov", ".wmv", ".mp3", ".flac",
                 ".zip", ".rar", ".7z", ".iso", ".exe", ".msi"],
  });

  document.getElementById("host").value      = stored.host;
  document.getElementById("port").value      = stored.port;
  document.getElementById("username").value  = stored.username;
  document.getElementById("password").value  = stored.password;
  document.getElementById("dest").value      = stored.dest;
  document.getElementById("intercept").checked = stored.intercept;

  // ── Extension tags ───────────────────────────────────────────────────────

  const extGrid    = document.getElementById("ext-grid");
  const activeExts = new Set(stored.extensions);

  ALL_EXTENSIONS.forEach(ext => {
    const tag = document.createElement("span");
    tag.className = "ext-tag" + (activeExts.has(ext) ? " active" : "");
    tag.textContent = ext;
    tag.addEventListener("click", () => {
      if (activeExts.has(ext)) {
        activeExts.delete(ext);
        tag.classList.remove("active");
      } else {
        activeExts.add(ext);
        tag.classList.add("active");
      }
    });
    extGrid.appendChild(tag);
  });

  // ── Test connection ──────────────────────────────────────────────────────

  document.getElementById("trust-btn").addEventListener("click", () => {
    const host = document.getElementById("host").value.trim() || "127.0.0.1";
    const port = document.getElementById("port").value || "9988";
    const url  = `https://${host}:${port}/status`;
    chrome.tabs.create({ url });
    const hint = document.getElementById("ssl-hint");
    hint.style.display = "block";
    hint.innerHTML = hint.innerHTML.replace(
      /https:\/\/\[host\]:\[port\]/,
      `https://${host}:${port}`
    );
  });

  document.getElementById("test-btn").addEventListener("click", async () => {
    const btn = document.getElementById("test-btn");
    btn.textContent = "Testing…";
    btn.disabled = true;

    try {
      const result = await chrome.runtime.sendMessage({
        type:     "TEST_CONNECTION",
        host:     document.getElementById("host").value.trim(),
        port:     parseInt(document.getElementById("port").value) || 9988,
        username: document.getElementById("username").value.trim(),
        password: document.getElementById("password").value,
      });

      setFeedback(
        result?.ok
          ? "✓ Connection successful — TurboDownloader is reachable"
          : `✗ Connection failed: ${result?.error || "Unknown error"}`,
        result?.ok ? "success" : "error"
      );
    } catch (e) {
      setFeedback(`✗ Error: ${e.message}`, "error");
    }

    btn.textContent = "Test connection";
    btn.disabled = false;
  });

  // ── Save ─────────────────────────────────────────────────────────────────

  document.getElementById("save-btn").addEventListener("click", async () => {
    const btn = document.getElementById("save-btn");
    btn.textContent = "Saving…";
    btn.disabled = true;

    const settings = {
      host:        document.getElementById("host").value.trim() || "127.0.0.1",
      port:        parseInt(document.getElementById("port").value) || 9988,
      username:    document.getElementById("username").value.trim(),
      password:    document.getElementById("password").value,
      dest:        document.getElementById("dest").value.trim(),
      intercept:   document.getElementById("intercept").checked,
      extensions:  [...activeExts],
      token:       null,
      tokenExpiry: 0,
    };

    await chrome.storage.local.set(settings);

    // SETTINGS_SAVED n'attend pas de réponse — on ignore l'erreur de channel fermé
    chrome.runtime.sendMessage({ type: "SETTINGS_SAVED" }).catch(() => {});

    // ── Visual confirmation ──────────────────────────────────────────────────
    btn.textContent  = "✓ Saved !";
    btn.style.background = "#2e6b3e";
    btn.disabled = false;
    setFeedback("✓ Settings saved successfully", "success");

    setTimeout(() => {
      btn.textContent = "Save";
      btn.style.background = "";
    }, 2500);
  });

  // ── Helpers ───────────────────────────────────────────────────────────────

  function setFeedback(msg, type) {
    const el = document.getElementById("feedback");
    el.textContent = msg;
    el.className = `feedback ${type}`;
    setTimeout(() => { el.className = "feedback"; }, 4000);
  }

})();
