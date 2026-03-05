"""
notifier.py — Desktop notifications pour TurboDownloader.
Utilise plyer (cross-platform). Fallback silencieux si non installé.
"""

try:
    from plyer import notification as _plyer_notif
    _PLYER_OK = True
except ImportError:
    _PLYER_OK = False


APP_NAME = "TurboDownloader"
# Duration d'affichage de la notification (secondes)
TOAST_TIMEOUT = 6


def notify(title: str, message: str) -> bool:
    """
    Displays a desktop notification.
    Returns True if sent, False if plyer is missing or an error occurred.
    """
    if not _PLYER_OK:
        print(f"[notifier] plyer not installed — notification skipped: {title} / {message}")
        return False
    try:
        _plyer_notif.notify(
            title=title,
            message=message,
            app_name=APP_NAME,
            timeout=TOAST_TIMEOUT,
        )
        return True
    except Exception as e:
        print(f"[notifier] notification error: {e}")
        return False


def notify_batch_done(done: int, errors: int, canceled: int) -> bool:
    """Batch completion notification — called when all workers are done."""
    parts = []
    if done:
        parts.append(f"{done} terminé{'s' if done > 1 else ''}")
    if errors:
        parts.append(f"{errors} erreur{'s' if errors > 1 else ''}")
    if canceled:
        parts.append(f"{canceled} annulé{'s' if canceled > 1 else ''}")

    total = done + errors + canceled
    if total == 0:
        return False

    title   = f"{APP_NAME} — Batch terminé"
    message = ", ".join(parts) if parts else "Aucun fichier traité"
    return notify(title, message)
