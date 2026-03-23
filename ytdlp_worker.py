"""
ytdlp_worker.py — yt-dlp integration for TurboDownloader.

Handles detection and downloading of streaming URLs
(YouTube, Vimeo, Twitch, TikTok, etc.) via the yt-dlp Python API.
"""

import os
import time
from urllib.parse import urlparse
import ffmpeg_setup


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
    """Delegates to ffmpeg_setup for consistent ffmpeg detection."""
    return ffmpeg_setup.ffmpeg_available()


def _best_format() -> str:
    """
    Returns the best yt-dlp format string depending on ffmpeg availability.
    - With ffmpeg    : separate video+audio streams merged → best quality
    - Without ffmpeg : pre-merged format only, no merge step needed
    """
    if _ffmpeg_available():
        return "bestvideo+bestaudio/best"
    return "best"


def _extract_qualities(info: dict) -> list:
    """Extracts deduplicated quality list from a single video info dict."""
    seen_heights = set()
    qualities = []
    formats = info.get("formats") or []
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
        has_video = (f.get("vcodec") or "none") != "none"
        if not has_video:
            continue
        qualities.append({
            "label":     f"{height}p",
            "height":    height,
            "format_id": f.get("format_id", ""),
            "ext":       f.get("ext", "mp4"),
            "filesize":  f.get("filesize") or f.get("filesize_approx"),
            "has_audio": (f.get("acodec") or "none") != "none",
        })
    # Always prepend "Best available"
    qualities.insert(0, {
        "label":     "Best available",
        "height":    99999,
        "format_id": "bestvideo+bestaudio/best" if _ffmpeg_available() else "best",
        "ext":       "mkv" if _ffmpeg_available() else "mp4",
        "filesize":  None,
        "has_audio": True,
    })
    return qualities


def _best_thumbnail(info: dict) -> str:
    """Returns the best thumbnail URL from an info dict."""
    thumbnails = info.get("thumbnails") or []
    if thumbnails:
        best = max(thumbnails,
                   key=lambda t: (t.get("width") or 0) * (t.get("height") or 0),
                   default=None)
        return (best or {}).get("url", "") or info.get("thumbnail", "")
    return info.get("thumbnail", "")


def fetch_formats(url: str) -> dict | None:
    """
    Fetches available formats/entries for a streaming URL without downloading.

    For a single video, returns:
    {
        "type":      "video",
        "title":     str,
        "thumbnail": str,
        "duration":  int,
        "uploader":  str,
        "url":       str,
        "qualities": [ {"label", "height", "format_id", "ext", "filesize", "has_audio"}, ... ]
    }

    For a playlist, returns:
    {
        "type":     "playlist",
        "title":    str,
        "uploader": str,
        "url":      str,
        "entries":  [
            {
                "title":     str,
                "thumbnail": str,
                "duration":  int,
                "url":       str,
                "qualities": [ ... ]   # may be empty if not extracted
            },
            ...
        ],
        "qualities": [ ... ]   # union of qualities from first entry (used as default)
    }

    Returns None on error.
    """
    try:
        import yt_dlp
    except ImportError:
        return None

    ydl_opts = {
        "quiet":         True,
        "no_color":      True,
        "skip_download": True,
        "extract_flat":  "in_playlist",
    }
    ffmpeg_setup.configure_yt_dlp_node(ydl_opts)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        print(f"[ytdlp] fetch_formats error: {e}")
        return None

    if not info:
        return None

    # ── Playlist detection ───────────────────────────────────────────────────
    entries = info.get("entries")
    if entries is not None:
        # It's a playlist
        entry_list = []
        for e in entries:
            if not e:
                continue
            entry_url = e.get("url") or e.get("webpage_url") or e.get("id")
            if not entry_url:
                continue
            # Ensure full URL for flat entries (may only have video id)
            if not entry_url.startswith("http"):
                entry_url = f"https://www.youtube.com/watch?v={entry_url}"
            entry_list.append({
                "title":     e.get("title") or e.get("id") or entry_url,
                "thumbnail": e.get("thumbnail") or e.get("thumbnails", [{}])[0].get("url", "") if e.get("thumbnails") else e.get("thumbnail", ""),
                "duration":  e.get("duration") or 0,
                "url":       entry_url,
                "qualities": [],   # populated lazily — not fetched upfront for speed
            })

        # Use qualities from playlist-level info if available, else empty
        top_qualities = _extract_qualities(info) if info.get("formats") else [{
            "label":     "Best available",
            "height":    99999,
            "format_id": "bestvideo+bestaudio/best" if _ffmpeg_available() else "best",
            "ext":       "mkv" if _ffmpeg_available() else "mp4",
            "filesize":  None,
            "has_audio": True,
        }]

        return {
            "type":      "playlist",
            "title":     info.get("title") or info.get("playlist_title") or url,
            "uploader":  info.get("uploader") or info.get("channel") or "",
            "url":       url,
            "entries":   entry_list,
            "qualities": top_qualities,
        }

    # ── Single video ─────────────────────────────────────────────────────────
    # Re-fetch without extract_flat to get full format list
    ydl_opts_full = {
        "quiet":         True,
        "no_color":      True,
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts_full) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        print(f"[ytdlp] fetch_formats (full) error: {e}")
        return None

    return {
        "type":      "video",
        "title":     info.get("title", url),
        "thumbnail": _best_thumbnail(info),
        "duration":  info.get("duration", 0),
        "uploader":  info.get("uploader", ""),
        "url":       url,
        "qualities": _extract_qualities(info),
    }


