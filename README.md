# TurboDownloader

A desktop download manager built for Jellyfin, Plex, and Emby server admins who need to bulk-download media libraries from HTTP indexes. Supports recursive directory crawling, multipart downloads, resume, and a bunch of quality-of-life features that make managing large media collections less painful.

---

## Features

- **Recursive HTML crawl** — paste a directory URL and TurboDownloader walks the entire tree, filters by extension, and presents a file selection popup
- **Multi-URL input** — paste multiple URLs at once (one per line, or space-separated), each one is processed sequentially
- **Multipart download** — splits files into N parallel segments for faster speeds on servers that support `Accept-Ranges`
- **Resume support** — interrupted downloads resume from where they left off via `.part` files in a configurable temp folder
- **Pause / Resume / Cancel** — per-file controls with proper cleanup of partial files on cancel
- **Auto retry** — configurable retry count and exponential backoff on network errors
- **Bandwidth throttle** — global MB/s cap shared across all active workers
- **Download history** — logs every completed download with size, duration, and date; one-click re-download
- **File tree popup** — expand/collapse folders, real-time search, sort by name or folders-first, check/uncheck subtrees
- **Configurable extensions** — choose which file types to detect during crawl, add custom ones
- **Windows taskbar progress** — live progress bar in the taskbar via `ITaskbarList3`
- **Desktop notifications** — batch completion alerts via `plyer`
- **Temp folder → move pattern** — files are written to a temp location first, then moved atomically to the destination so Jellyfin/Plex never sees incomplete files
- **Remote control** — control a TurboDownloader instance running on another machine over HTTPS; send downloads remotely, monitor progress, browse the server's filesystem

---

## Requirements

```
Python 3.10+
customtkinter
requests
beautifulsoup4
```

Optional (graceful fallback if missing):
```
plyer           # desktop notifications
comtypes        # Windows taskbar progress
```

Optional (required for Remote Control feature):
```
fastapi
uvicorn[standard]
python-jose[cryptography]
bcrypt
httpx
cryptography
```

Install all dependencies:
```bash
pip install customtkinter requests beautifulsoup4
pip install plyer comtypes                              # optional
pip install fastapi "uvicorn[standard]" python-jose[cryptography] bcrypt httpx cryptography  # remote control
```

---

## Usage

```bash
python main.py
```

1. Paste one or more URLs in the input box (directory URLs or direct file links)
2. Choose a destination folder
3. Hit **START** — directory URLs open a file tree popup, direct file URLs start immediately
4. Use the filter buttons to monitor downloads by state

---

## Remote Control

TurboDownloader supports a client/server mode over HTTPS, allowing you to send downloads to a machine running TurboDownloader from another PC on your network (or over the internet with port forwarding).

### Setting up the server

1. Open **Settings → Remote control — Server**
2. Toggle the server **on**
3. Set a username and password
4. Click **Generate now** to create a self-signed SSL certificate
5. Open port **9988** in Windows Firewall:
   ```
   netsh advfirewall firewall add rule name="TurboDownloader Remote" dir=in action=allow protocol=TCP localport=9988
   ```
6. The sidebar shows `📡 Server mode — listening on :9988` when active

### Connecting as a client

1. Open **Settings → Remote control — Client**
2. Enter the server's **Host / IP**, **Port**, **Username**, and **Password**
3. Optionally set a **Remote dest.** — the default download folder on the server machine. Click **📂 Browse…** (available once connected) to navigate the server's filesystem directly.
4. Click **Connect** — the sidebar shows `🔗 Client mode — connected to host:port`

Once connected, any URL you paste and start will be sent to the server instead of downloading locally. Downloads sent remotely appear with a 📡 badge on the server's queue.

### Notes

- The SSL certificate is self-signed — your browser or OS may show a security warning, which is expected and safe on a private network.
- The password is never stored in plain text — bcrypt hashed in `settings.json`.
- To disconnect, click the **✕** button in the remote status bar in the sidebar.
- Default port: **9988**. Configurable in Settings.

---

## Project structure

```
main.py              # entry point
downloader.py        # main app window, crawl, workers, orchestration
models.py            # DownloadItem and SegmentInfo dataclasses
widgets.py           # DownloadRow widget
tree_popup.py        # file selection popup (tree, search, sort)
ytdlp_popup.py       # yt-dlp quality selection popup
settings_popup.py    # settings window + load/save config
history.py           # download history manager + popup
remote_server.py     # remote control server (FastAPI) + client
notifier.py          # desktop notification wrapper
taskbar.py           # Windows taskbar progress (ITaskbarList3 via comtypes)
ffmpeg_setup.py      # ffmpeg and Node.js detection/setup
ytdlp_worker.py      # yt-dlp download worker
```

Config, history, and SSL certificates are stored in `~/.turbodownloader/`.

---

## Settings

| Setting | Description |
|---|---|
| Temp folder | Where `.part` files are written during download |
| Max retries | Number of automatic retries on network errors (exponential backoff) |
| Bandwidth limit | Global cap in MB/s across all workers (0 = unlimited) |
| Segments | Number of parallel segments per file (1 = disabled, requires `Accept-Ranges`) |
| Extensions | Which file extensions to detect during crawl |
| Notifications | Enable/disable batch completion desktop alerts |
| Remote Server | Enable HTTPS server, set credentials, generate SSL certificate |
| Remote Client | Connect to a remote TurboDownloader instance |

---

## Building from source

### Prerequisites

```bash
pip install pyinstaller
```

Place `ffmpeg.exe` (from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) → `ffmpeg-release-essentials.zip` → `bin/ffmpeg.exe`) and `icon.ico` next to `TurboDownloader.spec`.

### Step 1 — Build with PyInstaller

```bash
pyinstaller TurboDownloader.spec
```

Output: `dist/TurboDownloader/` folder containing the application and all dependencies.

### Step 2 — Create the installer (Windows)

1. Install [Inno Setup 6](https://jrsoftware.org/isinfo.php) (free)
2. Open `TurboDownloader.iss` in Inno Setup Compiler
3. Click **Build → Compile** (or run `iscc TurboDownloader.iss` from the command line)

Output: `installer/TurboDownloader_Setup.exe` — a single self-contained installer ready to distribute.

### Optional: UPX compression

Install [UPX](https://upx.github.io/) and add it to your PATH to reduce the final size by ~30%. PyInstaller will use it automatically when building.

---

## Legal disclaimer

TurboDownloader is designed to download files from HTTP servers **that you own, administrate, or have explicit permission to access** — such as your own Jellyfin, Plex, or Emby instance, or any server whose operator has granted you download rights.

**This tool is provided for lawful purposes only.** The author does not endorse, encourage, or condone the use of this software to:
- download copyrighted content without authorization,
- bypass access controls or authentication mechanisms,
- infringe on the intellectual property rights of any individual or organization,
- or violate any applicable local, national, or international law.

**You are solely responsible for how you use this software.** By using TurboDownloader, you agree that the author cannot be held liable for any misuse, damage, legal action, or consequence arising from your use of this tool. The author provides this software "as is", without warranty of any kind, and expressly disclaims any responsibility for unlawful or unauthorized use.

If you are unsure whether downloading a particular resource is legal in your jurisdiction, do not use this tool for that purpose.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
