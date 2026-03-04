import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


# État possible : waiting / downloading / done / error / canceled / skipped

@dataclass
class DownloadItem:
    url: str
    filename: str
    dest_path: str
    relative_path: str = ""         # sous-dossier relatif (arbo. originale)

    total_size: Optional[int] = None
    downloaded: int = 0
    resume_from: int = 0            # offset reprise

    started_at: float = field(default_factory=time.time)
    speed_window: deque = field(default_factory=lambda: deque(maxlen=10))
    # chaque entrée : (timestamp, bytes_depuis_dernier_sample)

    cancel_event: threading.Event = field(default_factory=threading.Event)

    # state: waiting / downloading / done / error / canceled / skipped
    state: str = "waiting"
    error_msg: str = ""
