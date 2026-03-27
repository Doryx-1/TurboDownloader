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

    # Remote duplicate-file resolution
    conflict_event:  Optional[threading.Event] = None   # set by client's resolve
    conflict_action: str = ""                           # "replace"|"skip"|"rename"

    # Playlist grouping
    playlist_group_id:    Optional[str] = None
    playlist_group_title: Optional[str] = None
    playlist_index:       int           = 0
    playlist_total:       int           = 0

    last_activity: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict (excludes threading primitives)."""
        return {
            "url":           self.url,
            "filename":      self.filename,
            "dest_path":     self.dest_path,
            "relative_path": self.relative_path,
            "total_size":    self.total_size,
            "resume_from":   self.resume_from,
            "downloaded":    self.downloaded,
            "state":         self.state,
            "temp_path":     self.temp_path,
            "worker_type":   self.worker_type,
            "yt_format_id":  self.yt_format_id,
            "yt_audio_only": self.yt_audio_only,
            "from_remote":   self.from_remote,
            "segments": [
                {
                    "index":      s.index,
                    "byte_start": s.byte_start,
                    "byte_end":   s.byte_end,
                    "temp_path":  s.temp_path,
                    "downloaded": s.downloaded,
                    "done":       s.done,
                }
                for s in self.segments
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DownloadItem":
        """Reconstruct from a serialized dict. State always reset to 'waiting'."""
        item = cls(
            url=d["url"],
            filename=d["filename"],
            dest_path=d["dest_path"],
        )
        item.relative_path = d.get("relative_path", "")
        item.total_size    = d.get("total_size")
        item.resume_from   = d.get("resume_from", 0)
        item.downloaded    = d.get("downloaded", 0)
        item.state         = "waiting"   # always re-queue as waiting
        item.temp_path     = d.get("temp_path", "")
        item.worker_type   = d.get("worker_type", "http")
        item.yt_format_id  = d.get("yt_format_id")
        item.yt_audio_only = d.get("yt_audio_only", False)
        item.from_remote   = False   # never restore remote items
        item.segments = [
            SegmentInfo(
                index=s["index"],
                byte_start=s["byte_start"],
                byte_end=s["byte_end"],
                temp_path=s.get("temp_path", ""),
                downloaded=s.get("downloaded", 0),
                done=s.get("done", False),
            )
            for s in d.get("segments", [])
        ]
        return item
