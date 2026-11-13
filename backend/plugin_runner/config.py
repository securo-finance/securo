"""The runner's settings, from its own environment only.

The runner container gets these and nothing else: no `SECRET_KEY`, no
database URL, no provider credentials.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


#: Shortest runner token and egress secret accepted. Plugin code runs next to
#: the runner's API, so a guessable token would let a plugin install code
#: under another plugin's name and receive that plugin's secrets.
MIN_SECRET_LENGTH = 32


def secret_problem(name: str, value: str) -> str | None:
    """Why `value` can't be used as `name`, or None when it can."""
    if not value:
        return f"{name} must be set"
    if len(value) < MIN_SECRET_LENGTH:
        return f"{name} must be at least {MIN_SECRET_LENGTH} characters (e.g. `openssl rand -hex 32`)"
    if "change-in-production" in value:
        return f"{name} is a placeholder; set it to a long random value (e.g. `openssl rand -hex 32`)"
    return None


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name) or default)


@dataclass(frozen=True)
class RunnerSettings:
    token: str = field(default_factory=lambda: os.environ.get("PLUGINS_RUNNER_TOKEN", ""))
    """Shared with the backend; every request but /health must carry it."""
    egress_secret: str = field(default_factory=lambda: os.environ.get("PLUGINS_EGRESS_SECRET", ""))
    egress_proxy: str = field(default_factory=lambda: os.environ.get("PLUGINS_EGRESS_PROXY", "plugins-egress:3128"))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("PLUGINS_DATA_DIR", "/var/lib/securo-plugins")))
    package_index: str = field(
        default_factory=lambda: os.environ.get("PLUGINS_PACKAGE_INDEX", "https://pypi.org/simple")
    )
    package_hosts: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            h.strip()
            for h in os.environ.get("PLUGINS_PACKAGE_HOSTS", "pypi.org,files.pythonhosted.org").split(",")
            if h.strip()
        )
    )
    call_timeout: int = field(default_factory=lambda: _int("PLUGINS_CALL_TIMEOUT", 60))
    install_timeout: int = field(default_factory=lambda: _int("PLUGINS_INSTALL_TIMEOUT", 600))
    memory_mb: int = field(default_factory=lambda: _int("PLUGINS_MEMORY_MB", 512))
    max_calls: int = field(default_factory=lambda: _int("PLUGINS_MAX_CALLS", 4))
    archive_limit_mb: int = field(default_factory=lambda: _int("PLUGINS_ARCHIVE_LIMIT_MB", 20))
    first_uid: int = 20000
