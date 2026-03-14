# TurboDownloader — Browser Extension

Sends downloads from your browser directly to the TurboDownloader desktop app.

## Features
- **Popup** — scans the current page for downloadable links, select and send with one click
- **Context menu** — right-click any link → "Send to TurboDownloader"
- **Auto-intercept** — automatically redirects matching file downloads to TurboDownloader
- **Badge** — shows the number of downloadable links detected on the current page

## Requirements
- TurboDownloader desktop app v2.4+ with Remote Server enabled
- Chrome 109+, Edge 109+, or Firefox 109+

## Installation

### Chrome / Edge
1. Open `chrome://extensions` (or `edge://extensions`)
2. Enable **Developer mode** (top right toggle)
3. Click **Load unpacked**
4. Select the `turbodownloader-extension/` folder

### Firefox
1. Open `about:debugging#/runtime/this-firefox`
2. Click **Load Temporary Add-on**
3. Select the `manifest.json` file inside the extension folder

> For permanent Firefox installation, the extension needs to be signed via AMO.

## Setup
1. In TurboDownloader desktop → Settings → Remote Server → enable the server and set credentials
2. Click the extension icon → ⚙ Settings
3. Enter Host (`127.0.0.1` for local, or IP for remote), Port (`9988`), Username, Password
4. Click **Test connection** to verify
5. Click **Save**

## Usage

### Popup
- Click the extension icon to open the popup
- Downloadable links on the page are listed automatically
- Check the ones you want and click **Send to TurboDownloader**
- Optionally set a destination folder (resolved on the server)

### Context menu
- Right-click any link on a page → **Send to TurboDownloader**
- Right-click anywhere on a page → **Send all links on this page**

### Auto-intercept
- Enable in popup or settings
- Any download matching the configured extensions is automatically intercepted and sent to TurboDownloader instead of downloading in the browser

## Security note
The extension communicates over HTTPS with a self-signed certificate.
Chrome/Edge may show a certificate warning for localhost — this is expected.
The connection uses JWT authentication matching the desktop app's credentials.
