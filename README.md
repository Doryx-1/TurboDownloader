# TurboDownloader

> A powerful desktop download manager built for Jellyfin, Plex, and Emby server admins - with remote control, browser extension, and bulk media downloading built in.

![Version](https://img.shields.io/badge/version-2.7.5-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![License](https://img.shields.io/badge/license-Apache%202.0-green)

---

## What is TurboDownloader?

TurboDownloader lets you bulk-download media libraries from HTTP indexes directly to your media server. Paste a directory URL, browse the file tree, pick what you want, and let it run - with multipart downloads, resume support, bandwidth throttling, and full remote control from any machine on your network or the internet.

---

## Features

### Core downloader
- **Recursive HTML crawl** - paste a directory URL and TurboDownloader walks the entire tree, filters by extension, and presents a file selection popup
- **Multi-URL input** - paste multiple URLs at once (one per line, or space-separated)
- **Multipart download** - splits files into N parallel segments for faster speeds on servers that support `Accept-Ranges`
- **Resume support** - interrupted downloads resume from where they left off via `.part` files
- **Pause / Resume / Cancel** - per-file controls with proper cleanup of partial files on cancel
- **Auto retry** - configurable retry count and exponential backoff on network errors
- **Bandwidth throttle** - global MB/s cap shared across all active workers
- **Download history** - logs every completed download with size, duration, and date; one-click re-download
- **File tree popup** - expand/collapse folders, real-time search, sort by name or folders-first, check/uncheck subtrees
- **Configurable extensions** - choose which file types to detect during crawl, add custom ones
- **Windows taskbar progress** - live progress bar in the taskbar via `ITaskbarList3`
- **Desktop notifications** - batch completion alerts
- **Temp folder + move pattern** - files are written to a temp location first, then moved atomically so Jellyfin/Plex never sees incomplete files

### Streaming (yt-dlp)
- **Quality selection popup** - choose resolution, format, and audio-only mode before starting
- **ffmpeg integration** - automatic muxing of video and audio streams

### Remote control
- **Server mode** - expose TurboDownloader as a secured HTTPS API on your local network or internet
- **Client mode** - control a remote TurboDownloader instance from any other machine
- **JWT authentication** - token-based auth with bcrypt password hashing and configurable token lifetime
- **HTTPS** - self-signed SSL certificate auto-generated on first launch
- **Remote file browser** - browse the server's filesystem directly from the client to pick destination folders
- **Version gating** - client and server must run the same version; connection is hard-blocked if versions differ, with a popup offering to update whichever side is outdated
- **Remote update** - trigger a silent update on the server directly from the client when versions are mismatched; the server downloads and installs the latest release automatically
- **Heartbeat** - automatic reconnect on connection loss; version mismatch detected on reconnect surfaces a new popup instead of silently failing
- **📡 badge** - downloads sent by a remote client are clearly identified in the queue

### Browser extension (Chrome / Edge / Firefox)
- **Inline download buttons** - a `⬇` button appears next to every downloadable link on the page
- **Download all bar** - a sticky bar at the top of the page when multiple files are detected
- **Right-click menu** - "Send to TurboDownloader" on any link, or "Send all links on this page"
- **Auto-intercept** - automatically redirects matching file downloads to TurboDownloader instead of the browser
- **Badge counter** - the extension icon shows the number of downloadable links detected on the current tab
- **Native popup** - sending links opens TurboDownloader's own file tree popup so you can pick the destination folder naturally

### System
- **System tray** - minimize to tray, restore from tray icon
- **Single instance** - launching a second instance forwards the URL to the running one instead of opening a new window
- **Custom protocol** - `turbodownloader://` URI scheme registered at install, used by the browser extension to wake the app
- **Auto-update** - checks GitHub releases at startup and offers to download and install the latest version

---

## Installation

Download `TurboDownloader_Setup.exe` from the [Releases](../../releases) page and run it. No Python required.

The browser extension `.crx` is also available on the Releases page.

---

## Quick start

1. Paste one or more URLs in the input box (directory URLs or direct file links)
2. Choose a destination folder
3. Hit **Start** - directory URLs open a file tree popup to select which files to download
4. Use the filter buttons to monitor downloads by state

---

## Remote Control - Setup guide

The remote control feature lets you send downloads to a TurboDownloader instance running on another machine - for example, sending a download from your laptop directly to your media server.

### Setting up the server

On the machine that will receive and run the downloads (your media server):

1. Open **Settings > Remote control - Server**
2. Toggle the server **on**
3. Set a **username** and **password**
4. Click **Generate now** to create the SSL certificate
5. Open port **9988** in Windows Firewall:
   ```
   netsh advfirewall firewall add rule name="TurboDownloader Remote" dir=in action=allow protocol=TCP localport=9988
   ```
6. The sidebar shows `📡 Server mode - listening on :9988` when active

### Connecting as a client

On the machine you want to control from (your laptop, another PC):

1. Open **Settings > Remote control - Client**
2. Enter the server's **Host / IP**, **Port** (`9988`), **Username**, and **Password**
3. Optionally set a **Remote dest.** - click **Browse...** once connected to navigate the server's filesystem and pick a default destination folder
4. Click **Connect** - the sidebar shows `Client mode - connected to host:port`

Once connected, any URL you start will be sent to the server. TurboDownloader opens its file tree popup so you can pick the destination folder.

To disconnect, click the **X** button in the remote status bar.

> **Note:** The connection uses self-signed HTTPS. Port forwarding on your router is required for internet access outside your local network.

### Version gating

Client and server must be on the same version. If they differ, the connection is refused and a popup appears offering to update whichever side is outdated:

- If the server is older: the client sends an update request, the server silently downloads and installs the latest release, then restarts.
- If the client is older: a standard update popup opens for the client.

---

## Browser Extension - Setup guide

### Installation

**Chrome / Edge - using the CRX file (recommended):**
1. Download `WebExtension.crx` from the [Releases](../../releases) page
2. Go to `chrome://extensions` or `edge://extensions`
3. Enable **Developer mode**
4. Drag and drop the `.crx` file onto the page

**Chrome / Edge - load unpacked:**
1. Download and unzip the extension folder
2. Go to `chrome://extensions` or `edge://extensions`
3. Enable **Developer mode**
4. Click **Load unpacked** and select the unzipped folder

**Firefox:**
1. Go to `about:debugging#/runtime/this-firefox`
2. Click **Load Temporary Add-on**
3. Select `manifest.json` inside the extension folder

### Configuration

1. Make sure TurboDownloader is running with **Remote Server enabled** (see above)
2. Click the extension icon > **Settings**
3. Enter:
   - **Host**: `127.0.0.1` if TurboDownloader is on the same machine, or the server's IP
   - **Port**: `9988`
   - **Username** and **Password**: same as configured in TurboDownloader
4. Click **Test connection** - you should see `Connected successfully`
5. Click **Save**

### Usage

| Method | How |
|---|---|
| **Inline button** | Click the `⬇` button that appears next to any downloadable link on the page |
| **Download all bar** | Click `⬇ Download all (N)` in the sticky bar at the top of the page |
| **Right-click a link** | Right-click > **Send to TurboDownloader** |
| **Right-click the page** | Right-click > **Send all links on this page** |
| **Auto-intercept** | Enable in Settings - browser downloads of matching files are automatically redirected |

When you send a link, TurboDownloader comes to the front and opens its file tree popup so you can choose the destination folder.

---

## Settings reference

| Setting | Description |
|---|---|
| Temp folder | Where `.part` files are written during download |
| Max retries | Number of automatic retries on network errors (exponential backoff) |
| Bandwidth limit | Global cap in MB/s across all workers (0 = unlimited) |
| Segments | Number of parallel segments per file (1 = disabled, requires `Accept-Ranges`) |
| Extensions | Which file extensions to detect during crawl |
| Notifications | Enable/disable batch completion desktop alerts |
| Check for updates | Automatic update check at startup |
| Remote Server | Enable HTTPS server, set credentials, configure token lifetime, generate SSL certificate |
| Remote Client | Connect to a remote TurboDownloader instance, browse remote filesystem |

Config, history, and SSL certificates are stored in `~/.turbodownloader/`.

---

## Security

v2.7.5 includes 9 security fixes:

| # | Area | Fix |
|---|---|---|
| 1 | **HTTPS** | Re-enabled TLS (`use_ssl=True`); browser extension updated to `https://127.0.0.1:9988` |
| 2 | **Credentials in URL** | `/admin/trigger-update` credentials moved from query params to JSON body |
| 3 | **Installer integrity** | SHA-256 verification of the downloaded installer before launch (best-effort, non-blocking if no `.sha256` asset is present) |
| 4 | **Path traversal** | `unquote()` applied before `basename()` + illegal filename characters stripped on save |
| 5 | **Encryption key** | Fernet key is now a random key stored in `~/.turbodownloader/keystore` instead of a machine-derived key |
| 6 | **Profile passwords** | Connection profile passwords are now encrypted with `_encrypt_password()` at rest |
| 7 | **Password hashing** | SHA-256 fallback removed from `hash_password()` — bcrypt is required; a migration path is kept in `verify_password()` for existing hashes |
| 8 | **Brute-force protection** | Login endpoint no longer blocks with a `sleep()` — responds immediately with `429 Too Many Requests` + `Retry-After` header |
| 9 | **Token TTL** | Local token expiry is now enforced: timestamp stored in JSON and verified on every call |

---

## Legal disclaimer

TurboDownloader is designed to download files from HTTP servers **that you own, administrate, or have explicit permission to access** - such as your own Jellyfin, Plex, or Emby instance, or any server whose operator has granted you download rights.

**This tool is provided for lawful purposes only.** The author does not endorse, encourage, or condone the use of this software to:
- download copyrighted content without authorization,
- bypass access controls or authentication mechanisms,
- infringe on the intellectual property rights of any individual or organization,
- or violate any applicable local, national, or international law.

**You are solely responsible for how you use this software.** By using TurboDownloader, you agree that the author cannot be held liable for any misuse, damage, legal action, or consequence arising from your use of this tool. The author provides this software "as is", without warranty of any kind, and expressly disclaims any responsibility for unlawful or unauthorized use.

---

## License

Apache 2.0 - see [LICENSE](LICENSE).
