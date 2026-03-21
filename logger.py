"""
logger.py — Centralized logging for TurboDownloader.

Usage:
    from logger import get_logger
    log = get_logger(__name__)
    log.info("Server started")
    log.debug("Token: %s", token)   # only shown with --debug

Configure at startup via setup_logging(debug=True/False).
"""

import logging
import sys

_LOG_FORMAT        = "[%(asctime)s] %(levelname)-7s %(name)s — %(message)s"
_LOG_FORMAT_SIMPLE = "%(levelname)-7s %(name)s — %(message)s"
_DATE_FORMAT       = "%H:%M:%S"


def setup_logging(debug: bool = False):
    """
    Call once at startup (in main.py).
    debug=True → show DEBUG messages in console.
    debug=False → show INFO and above only.
    """
    level = logging.DEBUG if debug else logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT_SIMPLE, datefmt=_DATE_FORMAT))

    root = logging.getLogger("turbodownloader")
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
    root.propagate = False

    if debug:
        root.debug("Debug logging enabled")


def get_logger(name: str) -> logging.Logger:
    """
    Returns a logger scoped under 'turbodownloader'.
    Usage: log = get_logger(__name__)
    """
    if not name.startswith("turbodownloader"):
        name = f"turbodownloader.{name}"
    return logging.getLogger(name)
