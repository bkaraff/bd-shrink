"""Logging setup: mirror logs to a system path, a user path, and the work dir.

Main log file:
  - /var/log/bd-shrink/bd_shrink_YYYYMMDD_HHMMSS.log (if writable)
  - ~/.local/share/bd-shrink/logs/bd_shrink_YYYYMMDD_HHMMSS.log (fallback)
  - ${WORK_DIR}/bd_shrink.log (always; for convenience during resume)
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from bd_shrink.config import Config

LOGGER_NAME = "bd_shrink"
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _system_log_dir() -> Path:
    return Path("/var/log/bd-shrink")


def _user_log_dir() -> Path:
    return Path.home() / ".local" / "share" / "bd-shrink" / "logs"


def _writable_log_dir(candidate: Path) -> Optional[Path]:
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_test"
        probe.touch()
        probe.unlink()
        return candidate
    except OSError:
        return None


def _new_log_path(work_dir: str) -> Optional[str]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for candidate in (_system_log_dir(), _user_log_dir()):
        d = _writable_log_dir(candidate)
        if d is not None:
            return str(d / f"bd_shrink_{stamp}.log")
    return None


def setup_logging(
    work_dir: str,
    config: Optional[Config] = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure and return the bd_shrink logger.

    Attaches up to two file handlers (a timestamped main log + a work-dir
    mirror) and a stderr stream handler. Safe to call once per run; clears
    any previous handlers to avoid duplicate logging on resume.

    Args:
        work_dir: Work directory; always mirrored to bd_shrink.log here.
        config: Config (unused for now; reserved for --verbose/level flags).
        level: Logging level (default INFO).

    Returns:
        Configured logger named "bd_shrink".
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)

    # Reset handlers (resume re-invokes setup_logging).
    for h in list(logger.handlers):
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    formatter = logging.Formatter(_FORMAT, _DATEFMT)

    # Main log file (system or user-writable), best effort.
    main_log_path = _new_log_path(work_dir)
    if main_log_path is not None:
        try:
            fh = logging.FileHandler(main_log_path)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except OSError:
            pass

    # Work-dir mirror (always).
    try:
        os.makedirs(work_dir, exist_ok=True)
        work_log = os.path.join(work_dir, "bd_shrink.log")
        wfh = logging.FileHandler(work_log)
        wfh.setFormatter(formatter)
        logger.addHandler(wfh)
    except OSError:
        pass

    # Stderr stream handler (user-facing progress).
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    logger.propagate = False
    return logger