def run(idx: int, app) -> None:
    """
    yt-dlp download worker.
    Reads quality settings from it.yt_format_id and it.yt_audio_only.
    """
    it = app.items[idx]

    audio_only  = it.yt_audio_only
    format_id   = it.yt_format_id
    is_retry    = it.yt_retry  # True on auto-retry attempt

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
    _last_ui_update = 0.0   # throttle: max 5 UI updates/s per item

    def _progress_hook(d: dict) -> None:
        nonlocal _last_ui_update
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

            # Throttle UI updates to max 5/s
            now = time.time()
            if now - _last_ui_update >= 0.20:
                _last_ui_update = now
                app.ui(app._update_row_ui, idx)

        elif status == "finished":
            # File downloaded — post-processing (remux/convert) starting
            it.state      = "moving"   # "moving" displays as "Converting…" for yt-dlp
            it.total_size = d.get("total_bytes") or it.total_size
            it.downloaded = it.total_size or it.downloaded
            _last_ui_update = time.time()
            app.ui(app._update_row_ui, idx)
            app.ui(app._refresh_filter_counts)

    # ── yt-dlp options ───────────────────────────────────────────────────────
    has_ffmpeg = _ffmpeg_available()

    # Log ffmpeg status — utile pour débugger les problèmes de conversion
    import logging as _logging
    _wlog = _logging.getLogger("turbodownloader.ytdlp_worker")
    _wlog.debug("ffmpeg available: %s | path: %s | audio_only: %s | format_id: %s",
                has_ffmpeg, ffmpeg_setup.ffmpeg_path(), audio_only, format_id)

    if audio_only:
        if has_ffmpeg:
            # Force audio-only stream — never match a video format
            # bestaudio picks the best audio-only stream (opus/m4a/webm)
            # FFmpegExtractAudio then converts it to mp3
            fmt = "bestaudio"
        else:
            # No ffmpeg on this machine → can't convert to mp3
            # Warn the user via error_msg but still download best audio
            _wlog.warning("Audio-only requested but ffmpeg not found — will download m4a/webm, not mp3")
            it.error_msg = "⚠ ffmpeg not found on server — downloading best audio (not mp3)"
            app.ui(app._update_row_ui, idx)
            fmt = "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio"
    elif format_id:
        # Specific resolution chosen in popup
        fmt = f"{format_id}+bestaudio/{format_id}" if has_ffmpeg else format_id
    else:
        # "Best available" — prefer 1080p, allow lower if not available
        # Works with or without ffmpeg: yt-dlp picks best pre-merged if no ffmpeg
        fmt = (
            "bestvideo[height<=1080]+bestaudio/bestvideo+bestaudio/best"
            if has_ffmpeg else
            "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height<=1080]+bestaudio/"
            "best[height<=1080]/best"
        )

    ydl_opts = {
        "format":         fmt,
        "outtmpl":        os.path.join(dest_dir, "%(title)s.%(ext)s"),
        "quiet":          True,
        "no_color":       True,
        "progress_hooks": [_progress_hook],
        "noprogress":     False,
    }
    if audio_only and has_ffmpeg:
        # FFmpegExtractAudio converts the downloaded audio stream to mp3
        # keepvideo=False ensures the original file is deleted after conversion
        ydl_opts["postprocessors"] = [{
            "key":              "FFmpegExtractAudio",
            "preferredcodec":   "mp3",
            "preferredquality": "320",
        }]
        ydl_opts["keepvideo"] = False
        # Never set merge_output_format in audio-only mode — it would override
        # the FFmpegExtractAudio postprocessor and produce MKV instead of MP3
    elif has_ffmpeg:
        ydl_opts["merge_output_format"] = "mkv"
    else:
        pass  # No ffmpeg — m4a stays as-is

    # ── Inject ffmpeg location and Node.js runtime ───────────────────────────
    ffmpeg_setup.configure_yt_dlp_ffmpeg(ydl_opts)
    ffmpeg_setup.configure_yt_dlp_node(ydl_opts)

    # ── On retry: force web player client to bypass n-challenge failures ─────
    if is_retry:
        ydl_opts["extractor_args"] = {
            "youtube": {"player_client": ["web"], "player_skip": ["webpage"]}
        }

    # ── Download ─────────────────────────────────────────────────────────────
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(it.url, download=True)

            # Update row name with the real video title
            if info:
                final_name = ydl.prepare_filename(info)
                # For audio-only with ffmpeg: postprocessor renames to .mp3
                # prepare_filename returns the pre-conversion extension (.webm/.m4a)
                # so we must override it manually
                if audio_only and has_ffmpeg:
                    base = os.path.splitext(final_name)[0]
                    final_name = base + ".mp3"
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
