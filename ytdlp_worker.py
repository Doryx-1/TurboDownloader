"""
ytdlp_worker.py — yt-dlp integration for TurboDownloader.

Handles detection and downloading of streaming URLs
(YouTube, Vimeo, Twitch, TikTok, etc.) via the yt-dlp Python API.
"""

import os
import time
from urllib.parse import urlparse


# ── Domains routed to yt-dlp ────────────────────────────────────────────────

YTDLP_DOMAINS = (
    "youtube.com", "youtu.be",
    "vimeo.com",
    "dailymotion.com",
    "twitch.tv",
    "twitter.com", "x.com",
    "instagram.com",
    "facebook.com", "fb.watch",
    "tiktok.com",
    "reddit.com", "v.redd.it",
)


def is_ytdlp_url(url: str) -> bool:
    """Returns True if the URL should be handled by yt-dlp."""
    try:
        host = urlparse(url).netloc.lower().lstrip("www.")
        return any(host == d or host.endswith("." + d) for d in YTDLP_DOMAINS)
    except Exception:
        return False


def check_ytdlp() -> bool:
    """Returns True if yt-dlp is importable."""
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def _ffmpeg_available() -> bool:
    """Returns True if ffmpeg is findable on PATH."""
    import shutil
    return shutil.which("ffmpeg") is not None


def _best_format() -> str:
    """
    Returns the best yt-dlp format string depending on ffmpeg availability.
    - With ffmpeg    : separate video+audio streams merged → best quality
    - Without ffmpeg : pre-merged format only, no merge step needed
    """
    if _ffmpeg_available():
        return "bestvideo+bestaudio/best"
    return "best"


