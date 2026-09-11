"""Logging configuration.

A GitHub code search runs for hours across rate-limit pauses, so progress output
needs timestamps and a level that can be turned down without editing code.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def configure(level: str = "INFO") -> None:
    """Install a single stderr handler. Safe to call more than once."""
    global _CONFIGURED
    if _CONFIGURED:
        logging.getLogger().setLevel(level)
        return

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # The HTTP stack logs one line per connection at INFO; that buries our own output.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    _CONFIGURED = True
