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
    speed_window: deque = field(default_factory=lambda: deque(maxlen=50))
    # chaque entrée : (timestamp, bytes_depuis_dernier_sample)

    cancel_event: threading.Event = field(default_factory=threading.Event)
    pause_event:  threading.Event = field(default_factory=threading.Event)

    # state: waiting / downloading / paused / moving / done / error / canceled / skipped
    state: str = "waiting"
    temp_path: str = ""             # chemin du .part en cours (vide si DL direct)
    retry_count: int = 0            # nombre de tentatives déjà effectuées
    error_msg: str = ""
