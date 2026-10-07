"""Configuration loading and validation for the Dude engine.

One documented configuration path per process: environment variables (loaded
from a local .env file if present, then from the process environment). Values
are validated at startup with actionable, non-sensitive errors.

Phase 0 keeps the surface intentionally tiny: log level and data directory.
No secrets, no model-provider settings, no feature flags.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(Exception):
    """Raised for invalid configuration; message is safe to show to a user."""


@dataclass(frozen=True)
class EngineConfig:
    log_level: str  # DEBUG | INFO | WARNING | ERROR
    data_dir: Path


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from a local .env, if present.

    Never overrides variables already set in the real environment. Malformed
    lines are skipped silently — this file is a developer convenience only.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except OSError:
        return  # unreadable file is not fatal; real env still applies
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def default_data_dir() -> Path:
    """Per-user application data directory (never the install directory)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            # Very defensive fallback; LOCALAPPDATA is set on all real systems.
            base = str(Path.home() / "AppData" / "Local")
        return Path(base) / "Dude"
    return Path.home() / ".local" / "share" / "Dude"


def load_config() -> EngineConfig:
    """Load and validate configuration. Raises ConfigError on bad values."""
    _load_env_file(Path(".env"))

    log_level = os.environ.get("DUDE_LOG_LEVEL", "INFO").strip().upper()
    if log_level not in VALID_LOG_LEVELS:
        raise ConfigError(
            f"DUDE_LOG_LEVEL must be one of {', '.join(VALID_LOG_LEVELS)} "
            f"(got an invalid value)."
        )

    raw_dir = os.environ.get("DUDE_DATA_DIR", "").strip()
    data_dir = Path(raw_dir) if raw_dir else default_data_dir()

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"Could not create data directory '{data_dir}': {exc.strerror or exc}"
        )

    return EngineConfig(log_level=log_level, data_dir=data_dir)
