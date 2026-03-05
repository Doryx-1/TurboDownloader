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
plyer       # desktop notifications
comtypes    # Windows taskbar progress
```

Install dependencies:
```bash
pip install customtkinter requests beautifulsoup4
pip install plyer comtypes   # optional
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

## Project structure

```
main.py              # entry point
downloader.py        # main app window, crawl, workers, orchestration
models.py            # DownloadItem and SegmentInfo dataclasses
widgets.py           # DownloadRow widget
tree_popup.py        # file selection popup (tree, search, sort)
settings_popup.py    # settings window + load/save config
history.py           # download history manager + popup
notifier.py          # desktop notification wrapper
taskbar.py           # Windows taskbar progress (ITaskbarList3 via comtypes)
```

Config and history are stored in `~/.turbodownloader/`.

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
