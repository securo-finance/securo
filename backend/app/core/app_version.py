"""The running backend's version."""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path


def securo_version() -> str | None:
    """The running backend's version, or None when it can't be told.

    pyproject.toml ships with the code, so it can't lag behind it. Package
    metadata is only a fallback: the Docker image doesn't install the project
    as a package at all, and an editable install's metadata keeps the version
    it was installed at, which goes stale on a long-lived development image.
    """
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        return tomllib.loads(pyproject.read_text())["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        pass
    try:
        return package_version("securo-backend")
    except PackageNotFoundError:
        return None
