import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, List


# ── Multipart segment ───────────────────────────────────────────────────────

@dataclass
class SegmentInfo:
    """Describes a segment of a multipart download."""
    index:      int             # 0-based segment index
    byte_start: int             # first byte (inclusive)
    byte_end:   int             # last byte (inclusive)
    temp_path:  str  = ""       # path to the .part.N temp file
    downloaded: int  = 0        # bytes received for THIS segment
    done:       bool = False    # segment fully downloaded
    error:      str  = ""       # error message if failed


# ── Download item ───────────────────────────────────────────────────────────

@dataclass
class DownloadItem:
    url: str
    filename: str
    dest_path: str
    relative_path: str = ""         # relative subfolder (original tree structure)

    total_size: Optional[int] = None
    downloaded: int = 0
    resume_from: int = 0            # resume byte offset

    started_at: float = field(default_factory=time.time)
    speed_window: deque = field(default_factory=lambda: deque(maxlen=50))
    # each entry: (timestamp, bytes_since_last_sample)

    cancel_event: threading.Event = field(default_factory=threading.Event)
    pause_event:  threading.Event = field(default_factory=threading.Event)

    # state: waiting / downloading / paused / moving / done / error / canceled / skipped
    state: str = "waiting"
    temp_path: str = ""             # current .part path (empty for direct DL)
    retry_count: int = 0            # number of attempts already made
    error_msg: str = ""

    # Multipart segments — empty for standard single-stream download
    segments: List[SegmentInfo] = field(default_factory=list)

    # ── Worker type & extended metadata ────────────────────────────────────────
    # Formally declared here to avoid dynamic attribute creation at runtime.

    # "http" for standard HTTP downloads, "ytdlp" for streaming URLs
    worker_type:  str  = "http"

    # True when this download was injected by a remote client (shows 📡 badge)
    from_remote:  bool = False

    # yt-dlp specific — only relevant when worker_type == "ytdlp"
    yt_format_id:  Optional[str] = None   # format_id chosen in quality popup
    yt_audio_only: bool          = False  # audio-only extraction mode
    yt_retry:      bool          = False  # True on auto-retry with alternate client
