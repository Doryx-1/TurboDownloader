"""speed_tracker.py — SpeedTrackerMixin for TurboDownloader."""
import time
from logger import get_logger

_log = get_logger("speed_tracker")


class SpeedTrackerMixin:

    def _record_bytes(self, n: int):
        """Records n downloaded bytes — called from worker threads."""
        now = time.time()
        with self._speed_lock:
            self._speed_samples.append((now, n))
            self._global_total_bytes += n
            while self._speed_samples and now - self._speed_samples[0][0] > 3.0:
                self._speed_samples.popleft()

    def _tick_global_speed(self):
        with self._speed_lock:
            now = time.time()
            while self._speed_samples and now - self._speed_samples[0][0] > 3.0:
                self._speed_samples.popleft()
            samples = list(self._speed_samples)
            total = self._global_total_bytes

        if len(samples) >= 2:
            window_bytes = sum(s[1] for s in samples)
            window_sec = samples[-1][0] - samples[0][0]
            speed = window_bytes / max(window_sec, 0.1)
        elif samples:
            speed = samples[0][1]
        else:
            speed = 0.0

        speed_text = "–" if speed == 0.0 else self._fmt_speed(speed)
        self.global_speed_label.configure(text=speed_text)
        self.global_dl_label.configure(text=f"Total: {self._fmt_size(total)}")

        # ── Taskbar update ────────────────────────────────────────────
        if self._taskbar:
            active = [it for it in self.items.values()
                      if it.state in ("downloading", "moving", "waiting", "paused")]
            has_error = any(it.state == "error" for it in self.items.values())

            if not active:
                if has_error:
                    self._taskbar.set_error()
                else:
                    self._taskbar.clear()
            else:
                known    = [it for it in active if it.total_size]
                all_paused = all(it.state == "paused" for it in active)
                if all_paused:
                    self._taskbar.set_paused()
                elif not known:
                    self._taskbar.set_indeterminate()
                else:
                    total_dl  = sum(it.resume_from + it.downloaded for it in known)
                    total_sz  = sum(it.total_size for it in known)
                    ratio     = total_dl / total_sz if total_sz else 0.0
                    if has_error:
                        self._taskbar.set_error()
                    else:
                        self._taskbar.set_progress(ratio)

        self.after(1000, self._tick_global_speed)
