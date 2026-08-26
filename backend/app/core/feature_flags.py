"""Environment-driven capability flags.

One parser, so everything that asks "is this capability enabled on this
deployment?" gets the same answer: the `/api/info` payload the frontend
reads, and the module resolver's deployment layer.
"""
import os

TRUTHY = ("1", "true", "yes", "on")


def feature_flag(name: str, default: str = "false") -> bool:
    """Read a boolean capability flag from the environment."""
    return os.getenv(name, default).strip().lower() in TRUTHY
