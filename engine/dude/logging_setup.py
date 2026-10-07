"""Logging setup for the Dude engine.

Diagnostic logs go to standard error (and mirror to a file under the data
directory). Standard output is reserved exclusively for protocol frames —
this module never writes there.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config import EngineConfig

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def setup_logging(config: EngineConfig, log_file_name: str = "engine.log") -> logging.Handler:
    """Configure the root logger: stderr always, file mirror best-effort.

    Returns the file handler when a file sink was attached, else None.
    Diagnostics deliberately stay away from stdout (protocol channel).
    """
    root = logging.getLogger()
    root.setLevel(config.log_level)
    root.handlers.clear()

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(stderr_handler)

    file_handler: logging.Handler | None = None
    try:
        log_path = Path(config.data_dir) / log_file_name
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        root.addHandler(file_handler)
    except OSError:
        # A read-only or unwritable data dir must not stop the engine; the
        # stderr sink still works. The config error path already reported the
        # directory problem at startup.
        logging.getLogger(__name__).warning(
            "could not attach log file sink; logging to stderr only"
        )

    return file_handler