def fetch_formats(url: str) -> dict:
    """
    Fetches available formats for a streaming URL without downloading.

    Returns a dict:
    {
        "title":     str,
        "thumbnail": str,           # URL of the thumbnail (best available)
        "duration":  int,           # seconds
        "qualities": [              # deduplicated, sorted best-first
            {"label": "1080p", "format_id": "...", "ext": "mp4", "filesize": int|None},
            ...
        ]
    }
    Returns None on error.
    """
    try:
        import yt_dlp
    except ImportError:
        return None

    ydl_opts = {
        "quiet":   True,
        "no_color": True,
        "skip_download": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        print(f"[ytdlp] fetch_formats error: {e}")
        return None

    if not info:
        return None

    # ── Collect unique resolutions from all formats ──────────────────────
    seen_heights = set()
    qualities = []

    formats = info.get("formats") or []
    # Sort by height descending so we pick best format per resolution
    formats_sorted = sorted(
        formats,
        key=lambda f: (f.get("height") or 0, f.get("tbr") or 0),
        reverse=True,
    )

    for f in formats_sorted:
        height = f.get("height")
        if not height:
            continue
        if height in seen_heights:
            continue
        seen_heights.add(height)
        label = f"{height}p"
        # Prefer format with both video+audio if available
        has_audio = (f.get("acodec") or "none") != "none"
        has_video = (f.get("vcodec") or "none") != "none"
        if not has_video:
            continue
        qualities.append({
            "label":     label,
            "height":    height,
            "format_id": f.get("format_id", ""),
            "ext":       f.get("ext", "mp4"),
            "filesize":  f.get("filesize") or f.get("filesize_approx"),
            "has_audio": has_audio,
        })

    # Always add "best" as fallback at top
    qualities.insert(0, {
        "label":     "Best available",
        "height":    99999,
        "format_id": "bestvideo+bestaudio/best" if _ffmpeg_available() else "best",
        "ext":       "mkv" if _ffmpeg_available() else "mp4",
        "filesize":  None,
        "has_audio": True,
    })

    # Thumbnail — pick best
    thumbnails = info.get("thumbnails") or []
    thumbnail_url = ""
    if thumbnails:
        # Prefer highest resolution
        best_thumb = max(thumbnails,
                         key=lambda t: (t.get("width") or 0) * (t.get("height") or 0),
                         default=None)
        thumbnail_url = (best_thumb or {}).get("url", "") or info.get("thumbnail", "")

    return {
        "title":     info.get("title", url),
        "thumbnail": thumbnail_url,
        "duration":  info.get("duration", 0),
        "uploader":  info.get("uploader", ""),
        "qualities": qualities,
    }




def run(idx: int, app) -> None:
    """
    yt-dlp download worker.
    Reads quality settings from it.yt_format_id and it.yt_audio_only.
    """
    it = app.items[idx]

    audio_only = getattr(it, "yt_audio_only", False)
    format_id  = getattr(it, "yt_format_id",  None)  # None → use default best

    # ── yt-dlp availability check ────────────────────────────────────────────
    try:
        import yt_dlp
    except ImportError:
        it.state     = "error"
        it.error_msg = "yt-dlp not installed — run: pip install yt-dlp"
        app.ui(app._update_row_ui, idx)
        app.ui(app._refresh_filter_counts)
        return

    # ── Early cancel check ───────────────────────────────────────────────────
    if app.stop_all_event.is_set() or it.cancel_event.is_set():
        it.state = "canceled"
        app.ui(app._update_row_ui, idx)
        app.ui(app._refresh_filter_counts)
        return

    it.state      = "downloading"
    it.started_at = time.time()
    it.error_msg  = ""
    app.ui(app._update_row_ui, idx)
    app.ui(app._refresh_filter_counts)

    dest_dir = os.path.dirname(it.dest_path)
    os.makedirs(dest_dir, exist_ok=True)

    # ── Progress hook ────────────────────────────────────────────────────────
    def _progress_hook(d: dict) -> None:
        if app.stop_all_event.is_set() or it.cancel_event.is_set():
            raise yt_dlp.utils.DownloadCancelled()

        status = d.get("status", "")

        if status == "downloading":
            total   = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            current = d.get("downloaded_bytes", 0)

            it.total_size  = total or None
            it.downloaded  = current
            it.resume_from = 0

            # Feed global speed tracker with the delta since last hook call
            delta = current - getattr(it, "_yt_last_bytes", 0)
            if delta > 0:
                it._yt_last_bytes = current   # type: ignore[attr-defined]
                app._record_bytes(delta)
                it.speed_window.append((time.time(), delta))

            app.ui(app._update_row_ui, idx)

        elif status == "finished":
            # File downloaded — post-processing (remux/convert) starting
            it.state      = "moving"   # "moving" displays as "Converting…" for yt-dlp
            it.total_size = d.get("total_bytes") or it.total_size
            it.downloaded = it.total_size or it.downloaded
            app.ui(app._update_row_ui, idx)
            app.ui(app._refresh_filter_counts)

    # ── yt-dlp options ───────────────────────────────────────────────────────
    has_ffmpeg = _ffmpeg_available()

    if audio_only:
        fmt = "bestaudio/best"
    elif format_id:
        # Specific resolution chosen in popup — add bestaudio for merging if needed
        fmt = f"{format_id}+bestaudio/{format_id}" if has_ffmpeg else format_id
    else:
        fmt = _best_format()

    ydl_opts = {
        "format":         fmt,
        "outtmpl":        os.path.join(dest_dir, "%(title)s.%(ext)s"),
        "quiet":          True,
        "no_color":       True,
        "progress_hooks": [_progress_hook],
        "noprogress":     False,
    }
    if audio_only and has_ffmpeg:
        # Convert to mp3
        ydl_opts["postprocessors"] = [{
            "key":            "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "320",
        }]
        ydl_opts["outtmpl"] = os.path.join(dest_dir, "%(title)s.%(ext)s")
    elif has_ffmpeg:
        ydl_opts["merge_output_format"] = "mkv"

    # ── Download ─────────────────────────────────────────────────────────────
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(it.url, download=True)

            # Update row name with the real video title
            if info:
                final_name = ydl.prepare_filename(info)
                it.filename  = os.path.basename(final_name)
                it.dest_path = final_name
                if idx in app.rows:
                    app.ui(lambda n=it.filename:
                           app.rows[idx].name_lbl.configure(text=n)
                           if idx in app.rows else None)

    except yt_dlp.utils.DownloadCancelled:
        it.state = "canceled"
        app.ui(app._update_row_ui, idx)
        app.ui(app._refresh_filter_counts)
        return

    except Exception as e:
        it.state     = "error"
        it.error_msg = str(e)[:120]
        app.ui(app._update_row_ui, idx)
        app.ui(app._refresh_filter_counts)
        return

    # ── Success ──────────────────────────────────────────────────────────────
    duration = time.time() - it.started_at
    size     = it.total_size or it.downloaded
    app._history.log_entry(
        filename=it.filename,
        url=it.url,
        size_bytes=size,
        duration_s=duration,
    )
    it.state = "done"
    app.ui(app._update_row_ui, idx)
    app.ui(app._refresh_filter_counts)
