from typing import Optional

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column


class PluginOrigin:
    """Which plugin created a row, for rows a plugin created.

    `source` is the plugin id, `source_key` the item's key in its manifest,
    and `source_hash` a hash of the row's content when the plugin last wrote
    it. A row whose content no longer matches the hash was edited and belongs
    to the user: updates leave it alone and removing the plugin keeps it.
    """

    source: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    source_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
